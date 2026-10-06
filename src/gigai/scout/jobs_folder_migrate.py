"""0.1.11.4 J2: the one-time import of the flat resumes folder into the jobs folder.

Before 0.1.11.4 a job's resume was a flat file of the resumes folder,
``<company>-<role>-<YYYY-MM-DD>.md``.  ``migrate`` copies each of them ONCE to
``<jobs>/<company>/<role>/resume.md`` (``jobs_folder``'s layout).

**Which files.**  Only the job markdown files the resumes folder's index knows by a job key.
``master.md`` is not a job and stays; a PDF never goes into the jobs folder.

**Company and role come from the stored job record**, never from the file's name: the index
gives the file's job key, the key names the stored resume under ``<home>/scout``, and that
record's job gives the company (its display name) and the title, by the very rule a pick uses
(``tailored_resume.folder_job_ref``).  So a file and a later pick of the same job share one
folder, and two files of one job (a ``-2`` beside the first) fold into it.

**Not moved, and listed.**  A file with no stored job (an orphan: the index does not know it,
or its stored resume is gone); a file that holds contact details (the jobs folder never holds
them; agents read it); a file that is not text.

**Copy, never move.**  The old folder is left byte for byte as it was, and no note is written
into it.  Nothing in the jobs folder is ever replaced: a file whose bytes GigAI wrote becomes
``resume.md`` (GigAI's own there, so a later pick rewrites it); a file the user edited (its
digest differs from the index's) is copied as the user's own, ``resume.md`` when that name is
free and ``resume-2.md`` otherwise, and GigAI never replaces it.  A job whose folder already
holds the resume GigAI picked since is not given the older flat copy.

**Once.**  ``<home>/scout/jobs-folder-migration.json`` (0600: file names and digests, no text)
records what was imported, so a second run is a no-op ("already migrated").  A dry run plans
with the same code and writes nothing at all.

The safety is ``jobs_folder``'s: one writer (the folder lock), atomic writes, the index.
Nothing here calls a model or the network.  Errors name a rule, never a resume's text.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
import json
import os
from pathlib import Path, PurePosixPath

from ..canonical import digest_imported_bytes
from . import jobs_folder, resumes_folder
from .find_jobs.discovery.storage import atomic_write
from .jobs_folder import RESUME_NAME, JobRef, JobsFolderError
from .resume_pii import detect_contact_details
from .resumes_folder import _MAX_NAME_TRIES, _folder_lock, _is_numbered, _make_folder, _numbered, _read_json, _untouched, ResumesFolderError
from .target_resolution import _display_path

RECORD_SCHEMA = "scout-jobs-folder-migration:1"
RESPONSE_SCHEMA = "scout-jobs-folder-migrate:1"

#: Why a file was not moved.
NO_STORED_JOB = "no_stored_job"
NOT_IN_INDEX = "not_in_index"
NOT_TEXT = "not_text"
#: Why a file counts as migrated already.
RECORDED = "recorded"
SAME_BYTES = "same_bytes"
NEWER_RESUME = "job_folder_has_resume"

MIGRATE_COMMAND = "gigai scout jobs-folder migrate"


def record_path(home_root: Path) -> Path:
    return Path(home_root) / "scout" / "jobs-folder-migration.json"


@dataclass
class _Record:
    """What was imported from one old folder: ``files`` (name -> digest and where it went) and the contact skips."""

    files: dict[str, dict[str, str]] = field(default_factory=dict)
    skipped: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict[str, object]:
        return {"files": {name: self.files[name] for name in sorted(self.files)}, "skipped": {name: self.skipped[name] for name in sorted(self.skipped)}}


def _load_record(home_root: Path, old: Path) -> _Record:
    raw = _read_json(record_path(home_root))
    if raw is None or raw.get("schema_version") != RECORD_SCHEMA or raw.get("from") != os.fspath(old):
        return _Record()  # another folder's import is not this folder's
    files, skipped = raw.get("files"), raw.get("skipped")
    return _Record(
        {
            name: {"sha256": entry["sha256"], "to": entry["to"]}
            for name, entry in (files.items() if type(files) is dict else ())
            if isinstance(name, str) and type(entry) is dict and isinstance(entry.get("sha256"), str) and isinstance(entry.get("to"), str)
        },
        {name: sha for name, sha in (skipped.items() if type(skipped) is dict else ()) if isinstance(name, str) and isinstance(sha, str)},
    )


def _save_record(home_root: Path, old: Path, record: _Record) -> None:
    path = record_path(home_root)
    if path.is_symlink():
        raise OSError("the jobs folder migration record path is a symlink")
    payload = {"schema_version": RECORD_SCHEMA, "from": os.fspath(old), **record.to_json()}
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


# --- which files ----------------------------------------------------------------------------


def _job_markdown(name: str, entry: dict[str, object]) -> bool:
    """An index entry of a job's markdown: not the master's file, not a PDF."""

    return name.endswith(resumes_folder.MARKDOWN) and entry.get("key") != resumes_folder.MASTER_KEY and not _is_numbered(name, resumes_folder.MASTER_NAME)


def _stored_file(home_root: Path, key: object) -> Path | None:
    """Where the store keeps the resume ``key`` names (``resumes_folder.job_key``), or ``None`` for a key that is no store path."""

    if not isinstance(key, str) or not key or key.startswith("sha256:"):
        return None
    parts = PurePosixPath(key)
    if parts.is_absolute() or any(part in ("", ".", "..") for part in parts.parts):
        return None
    return Path(home_root) / "scout" / f"{key}.json"


def _stored_job(home_root: Path, key: object) -> tuple[JobRef, date] | None:
    """``(JobRef, day)`` of the job whose stored resume ``key`` names, or ``None`` (an orphan)."""

    from .tailored_resume import folder_job_ref, read_tailored_resume

    path = _stored_file(home_root, key)
    response = read_tailored_resume(path) if path is not None else None
    if response is None:
        return None
    job, day = folder_job_ref(response, home_root=home_root)
    # The index's key is the job's key from now on (a later pick computes the same one from the store path).
    return replace(job, key=str(key)), day


def pending_count(home_root: Path) -> int:
    """How many job resumes of the old folder ``migrate`` has not imported yet.  Cheap: the two indexes and
    ``is_file`` per name, no file's bytes (``scout status`` and ``jobs-folder`` print one line from it)."""

    home_root = Path(home_root)
    old = resumes_folder.resumes_folder(home_root).path
    files = resumes_folder._load_index(home_root, old)
    if not files:
        return 0
    record = _load_record(home_root, old)
    count = 0
    for name, entry in files.items():
        if not _job_markdown(name, entry) or name in record.files or name in record.skipped:
            continue
        stored = _stored_file(home_root, entry.get("key"))
        if stored is not None and stored.is_file() and (old / name).is_file() and not (old / name).is_symlink():
            count += 1
    return count


