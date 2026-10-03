"""0.1.10.7 M3a (daily-brief.md, DESIGN 10.7 and 11): ``gigai scout new`` -- "what's new?".

One call over ALL active profiles, read from the stored index through the
posting read model (``postings.py``): no board request, and no model call
unless the caller said yes.

THE FLOW

- New postings (first seen after the anchor, still listed): each posting once,
  tagged with every active profile it matches (best first) and shown for its
  best profile (``profile_id`` filters to one profile and shows its own row).
  At most 50 are listed, best score first; the counts are of all of them.
  Those not assessed yet are assessed only on a yes: without one the response
  is ``status: "ask"`` with the question (the count per profile and the
  estimate from the recorded model calls) and the grid with rank only.
  ``assess=True`` assesses them through ``run_quick_assessment`` (the
  posting's stored text, nothing fetched) and the grid then has scores;
  ``assess=False`` is the grid with rank only.
- Nothing new: ``status: "nothing_new"`` and the 10 postings that still need
  attention, ordered by score, open questions, tailoring needed.
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

from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
import re
import textwrap
import threading
import time

from ..canonical import digest_imported_bytes
from . import postings
from .data_labels import ENVELOPE_KEY, PUBLIC_UNTRUSTED, UNTRUSTED_TEXT_RULE, USER_PRIVATE, assert_not_mixed, labels_envelope
from .pipeline.busy import LiveBatch, assess_batch
from .pipeline.store import MODEL_STEPS, PipelineStore, PipelineStoreError, PostingRecord, pipeline_path
from .postings import PostingModelError, PostingText, ProfileView, split_board

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
#: New postings listed in one response, best score first; ``counts.new`` is all of them and the question counts all of them.
NEW_ROWS_LIMIT = 50
UNMET_SHOWN = 4
EVIDENCE_SHOWN = 3
DESCRIPTION_CHARS = 400
PIPELINE_COMMAND = "gigai scout new --process"

#: States that still want something from the user (a posting Scout labelled recommended is left out by the query).
_ATTENTION_STATES = ("needs_answers", "matched", "assessed", "tailored", "not_assessed")
_NOT_ASSESSED = "not_assessed"
_WAITING_STATES = frozenset({"blocked", "ready", "awaiting_approval"})
_AWAITING_APPROVAL = "awaiting_approval"

POSTINGS_LABELS = {
    "/rows/*/title": PUBLIC_UNTRUSTED,
    "/rows/*/company": PUBLIC_UNTRUSTED,
    "/rows/*/company_name": PUBLIC_UNTRUSTED,
    "/rows/*/location": PUBLIC_UNTRUSTED,
    "/rows/*/salary": PUBLIC_UNTRUSTED,
    "/rows/*/description": PUBLIC_UNTRUSTED,
    "/rows/*/unmet/*": PUBLIC_UNTRUSTED,
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
) -> dict[str, object]:
    """One posting of the grid. Posting text and what a model derived from it only: nothing of the user's."""

    score, kind = _score(row)
    unmet: list[str] = []
    questions: list[dict[str, object]] = []
    assessment: dict[str, object] | None = None
    if item is not None:
        result = item.result  # type: ignore[attr-defined]
        unmet = [entry.requirement for entry in result.matrix if entry.status.value != "met"]
        if result.structured_questions:
            questions = [{"question_id": question.question_id, "question": question.question} for question in result.structured_questions]
        else:
            questions = [{"question_id": None, "question": question} for question in result.questions]
        assessment = {
            "verdict": None if result.verdict is None else result.verdict.value,
            "met": row.reqs_met, "requirements": row.reqs_total, "percent": score if kind == "assessment" else None,
            "assessed_at": row.assessed_at,
        }
    assessed = row.state != _NOT_ASSESSED
    return {
        "job_identity": row.job,
        "normalized_url": row.job,
        "job_url": text.url if text is not None else row.job,
        "title": text.title if text is not None else None,
        "company": text.company if text is not None else None,
        "company_name": display_company_name(text.company) if text is not None else None,
        "location": text.location if text is not None else None,
        "work_mode": text.work_mode if text is not None else "unknown",
        "salary": text.salary if text is not None else None,
        "description": _excerpt(text.text) if text is not None else None,
        "first_seen": row.first_seen,
        "removed_at": row.removed_at,
        "profile_id": row.profile_id,
        "profiles": [
            {"profile_id": item_.profile_id, "match_rank": item_.match_rank, "rank_score": item_.rank_score, "state": item_.state}
            for item_ in group
        ],
        "state": row.state,
        "stale_reason": row.stale_code,
        "score": score,
        "score_kind": kind,
        "rank_score": row.rank_score,
        "assessment": assessment,
        "needs_tailoring": (bool(unmet) and not row.tailored) if assessed and item is not None else None,
        "unmet": unmet[:UNMET_SHOWN],
        "open_questions": questions,
        "label": row.label,
        "ats_score": row.ats_score,
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
    live: LiveBatch | None = None,
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
    from .find_jobs.market_acquisition import AcquireLimits
    from .quick_assess import QuickAssessError, run_quick_assessment

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
            run_quick_assessment(request, home_root=home_root, target=target, config=config, resolved_job=resolved_job)  # type: ignore[arg-type]
        except QuickAssessError as exc:
            if exc.code in FATAL_CODES:
                stop.append(exc.code)  # no model, no profile, no resume: the next call would fail the same way
            return (exc.code, None)
        return None

    # PL5: the batch is live work the pipeline's runner yields to (DESIGN 7), like an "assess all" batch.
    try:
        with marked as live:
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
            failed.append(failure)
    return {
        "requested": len(pairs), "assessed": len(pairs) - len(failed), "failed": failed, "stopped": stop[0] if stop else None,
        "fetched_on_demand": len(fetched),
    }


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


def _question(
    pairs: Sequence[tuple[str, str]], new_count: int, views: Sequence[ProfileView], since: str, *, home_root: Path, target: Path
) -> tuple[dict[str, object], str]:
    """The approval question: ids and numbers, and the sentence (it names the profile tags)."""

    from .call_metrics import KIND_ASSESS, CallMetricsError, estimate
    from .quick_assess import _default_model_target

    per_profile: dict[str, int] = {}
    for _job, profile_id in pairs:
        per_profile[profile_id] = per_profile.get(profile_id, 0) + 1
    model = _default_model_target(target).value
    try:
        found = estimate(KIND_ASSESS, model, len(pairs), home_root=home_root, target=target)
    except (CallMetricsError, PipelineStoreError):
        found = {"calls": None, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}
    calls = found["calls"] if isinstance(found["calls"], int) else len(pairs)
    by_profile = [{"profile_id": view.profile_id, "count": per_profile[view.profile_id]} for view in views if view.profile_id in per_profile]
    labels = {view.profile_id: view.label for view in views}
    named = ", ".join(f"{labels[item['profile_id']]} {item['count']}" for item in by_profile)  # type: ignore[index]
    plural = "s" if new_count != 1 else ""
    across = f" across {len(by_profile)} profiles ({named})" if len(by_profile) > 1 else (f" ({named})" if named else "")
    ask = "Assess them?" if len(pairs) == new_count else f"Assess the {len(pairs)} not assessed yet?"
    sentence = f"{new_count} new posting{plural}{across}. {ask} ~{calls} calls{_tokens(found['tokens'])}"
    question = {
        "kind": "assess_new",
        "new": new_count,
        "to_assess": len(pairs),
        "by_profile": by_profile,
        "model_target": model,
        "estimate": {
            "calls": calls, "tokens": found["tokens"], "seconds": found["seconds"], "cost": found["cost"],
            "basis_calls": found["basis_calls"],
        },
        "yes": {"cli": f"gigai scout new --yes --since {since}", "api": {"method": "POST", "path": "/api/new", "body": {"assess": True, "since": since}}},
        "no": {"cli": f"gigai scout new --no-assess --since {since}", "api": {"method": "POST", "path": "/api/new", "body": {"assess": False, "since": since}}},
        "text": sentence,
    }
    return question, sentence


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
) -> dict[str, object]:
    """What is new since the last check, as the ``scout-new:1`` response. See the module docstring.

    ``process``: first approve the pending pipeline approvals (as
    ``decided_by``) and run the waiting pipeline steps once
    (:func:`process_waiting`, reported as ``processed``); such a call never
    moves the anchor.

    ``yours`` answers the separate ``scout-new-yours:1`` response instead
    (:func:`scout_new_yours`): it never assesses and never moves the anchor.

    ``assess``: ``True`` assesses the new postings that have no assessment
    (model calls), ``False`` never does, ``None`` asks (``status: "ask"``)
    when there are any. Raises :class:`ScoutNewError` / ``PostingModelError``
    / ``PipelineStoreError``.
    """

    from ..workpad import committed_read_cache

    with committed_read_cache():
        return _scout_new(
            Path(home_root), Path(target), profile_id=profile_id, peek=peek, assess=assess, since=since, now=now, config=config,
            yours=yours, process=process and not yours, decided_by=decided_by,
        )


