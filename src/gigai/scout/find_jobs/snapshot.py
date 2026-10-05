"""Metadata snapshot of the company index (0110-026, packets S1 export and S3 import).

A snapshot is what a fresh install downloads instead of crawling every board:
the company index's public posting metadata, the title-tag table and the
per-board validators (ETag / Last-Modified). It is built on the operator's
machine from a cache directory and published by the operator as a release
asset; the export side never talks to a network and never runs ``gh``. The
import side makes plain GET requests for the manifest and the files it names,
and none at all when the setting is off.

**Metadata only.** No description, body or text field is ever written, and no
path of the machine it was built on. :func:`export_snapshot` scans every
output before it reports success and removes what it wrote when the scan, the
read-back or a digest check fails.

Layout (everything under one output directory)::

    index-<date>.jsonl.xz    one posting per line (live postings only)
    tags-<date>.jsonl.xz     one title tag per line (current tagger version)
    boards-<date>.jsonl.xz   one board per line (validators)
    delta-<date>.jsonl.xz    only with a base manifest: puts and deletes since it
    manifest.json            written LAST; names and digests every file

This module is split in three sections: **format** (manifest, file names, the
digest/read-back helpers both sides use), **import** and **export**. The
import section uses nothing from the export section.

Import (:func:`import_snapshot`) downloads only what it needs (the three full
files, or the one delta when the local snapshot is the delta's base), checks
every SHA-256 before it reads a row, plans every change in memory and only
then writes. Rules:

* a board the machine checked itself at or after the snapshot did is left
  alone (local fresher wins), and so is a posting that changed locally later;
* nothing local is deleted. The one exception is a board (or posting) the
  snapshot itself put there and the machine has not checked since: the
  snapshot may mark its postings removed and, for a board on the removal
  list, delete it;
* validators (ETag / Last-Modified / body digest) are taken only when the
  board's stored postings end up equal to the snapshot's; a board that mixes
  local and snapshot postings loses its validators, so the next refresh reads
  it in full;
* the index writes are journalled: a failure restores every file, and a crash
  is undone at the start of the next import;
* unreachable, not published, or turned off is a quiet result with a reason,
  never an exception.

State lives in ``<home>/cache/scout/snapshot/`` (``state.json``: what was
imported and the last attempt; ``boards.json``: the boards the last snapshot
listed, and for those it created the ``checked_at`` it gave them).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
import json
import logging
import lzma
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time
from urllib.parse import urljoin, urlsplit

import httpx

from ...canonical import digest_imported_bytes
from .company_index import CompanyIndex, CompanyIndexEntry, IndexedPosting, company_key, index_stamp
from .posting_tags import TAGGER_VERSION, normalize_title
from .tag_store import SOURCE_MODEL, TagStore, TitleTag

# =====================================================================
# Format: manifest, files, digests (shared by export and the later import)
# =====================================================================

FORMAT_NAME = "gigai-scout-snapshot"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"

ROLE_INDEX = "index"
ROLE_TAGS = "tags"
ROLE_BOARDS = "boards"
ROLE_DELTA = "delta"
FULL_ROLES = (ROLE_INDEX, ROLE_TAGS, ROLE_BOARDS)

#: A reader refuses a manifest whose format_version it does not know.
_XZ_PRESET = 6


class SnapshotError(ValueError):
    """A snapshot that cannot be written or trusted. ``code`` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def file_name(role: str, date: str) -> str:
    return f"{role}-{date}.jsonl.xz"


def sha256_hex(data: bytes) -> str:
    """Hex SHA-256 of ``data`` through the canonical module (the one place product code hashes)."""

    return digest_imported_bytes(data).split(":", 1)[1]


def sha256_file(path: Path) -> str:
    return sha256_hex(path.read_bytes())