def pending_line(count: int) -> str | None:
    """The one plain line that offers the import of ``count`` resumes (``pending_count``), or ``None`` when nothing waits."""

    if not count:
        return None
    noun = "1 resume" if count == 1 else f"{count} resumes"
    return f"{noun} in your old resumes folder can be moved: {MIGRATE_COMMAND} --dry-run"


# --- the import -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Migration:
    """What one run planned or did.  File and folder names only, never a resume's text."""

    dry_run: bool
    old: Path
    new: Path
    #: ``{source, to, folder, file, edited, folder_suffix, file_suffix}``: copied (or, in a dry run, to be copied).
    moves: tuple[dict[str, object], ...] = ()
    #: ``{source, to, reason}``: imported by an earlier run, or the job's folder already holds it.
    already: tuple[dict[str, object], ...] = ()
    #: ``{source, reason}``: no stored job.
    orphans: tuple[dict[str, object], ...] = ()
    #: ``{source, found}``: contact details in the file.
    contact: tuple[dict[str, object], ...] = ()
    #: ``{source, reason}``: not text.
    other: tuple[dict[str, object], ...] = ()

    @property
    def counts(self) -> dict[str, int]:
        return {
            "planned": len(self.moves),
            "migrated": 0 if self.dry_run else len(self.moves),
            "already": len(self.already),
            "skipped_orphan": len(self.orphans),
            "skipped_contact": len(self.contact),
            "skipped_other": len(self.other),
        }

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": RESPONSE_SCHEMA,
            "dry_run": self.dry_run,
            "from": {"path": os.fspath(self.old), "shown": _display_path(self.old)},
            "to": {"path": os.fspath(self.new), "shown": _display_path(self.new)},
            "counts": self.counts,
            "moves": list(self.moves),
            "already": list(self.already),
            "orphans": list(self.orphans),
            "contact": list(self.contact),
            "other": list(self.other),
        }

    def lines(self) -> list[str]:
        """The plain-text report."""

        old, new = _display_path(self.old), _display_path(self.new)
        lines: list[str] = []
        if self.dry_run:
            lines.append("Dry run: nothing was written.")
        count = len(self.moves)
        noun = "1 resume" if count == 1 else f"{count} resumes"
        if count:
            lines.append(f"{'Would copy' if self.dry_run else 'Copied'} {noun} from {old} into {new}:")
            for move in self.moves:
                notes = []
                if move["edited"]:
                    notes.append("you edited this file: it is copied as yours")
                if move["file_suffix"]:
                    notes.append(f"{RESUME_NAME} is taken there")
                if move["folder_suffix"]:
                    notes.append("another job has the plain folder name, so this one ends in the job's short id")
                lines.append(f"  {move['source']} -> {move['to']}" + (f" ({'; '.join(notes)})" if notes else ""))
        else:
            lines.append(f"Nothing to migrate from {old}.")
        lines.append(f"already migrated: {len(self.already)}")
        if self.orphans:
            lines.append(f"Not moved, no stored job ({len(self.orphans)}): {', '.join(str(item['source']) for item in self.orphans)}")
        if self.contact:
            lines.append(f"Not moved, contact details in the file ({len(self.contact)}): {', '.join(str(item['source']) for item in self.contact)}")
        if self.other:
            lines.append(f"Not moved, not a text file ({len(self.other)}): {', '.join(str(item['source']) for item in self.other)}")
        if self.dry_run and count:
            lines.append(f"Copy them: {MIGRATE_COMMAND}")
        elif count:
            lines.append(
                f"{old} is a legacy place for job resumes now: every file there was left as it was, and new resumes go to {new}. "
                "master.md and the PDFs stay there."
            )
        return lines


