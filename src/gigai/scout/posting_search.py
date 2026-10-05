"""0.1.10.7 M4a (DESIGN 10.3 B, 10.5): the live search over the stored index, and "Assess these".

What "Run find jobs" was for, without a run.

SEARCH (:func:`search_postings`). The postings every ACTIVE profile matches,
from the stored company index through the posting read model
(``postings.py``, which matches with ``index_search.read_indexed_boards``):
no board is asked, no run is created, no model is called. Each posting once,
tagged with every active profile it matches (best first) and shown for its
best profile, or for the one profile the call filters to. Filters: profiles,
words in the title, company or location, states, a time window ("new since
the last check", the last 7 or 30 days), postings the board no longer lists.
A changed setting (titles, countries, work mode) is seen by the very next
search: the read model matches that profile again. A search never moves the
"new since" anchor.

WEAK FIT (0110-10-02, ``fit.py``). A posting whose state is ``weak_fit`` (it
waits on answers, few requirements are met and its rank is low) is LEFT OUT
of a search unless the ``weak_fit`` state is asked for (or, for "Assess
these", the posting is named). ``counts.weak_fit`` is how many the other
filters select, listed or not: the number on the page's "Weak fit" chip.
The rows are in the grid's order (``scout_new.order_key``): inside a group by
the fit number, then the rank score, then the newest.

HISTORY (``history=True``). What old find-jobs runs assessed, imported by
``run_history.py``, each with the provenance the run sealed. Rows of a run
with no profile (the ``ephemeral`` pseudo-profile) and of a profile that is
no longer active are ``hidden``: they are listed only when asked for
(``include_hidden``, or ``profile_id`` naming them), never by default.

ASSESS THESE (:func:`assess_these`). The postings named (``jobs``) or the
ones a filter selects, each for its best profile (or ``profile_id``), that
have no current assessment. Nothing is assessed without approval: a call
without ``approve`` answers ``status: "ask"`` with the count and the estimate
from the recorded model calls, and makes no model call. With ``approve`` the
batch is recorded as an approved batch (``approval``: who approved, when, how
many, the estimate), registered as live work (the ``assess_batch`` lease:
the pipeline and the rank lane start nothing while it runs) and assessed
through the job page's own path (``run_quick_assessment``, from the posting
text already stored; one with none has its description fetched first, one request for it alone). The results are in the quick-assess
store, so the read model shows them at once. 0110-10-02: a posting whose rank
score is below the assess threshold (``fit.assess_min_rank``, 50) is left out
of the batch and counted; ``low_rank`` in the response is the separate
question for those ("3 low-ranked ones are skipped; assess those too? ~3
calls"), and ``include_low_rank`` assesses them with the rest.

LABELS (data_labels, P4): like ``scout new``, no response mixes. A response
holds posting text and what a model derived from it (``public-untrusted``)
next to ids, counts, codes and the profile tags, and nothing the user wrote.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
import threading
import uuid

from . import fit as fit_rules
from . import postings, run_history
from .data_labels import ENVELOPE_KEY, UNTRUSTED_TEXT_RULE, labels_envelope
from .pipeline.busy import assess_batch
from .pipeline.store import (
    EPHEMERAL_PROFILE,
    LEASE_ASSESS_BATCH,
    RENEW_SECONDS,
    PipelineStore,
    PipelineStoreError,
    PostingRecord,
    RunAssessment,
)
from .postings import PostingModelError, PostingModelPreparing, ProfileView
from .scout_new import (
    FIRST_USE_DAYS,
    POSTINGS_LABELS,
    _assess,
    _grouped,
    _row_json,
    _score,
    _shown,
    check_response,
    in_order,
    split_low_rank,
)

SCHEMA_VERSION = "scout-postings:1"
ASSESS_SCHEMA_VERSION = "scout-postings-assess:1"

STATUS_ASK = "ask"
STATUS_ASSESSED = "assessed"
STATUS_NOTHING = "nothing_to_assess"

WINDOW_NEW = "new"
WINDOW_7_DAYS = "7d"
WINDOW_30_DAYS = "30d"
WINDOWS: Mapping[str, int | None] = {WINDOW_NEW: None, WINDOW_7_DAYS: 7, WINDOW_30_DAYS: 30}

#: The state filters. ``assessed`` is any posting with an assessment; ``recommended`` the Scout label.
STATE_ASSESSED = "assessed"
STATE_RECOMMENDED = "recommended"
_ROW_STATES = frozenset({"not_assessed", "needs_answers", "matched", "not_a_match", "tailored", fit_rules.WEAK_FIT})
STATES = frozenset(_ROW_STATES | {STATE_ASSESSED, STATE_RECOMMENDED})

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_NOT_ASSESSED = "not_assessed"


class PostingSearchError(ValueError):
    """A search or an "assess these" call that cannot be answered; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- filters --------------------------------------------------------------------------------


