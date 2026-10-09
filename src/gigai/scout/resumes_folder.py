"""0110-10-05 A: the resumes folder -- where GigAI keeps ``master.md`` and the PDFs made without a header.

0.1.11.4 J1: a job's resume markdown is NOT written here any more.  It is ``resume.md`` in the
job's own folder of the jobs folder (``jobs_folder``, which reuses this module's setting rule,
lock and digest check).  The flat ``<company>-<role>-<date>.md`` files this module wrote before
are left as they are (``save_markdown`` stays for them and is no longer called by a resume's
save); the master's file and the headerless PDFs still go through this module.

**Where.**  ``~/Documents/GigAI/resumes`` for the default GigAI home (``~/.gigai``).  Any other
home (``--home``, ``GIGAI_HOME``: a scratch or test home) defaults to ``<home>/resumes``, so
nothing but the user's own install writes into their Documents.  ``set_resumes_folder`` saves
another place in ``<home>/scout/resumes-folder.json`` (0600); the folder is created then, and
files already written stay where they are.

**What.**  Per job the tailored resume's markdown and the PDFs rendered WITHOUT a header, named
``<company>-<role>-<YYYY-MM-DD>.{md,pdf}`` (``file_name``), never after the user.  A PDF made
with the Generate PDF form's name and contact details is never written here: it goes only where
the user saves it (the browser's download, an explicit ``--out``).

**Never contact data.**  ``save`` refuses a text that holds a contact shape
(``resume_pii.detect_contact_details``: an email, a phone number, a profile link, a street
address) with ``contact_data_found``.  GigAI stores no name or contact details (0110-046), and
this folder is GigAI's.

**Never the user's file.**  The folder is the user's to work in.  GigAI replaces or removes a
file there only when its bytes are exactly what GigAI last wrote (the digest is kept in
``<home>/scout/resumes-folder-index.json``: file names, digests and store keys, no text).  A
file the user changed, or one GigAI did not write, is left alone and the new file gets the next
free name (``...-2.md``).  A job has one markdown: a newer one replaces the older one GigAI
wrote.  PDFs are kept; the same name is written again only over GigAI's own untouched file.

**The master resume's file** (0.1.10.9 master P8).  ``master.md`` is the stored master, written
again after every change of it (``save_master``).  The same rule holds: when ``master.md`` is
not exactly what GigAI last wrote there (the user edited it and has not imported the edit yet),
it is left alone and the new revision is written beside it (``master-2.md``).  The index keeps,
for ``master.md``, the revision GigAI last wrote into it, so ``master_file`` can say "changes
not imported yet" and an import can tell which revision the edit was made on.  One more file may
be replaced: one whose bytes GigAI has just imported at the user's request (``imported``).

Nothing here calls a model or the network.  Errors name a rule, never a resume's text.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime, timezone
import fcntl
import json
import logging
import os
from pathlib import Path
import re
import unicodedata

from ..canonical import digest_imported_bytes
from .find_jobs.discovery.storage import atomic_write
from .job_store_layout import JOB_FOLDER, RESUMES, job_digest
from .resume_pii import detect_contact_details
from .target_resolution import _display_path

SETTING_SCHEMA = "scout-resumes-folder:1"
INDEX_SCHEMA = "scout-resumes-folder-index:1"
RESPONSE_SCHEMA = "scout-resumes-folder-response:1"

SOURCE_DEFAULT = "default"
SOURCE_SETTING = "setting"

MARKDOWN = ".md"
PDF = ".pdf"

#: The master resume's file in the folder, and what names it in the index.
MASTER_NAME = "master.md"
MASTER_KEY = "master"
#: ``master.md`` against what GigAI last wrote there: not in the folder, exactly that, or anything else.
MASTER_MISSING = "missing"
MASTER_CURRENT = "current"
MASTER_CHANGED = "changed"

#: A folder path's length bound (the setting is one line of text).
MAX_PATH_CHARS = 1024
_PART_MAX = 40
_ROLE_MAX = 60
#: How many ``-<n>`` names are tried before a write gives up (a folder full of the user's own files of one name).
_MAX_NAME_TRIES = 200

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_COMMENT = re.compile(r"[ \t]*<!--.*?-->")
_logger = logging.getLogger("gigai.scout.resumes_folder")


class ResumesFolderError(ValueError):
    """The folder cannot be used as asked; ``code`` is the API/CLI error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- where ----------------------------------------------------------------------------------


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)
    except (OSError, RuntimeError):
        return False