def _resume_names(directory: Path) -> dict[str, str | None]:
    """The ``resume.md`` / ``resume-<n>.md`` names taken in a job's folder, each with its digest (``None``: not a plain file)."""

    if directory.is_symlink() or not directory.is_dir():
        return {}
    return {child.name: resumes_folder._digest_of(child) for child in directory.iterdir() if _is_numbered(child.name, RESUME_NAME)}


def _run(home_root: Path, old: Path, root: Path, *, write: bool) -> tuple[Migration, bool]:
    """Plan the import and, with ``write``, do it.  One code path for both, so a dry run lists what a run does.

    Returns the report and whether anything is (or would be) written.
    """

    index = resumes_folder._load_index(home_root, old)
    record = _load_record(home_root, old)
    record_before = json.dumps(record.to_json(), sort_keys=True)
    jobs = jobs_folder._load_index(home_root, root)
    jobs_before = json.dumps(jobs, sort_keys=True)

    already: list[dict[str, object]] = []
    orphans: list[dict[str, object]] = []
    contact: list[dict[str, object]] = []
    other: list[dict[str, object]] = []
    waiting: list[tuple[str, bool, str, bytes, str, JobRef, date]] = []
    for name in sorted(index):
        entry = index[name]
        path = old / name
        if not _job_markdown(name, entry) or path.is_symlink() or not path.is_file():
            continue
        data = path.read_bytes()
        digest = digest_imported_bytes(data)
        done = record.files.get(name)
        if done is not None and done["sha256"] == digest:
            already.append({"source": name, "to": done["to"], "reason": RECORDED})
            continue
        stored = _stored_job(home_root, entry.get("key"))
        if stored is None:
            orphans.append({"source": name, "reason": NO_STORED_JOB})
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            other.append({"source": name, "reason": NOT_TEXT})
            continue
        found = detect_contact_details(text)
        if found:
            contact.append({"source": name, "found": found})
            record.skipped[name] = digest
            continue
        record.skipped.pop(name, None)
        job, day = stored
        waiting.append((job.key, digest != entry["sha256"], name, data, digest, job, day))
    if old.is_dir() and not old.is_symlink():
        # A markdown file the index does not know is not GigAI's and names no job: listed, never moved.
        for child in sorted(old.iterdir(), key=lambda item: item.name):
            name = child.name
            if name.endswith(resumes_folder.MARKDOWN) and name not in index and not _is_numbered(name, resumes_folder.MASTER_NAME) and child.is_file() and not child.is_symlink():
                orphans.append({"source": name, "reason": NOT_IN_INDEX})

    moves: list[dict[str, object]] = []
    claimed: dict[str, str] = {}  # folders this run gives a job: <company>/<role> -> job key
    placed: dict[str, dict[str, str | None]] = {}  # names this run takes in a job's folder
    own: dict[str, set[str]] = {}  # of those, GigAI's own
    try:
        # A job's files: the one GigAI wrote first (it is the job's resume.md), then the edited ones, by name.
        for _key, edited, name, data, digest, job, day in sorted(waiting, key=lambda item: item[:3]):
            entry = jobs.get(job.key) or {}
            relative = jobs_folder._choose_dir(root, job, entry.get("dir"), claimed)
            directory = root / relative
            files: dict[str, str] = dict(entry.get("files") or {}) if entry.get("dir") == relative else {}  # type: ignore[call-overload]
            taken = {**_resume_names(directory), **placed.get(relative, {})}
            mine = {item for item, sha in files.items() if _is_numbered(item, RESUME_NAME) and _untouched(directory / item, sha)} | own.get(relative, set())
            same = next((item for item in sorted(taken) if taken[item] == digest), None)
            if same is not None or (not edited and mine):
                # The job's folder holds these bytes, or the resume GigAI picked since: the older flat copy adds nothing.
                target = same if same is not None else sorted(mine)[0]
                already.append({"source": name, "to": f"{relative}/{target}", "reason": SAME_BYTES if same is not None else NEWER_RESUME})
                record.files[name] = {"sha256": digest, "to": f"{relative}/{target}"}
                continue
            chosen = next((candidate for candidate in (_numbered(RESUME_NAME, attempt) for attempt in range(1, _MAX_NAME_TRIES + 1)) if candidate not in taken), None)
            if chosen is None:
                raise JobsFolderError("folder_unwritable", "the job's folder has no free file name for this resume")
            if write:
                directory.mkdir(parents=True, exist_ok=True)
                jobs_folder._write_job_record(directory, job, day)
                atomic_write(directory / chosen, data)
                if not edited:
                    files[chosen] = digest  # GigAI's own bytes: a later pick may rewrite it
                jobs[job.key] = {"dir": relative, "files": files}
            claimed[relative] = job.key
            placed.setdefault(relative, {})[chosen] = digest
            if not edited:
                own.setdefault(relative, set()).add(chosen)
            record.files[name] = {"sha256": digest, "to": f"{relative}/{chosen}"}
            moves.append({
                "source": name, "to": f"{relative}/{chosen}", "folder": relative, "file": chosen, "edited": edited,
                "folder_suffix": relative.split("/", 1)[1] != jobs_folder.role_slug(job.role), "file_suffix": chosen != RESUME_NAME,
            })
    finally:
        changed = json.dumps(record.to_json(), sort_keys=True) != record_before or json.dumps(jobs, sort_keys=True) != jobs_before
        if write:
            # Also after a failure part way: what was copied is recorded, so the next run does not copy it twice.
            if json.dumps(jobs, sort_keys=True) != jobs_before:
                jobs_folder._save_index(home_root, root, jobs)
            if json.dumps(record.to_json(), sort_keys=True) != record_before:
                _save_record(home_root, old, record)
    report = Migration(not write, old, root, tuple(moves), tuple(already), tuple(orphans), tuple(contact), tuple(other))
    return report, changed


