"""0.1.10.7 M3a (daily-brief.md, DESIGN 10.7 and 11): ``gigai scout new`` -- "what's new?".

One call over ALL active profiles, read from the stored index through the
posting read model (``postings.py``): no board request, and no model call
unless the caller said yes.

THE FLOW

- New postings (first seen after the anchor, still listed): each posting once,
  tagged with every active profile it matches (best first) and shown for its
  best profile (``profile_id`` filters to one profile and shows its own row).
  At most 50 are listed, in the grid's order; the counts are of all of them.
  Those NO profile has assessed are assessed only on a yes: without one the
  response is ``status: "ask"`` with the question (the count per profile and
  the estimate from the recorded model calls) and the grid with rank only.
  ``assess=True`` assesses them through ``run_quick_assessment`` (the
  posting's stored text, nothing fetched) and the grid then has verdicts;
  ``assess=False`` is the grid with rank only.
- Nothing new: ``status: "nothing_new"`` and the 10 postings that still need
  attention, in the grid's order.
- THE GRID'S ORDER (0110-8-04, 0110-10-02, :func:`order_key`): a current
  assessment, then a stale one, then none; inside a group the verdict
  (matched, needs answers, other, weak fit, not a match), then the FIT number
  (``fit.py``: the share of requirements met, the must-haves weighted), the
  rank score, the newest. A not-assessed posting has no fit number and is
  ordered by its rank: a percentage is never compared with a rank score.
- WEAK FIT (0110-10-02, ``fit.py``): a needs-answers posting with few
  requirements met AND a low rank has the state ``weak_fit``. It is left out
  of the rows listed here (``counts.weak_fit`` says how many, the message how
  to list them), it is never one of the postings that "still need attention",
  and its row asks no question (``open_questions`` is empty).
- THE ASSESS THRESHOLD (0110-10-02): a yes assesses only the postings whose
  rank score is at least ``fit.assess_min_rank`` (50; a posting not ranked
  yet is assessed). The ones below are counted (``counts.low_rank_skipped``)
  and offered as their OWN question, ``low_rank_question`` ("112 low-ranked
  ones are skipped; assess those too? ~112 calls"), never answered by a plain
  yes: ``include_low_rank=True`` (``--include-low-rank``) beside a yes
  assesses them too. The same holds for ``reassess_stale``.
- OLD ASSESSMENTS (0110-8-08): the live postings that have only a stale
  assessment (made by an old run, an older prompt, other settings) are their
  OWN question, ``stale_question``, with the count and the estimate, next to
  the assess-new one. ``assess=True`` never answers it: ``reassess_stale=True``
  (``--reassess-stale``) does. It is not bound to the "new" window: an old
  assessment is old whenever the posting was first seen.
- ``counts.to_assess`` (0110-8-01) is the new postings no matching profile has
  assessed; ``counts.only_stale`` the live postings with only an old
  assessment. Neither depends on which profile a posting is shown under, so
  neither moves while the background rank fills in scores; ``ranking`` says
  how far that rank is, per profile.
- PROGRESS (0110-8-14): ``progress`` (the CLI: stderr) gets one line when a
  batch starts and lines as it goes ("assessed 120 of 333 · ~25 min left"),
  the time left from the recorded average per call until the batch has a
  pace of its own.
- Waiting pipeline work is offered with its command (``pipeline``: the jobs
  that wait, how many of them need an approval first, the model calls they
  would make). Nothing is started by a plain call. ``process=True``
  (``gigai scout new --process``) is the yes to that offer: the pending
  approvals are approved and the waiting steps run once on this thread
  (``pipeline.runner.run_once``: the pipeline's own switch, daily cap and
  yield rules apply), before the response is built. It never moves the anchor.
- While it assesses on a yes, the batch is marked live (``pipeline.busy``), so
  the pipeline's runner claims nothing until it is done.

THE ANCHOR (DESIGN 11): one per install, the time of the last plain call.
"New" is ``first_seen > anchor``; before the first call it is the last 7
days. A plain call moves the anchor to the time it read the postings, AFTER
the response is built and checked: a call that fails leaves it where it was.
``peek``, a ``profile_id`` call and the ``yours`` call never move it. ``since``
selects a window again (the yes, or the ``yours`` call, after a call that
already moved the anchor). ``mark_all_seen`` moves the same anchor.

LABELS (data_labels, P4): NO RESPONSE MIXES. The response holds posting text
and what a model derived from it (the ``postings`` envelope:
``public-untrusted``) next to ids, counts, codes, timestamps and the profile
tags, and nothing the user wrote: no resume, answer, story or note text. The
user's own evidence of what matches (resume lines, answers, stories: column 2
of the grid) is a SEPARATE call, ``scout_new_yours`` (``user-private`` only,
never posting text); ``yours_hint`` in the response says how to make it. Open
questions are the questions as asked, never an answer. No contact data: the
stores hold none (0110-046) and every string passes the outbound check on its
way out.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import textwrap
import threading
import time

from ..canonical import digest_imported_bytes
from . import fit as fit_rules
from . import postings
from .assess_causes import cause_fields, failure_lines
from .data_labels import ENVELOPE_KEY, PUBLIC_UNTRUSTED, UNTRUSTED_TEXT_RULE, USER_PRIVATE, assert_not_mixed, labels_envelope
from .pipeline.busy import LiveBatch, assess_batch
from .pipeline.store import MODEL_STEPS, PipelineStore, PipelineStoreError, PostingRecord, pipeline_path
from .postings import PostingModelError, PostingModelPreparing, PostingText, ProfileView, split_board
from .requirement_weights import minor_gap_text, minor_gaps

SCHEMA_VERSION = "scout-new:1"
SEEN_SCHEMA_VERSION = "scout-new-seen:1"
YOURS_SCHEMA_VERSION = "scout-new-yours:1"

STATUS_ASK = "ask"
STATUS_NEW = "new"
STATUS_NOTHING_NEW = "nothing_new"

SINCE_ANCHOR = "anchor"
SINCE_FIRST_USE = "first_use_7_days"
SINCE_GIVEN = "since"

FIRST_USE_DAYS = 7
ATTENTION_LIMIT = 10
#: New postings listed in one response, in the grid's order; ``counts.new`` is all of them and the question counts all of them.
NEW_ROWS_LIMIT = 50
UNMET_SHOWN = 4
EVIDENCE_SHOWN = 3
DESCRIPTION_CHARS = 400
PIPELINE_COMMAND = "gigai scout new --process"
WEAK_FIT_COMMAND = "gigai scout jobs list --state weak_fit"

#: States that still want something from the user (a posting Scout labelled recommended is left out by the query).
_ATTENTION_STATES = ("needs_answers", "matched", "assessed", "tailored", "not_assessed")
_NOT_ASSESSED = "not_assessed"
_WAITING_STATES = frozenset({"blocked", "ready", "awaiting_approval"})
_AWAITING_APPROVAL = "awaiting_approval"

POSTINGS_LABELS = {
    "/rows/*/title": PUBLIC_UNTRUSTED,
    "/rows/*/company": PUBLIC_UNTRUSTED,
    "/rows/*/company_slug": PUBLIC_UNTRUSTED,
    "/rows/*/company_name": PUBLIC_UNTRUSTED,
    "/rows/*/location": PUBLIC_UNTRUSTED,
    "/rows/*/salary": PUBLIC_UNTRUSTED,
    "/rows/*/description": PUBLIC_UNTRUSTED,
    "/rows/*/unmet/*": PUBLIC_UNTRUSTED,
    "/rows/*/minor_gaps/*": PUBLIC_UNTRUSTED,
    "/rows/*/minor_gap_text": PUBLIC_UNTRUSTED,
    "/rows/*/open_questions/*/question": PUBLIC_UNTRUSTED,
}
YOURS_LABELS = {"/evidence/*/lines/*": USER_PRIVATE}
YOURS_NOTE = "What matches, in your own words (resume lines, answers, stories), is a separate call: it is never sent next to posting text."


_SLUG_LIKE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def display_company_name(company: str | None) -> str | None:
    """A board slug ("tallgrass-health") as a name ("Tallgrass Health"); a real name passes through.

    The same rule as the UI's ``displayCompanyName`` (``ui/src/display.js``): a slug has no spaces and no capitals.
    """

    if not company or not _SLUG_LIKE.match(company):
        return company
    return " ".join(part[:1].upper() + part[1:] for part in company.split("-") if part)


class ScoutNewError(ValueError):
    """A ``scout new`` call that cannot be answered; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- reading --------------------------------------------------------------------------------