def scout_new_yours(
    home_root: Path, target: Path, *, profile_id: str | None = None, since: str | None = None, now: datetime | None = None
) -> dict[str, object]:
    """The user's own evidence of what matches, for the postings ``scout_new`` lists with the same filters.

    User-private only: never a posting's title, company or text (a posting is
    named by its job identity). Never assesses, never moves the anchor.
    """

    return scout_new(home_root, target, profile_id=profile_id, peek=True, assess=False, since=since, now=now, yours=True)


def _scout_new(
    home_root: Path, target: Path, *, profile_id: str | None, peek: bool, assess: bool | None, since: str | None,
    now: datetime | None, config: object | None, yours: bool, process: bool = False, decided_by: str = "operator",
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
        refreshed = postings.refresh(home_root, target, store=store, now=moment)
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

        groups = read_new()
        pairs = [(row.job, row.profile_id) for row in (_shown(group, profile_id) for group in groups.values()) if row.state == _NOT_ASSESSED]
        pairs.sort()
        new_count = len(groups)
        status = STATUS_NEW if groups else STATUS_NOTHING_NEW
        question: dict[str, object] | None = None
        sentence: str | None = None
        assessed: dict[str, object] | None = None
        if pairs and assess is True:
            texts = postings.posting_texts(home_root, [group[0] for group in groups.values()])
            assessed = _assess(pairs, texts, home_root=home_root, target=target, config=config)
            postings.refresh(home_root, target, store=store, now=moment)
            groups = read_new()
        elif pairs and assess is None:
            status = STATUS_ASK
            question, sentence = _question(pairs, new_count, views, since_at, home_root=home_root, target=target)

        if groups:
            shown = [(group, _shown(group, profile_id)) for group in groups.values()]
            shown.sort(key=lambda pair: (-(_score(pair[1])[0] if _score(pair[1])[0] is not None else -1), pair[1].job))
            message = f"{new_count} new posting{'s' if new_count != 1 else ''} since {_when(since_at)}."
            if len(shown) > NEW_ROWS_LIMIT:
                shown = shown[:NEW_ROWS_LIMIT]
                message += f" Showing the {NEW_ROWS_LIMIT} with the best score."
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

        for group, row in shown:
            item = None if row.state == _NOT_ASSESSED else read_quick_assessment(home_root, target, row.profile_id, row.job)
            rows_json.append(_row_json(group, row, texts.get(row.job), item))
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
                "to_assess": len(pairs) if assessed is None else len(assessed["failed"]),  # type: ignore[arg-type]
                "shown": len(rows_json),
                "by_profile": [{"profile_id": view.profile_id, "new": per_profile.get(view.profile_id, 0)} for view in views],
            },
            "message": message,
            "question": question,
            "assessed": assessed,
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
        lines.append(f"Assessed {assessed['assessed']} of {assessed['requested']}." + (f" Fetched {assessed['fetched_on_demand']} missing description(s) first." if assessed.get("fetched_on_demand") else ""))
        for item in assessed["failed"]:  # type: ignore[union-attr]
            lines.append(f"  not assessed ({item['error_code']}{': ' + str(item['reason']) if item.get('reason') else ''}): {item['job_identity']}")
    question = response.get("question")
    if isinstance(question, Mapping):
        lines.append(str(question["text"]))
        lines.append(f"  Yes: {question['yes']['cli']}")  # type: ignore[index]
        lines.append("  Below: ranked, not assessed.")
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
            details.append(f"[{tags}]")
            if row["score"] is None:
                score = ["not ranked yet"]
            elif row["score_kind"] == "assessment":
                score = [f"{row['score']}% of requirements met ({row['state'].replace('_', ' ')})"]
            else:
                score = [f"rank {row['score']} (not assessed)"]
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
    "ScoutNewError",
    "check_response",
    "mark_all_seen",
    "process_waiting",
    "render",
    "response_labels",
    "scout_new",
    "scout_new_yours",
]