def migrate(home_root: Path, *, dry_run: bool = False) -> Migration:
    """Import the old folder's job resumes into the jobs folder, once; with ``dry_run`` only say what a run would do.

    A dry run writes nothing (no folder, no index, no record).  A run with nothing to copy and
    nothing new to record writes nothing either, and one with nothing to copy never creates the
    jobs folder.  Raises ``JobsFolderError``.
    """

    home_root = Path(home_root)
    old = resumes_folder.resumes_folder(home_root).path
    root = jobs_folder.jobs_folder(home_root).path
    try:
        plan, changed = _run(home_root, old, root, write=False)
        if dry_run:
            return plan
        if not changed:
            return replace(plan, dry_run=False)
        if not plan.moves:
            return _run(home_root, old, root, write=True)[0]  # only the record: what counts as migrated already
        try:
            _make_folder(root, "jobs")
        except ResumesFolderError as exc:
            raise JobsFolderError(exc.code, str(exc)) from exc
        with _folder_lock(root):
            return _run(home_root, old, root, write=True)[0]
    except OSError as exc:
        raise JobsFolderError("folder_unwritable", "the old resumes could not be copied into the jobs folder") from exc


__all__ = [
    "MIGRATE_COMMAND",
    "RECORD_SCHEMA",
    "RESPONSE_SCHEMA",
    "Migration",
    "migrate",
    "pending_count",
    "pending_line",
    "record_path",
]