def _score(row: PostingRecord) -> tuple[int | None, str | None]:
    """What a row is ordered by: the assessment's share of requirements met, else the rank score."""

    if row.reqs_total:
        return round(100 * (row.reqs_met or 0) / row.reqs_total), "assessment"
    if row.rank_score is not None:
        return row.rank_score, "rank"
    return None, None


GROUP_CURRENT = "current"
GROUP_STALE = "stale"
GROUP_NOT_ASSESSED = "not_assessed"
_GROUP_ORDER = {GROUP_CURRENT: 0, GROUP_STALE: 1, GROUP_NOT_ASSESSED: 2}
#: Inside the assessed groups: matched, needs answers, anything else, weak fit, not a match.
_VERDICT_ORDER = {"matched": 0, "needs_answers": 1, fit_rules.WEAK_FIT: 3, "not_a_match": 4}
_VERDICT_WORDS = {
    "matched": "Matched", "needs_answers": "Needs your answers", fit_rules.WEAK_FIT: "Weak fit", "not_a_match": "Not a match",
}
_STALE_WORDS = {
    "posting_changed": "posting changed",
    "older_prompt": "older prompt",
    "settings_changed": "settings changed",
    "story_bank_changed": "answers changed",
    "resume_changed": "resume changed",
}
_RECOMMENDED = "recommended"
#: 0110-10-14: what a board means by the date stored as ``published_at`` (``ats_board_clients``). Lever's ``createdAt``
#: and Ashby's ``publishedAt`` are the day the posting went up; Greenhouse's list gives ``updated_at``, the posting's
#: last change. Served beside the date (``published_kind``) so an update is never shown as "posted".
PUBLISHED_KINDS = {"lever": "posted", "ashby": "posted", "greenhouse": "updated"}


def sort_group(row: PostingRecord) -> str:
    """``current`` (an assessment made on what the profile has now), ``stale`` (an old one) or ``not_assessed``."""

    if row.state == _NOT_ASSESSED:
        return GROUP_NOT_ASSESSED
    return GROUP_STALE if row.stale_code is not None else GROUP_CURRENT


def fit_of(row: PostingRecord) -> int | None:
    """The row's ONE fit number (``fit.py``), 0 to 100; ``None`` for a posting that is not assessed."""

    if row.state == _NOT_ASSESSED:
        return None
    # A row stored before the fit number existed has none until its facts are read again: the plain share meanwhile.
    return row.fit if row.fit is not None else fit_rules.plain_percent(row.reqs_met, row.reqs_total)


def order_key(row: PostingRecord) -> tuple[int, int, int, int, int]:
    """What the grid is ordered by before recency (0110-8-04, 0110-10-02); ``pipeline.store._POSTING_ORDER`` is the same key in SQL.

    The freshness group, the Scout label ``recommended`` first inside it, the
    verdict, then the real fit: the fit number (the share of requirements
    met, must-haves weighted), then the rank score. A not-assessed row has
    no fit number, so its group is ordered by rank.
    """

    group = sort_group(row)
    verdict = 0 if group == GROUP_NOT_ASSESSED else _VERDICT_ORDER.get(row.state, 2)
    found = fit_of(row)
    return (
        _GROUP_ORDER[group], 0 if row.label == _RECOMMENDED else 1, verdict,
        -(found if found is not None else -1), -(row.rank_score if row.rank_score is not None else -1),
    )


def in_order(shown: Iterable[tuple[Sequence[PostingRecord], PostingRecord]]) -> list[tuple[Sequence[PostingRecord], PostingRecord]]:
    """``(tags, row)`` pairs in the grid's order: :func:`order_key`, then the newest first, then the URL."""

    ordered = sorted(shown, key=lambda pair: pair[1].job)
    ordered.sort(key=lambda pair: pair[1].first_seen, reverse=True)  # stable: equal stamps keep the URL order
    ordered.sort(key=lambda pair: order_key(pair[1]))
    return ordered


def stale_label(row: PostingRecord) -> str | None:
    """"old assessment: older prompt" for a stale row; ``None`` for a current or a not-assessed one."""

    if row.state == _NOT_ASSESSED or row.stale_code is None:
        return None
    return f"old assessment: {_STALE_WORDS.get(row.stale_code, row.stale_code.replace('_', ' '))}"


def posting_dates(row: PostingRecord) -> dict[str, object]:
    """A posting's two dates, each under its own name (0110-10-14).

    ``published_at`` is the BOARD's date, the one the 7 / 30 days window judges (``None`` when the board gives none),
    and ``published_kind`` what the board means by it (:data:`PUBLISHED_KINDS`; a board kind this table does not know
    is ``updated``, the weaker claim). ``first_seen_at`` is when Scout first stored the posting: what "new since" judges.
    """

    kind = None if row.published_at is None else PUBLISHED_KINDS.get(row.board.partition(":")[0], "updated")
    return {"published_at": row.published_at, "published_kind": kind, "first_seen_at": row.first_seen}


def posted_text(row: Mapping[str, object]) -> str:
    """A served row's date as a line of the terminal says it: "posted 2026-09-24", "updated 2026-09-24" (the board's last
    change) or, when the board gives no date, "first seen 2026-10-01" (by Scout). ``""`` for a row with no date at all."""

    published, seen = row.get("published_at"), row.get("first_seen_at") or row.get("first_seen")
    if isinstance(published, str) and published:
        return f"{'updated' if row.get('published_kind') == 'updated' else 'posted'} {published[:10]}"
    return f"first seen {seen[:10]}" if isinstance(seen, str) and seen else ""


def score_text(row: PostingRecord) -> str:
    """The score column: the verdict word first, the fit number, "N of M requirements", the rank.

    Never a bare percent (0110-8-04): the one fit number (0110-10-02) is named ("fit 85%") and sits beside its "N of M".
    """

    rank = f"rank {row.rank_score}" if row.rank_score is not None else "not ranked yet"
    if row.state == _NOT_ASSESSED:
        parts = [rank, "not assessed"]
    else:
        verdict = _VERDICT_WORDS.get(row.state, "Assessed")
        old = stale_label(row)
        parts = [f"{verdict} ({old})" if old else verdict]
        found = fit_of(row)
        if found is not None:
            parts.append(f"fit {found}%")
        if row.reqs_total:
            parts.append(f"{row.reqs_met or 0} of {row.reqs_total} requirements")
        parts.append(rank)
    if row.tailored:
        parts.append("resume tailored")
    return " · ".join(parts)


def _grouped(rows: Iterable[PostingRecord]) -> dict[str, list[PostingRecord]]:
    groups: dict[str, list[PostingRecord]] = {}
    for row in rows:
        groups.setdefault(row.job, []).append(row)
    for group in groups.values():
        group.sort(key=lambda item: (item.match_rank, item.profile_id))
    return groups


def _shown(group: Sequence[PostingRecord], profile_id: str | None) -> PostingRecord:
    """The row a posting is shown by: the filter profile's, else its best profile's."""

    if profile_id is not None:
        return next((row for row in group if row.profile_id == profile_id), group[0])
    return group[0]


def _applied(resolved: object) -> frozenset[str]:
    """Jobs an application event already put past "needs attention" (applied and beyond)."""

    from .find_jobs.job_state import current_application_event, read_application_events

    try:
        events = read_application_events(resolved)
    except Exception:  # noqa: BLE001 - a display read: events that cannot be read hide nothing
        return frozenset()
    return frozenset(job for job, group in events.items() if current_application_event(group) is not None)


