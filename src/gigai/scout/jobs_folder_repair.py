"""0.1.11.10 fix C: find the job folders filed under ``company/`` because the company was lost, and move them.

The jobs folder files a job as ``<company>/<role>``; a job whose company was empty when it was stored went to
``<jobs>/company/<role>`` (the layout's folder for an unknown company, ``jobs_folder.company_slug``).  Once the company is
known, that folder is in the wrong place.  ``repair`` lists each one, where it belongs, and moves it on ``apply``.

**Where the company comes from** (the first that names one): the folder's own ``.gigai-job.json`` (``company``); the job's
stored record under ``<home>/scout`` (the resume, else the assessment, named by the record's job key); the company index's
posting for the job's address (``job_source.with_indexed_company``'s own lookup); last, a first-party careers host of that address
(``jobs.<name>.<tld>``, ``careers.<name>.<tld>``, never an ATS or job board: ``job_source.careers_host_company``).  The company is shown by the layout's
rule (``tailored_resume.folder_job_ref``: the index's display name).  A folder none of them names stays where it is and is
listed as ``unresolved``: nothing is guessed from a title.

**Dry run by default.**  Without ``apply`` nothing is written (no lock, no file).  With it, a folder is renamed whole (every
file in it, a cover letter and ``interview/`` included, goes with it), the folders index follows, and an empty ``company`` in
the folder's record is filled.  A target that exists is a ``collision``: reported, never overwritten and never merged.  A second
run finds nothing to move.  Nothing here calls a model or the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from . import jobs_folder
from .find_jobs.discovery.storage import atomic_write
from .jobs_folder import JOB_FILE, JOB_SCHEMA, JobsFolderError
from .resumes_folder import _folder_lock, _read_json

RESPONSE_SCHEMA = "scout-jobs-folder-repair:1"

#: The status of one folder.
MOVE = "move"
MOVED = "moved"
COLLISION = "collision"
UNRESOLVED = "unresolved"

#: Where a folder's company was read from.
FROM_RECORD = "folder_record"
FROM_STORED_JOB = "stored_job"
FROM_COMPANY_INDEX = "company_index"
FROM_CAREERS_HOST = "careers_host"

REPAIR_COMMAND = "gigai scout jobs-folder repair"


@dataclass(frozen=True)
class Misfiled:
    """One folder directly under ``company/``: where it is, where it belongs, and why that is (or is not) possible."""

    folder: str
    to: str | None
    company: str | None
    source: str | None
    status: str
    detail: str | None = None

    def to_json(self) -> dict[str, object]:
        return {"folder": self.folder, "to": self.to, "company": self.company, "source": self.source, "status": self.status, "detail": self.detail}


@dataclass
class RepairReport:
    folder: str
    dry_run: bool
    items: list[Misfiled] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for item in self.items if item.status == status)

    def counts(self) -> dict[str, int]:
        return {"found": len(self.items), "to_move": self.count(MOVE), "moved": self.count(MOVED), "collisions": self.count(COLLISION), "unresolved": self.count(UNRESOLVED)}

    def to_json(self) -> dict[str, object]:
        return {"schema_version": RESPONSE_SCHEMA, "folder": self.folder, "dry_run": self.dry_run, "counts": self.counts(), "folders": [item.to_json() for item in self.items]}

    def lines(self) -> list[str]:
        counts = self.counts()
        if not self.items:
            return [f"Nothing to repair: no job folder under company/ in {self.folder}."]
        out = [("Dry run (nothing moved): " if self.dry_run else "") + f"{counts['found']} job folder(s) under company/ in {self.folder}:"]
        for item in self.items:
            if item.status in (MOVE, MOVED):
                out.append(f"  {item.status}: {item.folder} -> {item.to} (company from {item.source})")
            elif item.status == COLLISION:
                out.append(f"  collision: {item.folder} -> {item.to} (not moved: {item.detail})")
            else:
                out.append(f"  unresolved: {item.folder} (not moved: {item.detail})")
        tail = f"{counts['to_move']} to move, {counts['collisions']} collision(s), {counts['unresolved']} unresolved."
        if not self.dry_run:
            tail = f"{counts['moved']} moved, {counts['collisions']} collision(s), {counts['unresolved']} unresolved."
        out.append(tail)
        if self.dry_run and counts["to_move"]:
            out.append(f"Nothing was moved. Run `{REPAIR_COMMAND} --apply` to move them.")
        return out


def _stored_company(home_root: Path, job_key: str) -> str | None:
    """The company on the job's stored resume, else its stored assessment (``<home>/scout/<job key>.json`` and its sibling store)."""

    parts = job_key.split("/")
    if len(parts) != 4 or ".." in parts or any(not part for part in parts):
        return None
    for store in (parts[1], "quick_assess"):
        raw = _read_json(Path(home_root) / "scout" / parts[0] / store / parts[2] / f"{parts[3]}.json")
        job = raw.get("job") if raw is not None else None
        company = job.get("company") if isinstance(job, dict) else None
        if isinstance(company, str) and company.strip():
            return company.strip()
    return None