def _names(value: Iterable[str] | str | None, allowed: Iterable[str] | None, what: str) -> tuple[str, ...]:
    if value is None:
        return ()
    items = value.split(",") if isinstance(value, str) else list(value)
    found = tuple(dict.fromkeys(item.strip() for item in items if isinstance(item, str) and item.strip()))
    if allowed is not None:
        known = frozenset(allowed)
        unknown = [item for item in found if item not in known]
        if unknown:
            raise PostingSearchError("invalid_value", f"{what} must be of: {', '.join(sorted(known))}")
    return found


def _index_words(home_root: Path, rows: Iterable[PostingRecord]) -> dict[str, str]:
    """``job -> "title company location"`` (case-folded) from the company index alone: one index file per board, no board body."""

    from .find_jobs.company_index import CompanyIndex
    from .find_jobs.contracts import FindJobsContractError, normalize_url

    by_board: dict[str, set[str]] = {}
    for row in rows:
        by_board.setdefault(row.board, set()).add(row.job)
    index = CompanyIndex.for_home(Path(home_root))
    found: dict[str, str] = {}
    for board, jobs in by_board.items():
        entry = index.read(*postings.split_board(board))
        if entry is None:
            continue
        for posting in entry.postings.values():
            try:
                job = normalize_url(posting.url)
            except FindJobsContractError:
                continue
            if job in jobs:
                found[job] = " ".join(str(part or "") for part in (posting.title, entry.company, posting.location)).casefold()
    return found


def _in_window(row: PostingRecord, window: str | None, since: str, moment: datetime) -> bool:
    if window is None:
        return True
    if window == WINDOW_NEW:
        return row.first_seen > since
    edge = postings.stamp(moment - timedelta(days=WINDOWS[window] or 0)) or ""
    return (row.published_at or row.first_seen) >= edge  # a board that gives no date: when it was first seen


def _wanted(row: PostingRecord, states: Sequence[str]) -> bool:
    if not states:
        return True
    for state in states:
        if state == STATE_ASSESSED and row.state != _NOT_ASSESSED:
            return True
        if state == STATE_RECOMMENDED and row.label == STATE_RECOMMENDED:
            return True
        if state == "tailored" and row.tailored:
            return True  # 0110-8-12: a tailored resume is a flag beside the verdict state, not a state
        if state == row.state:
            return True
    return False


def _hidden(row: PostingRecord, states: Sequence[str], named: bool) -> bool:
    """0110-10-02: a weak fit is listed only when asked for: the ``weak_fit`` state, or the posting named."""

    return row.state == fit_rules.WEAK_FIT and not named and fit_rules.WEAK_FIT not in states