def _excerpt(text: str | None) -> str | None:
    if not text:
        return None
    flat = " ".join(text.split())
    return flat if len(flat) <= DESCRIPTION_CHARS else flat[: DESCRIPTION_CHARS - 1].rstrip() + "…"


def _row_json(
    group: Sequence[PostingRecord],
    row: PostingRecord,
    text: PostingText | None,
    item: object | None,
    tag_pending: bool = False,
) -> dict[str, object]:
    """One posting of the grid. Posting text and what a model derived from it only: nothing of the user's."""

    score, kind = _score(row)
    unmet: list[str] = []
    gaps: list[str] = []
    questions: list[dict[str, object]] = []
    assessment: dict[str, object] | None = None
    if item is not None:
        result = item.result  # type: ignore[attr-defined]
        unmet = [entry.requirement for entry in result.matrix if entry.status.value != "met"]
        gaps = minor_gaps(result.matrix)  # 0110-10-03: a bonus or one-of-a-list row that is not met never blocks a match
        if result.structured_questions:
            questions = [{"question_id": question.question_id, "question": question.question} for question in result.structured_questions]
        else:
            questions = [{"question_id": None, "question": question} for question in result.questions]
        assessment = {
            "verdict": None if result.verdict is None else result.verdict.value,
            "met": row.reqs_met, "requirements": row.reqs_total, "percent": score if kind == "assessment" else None,
            "assessed_at": row.assessed_at,
        }
    if row.state == fit_rules.WEAK_FIT:
        questions = []  # 0110-10-02: a weak fit asks no question
    assessed = row.state != _NOT_ASSESSED
    if assessed and item is None:
        # An old run's assessment (0110-8-08): its counts are in the row, its detail is in the run, not in the quick store.
        assessment = {
            "verdict": None, "met": row.reqs_met, "requirements": row.reqs_total,
            "percent": score if kind == "assessment" else None, "assessed_at": row.assessed_at,
        }
    return {
        "job_identity": row.job,
        "normalized_url": row.job,
        "job_url": text.url if text is not None else row.job,
        "title": text.title if text is not None else None,
        # 0110-10-03: ``company`` is the NAME; the board token is ``company_slug`` (``company_name`` stays, the same name).
        "company": display_company_name(text.company_name or text.company) if text is not None else None,
        "company_slug": text.company if text is not None else None,
        # 0110-8-11: the index's name for the board when it has one ("Garner Health"), else the slug rule.
        "company_name": display_company_name(text.company_name or text.company) if text is not None else None,
        "location": text.location if text is not None else None,
        "work_mode": text.work_mode if text is not None else "unknown",
        "salary": text.salary if text is not None else None,
        "description": _excerpt(text.text) if text is not None else None,
        "first_seen": row.first_seen,
        **posting_dates(row),  # 0110-10-14: published_at (the board's date), published_kind, first_seen_at
        "removed_at": row.removed_at,
        "profile_id": row.profile_id,
        "profiles": [
            {"profile_id": item_.profile_id, "match_rank": item_.match_rank, "rank_score": item_.rank_score, "state": item_.state}
            for item_ in group
        ],
        "state": row.state,
        "tailored": row.tailored,
        "stale_reason": row.stale_code,
        "stale_label": stale_label(row),
        "sort_group": sort_group(row),
        "score": score,
        "score_kind": kind,
        "score_text": score_text(row),
        # 0110-10-02: the one fit number of the row (must-haves weighted); null when not assessed.
        "fit": fit_of(row),
        "rank_score": row.rank_score,
        "assessment": assessment,
        "assessment_detail": (item is not None) if assessed else None,
        "needs_tailoring": (bool(unmet) and not row.tailored) if assessed and item is not None else None,
        "unmet": unmet[:UNMET_SHOWN],
        # 0110-10-03: "1 minor gap: Helm" (every gap is in ``minor_gaps``), and the rows past the matrix bound ("+N not shown").
        "minor_gaps": gaps,
        "minor_gap_text": minor_gap_text(gaps),
        "rows_not_shown": item.result.rows_not_shown if item is not None else 0,  # type: ignore[attr-defined]
        "open_questions": questions,
        "label": row.label,
        "ats_score": row.ats_score,
        # 0110-8-05: matched by a generic title's words alone; the posting's function tag is not known yet.
        "tag_pending": tag_pending,
    }


def _evidence(row: PostingRecord, item: object | None) -> dict[str, object] | None:
    """The user's own evidence for what matches (resume lines, answers, stories): the ``yours`` call's."""

    if item is None:
        return None
    lines: list[str] = []
    for entry in item.result.matrix:  # type: ignore[attr-defined]
        if entry.status.value == "met":
            lines.extend(entry.resume_evidence)
    if not lines:
        return None
    return {"job_identity": row.job, "profile_id": row.profile_id, "lines": lines[:EVIDENCE_SHOWN]}


# --- assessing on a yes ---------------------------------------------------------------------


def _assess(
    pairs: Sequence[tuple[str, str]], texts: Mapping[str, PostingText], *, home_root: Path, target: Path, config: object | None,
    live: LiveBatch | None = None, progress: "BatchProgress | None" = None,
) -> dict[str, object]:
    """Assess each ``(job, profile)`` through the job page's path, from the stored posting text.

    0110-8-02: a posting with NO stored description is fetched on demand, ONE request for that posting alone
    (``job_input.fetch_missing_description``: public board API, the existing body cap, paced like the board clients), before it is
    given up on. ``fetched_on_demand`` counts the descriptions that way; a posting whose text cannot be had stays
    ``job_text_unavailable`` (or ``job_fetch_failed``) with a named ``reason``.

    ``live``: the caller already marked the batch live (``pipeline.busy``,
    "assess these") and this keeps its marker fresh; without it the batch is
    marked here for as long as it runs.
    """

    from .find_jobs import job_input
    from .find_jobs.assess_all import FATAL_CODES, assess_concurrency
    from .find_jobs.assess_contracts import ORIGIN_JOB_PAGE, AssessJobInput, AssessRequest, AssessResumeInput, ResolvedJob
    from ..workpad import committed_read_cache
    from .find_jobs.market_acquisition import AcquireLimits
    from .quick_assess import ERROR_NOT_STORED, QuickAssessError, run_quick_assessment

    marked = nullcontext(live) if live is not None else assess_batch(home_root, target)

    failed: list[dict[str, object]] = []
    stop: list[str] = []
    fetched: list[str] = []
    pace = threading.Lock()
    clients: list[object] = []
    next_request = [0.0]
    gap = AcquireLimits.from_environment().min_request_interval_seconds

    def fetch_missing(text: PostingText) -> ResolvedJob:
        """The one request; the lock keeps the batch's requests a polite interval apart and shares one client."""

        if text.board is None or text.posting_id is None:
            raise job_input.PostingTextUnavailable("job_text_unavailable", job_input.REASON_NO_TEXT, "the posting is not in the stored index")
        provider, token = split_board(text.board)
        with pace:
            if not clients:
                clients.append(job_input.job_fetch_client())
            wait = next_request[0] - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            next_request[0] = time.monotonic() + gap
            client = clients[0]
        return job_input.fetch_missing_description(  # type: ignore[arg-type]
            client, provider=provider, token=token, posting_id=text.posting_id, url=text.url
        )

    def one(pair: tuple[str, str]) -> tuple[str, str | None] | None:
        try:
            return assess_one(pair)
        finally:
            live.beat()
            if progress is not None:
                progress.done()

    def assess_one(pair: tuple[str, str]) -> tuple[str, str | None] | None:
        job, profile_id = pair
        if stop:
            return ("not_started", None)
        text = texts.get(job)
        if text is None:
            return ("job_text_unavailable", job_input.REASON_NO_TEXT)
        resolved_job = None
        if (text.text or "").strip():
            body = text.text or ""
            resolved_job = ResolvedJob(
                job_identity=job, source_url=text.url, normalized_url=job, fetch_kind="ats_board", title=text.title,
                company=text.company, location=text.location, text=body,
                text_sha256=digest_imported_bytes(body.encode("utf-8")),
            )
        else:
            try:
                resolved_job = fetch_missing(text)
            except job_input.PostingTextUnavailable as exc:
                return (exc.code, exc.reason)
            fetched.append(job)
        request = AssessRequest(
            job=AssessJobInput(job_url=text.url), resume=AssessResumeInput(profile_id=profile_id), origin=ORIGIN_JOB_PAGE
        )
        try:
            # 0.1.10.11 S4: the read scope is per thread, and this runs on the batch's own threads. The call is the job
            # page's Assess (it reads the journal and writes a file in the home, never a journal transition): 87 git
            # processes a posting without the scope, 6 with it.
            with committed_read_cache():
                stored = run_quick_assessment(request, home_root=home_root, target=target, config=config, resolved_job=resolved_job)  # type: ignore[arg-type]
        except QuickAssessError as exc:
            if exc.code in FATAL_CODES:
                stop.append(exc.code)  # no model, no profile, no resume: the next call would fail the same way
            return (exc.code, None)
        # 0110-8-09: "assessed" means a record the grid will read, under THIS job and profile; anything else is a named failure.
        if stored.job.job_identity != job or stored.resume.profile_id != profile_id or not Path(stored.stored_path or "").is_file():
            return (ERROR_NOT_STORED, None)
        return None

    # PL5: the batch is live work the pipeline's runner yields to (DESIGN 7), like an "assess all" batch.
    try:
        with marked as live:
            if progress is not None:
                progress.start()
            with ThreadPoolExecutor(max_workers=max(1, assess_concurrency()), thread_name_prefix="scout-new-assess") as pool:
                outcomes = list(pool.map(one, pairs))
    finally:
        for client in clients:
            client.close()  # type: ignore[attr-defined]
    for (job, profile_id), outcome in zip(pairs, outcomes):
        if outcome is not None:
            code, reason = outcome
            failure: dict[str, object] = {"job_identity": job, "profile_id": profile_id, "error_code": code}
            if reason is not None:
                failure["reason"] = reason
            failure.update(cause_fields(code))  # 0110-10-13: did a model call start, may it have used tokens, what next
            failed.append(failure)
    return {
        "requested": len(pairs), "assessed": len(pairs) - len(failed), "failed": failed, "stopped": stop[0] if stop else None,
        "fetched_on_demand": len(fetched),
    }