def _indexed_company(home_root: Path, job_url: str) -> str | None:
    from .find_jobs import free_search

    try:
        found = free_search.find_posting(home_root, job_url)
    except (OSError, ValueError, LookupError):
        return None
    company = found[0].company if found is not None else None
    return company.strip() if isinstance(company, str) and company.strip() else None


def _company_of(home_root: Path, record: dict[str, object]) -> tuple[str | None, str | None]:
    """``(display name, where it came from)`` for a folder's record, or ``(None, None)``."""

    from .find_jobs.company_names import company_display_name
    from .find_jobs.job_source import careers_host_company

    key, url = record.get("job_key"), record.get("job_url")
    candidates = (
        (record.get("company"), FROM_RECORD),
        (_stored_company(home_root, key) if isinstance(key, str) else None, FROM_STORED_JOB),
        (_indexed_company(home_root, url) if isinstance(url, str) and url else None, FROM_COMPANY_INDEX),
        (careers_host_company(home_root, url) if isinstance(url, str) and url else None, FROM_CAREERS_HOST),
    )
    for value, source in candidates:
        if isinstance(value, str) and value.strip():
            name = company_display_name(home_root, value.strip()) or value.strip()
            if jobs_folder.company_slug(name) != jobs_folder.company_slug(""):
                return name, source
    return None, None


def _plan(home_root: Path, root: Path) -> list[tuple[Misfiled, str | None]]:
    """Every folder directly under ``company/`` that is a GigAI job folder, with the company name to write (or ``None``)."""

    unknown = root / jobs_folder.company_slug("")
    if unknown.is_symlink() or not unknown.is_dir():
        return []
    claimed: set[str] = set()
    planned: list[tuple[Misfiled, str | None]] = []
    for directory in sorted(unknown.iterdir(), key=lambda path: path.name):
        record = _read_json(directory / JOB_FILE) if directory.is_dir() and not directory.is_symlink() else None
        if record is None or record.get("schema_version") != JOB_SCHEMA:
            continue  # not a folder GigAI made: left alone, not listed
        shown = f"company/{directory.name}"
        name, source = _company_of(home_root, record)
        if name is None:
            planned.append((Misfiled(shown, None, None, None, UNRESOLVED, "no company is known for this job (not in its record, its stored job or the company index)"), None))
            continue
        relative = f"{jobs_folder.company_slug(name)}/{directory.name}"
        target = root / relative
        if relative in claimed or target.is_symlink() or target.exists():
            planned.append((Misfiled(shown, relative, name, source, COLLISION, "that folder already exists" if relative not in claimed else "another folder is moving there"), None))
            continue
        claimed.add(relative)
        planned.append((Misfiled(shown, relative, name, source, MOVE), name))
    return planned


def repair(home_root: Path, *, apply: bool = False) -> RepairReport:
    """List (and with ``apply`` move) the job folders under ``company/`` whose company is known.  Raises ``JobsFolderError``."""

    home_root = Path(home_root)
    folder = jobs_folder.jobs_folder(home_root)
    root = folder.path
    report = RepairReport(folder.shown, dry_run=not apply)
    if not apply:
        report.items = [item for item, _ in _plan(home_root, root)]
        return report
    try:
        if not root.is_dir():
            return report
        with _folder_lock(root):
            index = jobs_folder._load_index(home_root, root)  # noqa: SLF001 - the jobs folder's own index
            changed = False
            for item, name in _plan(home_root, root):
                if item.status != MOVE or item.to is None or name is None:
                    report.items.append(item)
                    continue
                old, new = root / item.folder, root / item.to
                new.parent.mkdir(parents=True, exist_ok=True)
                os.rename(old, new)
                record = _read_json(new / JOB_FILE) or {}
                if not str(record.get("company") or "").strip():
                    atomic_write(new / JOB_FILE, (json.dumps({**record, "company": name}, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))
                    os.chmod(new / JOB_FILE, 0o600)
                for entry in index.values():
                    if entry["dir"] == item.folder:
                        entry["dir"] = item.to
                        changed = True
                report.items.append(Misfiled(item.folder, item.to, item.company, item.source, MOVED))
            if changed:
                jobs_folder._save_index(home_root, root, index)  # noqa: SLF001
            try:
                (root / jobs_folder.company_slug("")).rmdir()  # only when empty: nothing else is ever removed
            except OSError:
                pass
    except OSError as exc:
        raise JobsFolderError("folder_unwritable", "a job folder could not be moved in the jobs folder") from exc
    return report


__all__ = ["COLLISION", "MOVE", "MOVED", "REPAIR_COMMAND", "RESPONSE_SCHEMA", "UNRESOLVED", "Misfiled", "RepairReport", "repair"]
