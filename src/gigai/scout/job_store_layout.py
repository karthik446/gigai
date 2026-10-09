"""0.1.11.9 PJ1: where a JOB's own records live -- one assessment, one resume, one suggestion record per job.

Until 0.1.11.9 these three stores were kept per profile:
``<home>/scout/<project>/<store>/<profile id>/<sha256(job identity)>.json``.  The per-job model keeps one
record per job in ONE folder of each store, ``JOB_FOLDER``::

    quick_assess/job/<sha256(job identity)>.json
    resumes/job/<sha256(job identity)>.json   (+ .md, + .layout)
    suggestions/job/<sha256(job identity)>.json

**The file name does not change**, only the folder does: it was already the digest of the job identity.
A profile's folder is left as it is (``job_store_migration`` copies, never moves), so an older GigAI opened
on the same home still finds what it wrote.

``JOB_FOLDER`` can never be a profile's folder: a profile id is ``profile_<uuid>`` and a pasted resume's
folder is ``ephemeral``.

This module only names paths.  It reads nothing and writes nothing.
"""

from __future__ import annotations

from pathlib import Path, PurePath

from ..canonical import digest_imported_bytes
from .find_jobs.discovery.storage import project_id

#: The one folder of a per-job store.
JOB_FOLDER = "job"
#: The folder of a pasted resume's records (``quick_assess.EPHEMERAL_RESUME_KEY``): no profile, not migrated.
EPHEMERAL_FOLDER = "ephemeral"

ASSESSMENTS = "quick_assess"
RESUMES = "resumes"
SUGGESTIONS = "suggestions"
#: The stores that are keyed by job from 0.1.11.9 on.
JOB_STORES = (ASSESSMENTS, RESUMES, SUGGESTIONS)

RECORD_SUFFIX = ".json"
MARKDOWN_SUFFIX = ".md"
#: The job's own PDF spacing, beside its stored resume (the PDF renderer's ``JOB_LAYOUT_SUFFIX``; not imported here).
LAYOUT_SUFFIX = ".layout"
#: ``suggestions.proposed_resume_path``: a proposed selection's resume, beside the suggestion record.
PROPOSED_RESUME_SUFFIX = ".proposed-resume.json"
#: Every file a job can have in a store, by what follows the digest in its name.
JOB_FILE_SUFFIXES = (RECORD_SUFFIX, MARKDOWN_SUFFIX, LAYOUT_SUFFIX, PROPOSED_RESUME_SUFFIX)


def job_digest(job_identity: str) -> str:
    """``sha256(job identity)`` as hex: the name of a job's file in every store."""

    return digest_imported_bytes(job_identity.encode("utf-8")).removeprefix("sha256:")


def job_store_dir(store_dir: Path) -> Path:
    """The per-job folder of the store at ``store_dir`` (``.../quick_assess`` -> ``.../quick_assess/job``)."""

    return Path(store_dir) / JOB_FOLDER


def job_store_path(store_dir: Path, job_identity: str, suffix: str = RECORD_SUFFIX) -> Path:
    """Where the store at ``store_dir`` keeps the job's file: ``<store>/job/<sha256(job identity)><suffix>``."""

    return job_store_dir(store_dir) / f"{job_digest(job_identity)}{suffix}"


#: 0.1.11.10 reservations: job-keyed stores that do not exist yet, each a NEW top-level folder of the scout
#: project (a sibling of ``quick_assess``/``resumes``/``suggestions``, not a folder of one of them). Deliberately
#: left out of ``JOB_STORES`` -- that would pull them into ``job_store_migration``'s scan/copy, which 0.1.11.9
#: PJ5 does not do. Nothing is written, created, or migrated for either -- see ``job_pathways_path``/``job_prep_path``.
PATHWAYS = "pathways"
PREP = "prep"


def job_pathways_path(home_root: Path, target: Path, job_identity: str) -> Path:
    """0.1.11.10 reservation: where a job's pathways record WILL live, ``pathways/job/<sha256(job identity)>.json``.

    Nothing is read or written here yet; this only reserves the path so 0.1.11.10 keys it the same way the other
    job stores are keyed (``job_quick_assess_path``'s digest).
    """

    return job_store_path(home_root / "scout" / project_id(home_root, target) / PATHWAYS, job_identity)


def job_prep_path(home_root: Path, target: Path, job_identity: str) -> Path:
    """0.1.11.10 reservation: where a job's interview-prep record WILL live, ``prep/job/<sha256(job identity)>.json``.

    This is a NEW job-keyed store, separate from ``interview_prep/`` (``interview_prep/storage.py``), which stays
    keyed ``<profile_id>/<sha256(posting_id)>.json`` and is not touched by this reservation; 0.1.11.10 decides
    whether/how the two relate. Nothing is read or written here yet.
    """

    return job_store_path(home_root / "scout" / project_id(home_root, target) / PREP, job_identity)


def is_profile_folder(name: str) -> bool:
    """A folder of a store that holds ONE profile's records: any but the per-job one and a pasted resume's."""

    return name not in (JOB_FOLDER, EPHEMERAL_FOLDER)


def job_keyed(path: str | PurePath) -> bool:
    """Whether ``path`` names a file of a per-job folder (``<store>/job/<name>``)."""

    parts = PurePath(path).parts
    return len(parts) >= 3 and parts[-2] == JOB_FOLDER and parts[-3] in JOB_STORES


__all__ = [
    "ASSESSMENTS",
    "EPHEMERAL_FOLDER",
    "JOB_FILE_SUFFIXES",
    "JOB_FOLDER",
    "JOB_STORES",
    "LAYOUT_SUFFIX",
    "MARKDOWN_SUFFIX",
    "PATHWAYS",
    "PREP",
    "PROPOSED_RESUME_SUFFIX",
    "RECORD_SUFFIX",
    "RESUMES",
    "SUGGESTIONS",
    "is_profile_folder",
    "job_digest",
    "job_keyed",
    "job_pathways_path",
    "job_prep_path",
    "job_store_dir",
    "job_store_path",
]