# --- progress (0110-8-14) -------------------------------------------------------------------

#: A batch of this many or fewer says every result; a longer one a line at most this often (and always the last).
PROGRESS_EVERY_ITEMS = 20
PROGRESS_EVERY_SECONDS = 10.0


def _about(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    return f"~{minutes} min" if minutes < 120 else f"~{minutes / 60:.1f} h"


class BatchProgress:
    """The progress lines of one assess batch: "assessed 120 of 333 · ~25 min left".

    ``emit`` gets each line (the CLI writes them to stderr; stdout stays the
    response). The time left comes from ``average_seconds`` (the recorded
    average of one call, ``call_metrics.estimate``) over ``concurrency``
    calls at a time, until the batch has a pace of its own; then from that
    pace. Safe to call from the batch's worker threads: the count only
    rises, and lines leave in that order.
    """

    def __init__(
        self, total: int, emit: Callable[[str], None], *, average_seconds: float | None = None, concurrency: int = 1,
        done_word: str = "assessed", doing_word: str = "assessing", clock: Callable[[], float] = time.monotonic,
        every_seconds: float = PROGRESS_EVERY_SECONDS,
    ) -> None:
        self.total, self._emit = total, emit
        self._average = average_seconds if isinstance(average_seconds, (int, float)) and average_seconds > 0 else None
        self._concurrency = max(1, concurrency)
        self._done_word, self._doing_word = done_word, doing_word
        self._clock, self._every = clock, every_seconds
        self._lock = threading.Lock()
        self._count = 0
        self._started = self._last = clock()

    def _left(self, count: int, now: float) -> float | None:
        remaining = self.total - count
        elapsed = now - self._started
        if count >= max(3, 2 * self._concurrency) and elapsed > 0:
            return elapsed / count * remaining
        if self._average is not None:
            return remaining * self._average / self._concurrency
        return None

    def start(self) -> None:
        with self._lock:
            self._started = self._last = self._clock()
            line = f"{self._doing_word} {self.total} posting{'s' if self.total != 1 else ''}, {self._concurrency} at a time"
            left = self._left(0, self._started)
            self._emit(line if left is None else f"{line} · {_about(left)}")

    def done(self) -> None:
        with self._lock:
            self._count += 1
            now = self._clock()
            last = self._count >= self.total
            if not last and self.total > PROGRESS_EVERY_ITEMS and now - self._last < self._every:
                return
            self._last = now
            line = f"{self._done_word} {self._count} of {self.total}"
            left = None if last else self._left(self._count, now)
            self._emit(line if left is None else f"{line} · {_about(left)} left")


def _ranking(store: PipelineStore, views: Sequence[ProfileView], home_root: Path, target: Path) -> dict[str, object]:
    """How far the background rank is, per active profile: ``ranked`` of ``total`` live matches."""

    from .pipeline.rank_lane import rank_status

    found = store.posting_rank_progress()
    by_profile = [
        {"profile_id": view.profile_id, "ranked": found.get(view.profile_id, (0, 0))[0], "total": found.get(view.profile_id, (0, 0))[1]}
        for view in views
    ]
    try:
        enabled = bool(rank_status(home_root, target)["enabled"])
    except (PipelineStoreError, OSError, ValueError):  # a display read: a setting that cannot be read is "not ranking"
        enabled = False
    return {
        "enabled": enabled,
        "in_progress": enabled and any(item["ranked"] < item["total"] for item in by_profile),  # type: ignore[operator]
        "by_profile": by_profile,
    }


def _ranking_line(ranking: Mapping[str, object], labels: Mapping[str, object]) -> str:
    return ", ".join(
        f"{labels.get(item['profile_id'], item['profile_id'])} {item['ranked']} of {item['total']} ranked"
        for item in ranking["by_profile"]  # type: ignore[union-attr]
    )


# --- the pipeline offer (read only) ---------------------------------------------------------


def _pipeline_offer(store: PipelineStore) -> dict[str, object] | None:
    """The jobs that wait in the pipeline, the ones among them that need an approval first, and the calls they would make."""

    waiting = [step for step in store.steps() if step.state in _WAITING_STATES]
    if not waiting:
        return None
    jobs = len({(step.profile_id, step.job) for step in waiting})
    gated = {(step.profile_id, step.job) for step in waiting if step.state == _AWAITING_APPROVAL}
    approvals = sorted({step.approval_id for step in waiting if step.state == _AWAITING_APPROVAL and step.approval_id})
    calls = sum(1 for step in waiting if step.name in MODEL_STEPS)
    need = f" ({len(gated)} need your approval)" if gated else ""
    return {
        "waiting": jobs,
        "awaiting_approval": len(gated),
        "approvals": approvals,
        "est_calls": calls,
        "command": PIPELINE_COMMAND,
        "text": f"{jobs} waiting{need}, process now? ~{calls} calls",
    }


def process_waiting(home_root: Path, target: Path, *, config: object | None = None, decided_by: str = "operator") -> dict[str, object]:
    """The yes to the pipeline offer: approve what waits for an approval, then run the waiting steps once.

    ``{approved: [{id, jobs}], drain: scout-pipeline-drain:1}``: ids, codes
    and counts. The drain is the pipeline's own (``run_once``): switched off,
    or yielding to live work, it runs nothing and says so; the daily cap of
    model calls holds, so a job over it waits for the next day.
    """

    from .pipeline.runner import run_once
    from .pipeline.triggers import approve_all

    approved = approve_all(home_root, target, decided_by=decided_by)
    drain = run_once(home_root, target, config=config)
    return {
        "approved": [{"id": item["id"], "jobs": item["decided_jobs"]} for item in approved],
        "drain": drain.to_json(),
    }


# --- the response ---------------------------------------------------------------------------


def _when(value: str) -> str:
    """An instant as a person reads it, in the local time zone: ``Tue 14:02``-like, with the date."""

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%a %d %b %H:%M")


def _tokens(value: object) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return f", ~{value / 1000:.0f}k tokens" if value >= 1000 else f", ~{int(value)} tokens"


def _estimate(count: int, *, home_root: Path, target: Path) -> tuple[str, dict[str, object]]:
    """``(model target, estimate)`` for ``count`` assess calls, from the recorded model calls (``call_metrics.estimate``)."""

    from .call_metrics import KIND_ASSESS, CallMetricsError, estimate
    from .quick_assess import _default_model_target

    model = _default_model_target(target).value
    try:
        found = estimate(KIND_ASSESS, model, count, home_root=home_root, target=target)
    except (CallMetricsError, PipelineStoreError):
        found = {"calls": None, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}
    calls = found["calls"] if isinstance(found["calls"], int) else count
    return model, {
        "calls": calls, "tokens": found["tokens"], "seconds": found["seconds"], "cost": found["cost"],
        "basis_calls": found["basis_calls"],
    }


def _by_profile(pairs: Sequence[tuple[str, str]], views: Sequence[ProfileView]) -> list[dict[str, object]]:
    per_profile: dict[str, int] = {}
    for _job, profile_id in pairs:
        per_profile[profile_id] = per_profile.get(profile_id, 0) + 1
    return [{"profile_id": view.profile_id, "count": per_profile[view.profile_id]} for view in views if view.profile_id in per_profile]


def _answers(since: str, profile_id: str | None, *, flag: str, body: Mapping[str, object]) -> dict[str, object]:
    """How a question is answered: the CLI command and the API call, for the same window (and profile)."""

    profile = f" --profile {profile_id}" if profile_id is not None else ""
    api_body = {**body, "since": since, **({"profile_id": profile_id} if profile_id is not None else {})}
    return {"cli": f"gigai scout new {flag} --since {since}{profile}", "api": {"method": "POST", "path": "/api/new", "body": api_body}}


def _question(
    pairs: Sequence[tuple[str, str]], new_count: int, views: Sequence[ProfileView], since: str, *, home_root: Path, target: Path,
    profile_id: str | None = None, low_rank: int = 0,
) -> tuple[dict[str, object], str]:
    """The approval question: ids and numbers, and the sentence (it names the profile tags).

    ``low_rank`` (0110-10-02): how many new postings the assess threshold holds back; they are their own question.
    """

    model, found = _estimate(len(pairs), home_root=home_root, target=target)
    by_profile = _by_profile(pairs, views)
    labels = {view.profile_id: view.label for view in views}
    named = ", ".join(f"{labels[item['profile_id']]} {item['count']}" for item in by_profile)  # type: ignore[index]
    plural = "s" if new_count != 1 else ""
    across = f" across {len(by_profile)} profiles ({named})" if len(by_profile) > 1 else (f" ({named})" if named else "")
    if low_rank:
        ask = f"Assess {len(pairs)} of them ({low_rank} low-ranked {'one is' if low_rank == 1 else 'ones are'} a separate question)?"
    else:
        ask = "Assess them?" if len(pairs) == new_count else f"Assess the {len(pairs)} not assessed yet?"
    sentence = f"{new_count} new posting{plural}{across}. {ask} ~{found['calls']} calls{_tokens(found['tokens'])}"
    question = {
        "kind": "assess_new",
        "new": new_count,
        "to_assess": len(pairs),
        "low_rank_skipped": low_rank,
        "by_profile": by_profile,
        "model_target": model,
        "estimate": found,
        "yes": _answers(since, profile_id, flag="--yes", body={"assess": True}),
        "no": _answers(since, profile_id, flag="--no-assess", body={"assess": False}),
        "text": sentence,
    }
    return question, sentence


def _stale_question(
    pairs: Sequence[tuple[str, str]], views: Sequence[ProfileView], since: str, *, home_root: Path, target: Path,
    profile_id: str | None = None, low: Sequence[tuple[str, str]] = (), min_rank: int = 0,
) -> dict[str, object]:
    """0110-8-08: the postings that have only an old assessment, as their own question. Never answered by the assess-new yes.

    0110-10-02: ``low`` are the ones below the assess threshold. They are left out of the yes and counted
    (``low_rank_skipped``); when ONLY they are left, the question is about them and its yes names ``--include-low-rank``.
    """

    asked = pairs or low
    model, found = _estimate(len(asked), home_root=home_root, target=target)
    have = "has" if len(asked) == 1 else "have"
    those = "that one" if len(asked) == 1 else "those"
    if pairs:
        flag, body = "--reassess-stale", {"assess": False, "reassess_stale": True}
        text = f"{len(pairs)} {have} only an old assessment; re-assess? ~{found['calls']} calls{_tokens(found['tokens'])}"
        if low:
            more = "is" if len(low) == 1 else "are"
            text += f" ({len(low)} more {more} low-ranked, rank below {min_rank}, and left out; add --include-low-rank to include them)"
    else:
        flag, body = "--reassess-stale --include-low-rank", {"assess": False, "reassess_stale": True, "include_low_rank": True}
        text = (
            f"{len(low)} low-ranked (rank below {min_rank}) {have} only an old assessment and {'is' if len(low) == 1 else 'are'} left out; "
            f"re-assess {those} too? ~{found['calls']} calls{_tokens(found['tokens'])}"
        )
    return {
        "kind": "reassess_stale",
        "to_reassess": len(pairs),
        "low_rank_skipped": len(low),
        "by_profile": _by_profile(asked, views),
        "model_target": model,
        "estimate": found,
        "yes": _answers(since, profile_id, flag=flag, body=body),
        "text": text,
    }


def _low_rank_question(
    low: Sequence[tuple[str, str]], views: Sequence[ProfileView], since: str, setting: fit_rules.FitSetting, *, home_root: Path,
    target: Path, profile_id: str | None = None,
) -> dict[str, object]:
    """0110-10-02: the new postings below the assess threshold, as their own question. Never answered by a plain yes."""

    model, found = _estimate(len(low), home_root=home_root, target=target)
    one = len(low) == 1
    return {
        "kind": "assess_low_rank",
        "skipped": len(low),
        "min_rank": setting.assess_min_rank,
        "by_profile": _by_profile(low, views),
        "model_target": model,
        "estimate": found,
        "yes": _answers(since, profile_id, flag="--yes --include-low-rank", body={"assess": True, "include_low_rank": True}),
        "text": (
            f"{len(low)} low-ranked {'one is' if one else 'ones are'} skipped (rank below {setting.assess_min_rank}); "
            f"assess {'that' if one else 'those'} too? ~{found['calls']} calls{_tokens(found['tokens'])}"
        ),
    }


def split_low_rank(
    pairs: Sequence[tuple[str, str]], ranks: Mapping[tuple[str, str], int | None], setting: fit_rules.FitSetting, *,
    include: bool = False,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """``(to assess, low-ranked and skipped)`` of ``(job, profile)`` pairs by the assess threshold; ``include`` skips none."""

    if include:
        return list(pairs), []
    keep = [pair for pair in pairs if not fit_rules.is_low_rank(ranks.get(pair), setting)]
    low = [pair for pair in pairs if fit_rules.is_low_rank(ranks.get(pair), setting)]
    return keep, low


def _current(row: PostingRecord) -> bool:
    return row.state != _NOT_ASSESSED and row.stale_code is None


def _new_pairs(groups: Mapping[str, Sequence[PostingRecord]], profile_id: str | None) -> list[tuple[str, str]]:
    """0110-8-01: the new postings NO matching profile has assessed (with ``profile_id``: that this profile has not), each for the profile it is shown under."""

    pairs = []
    for group in groups.values():
        row = _shown(group, profile_id)
        if all(item.state == _NOT_ASSESSED for item in ([row] if profile_id is not None else group)):
            pairs.append((row.job, row.profile_id))
    return sorted(pairs)


def _stale_rows(store: PipelineStore, profile_id: str | None) -> list[PostingRecord]:
    """0110-8-08: the live postings with an assessment and no CURRENT one under any matching profile, each as the row of the profile whose old assessment is shown."""

    rows = []
    for group in _grouped(store.postings(profile_id=profile_id)).values():
        if any(_current(item) for item in group):
            continue
        old = next((item for item in group if item.state != _NOT_ASSESSED), None)
        if old is not None:
            rows.append(old)
    return sorted(rows, key=lambda row: (row.job, row.profile_id))


def response_labels(response: Mapping[str, object]) -> tuple[str, ...]:
    """Every label a response declares, in any of its ``_labels``."""

    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key == ENVELOPE_KEY and isinstance(value, Mapping):
                    found.extend(str(label) for label in value.values())
                else:
                    walk(value)
        elif isinstance(node, (list, tuple)):
            for item in node:
                walk(item)

    walk(response)
    return tuple(found)


def check_response(response: Mapping[str, object]) -> None:
    """The ``scout new`` contract, checked on every response before it is used: one response never mixes (``LabelError``)."""

    assert_not_mixed(response_labels(response), what="scout new")


def scout_new(
    home_root: Path,
    target: Path,
    *,
    profile_id: str | None = None,
    peek: bool = False,
    assess: bool | None = None,
    since: str | None = None,
    now: datetime | None = None,
    config: object | None = None,
    yours: bool = False,
    process: bool = False,
    decided_by: str = "operator",
    reassess_stale: bool = False,
    progress: Callable[[str], None] | None = None,
    model_wait: float | None = None,
    build_progress: Callable[[str, int, int], None] | None = None,
    include_low_rank: bool = False,
) -> dict[str, object]:
    """What is new since the last check, as the ``scout-new:1`` response. See the module docstring.

    ``model_wait`` (0110-9-01, the server's GET): how long to wait for a build
    of the posting read model (``postings.refresh(wait=...)``); past it the
    rows are read as stored, or ``PostingModelPreparing`` is raised when there
    are none yet. ``None`` waits for the build; ``build_progress`` then gets
    how far it is.

    ``process``: first approve the pending pipeline approvals (as
    ``decided_by``) and run the waiting pipeline steps once
    (:func:`process_waiting`, reported as ``processed``); such a call never
    moves the anchor.

    ``yours`` answers the separate ``scout-new-yours:1`` response instead
    (:func:`scout_new_yours`): it never assesses and never moves the anchor.

    ``assess``: ``True`` assesses the new postings that have no assessment
    (model calls), ``False`` never does, ``None`` asks (``status: "ask"``)
    when there are any. ``reassess_stale``: the yes to the OTHER question
    (``stale_question``): assess again the live postings that have only an
    old assessment. ``assess=True`` alone never does that. Either yes leaves
    out the postings below the assess threshold (``fit.assess_min_rank``)
    unless ``include_low_rank``. ``progress`` gets
    the progress lines of a batch (and how far the background rank is).
    Raises :class:`ScoutNewError` / ``PostingModelError`` /
    ``PipelineStoreError``.
    """

    from ..workpad import committed_read_cache

    with committed_read_cache():
        return _scout_new(
            Path(home_root), Path(target), profile_id=profile_id, peek=peek, assess=assess, since=since, now=now, config=config,
            yours=yours, process=process and not yours, decided_by=decided_by, reassess_stale=reassess_stale and not yours,
            progress=progress, model_wait=model_wait, build_progress=build_progress,
            include_low_rank=include_low_rank and not yours,
        )


def scout_new_yours(
    home_root: Path, target: Path, *, profile_id: str | None = None, since: str | None = None, now: datetime | None = None,
    model_wait: float | None = None,
) -> dict[str, object]:
    """The user's own evidence of what matches, for the postings ``scout_new`` lists with the same filters.

    User-private only: never a posting's title, company or text (a posting is
    named by its job identity). Never assesses, never moves the anchor.
    """

    return scout_new(
        home_root, target, profile_id=profile_id, peek=True, assess=False, since=since, now=now, yours=True, model_wait=model_wait
    )


def _scout_new(
    home_root: Path, target: Path, *, profile_id: str | None, peek: bool, assess: bool | None, since: str | None,
    now: datetime | None, config: object | None, yours: bool, process: bool = False, decided_by: str = "operator",
    reassess_stale: bool = False, progress: Callable[[str], None] | None = None, model_wait: float | None = None,
    build_progress: Callable[[str, int, int], None] | None = None, include_low_rank: bool = False,
) -> dict[str, object]:
    # Before anything is read: what the steps store (a Scout label, a tailored resume) is in the rows below.
    processed = process_waiting(home_root, target, config=config, decided_by=decided_by) if process else None
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    checked_at = postings.stamp(moment)
    assert checked_at is not None
    given = None
    if since is not None:
        given = postings.stamp(since)
        if given is None:
            raise ScoutNewError("invalid_value", "since must be an ISO-8601 time, like the since of an earlier response")
    store = postings.open_store(home_root, target)
    try:
        refreshed = postings.refresh(home_root, target, store=store, now=moment, wait=model_wait, progress=build_progress)
        views = refreshed.profiles
        if profile_id is not None and profile_id not in {view.profile_id for view in views}:
            raise ScoutNewError("profile_not_found", "no active Scout profile has this id")
        anchor = store.anchor()
        if given is not None:
            since_at, source = given, SINCE_GIVEN
        elif anchor is not None:
            since_at, source = postings.stamp(anchor.last_checked_at) or checked_at, SINCE_ANCHOR
        else:
            since_at, source = postings.stamp(moment - timedelta(days=FIRST_USE_DAYS)) or checked_at, SINCE_FIRST_USE

        def read_new() -> dict[str, list[PostingRecord]]:
            selected = store.postings(since=since_at, profile_id=profile_id)
            if profile_id is None:
                return _grouped(selected)
            return _grouped(store.postings(jobs={row.job for row in selected}))

        setting = fit_rules.fit_setting(home_root, target)

        def to_assess() -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
            """``(new to assess, new low-ranked, stale to re-assess, stale low-ranked)`` as the stores are now (0110-10-02)."""

            ranks = {(row.job, row.profile_id): row.rank_score for group in groups.values() for row in group}
            new_keep, new_low = split_low_rank(_new_pairs(groups, profile_id), ranks, setting, include=include_low_rank)
            old = _stale_rows(store, profile_id)
            old_ranks = {(row.job, row.profile_id): row.rank_score for row in old}
            old_keep, old_low = split_low_rank(list(old_ranks), old_ranks, setting, include=include_low_rank)
            return new_keep, new_low, old_keep, old_low

        groups = read_new()
        pairs, low_pairs, stale_pairs, low_stale = to_assess()
        new_count = len(groups)
        status = STATUS_NEW if groups else STATUS_NOTHING_NEW
        question: dict[str, object] | None = None
        sentence: str | None = None
        assessed: dict[str, object] | None = None
        reassessed: dict[str, object] | None = None
        labels = {view.profile_id: view.label for view in views}

        def batch(todo: Sequence[tuple[str, str]], rows: Iterable[PostingRecord], *, done_word: str, doing_word: str) -> dict[str, object]:
            from .find_jobs.assess_all import assess_concurrency

            lines: BatchProgress | None = None
            if progress is not None:
                found = _estimate(len(todo), home_root=home_root, target=target)[1]
                seconds, calls = found["seconds"], found["calls"]
                average = seconds / calls if isinstance(seconds, (int, float)) and isinstance(calls, int) and calls else None
                lines = BatchProgress(
                    len(todo), progress, average_seconds=average, concurrency=assess_concurrency(), done_word=done_word,
                    doing_word=doing_word,
                )
            return _assess(todo, postings.posting_texts(home_root, rows), home_root=home_root, target=target, config=config, progress=lines)

        approved_new = bool(pairs) and assess is True
        approved_stale = bool(stale_pairs) and reassess_stale
        if progress is not None and (approved_new or approved_stale):
            # A long batch is never silent, and a moving count is explained: how far the background rank is.
            progress(f"ranking: {_ranking_line(_ranking(store, views, home_root, target), labels)}")
        if approved_new:
            assessed = batch(pairs, [group[0] for group in groups.values()], done_word="assessed", doing_word="assessing")
        elif pairs and assess is None:
            status = STATUS_ASK
        if approved_stale:
            # 0110-8-08: only on its own yes. Each posting for the profile whose old assessment it has.
            reassessed = batch(
                stale_pairs, store.postings(jobs={job for job, _owner in stale_pairs}), done_word="re-assessed", doing_word="re-assessing"
            )
        if assessed is not None or reassessed is not None:
            postings.refresh(home_root, target, store=store, now=moment)
            groups = read_new()
            pairs, low_pairs, stale_pairs, low_stale = to_assess()  # what is still not assessed: the failures
        if status == STATUS_ASK:
            question, sentence = _question(
                pairs, new_count, views, since_at, home_root=home_root, target=target, profile_id=profile_id, low_rank=len(low_pairs),
            )
        stale_question = (
            _stale_question(
                stale_pairs, views, since_at, home_root=home_root, target=target, profile_id=profile_id, low=low_stale,
                min_rank=setting.assess_min_rank,
            )
            if stale_pairs or low_stale else None
        )
        low_rank_question = (
            _low_rank_question(low_pairs, views, since_at, setting, home_root=home_root, target=target, profile_id=profile_id)
            if low_pairs else None
        )

        weak_fit = 0
        if groups:
            shown = in_order((group, _shown(group, profile_id)) for group in groups.values())
            message = f"{new_count} new posting{'s' if new_count != 1 else ''} since {_when(since_at)}."
            # 0110-10-02: a weak fit is not listed; the count and how to list them are said instead.
            weak_fit = sum(1 for _group, row in shown if row.state == fit_rules.WEAK_FIT)
            shown = [(group, row) for group, row in shown if row.state != fit_rules.WEAK_FIT]
            if len(shown) > NEW_ROWS_LIMIT:
                shown = shown[:NEW_ROWS_LIMIT]
                message += f" Showing the first {NEW_ROWS_LIMIT}: assessed ones first, by fit, then by rank."
            if weak_fit:
                message += (
                    f" {weak_fit} weak fit{'s are' if weak_fit != 1 else ' is'} not listed (few requirements met and a low rank):"
                    f" {WEAK_FIT_COMMAND}"
                )
        else:
            applied = _applied(refreshed.resolved)
            best = [
                row for row in store.postings_by_score(states=_ATTENTION_STATES, profile_id=profile_id, limit=ATTENTION_LIMIT * 3)
                if row.job not in applied
            ][:ATTENTION_LIMIT]
            tags = _grouped(store.postings(jobs={row.job for row in best}))
            shown = [(tags.get(row.job, [row]), row) for row in best]
            if source == SINCE_FIRST_USE:
                message = f"Nothing new in the last {FIRST_USE_DAYS} days."
            else:
                message = f"Nothing new since your last check ({_when(since_at)})."
            if shown:
                message += f" Top {len(shown)} that still need your attention:"

        texts = postings.posting_texts(home_root, [row for _group, row in shown])
        rows_json: list[dict[str, object]] = []
        evidence: list[dict[str, object]] = []
        from .quick_assess import read_quick_assessment

        pending = postings.TagPending(home_root, views)
        for group, row in shown:
            item = None if row.state == _NOT_ASSESSED else read_quick_assessment(home_root, target, row.profile_id, row.job)
            text = texts.get(row.job)
            rows_json.append(_row_json(group, row, text, item, pending(row.profile_id, None if text is None else text.title)))
            found = _evidence(row, item)
            if found is not None:
                evidence.append(found)

        if yours:
            answer: dict[str, object] = {
                "schema_version": YOURS_SCHEMA_VERSION,
                "status": status,
                "since": since_at,
                "since_source": source,
                "checked_at": checked_at,
                "profile_id": profile_id,
                ENVELOPE_KEY: labels_envelope(YOURS_LABELS),
                "evidence": evidence,
            }
            check_response(answer)
            return answer

        hint_since = f" --since {since_at}"  # always: the yours call then reads exactly the postings listed here
        hint_profile = f" --profile {profile_id}" if profile_id is not None else ""
        query = f"since={since_at}" + (f"&profile_id={profile_id}" if profile_id else "")
        per_profile: dict[str, int] = {}
        for group in groups.values():
            for row in group:
                per_profile[row.profile_id] = per_profile.get(row.profile_id, 0) + 1
        advances = not peek and profile_id is None and not process
        response: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "since": since_at,
            "since_source": source,
            "checked_at": checked_at,
            "peek": bool(peek),
            "profile_id": profile_id,
            "anchor": {"last_checked_at": None if anchor is None else anchor.last_checked_at, "advances": advances},
            "counts": {
                "new": new_count,
                "to_assess": len(pairs),
                "low_rank_skipped": len(low_pairs),
                "only_stale": len(stale_pairs) + len(low_stale),
                "weak_fit": weak_fit,
                "shown": len(rows_json),
                "by_profile": [{"profile_id": view.profile_id, "new": per_profile.get(view.profile_id, 0)} for view in views],
            },
            "message": message,
            "question": question,
            "stale_question": stale_question,
            "low_rank_question": low_rank_question,
            "fit": setting.to_json(),
            "assessed": assessed,
            "reassessed": reassessed,
            "ranking": _ranking(store, views, home_root, target),
            "pipeline": _pipeline_offer(store),
            "processed": processed,
            "postings": {
                ENVELOPE_KEY: labels_envelope(POSTINGS_LABELS),
                "rule": UNTRUSTED_TEXT_RULE,
                "rows": rows_json,
            },
            # The profile tags: ids and the tag each profile shows under. Each profile's resume used, by id.
            "profiles": [
                {
                    "profile_id": view.profile_id, "label": view.label, "is_default": view.is_default,
                    "resume": {"record_id": view.resume_record_id, "revision_id": view.resume_revision_id},
                }
                for view in views
            ],
            "yours_hint": {
                "note": YOURS_NOTE,
                "available": len(evidence),
                "cli": f"gigai scout new --yours{hint_since}{hint_profile}",
                "api": {"method": "GET", "path": f"/api/new/yours?{query}"},
            },
        }
        del sentence
        check_response(response)
        # DESIGN 11: only now, with the response built and checked. A call that raised above has moved nothing.
        if advances:
            store.advance_anchor(checked_at, set_by="scout_new")
        return response
    finally:
        store.close()


def mark_all_seen(home_root: Path, target: Path, *, now: datetime | None = None) -> dict[str, object]:
    """"Mark all seen": move the one anchor to now. What ``POST /api/new/seen`` answers."""

    home_root, target = Path(home_root), Path(target)
    at = postings.stamp((now or datetime.now(UTC)).astimezone(UTC))
    assert at is not None
    store = PipelineStore(pipeline_path(home_root, target))
    try:
        before = store.anchor()
        after = store.advance_anchor(at, set_by="mark_all_seen")
    finally:
        store.close()
    return {
        "schema_version": SEEN_SCHEMA_VERSION,
        "previous": None if before is None else before.last_checked_at,
        "last_checked_at": after.last_checked_at,
        "set_by": after.set_by,
    }


# --- the terminal table ---------------------------------------------------------------------

_WIDTHS = (30, 34, 30, 34)
_HEADINGS = ("Details", "Score", "Needs tailoring?", "Open questions")


def _cell(lines: Iterable[str], width: int) -> list[str]:
    wrapped: list[str] = []
    for line in lines:
        wrapped.extend(textwrap.wrap(line, width=width, subsequent_indent="  ", break_long_words=False) or [""])
    return wrapped


def _table_row(cells: Sequence[Sequence[str]]) -> list[str]:
    height = max(len(cell) for cell in cells)
    return [
        " | ".join((cell[index] if index < len(cell) else "").ljust(width) for cell, width in zip(cells, _WIDTHS)).rstrip()
        for index in range(height)
    ]


def _fetched_note(batch: Mapping[str, object]) -> str:
    """0110-8-02: said for either batch (assess new, re-assess stale) when a missing description was fetched first."""

    return f" Fetched {batch['fetched_on_demand']} missing description(s) first." if batch.get("fetched_on_demand") else ""


def _failure_code(item: Mapping[str, object]) -> str:
    return f"{item['error_code']}{': ' + str(item['reason']) if item.get('reason') else ''}"


def render(response: Mapping[str, object]) -> str:
    """The response as the terminal shows it: the message, the question, the 4-column grid, the pipeline offer."""

    if response.get("schema_version") == YOURS_SCHEMA_VERSION:
        return _render_yours(response)
    listing = response["postings"]
    assert isinstance(listing, Mapping)
    labels = {item["profile_id"]: item["label"] for item in response["profiles"]}  # type: ignore[union-attr]
    lines = [str(response["message"])]
    assessed = response.get("assessed")
    if isinstance(assessed, Mapping):
        lines.append(f"Assessed {assessed['assessed']} of {assessed['requested']}." + _fetched_note(assessed))
        for item in assessed["failed"]:  # type: ignore[union-attr]
            lines.append(f"  not assessed ({_failure_code(item)}): {item['job_identity']}")
        lines.extend(failure_lines(assessed["failed"]))  # 0110-10-13
    reassessed = response.get("reassessed")
    if isinstance(reassessed, Mapping):
        lines.append(f"Re-assessed {reassessed['assessed']} of {reassessed['requested']}." + _fetched_note(reassessed))
        for item in reassessed["failed"]:  # type: ignore[union-attr]
            lines.append(f"  not re-assessed ({_failure_code(item)}): {item['job_identity']}")
        lines.extend(failure_lines(reassessed["failed"]))
    question = response.get("question")
    if isinstance(question, Mapping):
        lines.append(str(question["text"]))
        lines.append(f"  Yes: {question['yes']['cli']}")  # type: ignore[index]
        lines.append("  Below: ranked, not assessed.")
    low = response.get("low_rank_question")
    if isinstance(low, Mapping):
        lines.append(str(low["text"]))
        lines.append(f"  Yes: {low['yes']['cli']}")  # type: ignore[index]
    stale = response.get("stale_question")
    if isinstance(stale, Mapping):
        lines.append(str(stale["text"]))
        lines.append(f"  Yes: {stale['yes']['cli']}")  # type: ignore[index]
    ranking = response.get("ranking")
    if isinstance(ranking, Mapping) and ranking.get("in_progress"):
        lines.append(f"Ranking in progress: {_ranking_line(ranking, labels)}. Scores below can still change; the counts above do not.")
    rows = listing["rows"]
    assert isinstance(rows, list)
    if rows:
        rule = "-+-".join("-" * width for width in _WIDTHS)
        lines.extend(_table_row([[heading] for heading in _HEADINGS]))
        lines.append(rule)
        for row in rows:
            tags = ", ".join(str(labels.get(item["profile_id"], item["profile_id"])) for item in row["profiles"])
            details = [f"{row['company_name'] or '?'}: {row['title'] or row['job_identity']}", str(row["work_mode"])]
            if row["salary"]:
                details.append(str(row["salary"]))
            if posted_text(row):
                details.append(posted_text(row))  # 0110-10-14
            details.append(f"[{tags}]")
            if row.get("tag_pending"):
                details.append("tag pending")  # 0110-8-05: matched by a generic title's words; its function tag is not known yet
            # One part per line (the column is narrow): the verdict, "old assessment: ...", "N of M requirements", the rank.
            score = str(row["score_text"]).split(" · ") + ([str(row["minor_gap_text"])] if row.get("minor_gap_text") and row.get("state") == "matched" else [])
            if row.get("stale_label"):
                score[0:1] = [score[0].split(" (")[0], str(row["stale_label"])]
            if row["needs_tailoring"] is None:
                tailoring = ["-"]
            elif row["needs_tailoring"]:
                tailoring = ["yes"] + [f"- {text}" for text in row["unmet"]]
            else:
                tailoring = ["no"]
            questions = [f"- {item['question']}" for item in row["open_questions"]] or ["-"]
            cells = [_cell(part, width) for part, width in zip((details, score, tailoring, questions), _WIDTHS)]
            lines.extend(_table_row(cells))
            lines.append(rule)
    hint = response.get("yours_hint")
    if isinstance(hint, Mapping) and hint.get("available"):
        lines.append(f"What matches, from your own resume and answers (a separate call): {hint['cli']}")
    processed = response.get("processed")
    if isinstance(processed, Mapping):
        lines.append(_processed_line(processed))
    pipeline = response.get("pipeline")
    if isinstance(pipeline, Mapping):
        lines.append(f"Pipeline: {pipeline['text']} Run: {pipeline['command']}")
    return "\n".join(lines)


def _processed_line(processed: Mapping[str, object]) -> str:
    """What ``--process`` did, in one line."""

    drain = processed["drain"]
    approved = processed["approved"]
    assert isinstance(drain, Mapping) and isinstance(approved, list)
    steps = drain["steps"]
    assert isinstance(steps, list)
    start = f"Approved {sum(item['jobs'] for item in approved)} waiting job(s). " if approved else ""
    if drain["state"] == "disabled":
        return f"{start}The pipeline is off ({drain['reason']}). Nothing was run."
    if drain["state"] == "yielded":
        return f"{start}The pipeline is waiting: {str(drain['reason']).replace('_', ' ')} is running. Run it again when that is done."
    if not steps:
        return f"{start}Pipeline: nothing to run."
    waiting = sum(1 for step in steps if step.get("outcome") == "waiting")
    failed = sum(1 for step in steps if step.get("error_code"))
    line = f"{start}Pipeline: ran {len(steps) - waiting} step(s), {drain['model_calls']} model call(s)."
    if failed:
        line += f" {failed} did not finish; see `gigai scout pipeline status`."
    if waiting:
        line += f" {waiting} wait for tomorrow: today's model calls for the pipeline are used up."
    return line


def _render_yours(response: Mapping[str, object]) -> str:
    evidence = response["evidence"]
    assert isinstance(evidence, list)
    if not evidence:
        return "No evidence of what matches yet: none of these postings is assessed."
    lines = ["What matches, in your own words (posting by its link):"]
    for item in evidence:
        lines.append(str(item["job_identity"]))
        lines.extend(f"  + {line}" for line in item["lines"])
    return "\n".join(lines)


__all__ = [
    "ATTENTION_LIMIT",
    "BatchProgress",
    "GROUP_CURRENT",
    "GROUP_NOT_ASSESSED",
    "GROUP_STALE",
    "NEW_ROWS_LIMIT",
    "FIRST_USE_DAYS",
    "POSTINGS_LABELS",
    "SCHEMA_VERSION",
    "SEEN_SCHEMA_VERSION",
    "STATUS_ASK",
    "STATUS_NEW",
    "STATUS_NOTHING_NEW",
    "YOURS_LABELS",
    "YOURS_SCHEMA_VERSION",
    "PostingModelError",
    "PUBLISHED_KINDS",
    "PostingModelPreparing",
    "ScoutNewError",
    "check_response",
    "fit_of",
    "in_order",
    "mark_all_seen",
    "order_key",
    "posted_text",
    "posting_dates",
    "process_waiting",
    "render",
    "response_labels",
    "score_text",
    "scout_new",
    "scout_new_yours",
    "sort_group",
    "split_low_rank",
    "stale_label",
]