class _Selection:
    """One refreshed read of the read model with the call's filters applied: every matching posting, in order."""

    def __init__(
        self, home_root: Path, target: Path, store: PipelineStore, *, profile_ids: Sequence[str], query: str | None,
        states: Sequence[str], window: str | None, removed: bool, jobs: Sequence[str] | None, moment: datetime,
        model_wait: float | None = None,
    ) -> None:
        refreshed = postings.refresh(home_root, target, store=store, now=moment, wait=model_wait)
        #: Rows read as stored while a build runs (``postings.BUILD_STALE``), for a caller that acts on them.
        self.as_stored = postings.BUILD_STALE in refreshed.builds.values()
        self.views: tuple[ProfileView, ...] = refreshed.profiles
        self.resolved = refreshed.resolved
        active = {view.profile_id for view in self.views}
        self.hidden_profiles = tuple(item for item in profile_ids if item not in active)
        self.profile_ids = tuple(item for item in profile_ids if item in active)
        anchor = store.anchor()
        self.anchor = None if anchor is None else anchor.last_checked_at
        self.since = (
            postings.stamp(self.anchor) if self.anchor is not None else postings.stamp(moment - timedelta(days=FIRST_USE_DAYS))
        ) or ""
        rows = store.postings(jobs=jobs, live=False) if jobs is not None else store.postings(live=False)
        groups = _grouped(row for row in rows if (row.removed_at is not None) == removed)
        if self.hidden_profiles and not self.profile_ids:
            groups = {}  # only profiles with no live rows were asked for: nothing of an active profile is shown instead
        elif self.profile_ids:
            wanted = set(self.profile_ids)
            groups = {job: group for job, group in groups.items() if any(row.profile_id in wanted for row in group)}
        only = self.profile_ids[0] if len(self.profile_ids) == 1 else None
        shown = [(group, _shown(group, only)) for group in groups.values()]
        weak = fit_rules.WEAK_FIT
        shown = [
            (group, row) for group, row in shown
            if (_wanted(row, states) or row.state == weak) and _in_window(row, window, self.since, moment)
        ]
        words = [word for word in (query or "").casefold().split() if word]
        if words:
            text = _index_words(home_root, [row for _group, row in shown])
            shown = [(group, row) for group, row in shown if all(word in text.get(row.job, "") for word in words)]
        # 0110-10-02: the weak fits the OTHER filters select (the chip's number), then only the ones asked for stay.
        self.weak_fit = sum(1 for _group, row in shown if row.state == weak)
        shown = [(group, row) for group, row in shown if _wanted(row, states) and not _hidden(row, states, jobs is not None)]
        # 0110-8-04: the grid's one order (``scout_new.order_key``): current, stale, not assessed; verdict; rank.
        self.shown = shown = in_order(shown)
        self.new = sum(1 for _group, row in shown if row.first_seen > self.since and row.removed_at is None)

    def profiles_json(self) -> list[dict[str, object]]:
        counts: dict[str, int] = {}
        for group, _row in self.shown:
            for row in group:
                counts[row.profile_id] = counts.get(row.profile_id, 0) + 1
        return [
            {
                "profile_id": view.profile_id, "label": view.label, "is_default": view.is_default, "matched": counts.get(view.profile_id, 0),
                "resume": {"record_id": view.resume_record_id, "revision_id": view.resume_revision_id},
            }
            for view in self.views
        ]


def _rows_json(
    home_root: Path, target: Path, store: PipelineStore, shown: Sequence[tuple[Sequence[PostingRecord], PostingRecord]],
    views: Sequence[ProfileView] = (),
) -> list[dict[str, object]]:
    """The grid rows: ``scout new``'s own row, plus where a row's assessment came from when it was a run's."""

    from .quick_assess import read_quick_assessment

    texts = postings.posting_texts(home_root, [row for _group, row in shown])
    ran: dict[tuple[str, str], RunAssessment] = {}
    if shown and store.run_history_stamp()[0]:
        for profile_id in {row.profile_id for _group, row in shown}:
            ran.update({(item.profile_id, item.job): item for item in store.run_assessments(profile_id=profile_id, latest=True)})
    rows: list[dict[str, object]] = []
    pending = postings.TagPending(home_root, views)
    for group, row in shown:
        item = None if row.state == _NOT_ASSESSED else read_quick_assessment(home_root, target, row.profile_id, row.job)
        text = texts.get(row.job)
        entry = _row_json(group, row, text, item, pending(row.profile_id, None if text is None else text.title))
        origin: dict[str, object] | None = None
        if item is not None:
            origin = {"origin": "quick_assess"}
        old = ran.get((row.profile_id, row.job))
        if old is not None and (item is None or row.assessed_at == old.assessed_at):
            # The row's facts are an old run's: its counts and the provenance the run sealed (ids and digests).
            score, kind = _score(row)
            entry["assessment"] = {
                "verdict": None, "met": row.reqs_met, "requirements": row.reqs_total,
                "percent": score if kind == "assessment" else None, "assessed_at": row.assessed_at,
            }
            entry["assessment_detail"] = False
            origin = run_history.basis_json(old)
        entry["assessment_basis"] = origin
        rows.append(entry)
    return rows


