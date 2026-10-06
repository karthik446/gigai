"""0.1.11.4 J1: the jobs folder -- one folder per application, the visible place of a job's files.

``<jobs>/<company>/<role>/resume.md`` is the job's picked resume.  The same folder is where the
cover-letter skill writes ``cover-letter.md``, and ``interview/`` is the interview package's
name there: GigAI only reserves these two names (``COVER_LETTER_NAME``, ``INTERVIEW_DIR``) and
never creates either one, so ``interview/`` exists only once prep does.

**Where.**  ``~/Documents/GigAI/jobs`` for the default GigAI home (``~/.gigai``); any other home
(``--home``, ``GIGAI_HOME``: a scratch or test home) defaults to ``<home>/jobs``.
``set_jobs_folder`` saves another place in ``<home>/scout/jobs-folder.json`` (0600), by the
resumes folder's own rule (``resumes_folder._save_folder_setting``).  Folders already written
stay where they are.

**Names.**  ``<company>`` is the slug of the posting's company DISPLAY name ("Thrive Market" ->
``thrive-market``), ``<role>`` the slug of its title: lowercase ASCII, hyphens, length-capped,
never a date and never the user's name.  Two roles at one company are two folders under it.
When another job already holds ``<company>/<role>`` (same company, same title slug, another
posting) the job's short id is appended: ``<role>-<id>``.  That is decided ONCE: the folder's
``.gigai-job.json`` records the job (its key, the posting's address and identity digest, the
company and role as shown, the day it was created: the date lives there, not in a name), and
``<home>/scout/jobs-folder-index.json`` maps each job key to its folder, so the same job always
maps to the same folder and a re-pick rewrites it.  A read is one index lookup; nothing here
scans the folder.

**Never contact data.**  ``save_resume`` refuses a text that holds a contact shape
(``resume_pii.detect_contact_details``) with ``contact_data_found``.  Agents read this folder:
nothing with the PDF header's name or contact details, and no PDF, is ever written into it (a
generated PDF goes where the user saves it).

**Never the user's file.**  ``resume.md`` is replaced only when its bytes are exactly what GigAI
last wrote there (the digest is in the index: folder names, file names and digests, no text).
A ``resume.md`` the user changed, or one GigAI did not write, is left alone and the new resume
is written beside it as ``resume-2.md`` (GigAI's own; removed once ``resume.md`` is GigAI's
again).  A folder that is not this job's (no ``.gigai-job.json`` of this job) is never used.

Nothing here calls a model or the network.  Errors name a rule, never a resume's text.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
import json
import logging
import os
from pathlib import Path

from ..canonical import digest_imported_bytes
from .find_jobs.discovery.storage import atomic_write
from .resume_pii import detect_contact_details
from .resumes_folder import (
    SOURCE_DEFAULT,
    SOURCE_SETTING,
    ResumesFolderError,
    _folder_lock,
    _is_numbered,
    _make_folder,
    _MAX_NAME_TRIES,
    _numbered,
    _read_json,
    _same_path,
    _save_folder_setting,
    _saved_folder,
    _slug,
    _untouched,
    readable_markdown,
)
from .target_resolution import _display_path

SETTING_SCHEMA = "scout-jobs-folder:1"
INDEX_SCHEMA = "scout-jobs-folder-index:1"
RESPONSE_SCHEMA = "scout-jobs-folder-response:1"
JOB_SCHEMA = "scout-jobs-folder-job:1"

#: The job's picked resume, as a reader sees it.
RESUME_NAME = "resume.md"
#: Reserved: the cover-letter skill writes it beside the resume.  GigAI never creates it.
COVER_LETTER_NAME = "cover-letter.md"
#: Reserved: the interview package's folder, there only once prep exists.  GigAI never creates it empty.
INTERVIEW_DIR = "interview"
#: The record of which job a folder is (GigAI's own; ids, the posting's address and names, a date).
JOB_FILE = ".gigai-job.json"

_COMPANY_MAX = 40
_ROLE_MAX = 60
#: What a part is called when its text has no ASCII letter or digit (an empty slug).
_NO_COMPANY = "company"
_NO_ROLE = "role"
_SHORT_ID_CHARS = 8

_logger = logging.getLogger("gigai.scout.jobs_folder")


class JobsFolderError(ResumesFolderError):
    """The jobs folder cannot be used as asked; ``code`` is the API/CLI error code (the resumes folder's codes)."""


# --- where ----------------------------------------------------------------------------------


def default_folder(home_root: Path) -> Path:
    """``~/Documents/GigAI/jobs`` for the default GigAI home, ``<home>/jobs`` for any other."""

    home_root = Path(home_root).expanduser()
    try:
        user_home = Path.home()
    except RuntimeError:
        return home_root / "jobs"
    if _same_path(home_root, user_home / ".gigai"):
        return user_home / "Documents" / "GigAI" / "jobs"
    return home_root / "jobs"


def setting_path(home_root: Path) -> Path:
    return Path(home_root) / "scout" / "jobs-folder.json"


def index_path(home_root: Path) -> Path:
    return Path(home_root) / "scout" / "jobs-folder-index.json"


@dataclass(frozen=True)
class JobsFolder:
    """The folder and what said so (``default`` or ``setting``)."""

    path: Path
    source: str
    default: Path

    @property
    def shown(self) -> str:
        """The folder the way the user types it (``~/Documents/GigAI/jobs``)."""

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


def jobs_folder(home_root: Path) -> JobsFolder:
    """The jobs folder of this home: the saved setting, else the default.  Reads only."""

    default = default_folder(home_root)
    saved = _saved_folder(setting_path(home_root), SETTING_SCHEMA)
    if saved is not None:
        return JobsFolder(saved, SOURCE_SETTING, default)
    return JobsFolder(default, SOURCE_DEFAULT, default)


def set_jobs_folder(home_root: Path, value: object, *, now: datetime | None = None) -> JobsFolder:
    """Save ``value`` as the jobs folder (created when missing); ``None`` or ``""`` goes back to the default.

    The resumes folder's rule, word for word: an absolute path or one that starts with ``~``; a
    relative path, a file, and a folder inside the GigAI home other than the default one are
    refused (``invalid_value``).  Job folders already written are not moved.
    """

    home_root = Path(home_root)
    default = default_folder(home_root)
    try:
        folder = _save_folder_setting(home_root, value, setting_file=setting_path(home_root), schema=SETTING_SCHEMA, default=default, noun="jobs", now=now)
    except ResumesFolderError as exc:
        raise JobsFolderError(exc.code, str(exc)) from exc
    return jobs_folder(home_root) if folder is None else JobsFolder(folder, SOURCE_SETTING, default)


# --- names ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class JobRef:
    """The job a folder is for: its key (``resumes_folder.job_key``), the posting's address and the names it shows."""

    key: str
    job_identity: str
    company: str
    role: str


def company_slug(company: str) -> str:
    """``<company>``: the display name as a folder name ("Thrive Market" -> ``thrive-market``)."""

    return _slug(company or "", _COMPANY_MAX) or _NO_COMPANY


def role_slug(role: str) -> str:
    """``<role>``: the title as a folder name; never a date, never an id."""

    return _slug(role or "", _ROLE_MAX) or _NO_ROLE


def short_id(key: str) -> str:
    """The job's short id: what a collision appends to ``<role>`` (8 hex characters of the job key's digest)."""

    return digest_imported_bytes(key.encode("utf-8")).rsplit(":", 1)[-1][:_SHORT_ID_CHARS]


# --- the index and a folder's own record ------------------------------------------------------


def _load_index(home_root: Path, folder: Path) -> dict[str, dict[str, object]]:
    """``{job key: {"dir": "<company>/<role>", "files": {name: sha256}}}`` of this folder (another folder's is not this one's)."""

    raw = _read_json(index_path(home_root))
    if raw is None or raw.get("schema_version") != INDEX_SCHEMA or raw.get("folder") != os.fspath(folder):
        return {}
    jobs = raw.get("jobs")
    if type(jobs) is not dict:
        return {}
    kept: dict[str, dict[str, object]] = {}
    for key, entry in jobs.items():
        if not isinstance(key, str) or type(entry) is not dict or not _safe_dir(entry.get("dir")):
            continue
        files = entry.get("files")
        kept[key] = {
            "dir": entry["dir"],
            "files": {name: sha for name, sha in files.items() if isinstance(name, str) and isinstance(sha, str)} if type(files) is dict else {},
        }
    return kept


def _safe_dir(value: object) -> bool:
    """A stored ``<company>/<role>``: two plain names, so an index never points outside the folder."""

    if not isinstance(value, str):
        return False
    parts = value.split("/")
    return len(parts) == 2 and all(part and part == _slug(part, len(part)) for part in parts)


def _save_index(home_root: Path, folder: Path, jobs: dict[str, dict[str, object]]) -> None:
    path = index_path(home_root)
    if path.is_symlink():
        raise OSError("the jobs folder index path is a symlink")
    payload = {"schema_version": INDEX_SCHEMA, "folder": os.fspath(folder), "jobs": {key: jobs[key] for key in sorted(jobs)}}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


def _owner(directory: Path) -> str | None:
    """The job key a folder's ``.gigai-job.json`` names; ``None`` when it has none (not GigAI's job folder)."""

    raw = _read_json(directory / JOB_FILE)
    key = raw.get("job_key") if raw is not None and raw.get("schema_version") == JOB_SCHEMA else None
    return key if isinstance(key, str) and key else None


def _taken(directory: Path) -> bool:
    return directory.is_symlink() or directory.exists()


def _usable(directory: Path, key: str, *, recorded: bool = False) -> bool:
    """This job may write into ``directory``: it is free, or it is a plain folder whose record names this job.

    ``recorded``: the index already maps this job to it, so a folder whose record was removed is still this job's
    (the record is written again); one whose record names another job never is."""

    if not _taken(directory):
        return True
    if directory.is_symlink() or not directory.is_dir():
        return False
    owner = _owner(directory)
    return owner == key or (recorded and owner is None)


def _choose_dir(root: Path, job: JobRef, recorded: object) -> str:
    """``<company>/<role>`` for ``job``: the folder recorded for it, else the plain name, else ``<role>-<id>`` (a collision)."""

    if isinstance(recorded, str) and _usable(root / recorded, job.key, recorded=True):
        return recorded  # decided once: the same job, the same folder
    company, role = company_slug(job.company), role_slug(job.role)
    parent = root / company
    if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise JobsFolderError("folder_unwritable", "the jobs folder has a file where this job's company folder goes")
    suffixed = f"{role}-{short_id(job.key)}"
    for name in (role, suffixed, *(f"{suffixed}-{attempt}" for attempt in range(2, _MAX_NAME_TRIES + 1))):
        if _usable(parent / name, job.key):
            return f"{company}/{name}"
    raise JobsFolderError("folder_unwritable", "the jobs folder has no free folder name for this job")


def _write_job_record(directory: Path, job: JobRef, day: date) -> None:
    """``.gigai-job.json``, written once: which job this folder is, and the day it was made."""

    if _owner(directory) == job.key:
        return
    payload = {
        "schema_version": JOB_SCHEMA,
        "job_key": job.key,
        "job_url": job.job_identity,
        "identity_sha256": digest_imported_bytes(job.job_identity.encode("utf-8")),
        "company": job.company,
        "role": job.role,
        "created": day.isoformat(),
    }
    path = directory / JOB_FILE
    if path.is_symlink():
        raise OSError("the job record path is a symlink")
    atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


# --- writing --------------------------------------------------------------------------------


@dataclass(frozen=True)
class SavedJobFile:
    """A file GigAI wrote in a job's folder."""

    path: Path
    #: False when the file already held exactly these bytes.
    written: bool = True

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def shown(self) -> str:
        return _display_path(self.path)

    @property
    def folder(self) -> Path:
        return self.path.parent


def save_resume(home_root: Path, *, job: JobRef, markdown: str, day: date, imported: str | None = None) -> SavedJobFile:
    """Write one job's picked resume as ``resume.md`` in the job's folder and return where.

    ``markdown`` is the stored resume's; the file holds it as a reader sees it
    (``readable_markdown``).  A contact shape in it is refused, ``contact_data_found``.
    ``resume.md`` is replaced only when it is exactly what GigAI last wrote (or already holds
    these bytes); otherwise it is left alone and the resume goes beside it (``resume-2.md``).
    One more file may be replaced, by the master file's rule: one whose bytes are ``imported``
    (the digest of the text the user has just handed back for this job, so an edit made in
    ``resume.md`` itself and stored is not answered with a second file).
    ``day`` is recorded in the folder's ``.gigai-job.json`` the first time only.  Raises
    ``JobsFolderError``.
    """

    text = readable_markdown(markdown)
    found = detect_contact_details(text)
    if found:
        raise JobsFolderError(
            "contact_data_found",
            f"this resume holds contact details ({', '.join(found)}); GigAI's jobs folder never holds them",
        )
    home_root = Path(home_root)
    root = jobs_folder(home_root).path
    data = text.encode("utf-8")
    digest = digest_imported_bytes(data)
    try:
        _make_folder(root, "jobs")
    except ResumesFolderError as exc:
        raise JobsFolderError(exc.code, str(exc)) from exc
    try:
        with _folder_lock(root):
            jobs = _load_index(home_root, root)
            before = json.dumps(jobs, sort_keys=True)
            entry = jobs.get(job.key) or {}
            relative = _choose_dir(root, job, entry.get("dir"))
            directory = root / relative
            directory.mkdir(parents=True, exist_ok=True)
            _write_job_record(directory, job, day)
            files: dict[str, str] = dict(entry.get("files") or {}) if entry.get("dir") == relative else {}  # type: ignore[call-overload]
            for name in [name for name in files if not _untouched(directory / name, files[name])]:
                del files[name]  # changed or removed by the user: theirs now
            chosen: str | None = None
            for attempt in range(1, _MAX_NAME_TRIES + 1):
                candidate = _numbered(RESUME_NAME, attempt)
                path = directory / candidate
                # Free, GigAI's own and untouched, already exactly these bytes, or the file the user just handed back.
                if not _taken(path) or candidate in files or _untouched(path, digest) or (imported is not None and _untouched(path, imported)):
                    chosen = candidate
                    break
            if chosen is None:
                raise JobsFolderError("folder_unwritable", "the job's folder has no free file name for this resume")
            target = directory / chosen
            written = not _untouched(target, digest)
            if written:
                atomic_write(target, data)
            for name in [name for name in files if name != chosen and _is_numbered(name, RESUME_NAME)]:
                (directory / name).unlink()  # GigAI's own earlier resume beside it (untouched: checked above)
                del files[name]
            files[chosen] = digest
            jobs[job.key] = {"dir": relative, "files": files}
            if json.dumps(jobs, sort_keys=True) != before:
                _save_index(home_root, root, jobs)
            return SavedJobFile(target, written)
    except OSError as exc:
        raise JobsFolderError("folder_unwritable", "the resume could not be written into the jobs folder") from exc


def try_save_resume(home_root: Path, *, job: JobRef, markdown: str, day: date, imported: str | None = None) -> SavedJobFile | None:
    """``save_resume`` for a write path that must not fail because of the folder: ``None`` and one log line (the code only)."""

    try:
        return save_resume(home_root, job=job, markdown=markdown, day=day, imported=imported)
    except ResumesFolderError as exc:
        _logger.warning("jobs folder: the job's resume was not written (%s)", exc.code)
        return None


# --- reading (one index lookup; never a scan) -------------------------------------------------


@dataclass(frozen=True)
class JobFolder:
    """One job's folder, as the index names it."""

    path: Path
    #: ``<company>/<role>`` under the jobs folder (the posting's words).
    relative: str
    #: The job's resume file GigAI wrote that is still there (``resume.md``; ``resume-2.md`` beside one the user changed), or ``None``.
    resume: str | None

    @property
    def shown(self) -> str:
        return _display_path(self.path)

    @property
    def resume_path(self) -> Path | None:
        return None if self.resume is None else self.path / self.resume

    @property
    def resume_shown(self) -> str | None:
        return None if self.resume is None else f"{self.shown}/{self.resume}"

    def to_json(self) -> dict[str, object]:
        """Paths and names only, never a file's contents."""

        return {"path": os.fspath(self.path), "shown": self.shown, "relative": self.relative, "files": {"resume": self.resume}}


def job_folder(home_root: Path, key: str) -> JobFolder | None:
    """The folder of the job ``key`` names, or ``None`` when GigAI has not made one (or it is gone).  Reads only."""

    home_root = Path(home_root)
    root = jobs_folder(home_root).path
    entry = _load_index(home_root, root).get(key)
    if entry is None:
        return None
    relative = str(entry["dir"])
    directory = root / relative
    if directory.is_symlink() or not directory.is_dir():
        return None
    names = sorted(name for name in entry["files"] if (directory / name).is_file())  # type: ignore[union-attr]
    resume = RESUME_NAME if RESUME_NAME in names else next((name for name in names if _is_numbered(name, RESUME_NAME)), None)
    return JobFolder(directory, relative, resume)


def stored_job_folder(home_root: Path, stored_path: str | os.PathLike[str]) -> JobFolder | None:
    """``job_folder`` of the job whose resume the store keeps at ``stored_path`` (``tailored_resume_path``)."""

    from .resumes_folder import job_key

    return job_folder(home_root, job_key(Path(home_root), stored_path))


__all__ = [
    "COVER_LETTER_NAME",
    "INDEX_SCHEMA",
    "INTERVIEW_DIR",
    "JOB_FILE",
    "JOB_SCHEMA",
    "RESPONSE_SCHEMA",
    "RESUME_NAME",
    "SETTING_SCHEMA",
    "SOURCE_DEFAULT",
    "SOURCE_SETTING",
    "JobFolder",
    "JobRef",
    "JobsFolder",
    "JobsFolderError",
    "SavedJobFile",
    "company_slug",
    "default_folder",
    "index_path",
    "job_folder",
    "jobs_folder",
    "role_slug",
    "save_resume",
    "set_jobs_folder",
    "setting_path",
    "short_id",
    "stored_job_folder",
    "try_save_resume",
]
