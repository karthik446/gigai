"""0.1.11.9: a home as GigAI wrote it BEFORE the per-job stores, for tests. Synthetic only.

Until 0.1.11.9 a job's assessment, stored resume and suggestion record were kept once per role:
``<home>/scout/<project>/<store>/<role id>/<sha256(job identity)>.json``.  The stores now read and write
``<store>/job/``, so a test that needs the old layout builds it here, by path, never through a store's own
path function (those name the per-job folder).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from gigai.scout.find_jobs.discovery.storage import project_id
from gigai.scout.job_store_layout import JOB_FILE_SUFFIXES, JOB_FOLDER, JOB_STORES, job_digest


def project_dir(home: Path, target: Path) -> Path:
    return Path(home) / "scout" / project_id(Path(home), Path(target))


def role_path(home: Path, target: Path, store: str, profile_id: str | None, job: str, suffix: str = ".json") -> Path:
    """Where a role's folder (a pasted resume's for ``None``) held a job's file of ``store`` before 0.1.11.9."""

    return project_dir(home, target) / store / (profile_id or "ephemeral") / f"{job_digest(job)}{suffix}"


def job_path(home: Path, target: Path, store: str, job: str, suffix: str = ".json") -> Path:
    return project_dir(home, target) / store / JOB_FOLDER / f"{job_digest(job)}{suffix}"


def to_role_folder(home: Path, target: Path, profile_id: str, job: str) -> list[Path]:
    """Make ``job`` look as a GigAI before 0.1.11.9 left it: every file its per-job folders hold is MOVED to
    ``profile_id``'s folder, and every path inside that names a per-job file names the role's instead.

    The records are the product's own (written by a real assess / pick in the test), so every reader parses them.
    Returns the files now in the role's folders.
    """

    root = project_dir(home, target)
    moved: list[Path] = []
    for store in JOB_STORES:
        for suffix in JOB_FILE_SUFFIXES:
            source = job_path(home, target, store, job, suffix)
            if not source.is_file():
                continue
            dest = role_path(home, target, store, profile_id, job, suffix)
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = source.read_bytes()
            if suffix.endswith(".json"):
                for name in JOB_STORES:
                    data = data.replace(
                        (os.fspath(root / name / JOB_FOLDER) + os.sep).encode("utf-8"), (os.fspath(root / name / profile_id) + os.sep).encode("utf-8"),
                    )
            dest.write_bytes(data)
            source.unlink()
            moved.append(dest)
    for store in JOB_STORES:
        folder = root / store / JOB_FOLDER
        if folder.is_dir() and not any(folder.iterdir()):
            folder.rmdir()
    _forget_job_key(home, root.name, profile_id, job)
    return moved


def _forget_job_key(home: Path, project: str, profile_id: str, job: str) -> None:
    """The rest of what a GigAI before 0.1.11.9 left: no migration record, and the jobs folder (its index and the
    job folder's own record) names the job by the ROLE's key."""

    from gigai.scout import jobs_folder

    (Path(home) / "scout" / "job-stores-migration.json").unlink(missing_ok=True)
    key, old = f"{project}/resumes/{JOB_FOLDER}/{job_digest(job)}", f"{project}/resumes/{profile_id}/{job_digest(job)}"
    folder = jobs_folder.jobs_folder(Path(home)).path
    index = jobs_folder._load_index(Path(home), folder)
    if key not in index:
        return
    index[old] = index.pop(key)
    jobs_folder._save_index(Path(home), folder, index)
    record = folder / str(index[old]["dir"]) / jobs_folder.JOB_FILE
    if record.is_file():
        value = json.loads(record.read_bytes())
        if value.get("job_key") == key:
            value["job_key"] = old
            record.write_text(json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def tree(folder: Path) -> dict[str, bytes]:
    """``{relative path: bytes}`` of every file under ``folder`` (to compare a folder before and after)."""

    folder = Path(folder)
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}


def role_folders(home: Path, target: Path) -> dict[str, bytes]:
    """Every file of every folder of the three stores but the per-job one: what must never change again."""

    root = project_dir(home, target)
    found: dict[str, bytes] = {}
    for store in JOB_STORES:
        base = root / store
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if folder.is_dir() and folder.name != JOB_FOLDER:
                found.update({f"{store}/{folder.name}/{name}": data for name, data in tree(folder).items()})
    return found
