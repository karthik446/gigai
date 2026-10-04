"""0110-10-04: one posting, one source -- where an assessment (or a tailoring) of a job URL reads its posting from.

``job_input.resolve_job`` answers "what is at this URL?" by asking the
network. That is the wrong question for a posting Scout already holds: a
Greenhouse board embedded in a company's own site has postings whose URL is
the company's page (``https://www.<company>/jobs?gh_jid=<id>``). The batch
(``scout new``) assessed such a posting from the index's text
(``ats_board``); a re-assessment after an answer then fetched the URL, got
the company's page, and stored its navigation as the posting
(``fetch_kind: generic``, no location, and no join to the index posting's
rank, pay and work mode).

``resolve_job_for_assessment`` is what every URL assessment calls instead
(``quick_assess.run_quick_assessment``, ``tailored_resume.run_tailored_resume``;
the pipeline's steps pass the base assessment's own posting and never get
here). In order:

1. Pasted text: unchanged (no request).
2. THE INDEX POSTING. The posting store maps a job identity (the normalized
   URL, whatever host it is on) to its board; the text is read from the
   index and the board cache, with no request, exactly as ``scout new``
   builds it -- so an unchanged posting keeps its ``fetch_kind`` and
   ``text_sha256``, and a changed one is read as it is now. An indexed
   posting with no stored description gets the one request for that posting
   alone (``job_input.fetch_missing_description``: the board's own API).
   A posting first assessed from the board's single-job endpoint
   (``ats_single``) and since indexed reads as ``ats_board`` from then on:
   the same text and digest (both are the board's ``content``), and the kind
   the grid joins the index posting's rank, pay and work mode on.
3. The URL (``resolve_job``), for a posting the index does not hold.
4. NEVER A DOWNGRADE. When step 3 could only scrape a page (``generic``) and
   a stored assessment of this job holds the ATS text it was made on, that
   stored posting is used again, as the pipeline's steps do. A failed fetch
   is still a failure: nothing is assessed from old text because a board
   did not answer.

Every lookup here is best effort: an unbound folder, a project with no
posting store or an unreadable index is "not known here", and step 3 decides.
The index lookup is one keyed read of the posting store and one board's
files; the stored assessments are only searched in step 4.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING, Callable

from ...canonical import digest_imported_bytes
from .assess_contracts import AssessJobInput, ResolvedJob
from .contracts import FindJobsContractError, normalize_url

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx

#: ``ResolvedJob.fetch_kind`` values whose text is the board's own.
ATS_FETCH_KINDS = frozenset({"ats_single", "ats_board"})


def resolve_job_for_assessment(
    job: AssessJobInput,
    *,
    home_root: Path,
    target: Path,
    open_client: Callable[[], "httpx.Client"],
    resolve: Callable[[AssessJobInput, "httpx.Client"], ResolvedJob],
) -> ResolvedJob:
    """``job`` resolved from the source Scout already holds for it; see the module docstring.

    ``open_client`` and ``resolve`` are the caller's ``job_fetch_client`` and
    ``resolve_job`` (a client is opened only when a request is needed).
    Raises what ``resolve_job`` raises.
    """

    if job.job_url is None:
        return resolve(job, None)  # type: ignore[arg-type] - pasted text: no request, no client
    normalized = normalize_url(job.job_url)
    indexed = index_posting(home_root, target, normalized, open_client=open_client)
    if indexed is not None:
        return indexed
    with open_client() as client:
        fetched = resolve(job, client)
    if fetched.fetch_kind != "generic":
        return fetched
    return stored_ats_posting(home_root, target, normalized) or fetched


def stored_ats_posting(home_root: Path, target: Path, job_identity: str) -> ResolvedJob | None:
    """The posting a stored assessment of ``job_identity`` was made on, when that was the board's own text."""

    from ...workpad import WorkpadError
    from ..quick_assess import QuickAssessError, find_quick_assessment_by_job_identity

    try:
        item = find_quick_assessment_by_job_identity(Path(home_root), Path(target), job_identity)
    except (QuickAssessError, FindJobsContractError, WorkpadError, OSError, ValueError):
        return None
    if item is None or item.job.fetch_kind not in ATS_FETCH_KINDS or not (item.posting_text or "").strip():
        return None
    return replace(item.job, text=item.posting_text)


def index_posting(
    home_root: Path, target: Path, job_identity: str, *, open_client: Callable[[], "httpx.Client"] | None = None
) -> ResolvedJob | None:
    """The index's posting for ``job_identity`` (``ats_board``), or ``None`` when the index does not hold it.

    The same ``ResolvedJob`` ``scout new`` assesses (identity, text and
    digest), so the two never disagree about one posting. With
    ``open_client``, a posting whose description is not stored is fetched
    for that posting alone; a board that does not give it is ``None``.
    """

    from ...workpad import WorkpadError
    from .. import postings
    from ..pipeline.store import PipelineStoreError, pipeline_path
    from . import job_input

    home_root, target = Path(home_root), Path(target)
    try:
        if not pipeline_path(home_root, target).is_file():
            return None  # no posting store yet: nothing is created for a read
        rows = postings.open_store(home_root, target).postings(jobs={job_identity}, live=False)
        text = postings.posting_texts(home_root, rows[:1]).get(job_identity) if rows else None
    except (PipelineStoreError, FindJobsContractError, WorkpadError, sqlite3.Error, OSError, ValueError):
        return None
    if text is None:
        return None
    body = text.text or ""
    if body.strip():
        return ResolvedJob(
            job_identity=job_identity, source_url=text.url, normalized_url=job_identity, fetch_kind="ats_board",
            title=text.title, company=text.company, location=text.location, text=body,
            text_sha256=digest_imported_bytes(body.encode("utf-8")),
        )
    if open_client is None or text.board is None or text.posting_id is None:
        return None
    provider, token = postings.split_board(text.board)
    try:
        with open_client() as client:
            found = job_input.fetch_missing_description(
                client, provider=provider, token=token, posting_id=text.posting_id, url=text.url
            )
    except job_input.PostingTextUnavailable:
        return None
    return replace(found, job_identity=job_identity, normalized_url=job_identity)


__all__ = ["ATS_FETCH_KINDS", "index_posting", "resolve_job_for_assessment", "stored_ats_posting"]