def default_folder(home_root: Path) -> Path:
    """``~/Documents/GigAI/resumes`` for the default GigAI home, ``<home>/resumes`` for any other."""

    home_root = Path(home_root).expanduser()
    try:
        user_home = Path.home()
    except RuntimeError:
        return home_root / "resumes"
    if _same_path(home_root, user_home / ".gigai"):
        return user_home / "Documents" / "GigAI" / "resumes"
    return home_root / "resumes"


def setting_path(home_root: Path) -> Path:
    return Path(home_root) / "scout" / "resumes-folder.json"


def index_path(home_root: Path) -> Path:
    return Path(home_root) / "scout" / "resumes-folder-index.json"


def _read_json(path: Path) -> dict[str, object] | None:
    try:
        if path.is_symlink() or not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if type(value) is dict else None


@dataclass(frozen=True)
class ResumesFolder:
    """The folder and what said so (``default`` or ``setting``)."""

    path: Path
    source: str
    default: Path

    @property
    def shown(self) -> str:
        """The folder the way the user types it (``~/Documents/GigAI/resumes``)."""

        return _display_path(self.path)

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": RESPONSE_SCHEMA,
            "path": os.fspath(self.path),
            "shown": self.shown,
            "source": self.source,
            "default": _display_path(self.default),
            "exists": self.path.is_dir(),
        }


def resumes_folder(home_root: Path) -> ResumesFolder:
    """The resumes folder of this home: the saved setting, else the default.  Reads only."""

    default = default_folder(home_root)
    saved = _saved_folder(setting_path(home_root), SETTING_SCHEMA)
    if saved is not None:
        return ResumesFolder(saved, SOURCE_SETTING, default)
    return ResumesFolder(default, SOURCE_DEFAULT, default)


def _make_folder(path: Path, noun: str = "resumes") -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ResumesFolderError("folder_unwritable", f"the {noun} folder could not be created; choose another folder") from exc
    if not os.access(path, os.W_OK | os.X_OK):
        raise ResumesFolderError("folder_unwritable", f"the {noun} folder cannot be written; choose another folder")


def _saved_folder(setting_file: Path, schema: str) -> Path | None:
    """The folder a setting file names, or ``None`` (no file, another schema, a path that is not absolute).  Reads only."""

    raw = _read_json(setting_file)
    saved = raw.get("path") if raw is not None and raw.get("schema_version") == schema else None
    if isinstance(saved, str) and saved and not _CONTROL.search(saved) and Path(saved).is_absolute():
        return Path(saved)
    return None


def _save_folder_setting(
    home_root: Path, value: object, *, setting_file: Path, schema: str, default: Path, noun: str, now: datetime | None = None,
) -> Path | None:
    """The one rule of a visible folder's setting (the resumes folder's, and the jobs folder's): save ``value`` and
    return the folder (created when missing), or remove the setting and return ``None`` for ``None`` / ``""``.

    Refused (``invalid_value``): a relative path, a path that is a file, and a folder inside the GigAI
    home other than the default one (the home is GigAI's own store).  Raises ``ResumesFolderError``.
    """

    if value is None or (isinstance(value, str) and not value.strip()):
        try:
            if setting_file.is_symlink() or setting_file.exists():
                setting_file.unlink()
        except OSError as exc:
            raise ResumesFolderError("setting_write_failed", f"could not save the {noun} folder setting") from exc
        return None
    if not isinstance(value, str):
        raise ResumesFolderError("wrong_type", "path must be a string")
    text = value.strip()
    if len(text) > MAX_PATH_CHARS or _CONTROL.search(text):
        raise ResumesFolderError("invalid_value", f"path must be one line of at most {MAX_PATH_CHARS} characters")
    try:
        expanded = Path(text).expanduser()
    except RuntimeError as exc:
        raise ResumesFolderError("invalid_value", "path could not be resolved") from exc
    if not expanded.is_absolute():
        raise ResumesFolderError("invalid_value", f"path must be absolute or start with ~ (for example ~/Documents/GigAI/{noun})")
    folder = Path(os.path.normpath(expanded))
    if folder != default:
        try:
            store, resolved = Path(home_root).expanduser().resolve(strict=False), folder.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ResumesFolderError("invalid_value", "path could not be resolved") from exc
        if resolved == store or resolved.is_relative_to(store):
            raise ResumesFolderError("invalid_value", "path is inside the GigAI home, which is GigAI's own store; choose a folder outside it")
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise ResumesFolderError("invalid_value", "path is not a folder")
    _make_folder(folder, noun)
    stamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    payload = {"schema_version": schema, "path": os.fspath(folder), "updated_at": stamp}
    try:
        if setting_file.is_symlink():
            raise OSError(f"the {noun} folder setting path is a symlink")
        setting_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(setting_file, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        os.chmod(setting_file, 0o600)
    except OSError as exc:
        raise ResumesFolderError("setting_write_failed", f"could not save the {noun} folder setting") from exc
    return folder


def set_resumes_folder(home_root: Path, value: object, *, now: datetime | None = None) -> ResumesFolder:
    """Save ``value`` as the resumes folder (created when missing); ``None`` or ``""`` goes back to the default.

    ``value`` is an absolute path or one that starts with ``~``.  Refused (``invalid_value``): a
    relative path, a path that is a file, and a folder inside the GigAI home other than the
    default one (the home is GigAI's own store).  Files already in the old folder are not moved.
    """

    home_root = Path(home_root)
    default = default_folder(home_root)
    folder = _save_folder_setting(home_root, value, setting_file=setting_path(home_root), schema=SETTING_SCHEMA, default=default, noun="resumes", now=now)
    return resumes_folder(home_root) if folder is None else ResumesFolder(folder, SOURCE_SETTING, default)


# --- file names -----------------------------------------------------------------------------


def _slug(text: str, limit: int = _PART_MAX) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "-", ascii_text).strip("-")[:limit].strip("-")