def _history(
    store: PipelineStore, selection: _Selection, *, include_hidden: bool
) -> dict[str, object]:
    """What old runs assessed. Hidden rows (no profile, or a profile that is not active) only when asked for."""

    active = {view.profile_id for view in selection.views}
    asked = set(selection.profile_ids) | set(selection.hidden_profiles)
    rows: list[dict[str, object]] = []
    hidden = 0
    for item in store.run_assessments():
        shown = item.profile_id in active
        if asked and item.profile_id not in asked:
            continue
        if not shown and not (include_hidden or item.profile_id in asked):
            hidden += 1
            continue
        rows.append(run_history.history_row(item, active=shown))
    return {"runs_imported": store.run_history_stamp()[0], "hidden": hidden, "rows": rows}


# --- search ---------------------------------------------------------------------------------


def search_postings(
    home_root: Path,
    target: Path,
    *,
    profile_ids: Iterable[str] | str | None = None,
    query: str | None = None,
    states: Iterable[str] | str | None = None,
    window: str | None = None,
    removed: bool = False,
    history: bool = False,
    include_hidden: bool = False,
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
    now: datetime | None = None,
    model_wait: float | None = None,
) -> dict[str, object]:
    """The live search, as the ``scout-postings:1`` response. See the module docstring.

    ``model_wait`` (0110-9-01, the server's GET): how long to wait for a build
    of the posting read model; past it the rows are read as stored, or
    ``PostingModelPreparing`` is raised when there are none yet.

    Raises :class:`PostingSearchError` / ``PostingModelError`` / ``PipelineStoreError``.
    """

    from ..workpad import committed_read_cache
    from .pipeline.rank_lane import rank_status

    home_root, target = Path(home_root), Path(target)
    if window is not None and window not in WINDOWS:
        raise PostingSearchError("invalid_value", f"window must be one of: {', '.join(WINDOWS)}")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT or type(offset) is not int or offset < 0:
        raise PostingSearchError("invalid_value", f"limit must be 1..{MAX_LIMIT} and offset 0 or more")
    wanted_states = _names(states, STATES, "state")
    wanted_profiles = _names(profile_ids, None, "profile_id")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    with committed_read_cache():
        store = postings.open_store(home_root, target)
        try:
            selection = _Selection(
                home_root, target, store, profile_ids=wanted_profiles, query=query, states=wanted_states, window=window,
                removed=removed, jobs=None, moment=moment, model_wait=model_wait,
            )
            unknown = [item for item in selection.hidden_profiles if item != EPHEMERAL_PROFILE and not history]
            if unknown:
                raise PostingSearchError("profile_not_found", "no active Scout profile has this id")
            page = selection.shown[offset:offset + limit]
            by_state: dict[str, int] = {}
            for _group, row in selection.shown:
                by_state[row.state] = by_state.get(row.state, 0) + 1
            response: dict[str, object] = {
                "schema_version": SCHEMA_VERSION,
                "checked_at": postings.stamp(moment),
                "filters": {
                    "profile_ids": list(wanted_profiles), "query": query or None, "states": list(wanted_states), "window": window,
                    "removed": bool(removed), "limit": limit, "offset": offset,
                },
                "anchor": {"last_checked_at": selection.anchor, "since": selection.since},
                "counts": {
                    "matched": len(selection.shown), "shown": len(page), "new": selection.new,
                    "by_state": dict(sorted(by_state.items())),
                    # 0110-10-02: the weak fits these filters select; they are in ``matched`` only when the state is asked for.
                    "weak_fit": selection.weak_fit,
                },
                "postings": {
                    ENVELOPE_KEY: labels_envelope(POSTINGS_LABELS),
                    "rule": UNTRUSTED_TEXT_RULE,
                    "rows": _rows_json(home_root, target, store, page, selection.views),
                },
                "profiles": selection.profiles_json(),
                "rank": rank_status(home_root, target),
                "history": _history(store, selection, include_hidden=include_hidden) if history else None,
            }
            check_response(response)
            return response
        finally:
            store.close()


