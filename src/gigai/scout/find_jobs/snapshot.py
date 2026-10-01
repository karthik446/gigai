"""Metadata snapshot of the company index (0110-026, packet S1: export side).

A snapshot is what a fresh install downloads instead of crawling every board:
the company index's public posting metadata, the title-tag table and the
per-board validators (ETag / Last-Modified). It is built on the operator's
machine from a cache directory and published by the operator as a release
asset; nothing here talks to a network or runs ``gh``.

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

This module is split in two sections: **format** (manifest, file names, the
digest/read-back helpers an importer reuses) and **export**. The import side
(S3) is added below the format section and must not import from the export
section.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import lzma
import os
from pathlib import Path
import re
import tempfile

from .company_index import CompanyIndex, CompanyIndexEntry
from .posting_tags import TAGGER_VERSION, normalize_title
from .tag_store import TagStore

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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
            name: {"role": role, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data), "rows": rows}
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
    except BaseException:
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
    except BaseException:
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