def file_name(company: str, role: str, day: date, extension: str = PDF) -> str:
    """``<company>-<role>-<YYYY-MM-DD><extension>`` (lowercase ASCII, hyphens, each part length-capped);
    ``resume-<YYYY-MM-DD><extension>`` when neither is known.  Never carries the user's name (0110-046)."""

    parts = [part for part in (_slug(company), _slug(role, _ROLE_MAX)) if part] or ["resume"]
    return "-".join([*parts, day.isoformat()]) + extension


def job_key(home_root: Path, stored_path: str | os.PathLike[str]) -> str:
    """What names one job's stored tailored resume in the index: its store path under ``<home>/scout``, without the suffix."""

    stored = Path(stored_path).with_suffix("")
    try:
        return stored.relative_to(Path(home_root) / "scout").as_posix()
    except ValueError:
        return digest_imported_bytes(os.fspath(stored).encode("utf-8"))


def job_store_key(project_id: str, job_identity: str) -> str:
    """0.1.11.9 PJ1: what names a JOB in the index, ``<project>/resumes/job/<sha256(job identity)>``:
    ``job_key`` of the job's resume in the per-job store (``tailored_resume.job_tailored_resume_path``), no profile in it."""

    return f"{project_id}/{RESUMES}/{JOB_FOLDER}/{job_digest(job_identity)}"


def readable_markdown(markdown: str) -> str:
    """Tailored markdown as a reader sees it: without the per-line source comments (``<!-- R12 -->``)."""

    return "\n".join(_COMMENT.sub("", line).rstrip() for line in markdown.splitlines()).strip() + "\n"


# --- the index: which files are GigAI's own -------------------------------------------------


@dataclass(frozen=True)
class SavedFile:
    """A file in the resumes folder."""

    path: Path
    #: False when the file already held exactly these bytes.
    written: bool = True

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def shown(self) -> str:
        return _display_path(self.path)


def _load_index(home_root: Path, folder: Path) -> dict[str, dict[str, object]]:
    raw = _read_json(index_path(home_root))
    if raw is None or raw.get("schema_version") != INDEX_SCHEMA or raw.get("folder") != os.fspath(folder):
        return {}  # another folder's files are not this folder's
    files = raw.get("files")
    if type(files) is not dict:
        return {}
    return {
        name: _index_entry(entry)
        for name, entry in files.items()
        if isinstance(name, str) and type(entry) is dict and isinstance(entry.get("sha256"), str)
    }


def _index_entry(entry: dict[str, object]) -> dict[str, object]:
    """One file's index entry: its digest and key, and for the master's files the revision written into it."""

    kept: dict[str, object] = {"sha256": entry.get("sha256"), "key": entry.get("key")}
    if type(entry.get("revision")) is int and isinstance(entry.get("revision_id"), str):
        kept["revision"], kept["revision_id"] = entry["revision"], entry["revision_id"]
    return kept