def _compress(lines: Iterable[Mapping[str, object]]) -> tuple[bytes, int]:
    """Deterministic xz of canonical JSON lines (sorted keys, no timestamps)."""

    rows = 0
    parts: list[bytes] = []
    for line in lines:
        parts.append(json.dumps(line, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
        rows += 1
    raw = b"\n".join(parts) + (b"\n" if parts else b"")
    return lzma.compress(raw, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=_XZ_PRESET), rows


def read_lines(path: Path) -> Iterator[dict[str, object]]:
    """Decode one snapshot file back into its rows."""

    with lzma.open(path, "rb") as handle:
        for raw in handle:
            if raw.strip():
                yield json.loads(raw.decode("utf-8"))


def load_manifest(path: Path) -> dict[str, object]:
    """Read a manifest and refuse an unknown format or version."""

    try:
        manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SnapshotError("manifest_unreadable", f"cannot read manifest {path}: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("format") != FORMAT_NAME:
        raise SnapshotError("manifest_foreign", f"{path} is not a {FORMAT_NAME} manifest")
    if manifest.get("format_version") != FORMAT_VERSION:
        raise SnapshotError(
            "manifest_version", f"manifest format_version {manifest.get('format_version')!r} is not {FORMAT_VERSION}"
        )
    if not isinstance(manifest.get("files"), dict):
        raise SnapshotError("manifest_foreign", f"{path} lists no files")
    return manifest


def verify_files(manifest: Mapping[str, object], directory: Path) -> dict[str, int]:
    """Re-open every file the manifest lists: size, SHA-256 and row count must match.

    Returns ``{file name: rows}``; raises :class:`SnapshotError` on the first mismatch.
    """

    rows_by_file: dict[str, int] = {}
    files = manifest["files"]
    assert isinstance(files, dict)
    for name, meta in sorted(files.items()):
        path = Path(directory) / name
        if not path.is_file():
            raise SnapshotError("file_missing", f"{name} is listed in the manifest but is not in {directory}")
        if path.stat().st_size != meta.get("size"):
            raise SnapshotError("size_mismatch", f"{name}: size differs from the manifest")
        if sha256_file(path) != meta.get("sha256"):
            raise SnapshotError("digest_mismatch", f"{name}: SHA-256 differs from the manifest")
        try:
            rows = sum(1 for _ in read_lines(path))
        except (OSError, lzma.LZMAError, ValueError) as error:
            raise SnapshotError("file_unreadable", f"{name}: cannot be decoded: {error}") from error
        if rows != meta.get("rows"):
            raise SnapshotError("rows_mismatch", f"{name}: {rows} rows, the manifest says {meta.get('rows')}")
        rows_by_file[name] = rows
    return rows_by_file


# --- the privacy scan -------------------------------------------------

#: A key with one of these names (any depth) never ships.
_FORBIDDEN_KEYS = frozenset(
    {
        "description", "descriptions", "description_text", "descriptionplain", "descriptionhtml", "body", "content",
        "content_text", "html", "text", "resume", "profile", "profile_id", "path", "home", "workpad",
    }
)
_PRIVATE_PATH = re.compile(r"(?:^|[\s\"'=])(?:~/|/Users/|/home/|/private/|/var/folders/|[A-Za-z]:\\\\Users\\\\)|/\.gigai\b|\.gigai/")


def _walk_keys(value: object) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from _walk_keys(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_keys(item)


def _walk_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_strings(item)


def scan_for_private_content(directory: Path, names: Iterable[str], *, extra_markers: Iterable[str] = ()) -> None:
    """Refuse an output that carries a description-like field or a private path.

    Decodes each named file (and reads ``manifest.json`` when listed) and checks
    every key and string. ``extra_markers`` are literal strings (the build
    machine's home directory) that must appear nowhere.
    """

    markers = [m for m in extra_markers if m and len(m) > 1]
    for name in names:
        path = Path(directory) / name
        if name.endswith(".xz"):
            documents: Iterator[object] = iter(read_lines(path))
        else:
            documents = iter([json.loads(path.read_text(encoding="utf-8"))])
        for document in documents:
            for key in _walk_keys(document):
                if key.lower() in _FORBIDDEN_KEYS:
                    raise SnapshotError("forbidden_field", f"{name}: field {key!r} must never ship in a snapshot")
            for text in _walk_strings(document):
                if _PRIVATE_PATH.search(text) or any(marker in text for marker in markers):
                    raise SnapshotError("private_path", f"{name}: a value looks like a private path")


# =====================================================================
# Import (S3): fetch, verify, plan, then write
# =====================================================================

#: Where the operator publishes (the ``gh release upload`` commands the export prints). A setting, so a later data repo is a settings change.
DEFAULT_MANIFEST_URL = "https://github.com/karthik446/gigai/releases/download/scout-snapshot/manifest.json"
#: ``0``/``false``/``off``/``no`` turns the snapshot download off, ``1``/``true``/``on``/``yes`` on, whatever the setting says.
SNAPSHOT_ENV = "GIGAI_SCOUT_SNAPSHOT"
#: Overrides ``snapshot.manifest_url``.
MANIFEST_URL_ENV = "GIGAI_SCOUT_SNAPSHOT_MANIFEST_URL"
STATE_SCHEMA = "scout-snapshot-state:1"
LEDGER_SCHEMA = "scout-snapshot-boards:1"

RESULT_IMPORTED = "imported"
RESULT_UP_TO_DATE = "up_to_date"
RESULT_SKIPPED = "skipped"  # nothing was tried, or the snapshot could not be reached
RESULT_REFUSED = "refused"  # the snapshot was read and is not trusted
RESULT_FAILED = "failed"  # writing failed; every index file was restored

KIND_FULL = "full"
KIND_DELTA = "delta"

#: :func:`maybe_import_snapshot` asks for the manifest at most this often once the index holds anything.
CHECK_INTERVAL_HOURS = 6.0

_STATE_NAME = "state.json"
_LEDGER_NAME = "boards.json"
_ROLLBACK_NAME = "rollback"
_LOCK_NAME = "import.lock"
_LOCK_STALE_SECONDS = 1800.0
_MANIFEST_LIMIT = 1 << 20
_MAX_FILE_BYTES = 1 << 30
_FILE_NAME = re.compile(r"(index|tags|boards|delta)-\d{4}-\d{2}-\d{2}\.jsonl\.xz")
_LOOPBACK = frozenset({"127.0.0.1", "localhost", "::1"})
_OFF = frozenset({"0", "false", "off", "no"})
_ON = frozenset({"1", "true", "on", "yes"})
#: What ``model`` / ``prompt_version`` say for a function a snapshot supplied.
_TAG_ORIGIN = "snapshot"

_IMPORT_LOCK = threading.Lock()
_logger = logging.getLogger("gigai.scout.snapshot")


# --- the setting ------------------------------------------------------


@dataclass(frozen=True)
class SnapshotSetting:
    """Whether the snapshot may be downloaded, from where, and what said so."""

    enabled: bool
    manifest_url: str
    source: str

    def to_json(self) -> dict[str, object]:
        return {"enabled": self.enabled, "manifest_url": self.manifest_url, "source": self.source}


def snapshot_setting(home_root: Path, target: Path | None, *, environ: Mapping[str, str] | None = None) -> SnapshotSetting:
    """The ``snapshot`` block of the project's settings file: the environment, the file, then the defaults (ON).

    ``{"schema_version": "scout-settings:1", "snapshot": {"enabled": false,
    "manifest_url": "https://..."}}``. A file that cannot be read is OFF, as
    for the background refresh: a job that makes requests does not guess.
    """

    from .refresh_tick import (
        SETTINGS_SCHEMA,
        SOURCE_DEFAULT,
        SOURCE_ENVIRONMENT,
        SOURCE_SETTING,
        SOURCE_UNREADABLE,
        settings_path,
    )

    env = os.environ if environ is None else environ

    def forced(setting: SnapshotSetting) -> SnapshotSetting:
        url = (env.get(MANIFEST_URL_ENV) or "").strip()
        if url:
            setting = replace(setting, manifest_url=url, source=SOURCE_ENVIRONMENT)
        raw = (env.get(SNAPSHOT_ENV) or "").strip().lower()
        if raw in _OFF:
            return replace(setting, enabled=False, source=SOURCE_ENVIRONMENT)
        if raw in _ON:
            return replace(setting, enabled=True, source=SOURCE_ENVIRONMENT)
        return setting

    default = SnapshotSetting(True, DEFAULT_MANIFEST_URL, SOURCE_DEFAULT)
    unreadable = SnapshotSetting(False, DEFAULT_MANIFEST_URL, SOURCE_UNREADABLE)
    if target is None:
        return forced(default)
    try:
        path = settings_path(Path(home_root), Path(target))
    except Exception:  # noqa: BLE001 - no bound project yet: there is no settings file to read
        return forced(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return forced(default)
    except (OSError, ValueError):
        return forced(unreadable)
    if not isinstance(payload, dict) or payload.get("schema_version") != SETTINGS_SCHEMA:
        return forced(unreadable)
    block = payload.get("snapshot", {})
    if not isinstance(block, dict):
        return forced(unreadable)
    enabled = block.get("enabled", True)
    url = block.get("manifest_url", DEFAULT_MANIFEST_URL)
    if type(enabled) is not bool or type(url) is not str or not url.strip():
        return forced(unreadable)
    return forced(SnapshotSetting(enabled, url.strip(), SOURCE_SETTING if block else SOURCE_DEFAULT))


# --- results and state ------------------------------------------------


@dataclass
class ImportCounts:
    """What one import wrote."""

    boards: int = 0  # company files written
    postings: int = 0  # postings taken from the snapshot
    tags: int = 0  # title tags added, plus functions filled in
    boards_kept_local: int = 0  # the machine's own check was at least as new
    boards_removed: int = 0
    postings_removed: int = 0


@dataclass(frozen=True)
class ImportResult:
    """The outcome of one import attempt. Never an exception: ``reason`` says why nothing was imported."""

    status: str
    reason: str | None = None
    message: str = ""
    kind: str | None = None
    as_of: str | None = None
    source: str | None = None
    counts: Mapping[str, int] = field(default_factory=dict)

    @property
    def imported(self) -> bool:
        return self.status == RESULT_IMPORTED

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "reason": self.reason,
            "message": self.message,
            "kind": self.kind,
            "as_of": self.as_of,
            "source": self.source,
            "counts": dict(self.counts),
        }


def snapshot_dir(home_root: Path) -> Path:
    """``<home>/cache/scout/snapshot``: the import's own state, beside the caches it fills."""

    return Path(home_root) / "cache" / "scout" / "snapshot"


def _read_json(path: Path, schema: str) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) and payload.get("schema_version") == schema else {}


def _write_json(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
    try:
        temp.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")), encoding="utf-8")
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def _parse_time(value: object) -> datetime | None:
    if type(value) is not str or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _older(local: str | None, incoming: str | None) -> bool:
    """True when the local stamp is strictly before the incoming one. An unreadable incoming stamp never wins."""

    theirs = _parse_time(incoming)
    if theirs is None:
        return False
    mine = _parse_time(local)
    return mine is None or mine < theirs


def snapshot_status(home_root: Path, target: Path | None = None, *, environ: Mapping[str, str] | None = None) -> dict[str, object]:
    """What the UI and the CLI show: the setting, the snapshot in use and the last attempt. Reads two small files."""

    setting = snapshot_setting(Path(home_root), target, environ=environ)
    state = _read_json(snapshot_dir(home_root) / _STATE_NAME, STATE_SCHEMA)
    counts = state.get("counts")
    return {
        "enabled": setting.enabled,
        "setting_source": setting.source,
        "manifest_url": setting.manifest_url,
        "as_of": state.get("as_of"),
        "source": state.get("source"),
        "kind": state.get("kind"),
        "imported_at": state.get("imported_at"),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_result": state.get("last_result"),
        "last_reason": state.get("last_reason"),
        "last_message": state.get("last_message"),
        "counts": counts if isinstance(counts, dict) else None,
    }


# --- where the files come from ----------------------------------------


class _Unavailable(Exception):
    """The snapshot could not be reached. Not an error to the user: the import is a no-op with this reason."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class _DirSource:
    """A snapshot directory on this machine (an export's ``--out``): read in place, no request."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def manifest(self) -> bytes:
        try:
            return (self.directory / MANIFEST_NAME).read_bytes()
        except OSError:
            raise _Unavailable("not_published", "There is no snapshot manifest in that directory.") from None

    def materialize(self, files: Mapping[str, int], work: Path) -> Path:
        return self.directory


class _HttpSource:
    """The manifest URL and its sibling files (release assets), by plain GET."""

    def __init__(self, url: str, client: httpx.Client) -> None:
        self.url = url
        self._client = client

    def manifest(self) -> bytes:
        try:
            response = self._client.get(self.url)
        except (httpx.HTTPError, httpx.InvalidURL):
            raise _Unavailable("offline", "The snapshot could not be reached.") from None
        if response.status_code == 404:
            raise _Unavailable("not_published", "No snapshot is published at the configured address.")
        if response.status_code != 200:
            raise _Unavailable("unavailable", f"The snapshot address answered HTTP {response.status_code}.")
        if len(response.content) > _MANIFEST_LIMIT:
            raise SnapshotError("manifest_foreign", "the manifest is larger than a manifest can be")
        return response.content

    def materialize(self, files: Mapping[str, int], work: Path) -> Path:
        for name, size in files.items():
            try:
                with self._client.stream("GET", urljoin(self.url, name)) as response:
                    if response.status_code != 200:
                        raise _Unavailable("unavailable", f"A snapshot file answered HTTP {response.status_code}.")
                    received = 0
                    with (work / name).open("wb") as handle:
                        for chunk in response.iter_bytes():
                            received += len(chunk)
                            if received > size:
                                raise SnapshotError("size_mismatch", f"{name}: larger than the manifest says")
                            handle.write(chunk)
            except (httpx.HTTPError, httpx.InvalidURL):
                raise _Unavailable("offline", "The snapshot download was interrupted.") from None
        return work


def _default_client() -> httpx.Client:
    # Release assets redirect to the storage host. No proxy settings are read, as for the board requests.
    return httpx.Client(timeout=httpx.Timeout(10.0, read=60.0), follow_redirects=True, trust_env=False)


def _is_url(source: str) -> bool:
    return source.startswith(("http://", "https://"))


def _check_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme == "http" and (parts.hostname or "") not in _LOOPBACK:
        # The digests come from the manifest, so the manifest must come over TLS.
        raise SnapshotError("insecure_url", "the snapshot address must be https")


# --- reading a verified snapshot into a plan --------------------------


def _check_manifest(manifest: Mapping[str, object]) -> None:
    if manifest.get("contents") != "metadata-only":
        raise SnapshotError("not_metadata_only", "the manifest does not say the snapshot is metadata only")
    if _parse_time(manifest.get("as_of")) is None:
        raise SnapshotError("manifest_foreign", "the manifest has no usable as_of")
    files = manifest["files"]
    assert isinstance(files, dict)
    for name, meta in files.items():
        match = _FILE_NAME.fullmatch(name) if type(name) is str else None
        usable = (
            match is not None
            and isinstance(meta, dict)
            and meta.get("role") == match.group(1)
            and type(meta.get("sha256")) is str
            and type(meta.get("rows")) is int
            and type(meta.get("size")) is int
            and 0 <= meta["size"] <= _MAX_FILE_BYTES
        )
        if not usable:
            raise SnapshotError("manifest_foreign", "the manifest lists a file this version cannot use")


def _names_for(manifest: Mapping[str, object], kind: str) -> dict[str, int]:
    """``{file name: size}`` of the files one kind of import needs."""

    files = manifest["files"]
    assert isinstance(files, dict)
    if kind == KIND_DELTA:
        delta = manifest["delta"]
        assert isinstance(delta, dict)
        name = delta["file"]
        return {name: files[name]["size"]}
    wanted: dict[str, int] = {}
    for role in FULL_ROLES:
        names = [name for name, meta in sorted(files.items()) if meta["role"] == role]
        if len(names) != 1:
            raise SnapshotError("file_missing", f"the manifest must list exactly one {role} file")
        wanted[names[0]] = files[names[0]]["size"]
    return wanted


def _kinds(manifest: Mapping[str, object], state: Mapping[str, object]) -> tuple[str, ...]:
    """Delta first when the local snapshot is exactly the delta's base; the full files otherwise (and as the fallback)."""

    delta = manifest.get("delta")
    base = delta.get("base") if isinstance(delta, dict) else None
    files = manifest["files"]
    assert isinstance(files, dict)
    held = state.get("manifest_sha256")
    if held and isinstance(base, dict) and base.get("manifest_sha256") == held and isinstance(delta, dict):
        name = delta.get("file")
        if type(name) is str and name in files and files[name]["role"] == ROLE_DELTA:
            return (KIND_DELTA, KIND_FULL)
    return (KIND_FULL,)


class _NeedFull(Exception):
    """A delta cannot be applied to what is stored now (a board of its base is gone): take the full files."""


def _text(row: Mapping[str, object], name: str, *, required: bool = False) -> str | None:
    value = row.get(name)
    if type(value) is str and value:
        return value
    if required:
        raise SnapshotError("bad_row", f"a snapshot row has no usable {name!r}")
    return None


def _board_key(index: CompanyIndex, ats: object, slug: object) -> tuple[str, str]:
    try:
        index.path(ats, slug)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        raise SnapshotError("bad_row", "a snapshot row names a board this version cannot store") from None
    return ats, slug  # type: ignore[return-value]


def _posting_from_row(row: Mapping[str, object], last_seen: str) -> IndexedPosting:
    """One index row as a stored posting. Only the listed fields are copied: nothing else can ride in."""

    url = _text(row, "url", required=True)
    assert url is not None
    if not _is_url(url):
        raise SnapshotError("bad_row", "a snapshot posting has a url that is not http(s)")
    countries = row.get("countries")
    return IndexedPosting(
        posting_id=_text(row, "id", required=True),  # type: ignore[arg-type]
        title=_text(row, "title", required=True),  # type: ignore[arg-type]
        location=_text(row, "location") or "",
        url=url,
        updated_at=_text(row, "updated_at"),
        content_sha256=_text(row, "content_sha256"),
        first_seen=_text(row, "first_seen") or last_seen,
        last_seen=last_seen,
        changed_at=_text(row, "changed_at"),
        published_at=_text(row, "published_at"),
        countries=tuple(item for item in countries if type(item) is str) if isinstance(countries, list) else None,
        published_kind=_text(row, "published_kind"),  # 0110-10-14: without it a Greenhouse date is its last change (company_index)
    )


def _entry_from_rows(row: Mapping[str, object], postings: Iterable[Mapping[str, object]], stamp: str) -> CompanyIndexEntry:
    """A board row plus its posting rows as the company file the snapshot's builder had."""

    checked_at = _text(row, "checked_at") or stamp
    slug = _text(row, "slug", required=True)
    assert slug is not None
    listed = [_posting_from_row(posting, checked_at) for posting in postings]
    return CompanyIndexEntry(
        company=_text(row, "company") or slug,
        ats=_text(row, "ats", required=True),  # type: ignore[arg-type]
        slug=slug,
        checked_at=checked_at,
        etag=_text(row, "etag"),
        body_sha256=_text(row, "body_sha256"),
        postings={posting.posting_id: posting for posting in listed},
        last_modified=_text(row, "last_modified"),
        changed_at=_text(row, "changed_at"),
    )


def _same_listing(one: IndexedPosting, other: IndexedPosting) -> bool:
    return (one.title, one.location, one.url, one.updated_at, one.content_sha256) == (
        other.title,
        other.location,
        other.url,
        other.updated_at,
        other.content_sha256,
    )


def _take(local: IndexedPosting | None, incoming: IndexedPosting, *, owned: bool) -> tuple[IndexedPosting, bool]:
    """``(the posting to store, the snapshot's was taken)``. A posting that became news locally at or after the snapshot's stays."""

    if local is None:
        return incoming, True
    if owned or local.removed or local.touched_at < incoming.touched_at:
        return replace(incoming, first_seen=min(local.first_seen, incoming.first_seen)), True
    return local, False


@dataclass
class _Plan:
    """Every change one import will make, worked out before the first write."""

    #: ``company key -> the checked_at the snapshot gave a board it created`` (``None``: listed, but the file is the machine's own).
    ledger: dict[str, str | None]
    writes: dict[str, CompanyIndexEntry] = field(default_factory=dict)
    deletes: dict[str, tuple[str, str]] = field(default_factory=dict)
    tags: list[Mapping[str, object]] = field(default_factory=list)
    counts: ImportCounts = field(default_factory=ImportCounts)

    def owns(self, local: CompanyIndexEntry) -> bool:
        """The snapshot created this file and the machine has not checked the board since."""

        given = self.ledger.get(local.key)
        return given is not None and local.checked_at == given

    def write(self, entry: CompanyIndexEntry, local: CompanyIndexEntry | None, *, owned: bool, postings: int) -> None:
        self.ledger[entry.key] = entry.checked_at if owned else None
        if entry == local:
            return
        self.writes[entry.key] = entry
        self.counts.boards += 1
        self.counts.postings += postings

    def keep_local(self, key: str) -> None:
        self.ledger[key] = None
        self.counts.boards_kept_local += 1

    def remove(self, index: CompanyIndex, keys: Iterable[tuple[str, str]]) -> None:
        """The removal list: only a board the snapshot created and the machine never checked is deleted."""

        for ats, slug in sorted(set(keys)):
            key = company_key(ats, slug)
            local = index.read(ats, slug)
            if local is not None and self.owns(local):
                self.writes.pop(key, None)
                self.deletes[key] = (ats, slug)
                self.counts.boards_removed += 1
                self.counts.postings_removed += len(local.live())
            self.ledger.pop(key, None)


def _merge_board(plan: _Plan, local: CompanyIndexEntry, incoming: CompanyIndexEntry, *, owned: bool, complete: bool, gone: Iterable[str] = ()) -> None:
    """Fold a snapshot's view of one board into the stored file.

    ``complete`` (the full files): ``incoming`` lists every live posting, so a
    stored one it lacks is gone. A delta names the gone ones in ``gone``. Only
    an owned board has them marked removed; elsewhere they stay, the
    validators are dropped and the next refresh reads the board in full.
    """

    postings = dict(local.postings)
    taken = 0
    differs = False
    for posting_id, theirs in incoming.postings.items():
        stored, took = _take(postings.get(posting_id), theirs, owned=owned)
        postings[posting_id] = stored
        taken += took
        differs = differs or not _same_listing(stored, theirs)
    if complete:
        gone = [posting_id for posting_id in local.postings if posting_id not in incoming.postings]
    stale = [posting_id for posting_id in gone if posting_id in postings and not postings[posting_id].removed]
    if owned:
        for posting_id in stale:
            postings[posting_id] = replace(postings[posting_id], removed_at=incoming.checked_at)
        plan.counts.postings_removed += len(stale)
        plan.write(replace(incoming, postings=postings), local, owned=True, postings=taken)
    elif complete and not differs and not stale:
        plan.write(replace(incoming, postings=postings), local, owned=False, postings=taken)
    elif taken:
        mixed = replace(local, postings=postings, etag=None, last_modified=None, body_sha256=None)
        plan.write(mixed, local, owned=False, postings=taken)
    else:
        plan.keep_local(local.key)


def _removal_keys(index: CompanyIndex, manifest: Mapping[str, object]) -> list[tuple[str, str]]:
    removals = manifest.get("removals")
    boards = removals.get("boards") if isinstance(removals, dict) else None
    keys: list[tuple[str, str]] = []
    for item in boards if isinstance(boards, list) else ():
        if isinstance(item, list) and len(item) == 2:
            keys.append(_board_key(index, item[0], item[1]))
    return keys


def _plan_full(index: CompanyIndex, plan: _Plan, tables: Mapping[str, list[dict[str, object]]], manifest: Mapping[str, object], stamp: str) -> None:
    by_board: dict[tuple[str, str], list[dict[str, object]]] = {}
    for row in tables[ROLE_INDEX]:
        by_board.setdefault(_board_key(index, row.get("ats"), row.get("slug")), []).append(row)
    listed: set[tuple[str, str]] = set()
    for row in tables[ROLE_BOARDS]:
        ats, slug = _board_key(index, row.get("ats"), row.get("slug"))
        listed.add((ats, slug))
        incoming = _entry_from_rows(row, by_board.pop((ats, slug), ()), stamp)
        local = index.read(ats, slug)
        if local is None:
            plan.write(incoming, None, owned=True, postings=len(incoming.postings))
        elif plan.owns(local):
            _merge_board(plan, local, incoming, owned=True, complete=True)
        elif _older(local.checked_at, incoming.checked_at):
            _merge_board(plan, local, incoming, owned=False, complete=True)
        else:
            plan.keep_local(incoming.key)
    if by_board:
        raise SnapshotError("bad_row", "the snapshot has postings of a board it does not list")
    plan.tags = list(tables[ROLE_TAGS])
    plan.remove(index, (key for key in _removal_keys(index, manifest) if key not in listed))


def _plan_delta(index: CompanyIndex, plan: _Plan, lines: Iterable[Mapping[str, object]], manifest: Mapping[str, object], stamp: str) -> None:
    board_puts: dict[tuple[str, str], Mapping[str, object]] = {}
    board_dels: set[tuple[str, str]] = set()
    posting_puts: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    posting_dels: dict[tuple[str, str], list[str]] = {}
    for line in lines:
        table, op = line.get("table"), line.get("op")
        row, key = line.get("row"), line.get("key")
        if op == "put" and isinstance(row, dict) and table == ROLE_TAGS:
            plan.tags.append(row)
        elif op == "put" and isinstance(row, dict) and table in (ROLE_BOARDS, ROLE_INDEX):
            board = _board_key(index, row.get("ats"), row.get("slug"))
            if table == ROLE_BOARDS:
                board_puts[board] = row
            else:
                posting_puts.setdefault(board, []).append(row)
        elif op == "del" and isinstance(key, list) and table == ROLE_TAGS:
            continue  # a title no posting carries any more: its tag is the machine's to keep
        elif op == "del" and isinstance(key, list) and table == ROLE_BOARDS and len(key) == 2:
            board_dels.add(_board_key(index, key[0], key[1]))
        elif op == "del" and isinstance(key, list) and table == ROLE_INDEX and len(key) == 3 and type(key[2]) is str:
            posting_dels.setdefault(_board_key(index, key[0], key[1]), []).append(key[2])
        else:
            raise SnapshotError("bad_row", "the delta has a line this version cannot apply")

    for board in sorted(set(board_puts) | set(posting_puts) | set(posting_dels)):
        if board in board_dels:
            continue
        key = company_key(*board)
        row = board_puts.get(board)
        local = index.read(*board)
        if local is None:
            if key in plan.ledger:
                raise _NeedFull  # the base listed this board and its file is gone: a delta cannot rebuild it
            if row is not None:  # a board new since the base: the delta carries all of it
                incoming = _entry_from_rows(row, posting_puts.get(board, ()), stamp)
                plan.write(incoming, None, owned=True, postings=len(incoming.postings))
            continue
        owned = plan.owns(local)
        if not owned and (row is None or not _older(local.checked_at, _text(row, "checked_at"))):
            plan.keep_local(key)
            continue
        base = {"ats": local.ats, "slug": local.slug, "company": local.company, "checked_at": local.checked_at, "etag": local.etag,
                "last_modified": local.last_modified, "body_sha256": local.body_sha256, "changed_at": local.changed_at}
        incoming = _entry_from_rows(row if row is not None else base, posting_puts.get(board, ()), stamp)
        _merge_board(plan, local, incoming, owned=owned, complete=False, gone=posting_dels.get(board, ()))
    plan.remove(index, board_dels | set(_removal_keys(index, manifest)))


# --- writing ----------------------------------------------------------


class _Journal:
    """Undo for a batch of file replacements under one root.

    Every file about to be replaced or deleted is hard-linked into the
    rollback directory first (a replacement is a new file, so the link keeps
    the old bytes); ``plan.json`` is written last. :meth:`restore` puts every
    file back and removes the ones the batch created. A rollback directory
    found at the start of an import is a batch that never finished.
    """

    def __init__(self, root: Path, directory: Path) -> None:
        self.root = root
        self.directory = directory

    def begin(self, paths: Iterable[Path]) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)
        self.directory.mkdir(parents=True)
        entries: list[dict[str, object]] = []
        for number, path in enumerate(paths):
            backup: str | None = f"{number:06d}"
            try:
                os.link(path, self.directory / backup)
            except FileNotFoundError:
                backup = None
            except OSError:
                shutil.copy2(path, self.directory / backup)
            entries.append({"path": str(path.relative_to(self.root)), "backup": backup})
        _write_json(self.directory / "plan.json", {"schema_version": "scout-snapshot-rollback:1", "entries": entries})

    def restore(self) -> None:
        plan = _read_json(self.directory / "plan.json", "scout-snapshot-rollback:1")
        entries = plan.get("entries")
        for item in entries if isinstance(entries, list) else ():
            target = self.root / item["path"]
            if item["backup"] is None:
                target.unlink(missing_ok=True)
            else:
                os.replace(self.directory / item["backup"], target)
        shutil.rmtree(self.directory, ignore_errors=True)

    def commit(self) -> None:
        shutil.rmtree(self.directory, ignore_errors=True)


def _apply_tags(home: Path, rows: Iterable[Mapping[str, object]], tagger_version: object) -> int:
    """Add the snapshot's title tags. A stored tag stays; one still waiting for a model takes the snapshot's model answer.

    A snapshot built by another tagger version carries tags this version
    would read as "not tagged": none are written.
    """

    if tagger_version != TAGGER_VERSION:
        return 0
    incoming: dict[str, TitleTag] = {}
    for row in rows:
        if row.get("tagger_version") != TAGGER_VERSION:
            continue
        key = _text(row, "title_key", required=True)
        assert key is not None
        incoming[key] = TitleTag(
            title_key=key,
            level=_text(row, "level", required=True),  # type: ignore[arg-type]
            level_source=_text(row, "level_source", required=True),  # type: ignore[arg-type]
            function=_text(row, "function"),
            function_source=_text(row, "function_source"),
            tagger_version=TAGGER_VERSION,
        )
    if not incoming:
        return 0
    store = TagStore.for_home(home, tagger_version=TAGGER_VERSION)
    try:
        stored = store.get_many(incoming)
        written = store.write_rules(tag for key, tag in incoming.items() if key not in stored)
        for key, mine in stored.items():
            theirs = incoming[key]
            if mine.function is None and mine.function_source is None and theirs.function_source == SOURCE_MODEL:
                written += store.set_model_function(key, theirs.function, model=_TAG_ORIGIN, prompt_version=_TAG_ORIGIN)
    finally:
        store.close()
    return written


def _apply(home: Path, index: CompanyIndex, plan: _Plan, tagger_version: object, state: dict[str, object]) -> None:
    """Write the plan. Any failure puts every index file, the ledger and the state back as they were."""

    directory = snapshot_dir(home)
    paths = [index.path(entry.ats, entry.slug) for entry in plan.writes.values()]
    paths += [index.path(ats, slug) for ats, slug in plan.deletes.values()]
    paths += [directory / _LEDGER_NAME, directory / _STATE_NAME]
    journal = _Journal(Path(home) / "cache" / "scout", directory / _ROLLBACK_NAME)
    journal.begin(paths)
    try:
        for entry in plan.writes.values():
            index.write(entry)
        for ats, slug in plan.deletes.values():
            index.delete(ats, slug)
        plan.counts.tags = _apply_tags(home, plan.tags, tagger_version)
        _write_json(directory / _LEDGER_NAME, {"schema_version": LEDGER_SCHEMA, "boards": plan.ledger})
        _write_json(directory / _STATE_NAME, {**state, "counts": asdict(plan.counts)})
    except BaseException:  # noqa: BLE001 - cleans up (rollback/undo) and re-raises: nothing is swallowed
        journal.restore()
        raise
    journal.commit()


@contextmanager
def _exclusive(directory: Path) -> Iterator[bool]:
    """One import at a time: a lock in this process, a lock file across processes (stale after half an hour)."""

    if not _IMPORT_LOCK.acquire(blocking=False):
        yield False
        return
    try:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / _LOCK_NAME
        try:
            if time.time() - path.stat().st_mtime > _LOCK_STALE_SECONDS:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            yield False
            return
        try:
            yield True
        finally:
            path.unlink(missing_ok=True)
    finally:
        _IMPORT_LOCK.release()


def _local_is_older(index: CompanyIndex, as_of: str) -> bool:
    """The trigger's test: nothing is stored, or the last Update sources ended before the snapshot was built."""

    if next(iter(index.keys()), None) is None:
        return True
    summary = index.read_update_summary() or {}
    return _older(_text(summary, "finished_at") or _text(summary, "updated_at") or _text(summary, "started_at"), as_of)


def _run(home: Path, source_text: str, client: httpx.Client | None, moment: datetime, only_if_older: bool) -> ImportResult:
    directory = snapshot_dir(home)
    index = CompanyIndex.for_home(home)
    work = directory / f"download-{os.getpid()}"
    own_client: httpx.Client | None = None
    if _is_url(source_text):
        _check_url(source_text)
        if client is None:
            client = own_client = _default_client()
        source: _DirSource | _HttpSource = _HttpSource(source_text, client)
    else:
        path = Path(source_text).expanduser()
        source = _DirSource(path.parent if path.name == MANIFEST_NAME else path)
    try:
        raw = source.manifest()
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        (work / MANIFEST_NAME).write_bytes(raw)
        manifest = load_manifest(work / MANIFEST_NAME)
        _check_manifest(manifest)
        digest = sha256_hex(raw)
        as_of = str(manifest["as_of"])
        state = _read_json(directory / _STATE_NAME, STATE_SCHEMA)
        if state.get("manifest_sha256") == digest or state.get("as_of") == as_of:
            return ImportResult(RESULT_UP_TO_DATE, None, "The stored snapshot is the published one.", as_of=as_of, source=source_text)
        if _older(as_of, _text(state, "as_of")):
            return ImportResult(RESULT_SKIPPED, "snapshot_older", "The published snapshot is older than the stored one.", as_of=as_of, source=source_text)
        if only_if_older and not _local_is_older(index, as_of):
            return ImportResult(RESULT_SKIPPED, "local_fresher", "The stored postings are newer than the published snapshot.", as_of=as_of, source=source_text)
        stamp = index_stamp(_parse_time(as_of))
        files = manifest["files"]
        assert isinstance(files, dict)
        for kind in _kinds(manifest, state):
            wanted = _names_for(manifest, kind)
            where = source.materialize(wanted, work)
            verify_files({**manifest, "files": {name: files[name] for name in wanted}}, where)
            scan_for_private_content(where, wanted)
            plan = _Plan(ledger=dict(_ledger(directory)))
            try:
                if kind == KIND_DELTA:
                    (name,) = wanted
                    _plan_delta(index, plan, read_lines(where / name), manifest, stamp)
                else:
                    by_role = {files[name]["role"]: name for name in wanted}
                    tables = {role: list(read_lines(where / by_role[role])) for role in FULL_ROLES}
                    _plan_full(index, plan, tables, manifest, stamp)
            except _NeedFull:
                continue
            now = index_stamp(moment)
            message = f"Imported the snapshot as of {as_of}."
            record: dict[str, object] = {
                "schema_version": STATE_SCHEMA,
                "as_of": as_of,
                "manifest_sha256": digest,
                "source": source_text,
                "kind": kind,
                "imported_at": now,
                "last_attempt_at": now,
                "last_result": RESULT_IMPORTED,
                "last_reason": None,
                "last_message": message,
            }
            _apply(home, index, plan, manifest.get("tagger_version"), record)
            return ImportResult(RESULT_IMPORTED, None, message, kind=kind, counts=asdict(plan.counts), as_of=as_of, source=source_text)
        raise SnapshotError("file_missing", "the snapshot's full files could not be applied")
    finally:
        shutil.rmtree(work, ignore_errors=True)
        if own_client is not None:
            own_client.close()


def _ledger(directory: Path) -> dict[str, str | None]:
    boards = _read_json(directory / _LEDGER_NAME, LEDGER_SCHEMA).get("boards")
    if not isinstance(boards, dict):
        return {}
    return {key: value if type(value) is str else None for key, value in boards.items() if type(key) is str}


def _record_attempt(directory: Path, result: ImportResult, moment: datetime) -> None:
    """Note an attempt that imported nothing, keeping what the state says about the snapshot in use."""

    state = _read_json(directory / _STATE_NAME, STATE_SCHEMA) or {"schema_version": STATE_SCHEMA}
    state.update(last_attempt_at=index_stamp(moment), last_result=result.status, last_reason=result.reason, last_message=result.message)
    try:
        _write_json(directory / _STATE_NAME, state)
    except OSError:
        _logger.warning("snapshot: the attempt could not be recorded")


def _disabled() -> ImportResult:
    return ImportResult(RESULT_SKIPPED, "disabled", "The snapshot download is turned off.")


def _import(home: Path, source_text: str, client: httpx.Client | None, moment: datetime, only_if_older: bool) -> ImportResult:
    directory = snapshot_dir(home)
    try:
        with _exclusive(directory) as held:
            if not held:
                return ImportResult(RESULT_SKIPPED, "import_running", "Another snapshot import is running.", source=source_text)
            if (directory / _ROLLBACK_NAME).exists():  # an import that was cut off: undo it before anything is read
                _Journal(home / "cache" / "scout", directory / _ROLLBACK_NAME).restore()
            try:
                result = _run(home, source_text, client, moment, only_if_older)
            except _Unavailable as error:
                result = ImportResult(RESULT_SKIPPED, error.reason, str(error), source=source_text)
            except SnapshotError as error:
                result = ImportResult(RESULT_REFUSED, error.code, f"The snapshot was not used: {error}.", source=source_text)
            except Exception as error:  # noqa: BLE001 - a snapshot is optional: nothing it does may fail its caller
                _logger.warning("snapshot import failed: %s", type(error).__name__)
                result = ImportResult(RESULT_FAILED, "apply_failed", "The snapshot could not be written. The stored postings are unchanged.", source=source_text)
            if not result.imported:
                _record_attempt(directory, result, moment)
            return result
    except Exception as error:  # noqa: BLE001 - e.g. a home that cannot be written
        _logger.warning("snapshot import failed: %s", type(error).__name__)
        return ImportResult(RESULT_FAILED, "apply_failed", "The snapshot could not be written. The stored postings are unchanged.", source=source_text)


def import_snapshot(
    home: Path,
    manifest_url_or_dir: str | Path | None = None,
    *,
    target: Path | None = None,
    client: httpx.Client | None = None,
    now: datetime | None = None,
    environ: Mapping[str, str] | None = None,
) -> ImportResult:
    """Bring ``home``'s company index, tag store and validators up to the published snapshot.

    ``manifest_url_or_dir``: a manifest URL (its files are its siblings), a
    snapshot directory or its ``manifest.json``; default: the
    ``snapshot.manifest_url`` setting. With the setting off nothing is
    requested and nothing is written. Never raises for a snapshot that is
    unreachable, untrusted or unwritable: the result says which.

    Steps aside while an Update sources is running: both write the company
    files. (:func:`maybe_import_snapshot` is the entry point the update
    itself calls, and does not.)
    """

    from .sources_update import snapshot_is_live

    home = Path(home)
    moment = now or datetime.now(UTC)
    setting = snapshot_setting(home, target, environ=environ)
    if not setting.enabled:
        return _disabled()
    source_text = str(manifest_url_or_dir) if manifest_url_or_dir is not None else setting.manifest_url
    if snapshot_is_live(CompanyIndex.for_home(home).read_update_summary(), now=moment):
        return ImportResult(RESULT_SKIPPED, "update_running", "Update sources is running. Import the snapshot when it has finished.", source=source_text)
    return _import(home, source_text, client, moment, False)


def maybe_import_snapshot(
    home: Path,
    now: datetime | None = None,
    *,
    target: Path | None = None,
    client: httpx.Client | None = None,
    environ: Mapping[str, str] | None = None,
) -> ImportResult:
    """The first-run / Update sources trigger: import when the stored index is empty or older than the snapshot.

    Asks for the manifest at most every :data:`CHECK_INTERVAL_HOURS` once the
    index holds anything; an empty index asks every time. Call it before the
    update starts writing the index.
    """

    home = Path(home)
    moment = now or datetime.now(UTC)
    setting = snapshot_setting(home, target, environ=environ)
    if not setting.enabled:
        return _disabled()
    if next(iter(CompanyIndex.for_home(home).keys()), None) is not None:
        last = _parse_time(_read_json(snapshot_dir(home) / _STATE_NAME, STATE_SCHEMA).get("last_attempt_at"))
        if last is not None and moment - last < timedelta(hours=CHECK_INTERVAL_HOURS):
            return ImportResult(RESULT_SKIPPED, "checked_recently", "The snapshot was checked a short while ago.", source=setting.manifest_url)
    return _import(home, setting.manifest_url, client, moment, True)


# =====================================================================
# Export
# =====================================================================


@dataclass(frozen=True)
class ExportResult:
    out_dir: Path
    manifest_path: Path
    manifest: Mapping[str, object]
    files: tuple[str, ...]
    gh_commands: tuple[str, ...]


def _posting_row(entry: CompanyIndexEntry, posting_id: str) -> dict[str, object]:
    posting = entry.postings[posting_id]
    row: dict[str, object] = {
        "ats": entry.ats,
        "slug": entry.slug,
        "company": entry.company,
        "id": posting_id,
        "title": posting.title,
        "location": posting.location,
        "url": posting.url,
        "updated_at": posting.updated_at,
        "content_sha256": posting.content_sha256,
        "first_seen": posting.first_seen,
        "changed_at": posting.changed_at,
        "published_at": posting.published_at,
        "published_kind": posting.published_kind,
        "countries": list(posting.countries) if posting.countries is not None else None,
    }
    return row


def _board_row(entry: CompanyIndexEntry) -> dict[str, object]:
    return {
        "ats": entry.ats,
        "slug": entry.slug,
        "company": entry.company,
        "etag": entry.etag,
        "last_modified": entry.last_modified,
        "body_sha256": entry.body_sha256,
        "checked_at": entry.checked_at,
        "changed_at": entry.changed_at,
    }


def _tag_rows(home: Path, titles: Iterable[str]) -> list[dict[str, object]]:
    """Current-version tags for these titles. A home with no tag store ships none (the store is not created)."""

    path = Path(home) / "cache" / "scout" / "tags.sqlite"
    if not path.is_file():
        return []
    store = TagStore.for_home(home, tagger_version=TAGGER_VERSION)
    try:
        keys = sorted({normalize_title(title) for title in titles})
        found = store.get_many(keys)
    finally:
        store.close()
    return [
        {
            "title_key": tag.title_key,
            "level": tag.level,
            "level_source": tag.level_source,
            "function": tag.function,
            "function_source": tag.function_source,
            "tagger_version": tag.tagger_version,
        }
        for _, tag in sorted(found.items())
    ]


def _key(table: str, row: Mapping[str, object]) -> tuple[str, ...]:
    if table == ROLE_INDEX:
        return (str(row["ats"]), str(row["slug"]), str(row["id"]))
    if table == ROLE_TAGS:
        return (str(row["title_key"]),)
    return (str(row["ats"]), str(row["slug"]))


def _base_tables(base_manifest: Path) -> tuple[dict[str, object], dict[str, dict[tuple[str, ...], dict[str, object]]]]:
    """The base snapshot's rows by key. Its files must sit beside its manifest and pass their digests."""

    base_manifest = Path(base_manifest)
    manifest = load_manifest(base_manifest)
    verify_files(manifest, base_manifest.parent)
    tables: dict[str, dict[tuple[str, ...], dict[str, object]]] = {role: {} for role in FULL_ROLES}
    files = manifest["files"]
    assert isinstance(files, dict)
    for name, meta in files.items():
        role = meta.get("role")
        if role in tables:
            for row in read_lines(base_manifest.parent / name):
                tables[role][_key(role, row)] = row
    return manifest, tables


def _delta(
    current: Mapping[str, list[dict[str, object]]],
    base: Mapping[str, dict[tuple[str, ...], dict[str, object]]],
) -> tuple[list[dict[str, object]], list[list[str]], int]:
    """Puts for new/changed rows, deletes for rows gone. A gone board deletes all its postings once."""

    lines: list[dict[str, object]] = []
    now_boards = {_key(ROLE_BOARDS, row) for row in current[ROLE_BOARDS]}
    removed_boards = sorted(key for key in base[ROLE_BOARDS] if key not in now_boards)
    gone = set(removed_boards)
    removed_postings = 0
    for table in FULL_ROLES:
        seen: set[tuple[str, ...]] = set()
        for row in current[table]:
            key = _key(table, row)
            seen.add(key)
            if base[table].get(key) != row:
                lines.append({"table": table, "op": "put", "row": row})
        for key in sorted(base[table]):
            if key in seen:
                continue
            if table == ROLE_INDEX and key[:2] in gone:
                continue  # covered by the board delete
            lines.append({"table": table, "op": "del", "key": list(key)})
            if table == ROLE_INDEX:
                removed_postings += 1
    return lines, [list(key) for key in removed_boards], removed_postings


def export_snapshot(
    home: Path,
    out_dir: Path,
    base_manifest: Path | None = None,
    *,
    as_of: datetime | None = None,
    repo: str = "karthik446/gigai",
) -> ExportResult:
    """Write the metadata snapshot of ``home``'s company index into ``out_dir``.

    ``base_manifest`` (a previous export's manifest, its files beside it) adds a
    delta file and a removal list. Raises :class:`SnapshotError`, leaving no
    file of this run behind, when the scan or the read-back fails. Never
    publishes: the ``gh`` commands come back as text.
    """

    home = Path(home).expanduser().resolve(strict=False)
    out_dir = Path(out_dir).expanduser().resolve(strict=False)
    if out_dir == home or home in out_dir.parents:
        raise SnapshotError("out_inside_home", "the output directory must not be inside the GigAI home")
    stamp = (as_of or datetime.now(UTC)).astimezone(UTC)
    as_of_text = stamp.strftime("%Y-%m-%dT%H:%M:%SZ")
    date = stamp.strftime("%Y-%m-%d")

    index = CompanyIndex.for_home(home)
    entries = [entry for entry in (index.read(ats, slug) for ats, slug in index.keys()) if entry is not None]
    entries.sort(key=lambda entry: (entry.ats, entry.slug))
    if not entries:
        raise SnapshotError("empty_index", "the company index has no readable board: run `gigai scout sources update` first")

    postings = [_posting_row(entry, pid) for entry in entries for pid in sorted(entry.postings) if not entry.postings[pid].removed]
    boards = [_board_row(entry) for entry in entries]
    tags = _tag_rows(home, (str(row["title"]) for row in postings))
    current = {ROLE_INDEX: postings, ROLE_TAGS: tags, ROLE_BOARDS: boards}

    base_info: dict[str, object] | None = None
    delta_lines: list[dict[str, object]] = []
    removed_boards: list[list[str]] = []
    removed_postings = 0
    if base_manifest is not None:
        base_doc, base_tables = _base_tables(Path(base_manifest))
        delta_lines, removed_boards, removed_postings = _delta(current, base_tables)
        base_info = {
            "as_of": base_doc.get("as_of"),
            "manifest_sha256": sha256_file(Path(base_manifest)),
        }

    payloads: dict[str, tuple[str, bytes, int]] = {}
    for role in FULL_ROLES:
        data, rows = _compress(current[role])
        payloads[file_name(role, date)] = (role, data, rows)
    if base_manifest is not None:
        data, rows = _compress(delta_lines)
        payloads[file_name(ROLE_DELTA, date)] = (ROLE_DELTA, data, rows)

    manifest: dict[str, object] = {
        "format": FORMAT_NAME,
        "format_version": FORMAT_VERSION,
        "as_of": as_of_text,
        "tagger_version": TAGGER_VERSION,
        "contents": "metadata-only",
        "counts": {
            "boards": len(boards),
            "postings": len(postings),
            "tags": len(tags),
        },
        "files": {
            name: {"role": role, "sha256": sha256_hex(data), "size": len(data), "rows": rows}
            for name, (role, data, rows) in sorted(payloads.items())
        },
    }
    if base_info is not None:
        manifest["delta"] = {
            "base": base_info,
            "file": file_name(ROLE_DELTA, date),
            "puts": sum(1 for line in delta_lines if line["op"] == "put"),
            "deletes": sum(1 for line in delta_lines if line["op"] == "del"),
        }
        manifest["removals"] = {"boards": removed_boards, "postings_count": removed_postings}

    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    manifest_path = out_dir / MANIFEST_NAME
    try:
        for name, (_, data, _count) in payloads.items():
            _atomic_write(out_dir / name, data)
            written.append(out_dir / name)
        # The privacy scan and the read-back run on the files as written, BEFORE the manifest exists.
        scan_for_private_content(out_dir, payloads, extra_markers=(str(home), str(Path.home())))
        verify_files(manifest, out_dir)
        manifest_text = json.dumps(manifest, sort_keys=True, indent=2) + "\n"
        _atomic_write(manifest_path, manifest_text.encode("utf-8"))
        written.append(manifest_path)
        scan_for_private_content(out_dir, [MANIFEST_NAME], extra_markers=(str(home), str(Path.home())))
        verify_files(load_manifest(manifest_path), out_dir)
    except BaseException:  # noqa: BLE001 - cleans up (rollback/undo) and re-raises: nothing is swallowed
        for path in written:
            path.unlink(missing_ok=True)
        raise

    names = tuple(sorted(payloads)) + (MANIFEST_NAME,)
    return ExportResult(out_dir, manifest_path, manifest, names, gh_commands(out_dir, names, repo))


def _atomic_write(path: Path, data: bytes) -> None:
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(temp, path)
    except BaseException:  # noqa: BLE001 - cleans up (rollback/undo) and re-raises: nothing is swallowed
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass
        raise


def gh_commands(out_dir: Path, names: Iterable[str], repo: str, tag: str = "scout-snapshot") -> tuple[str, ...]:
    """The commands the operator runs next, as text. Data files first, ``manifest.json`` last."""

    data_files = " ".join(f'"{out_dir / name}"' for name in names if name != MANIFEST_NAME)
    manifest = f'"{out_dir / MANIFEST_NAME}"'
    return (
        f'gh release create {tag} --repo {repo} --title "Scout snapshot" --notes "Metadata-only company index snapshot (no descriptions)." --latest=false',
        f"gh release upload {tag} --repo {repo} {data_files}",
        f"gh release upload {tag} --repo {repo} --clobber {manifest}",
    )
