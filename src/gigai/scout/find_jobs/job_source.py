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
   0.1.11.4 S3: step 2 can also end with nothing for a posting the index
   DOES hold (no stored description, and its board gave none). Its URL is
   then the board's text, written by strangers, not the user's:
   ``resolve_job`` is told so (``trust=TRUST_STORED``) and asks the page
   only at a public ``https`` address, every redirect hop vetted. A URL the
   posting store does not hold is the user's own and is fetched as given.
4. NEVER A DOWNGRADE. When step 3 could only scrape a page (``generic``) and
   a stored assessment of this job holds the ATS text it was made on, that
   stored posting is used again, as the pipeline's steps do. A failed fetch
   is still a failure: nothing is assessed from old text because a board
   did not answer.

Every lookup here is best effort: an unbound folder, a project with no
posting store or an unreadable index is "not known here", and step 3 decides.
The index lookup is one keyed read of the posting store and one board's
files; the stored assessments are only searched in step 4.

0110-10-03 (d): ``index_job`` is the same lookup for a READ. ``GET /api/jobs``
joined a job's rank, pay, work mode and H-1B from a find-jobs run's row only,
so a job the index holds and no run acquired (every posting ``scout new``
assesses, a company-site ``gh_jid`` URL among them) read ``rank: null`` and no
pay. ``index_job`` gives that route the posting's read-model row and its
index text, by the job identity alone, whatever host the URL is on.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import sqlite3
from types import SimpleNamespace
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
    resolve: Callable[..., ResolvedJob],
) -> ResolvedJob:
    """``job`` resolved from the source Scout already holds for it; see the module docstring.

    ``open_client`` and ``resolve`` are the caller's ``job_fetch_client`` and
    ``resolve_job`` (a client is opened only when a request is needed).
    ``resolve`` is called with ``trust=TRUST_STORED`` (S3) when the posting
    store holds this URL, and with the job and the client alone otherwise.
    Raises what ``resolve_job`` raises.
    """

    from .job_input import TRUST_STORED

    if job.job_url is None:
        return resolve(job, None)  # type: ignore[arg-type] - pasted text: no request, no client
    normalized = normalize_url(job.job_url)
    indexed = index_posting(home_root, target, normalized, open_client=open_client)
    if indexed is not None:
        return indexed
    # S3: the one branch here that requests a URL the posting store holds (``index_posting``'s own request goes to the
    # board's API host). Decided before a client is opened.
    stored = index_holds(home_root, target, normalized)
    with open_client() as client:
        fetched = resolve(job, client, trust=TRUST_STORED) if stored else resolve(job, client)
    if fetched.fetch_kind != "generic":
        return fetched
    return stored_ats_posting(home_root, target, normalized) or fetched


def index_holds(home_root: Path, target: Path, job_identity: str) -> bool:
    """True when the posting store has a row for ``job_identity`` (a posting its board no longer lists included).

    One keyed read, no request, nothing created. An unbound folder, a
    project with no posting store or an unreadable one is ``False``: not
    known here.
    """

    from ...workpad import WorkpadError
    from .. import postings
    from ..pipeline.store import PipelineStoreError, pipeline_path

    home_root, target = Path(home_root), Path(target)
    try:
        if not pipeline_path(home_root, target).is_file():
            return False
        store = postings.open_store(home_root, target)
        try:
            return bool(store.postings(jobs={job_identity}, live=False))
        finally:
            store.close()
    except (PipelineStoreError, FindJobsContractError, WorkpadError, sqlite3.Error, OSError, ValueError):
        return False


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
        store = postings.open_store(home_root, target)
        try:
            rows = store.postings(jobs={job_identity}, live=False)
        finally:
            store.close()  # not left to the garbage collector: an open connection keeps the -wal and -shm files
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


@dataclass(frozen=True)
class IndexJob:
    """One job as the index and the posting read model hold it (no request, nothing written).

    ``row`` is the read model's row for the profile asked for (else the
    posting's best profile); ``rows`` every profile's; ``text`` the index's
    title, company, location, work mode, stated pay and description, ``None``
    when the board's files no longer give them; ``grid`` the Jobs grid's own
    row for the posting (``GET /api/postings``).
    """

    row: object
    rows: tuple[object, ...]
    text: object | None
    grid: dict[str, object]

    @property
    def provider(self) -> str:
        return self.row.board.partition(":")[0]  # type: ignore[attr-defined]

    @property
    def board_token(self) -> str:
        return self.row.board.partition(":")[2]  # type: ignore[attr-defined]


def index_job(home_root: Path, target: Path, job_identity: str, *, profile_id: str | None = None) -> IndexJob | None:
    """The index's posting for ``job_identity`` as a read serves it, or ``None`` when the index does not hold it.

    Read only: the stored read-model rows (no rebuild, a posting the board no
    longer lists included) and one board's files. Never raises.
    """

    from ...workpad import WorkpadError
    from .. import posting_search, postings
    from ..pipeline.store import PipelineStoreError, pipeline_path

    home_root, target = Path(home_root), Path(target)
    try:
        if not pipeline_path(home_root, target).is_file():
            return None  # no posting store yet: nothing is created for a read
        store = postings.open_store(home_root, target)
        rows = store.postings(jobs={job_identity}, live=False)
        if not rows:
            return None
        row = next((item for item in rows if item.profile_id == profile_id), rows[0])
        grid = posting_search._rows_json(home_root, target, store, [(rows, row)])[0]
        text = postings.posting_texts(home_root, [row]).get(job_identity)
    except (PipelineStoreError, FindJobsContractError, WorkpadError, sqlite3.Error, OSError, ValueError, LookupError):
        return None
    return IndexJob(row=row, rows=tuple(rows), text=text, grid=grid)


def company_index_job(home_root: Path, job_identity: str) -> dict[str, object] | None:
    """FB1: the company index's posting for ``job_identity`` as the job read serves it, or ``None`` when no board holds it.

    For a posting no profile holds (``index_job`` reads the read model only): the
    row the free search shows, by its address. Read only, never raises. The index
    keeps no description, so ``text`` is ``None`` (never an invented one); every
    field that needs an assessment or a profile is absent. ``removed_at`` is the
    board's own stamp when the posting is gone from it.
    """

    from ..scout_new import posting_dates
    from . import free_search

    try:
        if job_identity.startswith("text:"):
            return None
        found = free_search.find_posting(Path(home_root), job_identity)
    except (FindJobsContractError, sqlite3.Error, OSError, ValueError, LookupError):
        return None
    if found is None:
        return None
    entry, posting = found
    dates = posting_dates(
        SimpleNamespace(board=entry.key, published_at=posting.published_at, first_seen=posting.first_seen),  # type: ignore[arg-type]
    )
    return {
        "job_identity": job_identity, "normalized_url": job_identity, "source_url": posting.url, "url": posting.url, "fetch_kind": "company_index",
        "provider": entry.ats, "board_token": entry.slug, "first_seen": posting.first_seen, "removed_at": posting.removed_at,
        **dates,
        "title": posting.title, "company": entry.company, "location": posting.location,
        "work_mode": None, "salary": None, "text": None,
    }


__all__ = ["ATS_FETCH_KINDS", "IndexJob", "index_holds", "company_index_job", "index_job", "index_posting", "resolve_job_for_assessment", "stored_ats_posting"]