def _save_index(home_root: Path, folder: Path, files: dict[str, dict[str, object]]) -> None:
    path = index_path(home_root)
    if path.is_symlink():
        raise OSError("the resumes folder index path is a symlink")
    payload = {"schema_version": INDEX_SCHEMA, "folder": os.fspath(folder), "files": {name: files[name] for name in sorted(files)}}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


def _untouched(path: Path, sha256: object) -> bool:
    """``path`` still holds exactly the bytes GigAI last wrote there."""

    try:
        return not path.is_symlink() and path.is_file() and digest_imported_bytes(path.read_bytes()) == sha256
    except OSError:
        return False


@contextmanager
def _folder_lock(folder: Path) -> Iterator[None]:
    """One writer at a time for the folder's file names (and the index): an exclusive lock on the folder itself."""

    descriptor = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _numbered(name: str, attempt: int) -> str:
    stem, extension = os.path.splitext(name)
    return name if attempt == 1 else f"{stem}-{attempt}{extension}"


def _is_numbered(candidate: str, name: str) -> bool:
    stem, extension = os.path.splitext(name)
    return candidate == name or re.fullmatch(re.escape(stem) + r"-\d+" + re.escape(extension), candidate) is not None


def save(home_root: Path, *, name: str, data: bytes, text: str, key: str | None = None, replace_older: bool = False) -> SavedFile:
    """Write ``data`` into the resumes folder as ``name`` (or the next free ``-<n>`` name) and return where.

    ``text`` is what the file says (the markdown, or the text a PDF prints): a contact shape in
    it is refused, ``contact_data_found``.  ``key`` names the job the file belongs to
    (``job_key``); ``replace_older`` removes that job's earlier file of this kind under another
    name (an older date).  Only a file whose bytes are what GigAI last wrote is ever replaced or
    removed.  Raises ``ResumesFolderError``.
    """

    found = detect_contact_details(text)
    if found:
        raise ResumesFolderError(
            "contact_data_found",
            f"this resume holds contact details ({', '.join(found)}); GigAI's resumes folder never holds them",
        )
    home_root = Path(home_root)
    folder = resumes_folder(home_root).path
    _make_folder(folder)
    extension = os.path.splitext(name)[1]
    digest = digest_imported_bytes(data)
    try:
        with _folder_lock(folder):
            files = _load_index(home_root, folder)
            chosen: str | None = None
            if key is not None:
                for mine in [item for item, entry in files.items() if entry["key"] == key and item.endswith(extension)]:
                    if not _untouched(folder / mine, files[mine]["sha256"]):
                        del files[mine]  # changed or removed by the user: theirs now
                    elif _is_numbered(mine, name):
                        chosen = chosen or mine
                    elif replace_older:
                        (folder / mine).unlink()
                        del files[mine]
            if chosen is None:
                for attempt in range(1, _MAX_NAME_TRIES + 1):
                    candidate = _numbered(name, attempt)
                    path = folder / candidate
                    entry = files.get(candidate)
                    if not path.is_symlink() and not path.exists():
                        chosen = candidate
                    elif entry is not None and entry["key"] == key and _untouched(path, entry["sha256"]):
                        chosen = candidate  # GigAI's own, untouched, for the same job (or for no job)
                    if chosen is not None:
                        break
            if chosen is None:
                raise ResumesFolderError("folder_unwritable", "the resumes folder has no free file name for this resume")
            target = folder / chosen
            written = not _untouched(target, digest)
            if written:
                atomic_write(target, data)
            if written or files.get(chosen) != {"sha256": digest, "key": key}:
                files[chosen] = {"sha256": digest, "key": key}
                _save_index(home_root, folder, files)
            return SavedFile(target, written)
    except OSError as exc:
        raise ResumesFolderError("folder_unwritable", "the file could not be written into the resumes folder") from exc


def save_markdown(home_root: Path, *, key: str, company: str, role: str, day: date, markdown: str) -> SavedFile:
    """One job's tailored markdown, as a reader sees it; the job's earlier markdown GigAI wrote is replaced."""

    text = readable_markdown(markdown)
    return save(home_root, name=file_name(company, role, day, MARKDOWN), data=text.encode("utf-8"), text=text, key=key, replace_older=True)


