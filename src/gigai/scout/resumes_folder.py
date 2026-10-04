"""0110-10-05 A: the resumes folder -- the one visible place GigAI puts a job's resume files.

Everything about the folder is here (where it is, the setting, the file names, what may be
written into it and what may be replaced), so the tailored resumes, ``gigai scout resume pdf``
and, later, the master resume all go through one module.

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
from .resume_pii import detect_contact_details
from .target_resolution import _display_path

SETTING_SCHEMA = "scout-resumes-folder:1"
INDEX_SCHEMA = "scout-resumes-folder-index:1"
RESPONSE_SCHEMA = "scout-resumes-folder-response:1"

SOURCE_DEFAULT = "default"
SOURCE_SETTING = "setting"

MARKDOWN = ".md"
PDF = ".pdf"

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
    raw = _read_json(setting_path(home_root))
    saved = raw.get("path") if raw is not None and raw.get("schema_version") == SETTING_SCHEMA else None
    if isinstance(saved, str) and saved and not _CONTROL.search(saved) and Path(saved).is_absolute():
        return ResumesFolder(Path(saved), SOURCE_SETTING, default)
    return ResumesFolder(default, SOURCE_DEFAULT, default)


def _make_folder(path: Path) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ResumesFolderError("folder_unwritable", "the resumes folder could not be created; choose another folder") from exc
    if not os.access(path, os.W_OK | os.X_OK):
        raise ResumesFolderError("folder_unwritable", "the resumes folder cannot be written; choose another folder")


def set_resumes_folder(home_root: Path, value: object, *, now: datetime | None = None) -> ResumesFolder:
    """Save ``value`` as the resumes folder (created when missing); ``None`` or ``""`` goes back to the default.

    ``value`` is an absolute path or one that starts with ``~``.  Refused (``invalid_value``): a
    relative path, a path that is a file, and a folder inside the GigAI home other than the
    default one (the home is GigAI's own store).  Files already in the old folder are not moved.
    """

    home_root = Path(home_root)
    path_file = setting_path(home_root)
    if value is None or (isinstance(value, str) and not value.strip()):
        try:
            if path_file.is_symlink() or path_file.exists():
                path_file.unlink()
        except OSError as exc:
            raise ResumesFolderError("setting_write_failed", "could not save the resumes folder setting") from exc
        return resumes_folder(home_root)
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
        raise ResumesFolderError("invalid_value", "path must be absolute or start with ~ (for example ~/Documents/GigAI/resumes)")
    folder = Path(os.path.normpath(expanded))
    default = default_folder(home_root)
    if folder != default:
        try:
            store, resolved = home_root.expanduser().resolve(strict=False), folder.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise ResumesFolderError("invalid_value", "path could not be resolved") from exc
        if resolved == store or resolved.is_relative_to(store):
            raise ResumesFolderError("invalid_value", "path is inside the GigAI home, which is GigAI's own store; choose a folder outside it")
    if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
        raise ResumesFolderError("invalid_value", "path is not a folder")
    _make_folder(folder)
    stamp = (now or datetime.now(timezone.utc)).isoformat().replace("+00:00", "Z")
    payload = {"schema_version": SETTING_SCHEMA, "path": os.fspath(folder), "updated_at": stamp}
    try:
        if path_file.is_symlink():
            raise OSError("the resumes folder setting path is a symlink")
        path_file.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        atomic_write(path_file, (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
        os.chmod(path_file, 0o600)
    except OSError as exc:
        raise ResumesFolderError("setting_write_failed", "could not save the resumes folder setting") from exc
    return ResumesFolder(folder, SOURCE_SETTING, default)


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
        name: {"sha256": entry.get("sha256"), "key": entry.get("key")}
        for name, entry in files.items()
        if isinstance(name, str) and type(entry) is dict and isinstance(entry.get("sha256"), str)
    }


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


def sync_tailored(home_root: Path, target: Path) -> int:
    """Write the markdown of every stored tailored resume that was never put in the folder; how many were written.

    For a home from before the folder existed.  A resume whose file the user removed is not
    written again (its index entry stays); a later save of that resume writes it.  Never raises.
    """

    from .tailored_resume import export_tailored_markdown, read_tailored_resume, tailored_resume_dir

    home_root = Path(home_root)
    written = 0
    try:
        root = tailored_resume_dir(home_root, Path(target))
        if not root.is_dir():
            return 0
        known = {entry["key"] for entry in _load_index(home_root, resumes_folder(home_root).path).values()}
        # Names only: a stored resume is read (and parsed) just the once it is copied.
        for path in sorted(root.glob("*/*.json")):
            if job_key(home_root, path) in known:
                continue
            item = read_tailored_resume(path)
            if item is None:
                continue
            saved = export_tailored_markdown(item, home_root=home_root)
            written += int(saved is not None and saved.written)
    except Exception as exc:  # noqa: BLE001 - a start never fails because of the folder: logged by type
        _logger.warning("resumes folder: the stored tailored resumes were not copied (%s)", type(exc).__name__)
    return written


__all__ = [
    "INDEX_SCHEMA",
    "MARKDOWN",
    "MAX_PATH_CHARS",
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
    "readable_markdown",
    "resumes_folder",
    "save",
    "save_markdown",
    "save_pdf",
    "set_resumes_folder",
    "setting_path",
    "sync_tailored",
    "try_save_markdown",
]