# --- assess these ---------------------------------------------------------------------------


def _estimate(pairs: Sequence[tuple[str, str]], home_root: Path, target: Path) -> tuple[str, dict[str, object]]:
    from .call_metrics import KIND_ASSESS, CallMetricsError, estimate
    from .quick_assess import _default_model_target

    model = _default_model_target(target).value
    try:
        found = estimate(KIND_ASSESS, model, len(pairs), home_root=home_root, target=target)
    except (CallMetricsError, PipelineStoreError):
        found = {"calls": None, "tokens": None, "seconds": None, "cost": None, "basis_calls": 0}
    calls = found["calls"] if isinstance(found["calls"], int) else len(pairs)
    return model, {
        "calls": calls, "tokens": found["tokens"], "seconds": found["seconds"], "cost": found["cost"],
        "basis_calls": found["basis_calls"],
    }


def _renewing(store: PipelineStore, stop: threading.Event, holder: str) -> None:
    while not stop.wait(RENEW_SECONDS):
        try:
            store.renew_lease(LEASE_ASSESS_BATCH, worker=holder)
        except Exception:  # noqa: BLE001 - a renew that fails is tried again; the lease is ten minutes
            continue


def _half_applied(selection: "_Selection", store: PipelineStore, named: Sequence[str] | None, home_root: Path, target: Path) -> bool:
    """Whether a selection read as stored while a build runs holds a row that build wrote, or lacks a named posting.

    A running build's rows carry its stamp (``postings.building_stamp``); the rows an interrupted build left are newer
    than their profile's build record. Every row of a selected posting counts, not only the profile it is shown for:
    which profile that is depends on all of them.
    """

    found = {row.job for _group, row in selection.shown}
    if named is not None and any(job not in found for job in named):
        return True
    running = postings.building_stamp(home_root, target)
    built = {profile_id: build.built_at for profile_id, build in store.posting_builds().items()}
    return any(
        row.updated_at == running or row.updated_at > built.get(row.profile_id, "")
        for group, _row in selection.shown for row in group
    )