def save_pdf(home_root: Path, *, name: str, pdf: bytes, text: str, key: str | None = None) -> SavedFile:
    """A PDF rendered WITHOUT a header; ``text`` is what it prints.  A headered PDF is never passed here."""

    return save(home_root, name=name, data=pdf, text=text, key=key)


def try_save_markdown(home_root: Path, *, key: str, company: str, role: str, day: date, markdown: str) -> SavedFile | None:
    """``save_markdown`` for a write path that must not fail because of the folder: ``None`` and one log line (the code only)."""

    try:
        return save_markdown(home_root, key=key, company=company, role=role, day=day, markdown=markdown)
    except ResumesFolderError as exc:
        _logger.warning("resumes folder: the tailored markdown was not written (%s)", exc.code)
        return None


def job_files(home_root: Path, key: str) -> dict[str, str | None]:
    """The names of one job's files GigAI wrote that are still in the folder: ``{"markdown", "pdf"}`` (newest name each)."""

    home_root = Path(home_root)
    folder = resumes_folder(home_root).path
    found: dict[str, str | None] = {"markdown": None, "pdf": None}
    for name, entry in sorted(_load_index(home_root, folder).items()):
        if entry["key"] != key or not (folder / name).is_file():
            continue
        kind = "markdown" if name.endswith(MARKDOWN) else "pdf" if name.endswith(PDF) else None
        if kind is not None:
            found[kind] = name
    return found


# --- the master resume's file (0.1.10.9 master P8) -------------------------------------------


@dataclass(frozen=True)
class MasterFile:
    """``master.md`` in the resumes folder, against what GigAI last wrote there."""

    path: Path
    #: ``missing``, ``current`` (exactly what GigAI last wrote) or ``changed`` (edited since, or never GigAI's).
    state: str
    #: The master revision GigAI last wrote into ``master.md`` (its number and id); ``None`` when it never did.
    revision: int | None = None
    revision_id: str | None = None
    #: The digest of the file's bytes now (``None`` when it is missing or not a plain file).
    sha256: str | None = None
    #: GigAI's own file beside ``master.md`` that holds a newer revision while ``master.md`` has changes not imported.
    beside: Path | None = None
    #: ``save_master``: whether this call wrote bytes (``False`` when the file already held them).
    written: bool = False

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def shown(self) -> str:
        return _display_path(self.path)

    def to_json(self) -> dict[str, object]:
        return {
            "name": self.name, "path": self.shown, "state": self.state, "not_imported": self.state == MASTER_CHANGED,
            "revision": self.revision, "beside": self.beside.name if self.beside is not None else None,
        }


def _digest_of(path: Path) -> str | None:
    try:
        return digest_imported_bytes(path.read_bytes()) if not path.is_symlink() and path.is_file() else None
    except OSError:
        return None


def _master_entry(files: dict[str, dict[str, object]], name: str = MASTER_NAME) -> dict[str, object] | None:
    entry = files.get(name)
    return entry if entry is not None and entry["key"] == MASTER_KEY else None


def _master_besides(files: dict[str, dict[str, object]]) -> list[str]:
    """The index's names of the files GigAI wrote beside ``master.md`` (``master-2.md``, ...)."""

    return sorted(name for name, entry in files.items() if entry["key"] == MASTER_KEY and name != MASTER_NAME and _is_numbered(name, MASTER_NAME))


def master_file(home_root: Path) -> MasterFile:
    """How ``master.md`` stands in the resumes folder.  Reads only (the index and the one file's bytes)."""

    home_root = Path(home_root)
    folder = resumes_folder(home_root).path
    path = folder / MASTER_NAME
    files = _load_index(home_root, folder)
    entry = _master_entry(files) or {}
    revision, revision_id = entry.get("revision"), entry.get("revision_id")
    beside = next((folder / name for name in _master_besides(files) if _untouched(folder / name, files[name]["sha256"])), None)
    known = {"revision": revision if type(revision) is int else None, "revision_id": revision_id if isinstance(revision_id, str) else None, "beside": beside}
    if not path.is_symlink() and not path.exists():
        return MasterFile(path, MASTER_MISSING, **known)  # type: ignore[arg-type]
    digest = _digest_of(path)
    state = MASTER_CURRENT if digest is not None and digest == entry.get("sha256") else MASTER_CHANGED
    return MasterFile(path, state, sha256=digest, **known)  # type: ignore[arg-type]


