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
    "PROPOSED_RESUME_SUFFIX",
    "RECORD_SUFFIX",
    "RESUMES",
    "SUGGESTIONS",
    "is_profile_folder",
    "job_digest",
    "job_keyed",
    "job_store_dir",
    "job_store_path",
]
