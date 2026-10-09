"""0.1.11.9: the identity a job's pipeline is keyed by.

The pipeline keeps ONE set of steps a job. The same job posted once per
country is one job (``find_jobs/canonical_job.py``), so a caller that names
any copy must end at the canonical job's identity before a step is read or
written. :func:`job_identity_for` is the ONE place this is wired: every entry
of the pipeline that takes a job from a caller goes through it
(``triggers.process_now``, ``GET /api/pipeline/job``, ``POST
/api/pipeline/process``, the ``gigai scout pipeline`` commands that take a
JOB), and it defers to ``find_jobs/job_key.py``'s ``job_key`` for the rule.
"""

from __future__ import annotations

from pathlib import Path


def job_identity_for(job_identity: str, *, home_root: Path | None = None, target: Path | None = None) -> str:
    """The identity the pipeline keys ``job_identity``'s steps by: the canonical job's.

    ``home_root`` and ``target``: where the copies of a job can be looked up, for the
    canonical rule. With either missing, ``job_identity`` is answered unchanged.
    """

    if home_root is None or target is None:
        return job_identity
    from ..find_jobs.job_key import job_key

    return job_key(home_root, target, job_identity)


__all__ = ["job_identity_for"]