def save_master(home_root: Path, *, markdown: str, text: str, revision: int, revision_id: str, imported: str | None = None) -> MasterFile:
    """Write the stored master's ``markdown`` (revision ``revision``) into the resumes folder; how the file stands after.

    ``master.md`` is written when it is not there, when it is exactly what GigAI last wrote, or
    when its bytes are ``imported`` (the digest of the file GigAI has just imported at the user's
    request).  Otherwise it holds changes GigAI did not write: it is left alone, and the markdown
    goes beside it as ``master-2.md`` (GigAI's own; the next write replaces it), state
    ``changed``.  Once ``master.md`` is written again, GigAI's beside file is removed.  ``text``
    is what the file says without its id comments: a contact shape in it is refused,
    ``contact_data_found``.  Raises ``ResumesFolderError``.
    """

    found = detect_contact_details(text)
    if found:
        raise ResumesFolderError(
            "contact_data_found",
            f"the master resume holds contact details ({', '.join(found)}); GigAI's resumes folder never holds them",
        )
    home_root = Path(home_root)
    folder = resumes_folder(home_root).path
    _make_folder(folder)
    data = markdown.encode("utf-8")
    digest = digest_imported_bytes(data)
    stamp: dict[str, object] = {"sha256": digest, "key": MASTER_KEY, "revision": revision, "revision_id": revision_id}
    try:
        with _folder_lock(folder):
            files = _load_index(home_root, folder)
            before = {name: dict(entry) for name, entry in files.items()}
            path = folder / MASTER_NAME
            entry = _master_entry(files)
            free = not path.is_symlink() and not path.exists()
            mine = entry is not None and _untouched(path, entry["sha256"])
            asked = imported is not None and _untouched(path, imported)
            besides = _master_besides(files)
            if free or mine or asked:
                written = not _untouched(path, digest)
                if written:
                    atomic_write(path, data)
                files[MASTER_NAME] = stamp
                for name in besides:
                    if _untouched(folder / name, files[name]["sha256"]):
                        (folder / name).unlink()
                    del files[name]  # removed, or changed by the user: theirs now
                result = MasterFile(path, MASTER_CURRENT, revision, revision_id, digest, None, written)
            else:
                # master.md holds changes GigAI did not write (or was never GigAI's): never replaced.
                chosen: str | None = None
                for name in besides:
                    if chosen is None and _untouched(folder / name, files[name]["sha256"]):
                        chosen = name
                    elif not _untouched(folder / name, files[name]["sha256"]):
                        del files[name]
                for attempt in range(2, _MAX_NAME_TRIES + 1):
                    if chosen is not None:
                        break
                    candidate = folder / _numbered(MASTER_NAME, attempt)
                    if not candidate.is_symlink() and not candidate.exists():
                        chosen = candidate.name
                if chosen is None:
                    raise ResumesFolderError("folder_unwritable", "the resumes folder has no free file name for the master resume")
                target = folder / chosen
                written = not _untouched(target, digest)
                if written:
                    atomic_write(target, data)
                files[chosen] = stamp
                result = MasterFile(
                    path, MASTER_CHANGED, entry.get("revision") if entry else None, entry.get("revision_id") if entry else None,  # type: ignore[arg-type]
                    _digest_of(path), target, written,
                )
            if files != before:
                _save_index(home_root, folder, files)
            return result
    except OSError as exc:
        raise ResumesFolderError("folder_unwritable", "the master resume could not be written into the resumes folder") from exc


__all__ = [
    "INDEX_SCHEMA",
    "MARKDOWN",
    "MASTER_CHANGED",
    "MASTER_CURRENT",
    "MASTER_KEY",
    "MASTER_MISSING",
    "MASTER_NAME",
    "MAX_PATH_CHARS",
    "MasterFile",
    "PDF",
    "RESPONSE_SCHEMA",
    "SETTING_SCHEMA",
    "SOURCE_DEFAULT",
    "SOURCE_SETTING",
    "ResumesFolder",
    "ResumesFolderError",
    "SavedFile",
    "default_folder",
    "file_name",
    "index_path",
    "job_files",
    "job_key",
    "job_store_key",
    "master_file",
    "readable_markdown",
    "resumes_folder",
    "save",
    "save_markdown",
    "save_master",
    "save_pdf",
    "set_resumes_folder",
    "setting_path",
    "try_save_markdown",
]