def assess_these(
    home_root: Path,
    target: Path,
    *,
    jobs: Sequence[str] | None = None,
    profile_id: str | None = None,
    query: str | None = None,
    states: Iterable[str] | str | None = None,
    window: str | None = None,
    approve: bool = False,
    again: bool = False,
    decided_by: str = "operator",
    now: datetime | None = None,
    config: object | None = None,
    include_low_rank: bool = False,
    model_wait: float | None = None,
) -> dict[str, object]:
    """"Assess these": ask first (count and estimate), assess on approval. The ``scout-postings-assess:1`` response.

    ``jobs`` names the postings (job identities); without it the filter
    (``query``, ``states``, ``window``, ``profile_id``) selects them. A
    posting is assessed for its best profile, or for ``profile_id``. One with
    a current assessment is left out unless ``again``. Nothing is assessed
    unless ``approve`` is true. 0110-10-02: a posting whose rank score is
    below the assess threshold is left out and counted (``low_rank``) unless
    ``include_low_rank``.

    ``model_wait`` (0.1.10.11, the server's POST): as for :func:`search_postings`,
    the postings are selected from the rows as stored when a large build of the
    read model runs (the rows the Jobs list showed), instead of after it. Only
    rows of FINISHED builds are acted on: when a selected row was written by the
    build still running (its facts and its best-profile order are not final), or
    a named posting is not among the stored rows (the build may be adding it),
    the build is waited for, as before, and the selection is made from what it
    stored. The read of the RESULTS, after a batch, waits for the build too.
    """

    from ..workpad import committed_read_cache
    from .find_jobs.contracts import FindJobsContractError
    from .find_jobs.job_state import normalize_job_identity
    from .pipeline.store import DECIDED_BY

    home_root, target = Path(home_root), Path(target)
    if decided_by not in DECIDED_BY:
        raise PostingSearchError("invalid_value", f"actor must be one of: {', '.join(sorted(DECIDED_BY))}")
    if window is not None and window not in WINDOWS:
        raise PostingSearchError("invalid_value", f"window must be one of: {', '.join(WINDOWS)}")
    wanted_states = _names(states, STATES, "state")
    named: list[str] | None = None
    if jobs is not None:
        try:
            named = list(dict.fromkeys(normalize_job_identity(job) for job in jobs))
        except FindJobsContractError as exc:
            raise PostingSearchError("invalid_value", "jobs must be posting URLs (job identities)") from exc
        if not named:
            raise PostingSearchError("invalid_value", "jobs must name at least one posting")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    with committed_read_cache():
        store = postings.open_store(home_root, target)
        try:
            selection = _Selection(
                home_root, target, store, profile_ids=(profile_id,) if profile_id else (), query=query, states=wanted_states,
                window=window, removed=False, jobs=named, moment=moment, model_wait=model_wait,
            )
            if selection.as_stored and _half_applied(selection, store, named, home_root, target):
                selection = _Selection(
                    home_root, target, store, profile_ids=(profile_id,) if profile_id else (), query=query, states=wanted_states,
                    window=window, removed=False, jobs=named, moment=moment,
                )
            if selection.hidden_profiles:
                raise PostingSearchError("profile_not_found", "no active Scout profile has this id")
            found = {row.job for _group, row in selection.shown}
            not_found = [job for job in named or () if job not in found]
            candidates = sorted(
                (row.job, row.profile_id) for _group, row in selection.shown
                if again or row.state == _NOT_ASSESSED or row.stale_code is not None
            )
            current = len(selection.shown) - len(candidates)
            # 0110-10-02: only a rank score of the threshold or more is assessed by default; the rest is its own question.
            setting = fit_rules.fit_setting(home_root, target)
            ranks = {(row.job, row.profile_id): row.rank_score for _group, row in selection.shown}
            pairs, low = split_low_rank(candidates, ranks, setting, include=include_low_rank)
            model, estimate = _estimate(pairs, home_root, target)
            per_profile: dict[str, int] = {}
            for _job, owner in pairs:
                per_profile[owner] = per_profile.get(owner, 0) + 1
            labels = {view.profile_id: view.label for view in selection.views}
            by_profile = [{"profile_id": view.profile_id, "count": per_profile[view.profile_id]} for view in selection.views if view.profile_id in per_profile]
            named_profiles = ", ".join(f"{labels[item['profile_id']]} {item['count']}" for item in by_profile)  # type: ignore[index]
            tokens = estimate["tokens"]
            cost = f", ~{tokens / 1000:.0f}k tokens" if isinstance(tokens, (int, float)) and tokens >= 1000 else ""
            sentence = (
                f"Assess {len(pairs)} posting{'s' if len(pairs) != 1 else ''}"
                + (f" ({named_profiles})" if named_profiles else "") + f"? ~{estimate['calls']} calls{cost}"
            )
            body: dict[str, object] = {"approve": True}
            if named is not None:
                body["jobs"] = named
            for key, value in (("profile_id", profile_id), ("query", query), ("states", list(wanted_states) or None), ("window", window)):
                if value:
                    body[key] = value
            if again:
                body["again"] = True
            if include_low_rank:
                body["include_low_rank"] = True
            low_rank: dict[str, object] | None = None
            if low:
                low_estimate = _estimate(low, home_root, target)[1]
                low_tokens = low_estimate["tokens"]
                low_cost = f", ~{low_tokens / 1000:.0f}k tokens" if isinstance(low_tokens, (int, float)) and low_tokens >= 1000 else ""
                one = len(low) == 1
                low_rank = {
                    "kind": "assess_low_rank", "skipped": len(low), "min_rank": setting.assess_min_rank, "estimate": low_estimate,
                    "text": (
                        f"{len(low)} low-ranked {'one is' if one else 'ones are'} skipped (rank below {setting.assess_min_rank}); "
                        f"assess {'that' if one else 'those'} too? ~{low_estimate['calls']} calls{low_cost}"
                    ),
                    "yes": {"api": {"method": "POST", "path": "/api/postings/assess", "body": {**body, "include_low_rank": True}}},
                }
            question = {
                "kind": "assess_these", "selected": len(selection.shown), "to_assess": len(pairs), "already_current": current,
                "low_rank_skipped": len(low),
                "by_profile": by_profile, "model_target": model, "estimate": estimate, "text": sentence,
                "yes": {"api": {"method": "POST", "path": "/api/postings/assess", "body": body}},
            }
            assessed: dict[str, object] | None = None
            approval_id: str | None = None
            if not pairs and (approve or not low):
                status = STATUS_NOTHING  # nothing above the threshold: the low-ranked ones wait for their own yes
            elif not approve:
                status = STATUS_ASK  # nothing is assessed without approval: no model call was made
            else:
                holder = f"a{uuid.uuid4().hex[:12]}"  # one batch at a time, also among the request threads of one server
                if not store.take_lease(LEASE_ASSESS_BATCH, worker=holder):
                    raise PostingSearchError("assess_batch_running", "an assess batch is already running; wait for it to finish")
                renew_stop = threading.Event()
                renewer = threading.Thread(
                    target=_renewing, args=(store, renew_stop, holder), name="scout-assess-these-renew", daemon=True
                )
                # Live work the pipeline's runner and the rank lane yield to: the marker `scout new` leaves on a yes
                # (pipeline.busy). The lease above only keeps a second batch out.
                try:
                    with assess_batch(home_root, target) as live:
                        # The audit of the batch (DESIGN 10.2): who approved, when, how many, the estimate. Never pending.
                        approval_id = store.create_approval(
                            trigger="assess_these", jobs=len(pairs), est_calls=int(estimate["calls"]),  # type: ignore[call-overload]
                            est_tokens=int(tokens) if isinstance(tokens, (int, float)) else None, profile_id=profile_id,
                        )
                        store.decide_approval(approval_id, approved=True, decided_by=decided_by)
                        renewer.start()
                        texts = postings.posting_texts(home_root, [row for _group, row in selection.shown])
                        assessed = _assess(pairs, texts, home_root=home_root, target=target, config=config, live=live)
                finally:
                    renew_stop.set()
                    if renewer.is_alive():
                        renewer.join()
                    store.release_lease(LEASE_ASSESS_BATCH, worker=holder)
                status = STATUS_ASSESSED
                # The read model as it is with the results: the same postings, whatever state the filter asked for before.
                selection = _Selection(
                    home_root, target, store, profile_ids=(profile_id,) if profile_id else (), query=None, states=(), window=None,
                    removed=False, jobs=[job for job, _owner in pairs], moment=moment,
                )
            chosen = set(pairs)
            page = [pair for pair in selection.shown if (pair[1].job, pair[1].profile_id) in chosen][:MAX_LIMIT]
            response: dict[str, object] = {
                "schema_version": ASSESS_SCHEMA_VERSION,
                "status": status,
                "checked_at": postings.stamp(moment),
                "question": question if status == STATUS_ASK else None,
                "counts": {
                    "selected": len(found), "to_assess": len(pairs), "already_current": current, "not_found": len(not_found),
                    "low_rank_skipped": len(low),
                },
                "low_rank": low_rank,
                "not_found": not_found,
                "approval": None if approval_id is None else {"id": approval_id, "decided_by": decided_by, "jobs": len(pairs)},
                "assessed": assessed,
                "postings": {
                    ENVELOPE_KEY: labels_envelope(POSTINGS_LABELS),
                    "rule": UNTRUSTED_TEXT_RULE,
                    "rows": _rows_json(home_root, target, store, page, selection.views),
                },
                "profiles": selection.profiles_json(),
            }
            check_response(response)
            return response
        finally:
            store.close()


def render(response: Mapping[str, object]) -> str:
    """A search or "assess these" response as the terminal shows it: counts, the question, one line per posting."""

    labels = {item["profile_id"]: item["label"] for item in response["profiles"]}  # type: ignore[union-attr]
    counts = response["counts"]
    assert isinstance(counts, Mapping)
    lines: list[str] = []
    if response["schema_version"] == ASSESS_SCHEMA_VERSION:
        question, assessed = response.get("question"), response.get("assessed")
        if isinstance(question, Mapping):
            lines.append(str(question["text"]))
            lines.append("  Nothing was assessed. Yes: run the same command with --yes.")
        elif isinstance(assessed, Mapping):
            lines.append(f"Assessed {assessed['assessed']} of {assessed['requested']}." + (f" Fetched {assessed['fetched_on_demand']} missing description(s) first." if assessed.get("fetched_on_demand") else ""))
            for item in assessed["failed"]:  # type: ignore[union-attr]
                lines.append(f"  not assessed ({item['error_code']}{': ' + str(item['reason']) if item.get('reason') else ''}): {item['job_identity']}")
        elif not isinstance(response.get("low_rank"), Mapping):
            lines.append("Nothing to assess: every selected posting has a current assessment.")
        low = response.get("low_rank")
        if isinstance(low, Mapping):
            lines.append(str(low["text"]))
            lines.append("  Yes: run the same command with --yes --include-low-rank.")
        for job in response["not_found"]:  # type: ignore[union-attr]
            lines.append(f"  not in the stored postings: {job}")
    else:
        lines.append(f"{counts['matched']} posting(s) match, {counts['new']} new since the last check. Showing {counts['shown']}.")
    listing = response["postings"]
    assert isinstance(listing, Mapping)
    for row in listing["rows"]:  # type: ignore[union-attr]
        tags = ", ".join(str(labels.get(item["profile_id"], item["profile_id"])) for item in row["profiles"])
        gap = f" · {row['minor_gap_text']}" if row.get("minor_gap_text") else ""  # 0110-10-03
        lines.append(f"{row['company_name'] or row['company'] or '?'}: {row['title'] or row['job_identity']} [{tags}] {row['score_text']}{gap}")
        lines.append(f"  {row['job_identity']}")
    history = response.get("history")
    if isinstance(history, Mapping):
        lines.append(f"From old runs: {len(history['rows'])} assessment(s) of {history['runs_imported']} run(s); {history['hidden']} hidden.")  # type: ignore[arg-type]
        for item in history["rows"]:  # type: ignore[union-attr]
            lines.append(f"  {item['job_identity']} [{item['profile_id']}] {item['state']} ({item['basis']['origin']})")
    return "\n".join(lines)


__all__ = [
    "ASSESS_SCHEMA_VERSION",
    "DEFAULT_LIMIT",
    "MAX_LIMIT",
    "SCHEMA_VERSION",
    "STATES",
    "STATUS_ASK",
    "STATUS_ASSESSED",
    "STATUS_NOTHING",
    "WINDOWS",
    "PostingModelError",
    "PostingModelPreparing",
    "PostingSearchError",
    "assess_these",
    "render",
    "search_postings",
]
