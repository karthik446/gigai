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
The 7 and 30 days are counted from the day the posting WENT UP (its
``published_at``; 0110-10-14: never its last change), or from when Scout
first saw it when the board gives no date. ``sort="newest_posted"`` orders
the rows by that same date, the newest first, in place of the grid's order.
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

ALREADY APPLIED (0.1.11.5). A posting with an application (an application
event put it in an application state: applied, interview scheduled, offer
received, rejected, withdrawn) is LEFT OUT the same way, by the same rule
(:func:`_hidden`): of the list, of every count of it (``matched``,
``by_state``) and of the postings a filter selects for "Assess these",
unless the ``applied`` state is asked for or the posting is named.
``counts.applied`` is how many the other filters select, listed or not: the
number on the page's "Applied" chip. The events are read once per request.

ONE POSTING BY ITS ADDRESS (0.1.11.6, ``jobs``). A search that NAMES its
postings is an exact read, never a page of a list: each named posting the
store holds is a row whatever hides it from the list: an application, a weak
fit, a board that no longer lists it (``removed`` is not applied) or its
place past the first 200 rows. This is how a job page opened by its address
finds its posting. The profile, word, state and window filters still apply
when they are given.

RANKED LOW (0.1.11.2, ``fit.is_ranked_low``). The list is ordered best fit
first, and a posting NOTHING ASSESSED YET whose known rank score is below the
weak-fit rank (``fit.weak_fit_below_rank``, 50) is ranked low. It is ORDERED
lower, NEVER hidden or collapsed: it is listed with the rest, in rank order,
so it comes after the other ranked postings not assessed yet and before the
ones not ranked yet. A resume that gains the experience and a re-rank lift
it. Each such row carries ``ranked_low: true``, ``counts.ranked_low`` is how
many the list holds (the "Ranked low (N)" divider above them), and the
``ranked_low`` state is an optional filter that lists only them. An assessed
posting keeps the rules above. "Assess these" still counts them as its own
low-rank question. ``ranking`` in both responses says how far the
background rank is (ranked of total per profile): a batch taken while it runs
is the top of a half-ranked list.

HISTORY (``history=True``). What old find-jobs runs assessed, imported by
``run_history.py``, each with the provenance the run sealed. Rows of a run
with no profile (the ``ephemeral`` pseudo-profile) and of a profile that is
no longer active are ``hidden``: they are listed only when asked for
(``include_hidden``, or ``profile_id`` naming them), never by default.

ASSESS THESE (:func:`assess_these`). The postings named (``jobs``) or the
ones a filter selects, each for its best profile (or ``profile_id``), that
have no current assessment. Nothing is assessed without approval: a call
without ``approve`` answers ``status: "ask"`` with the count and the estimate
from the recorded model calls, and makes no model call; ``model_input_summary``
(0110-10-13, ``assess_preview``) says what the postings asked about would send
and where: profile, resume source, answers and stories, the model target, and
whether a posting is fetched first; never a line of the user's text. With ``approve`` the
batch is recorded as an approved batch (``approval``: who approved, when, how
many, the estimate), registered as live work (the ``assess_batch`` lease:
the pipeline and the rank lane start nothing while it runs) and assessed
through the job page's own path (``run_quick_assessment``, from the posting
text already stored; one with none has its description fetched first, one request for it alone). The results are in the quick-assess
store, so the read model shows them at once. 0110-10-02: a posting whose rank
score is below the assess threshold (``fit.assess_min_rank``, 50) is left out
of the batch and counted; ``low_rank`` in the response is the separate
question for those ("3 low-ranked ones are skipped; assess those too? ~3
calls"), and ``include_low_rank`` assesses them with the rest. 0110-10-11 (the
operator's rule, ``scout_new.BATCH_LIMIT``): one approval assesses 50 of them
and never more. 0.1.11.2: the TOP 50 BY RANK (``scout_new.top_ranked_batch``:
the best rank score first; a posting not ranked yet after the ranked ones, the
newest first). The question says the real total and the 50 ("Assess the
top 50 by rank of 120 postings? ~50 calls ... (70 more after these 50)"), its
estimate is the batch's, ``question.batch`` / ``more_after`` and
``counts.batch`` / ``more_after`` say the same in numbers, and the same call
again assesses the next 50.

US ONLY (0.1.11.8 N1, ``find_jobs/job_copies.py``). A switch of its own on the
list, by the POSTING'S LOCATION (never by its board): a posting is left out
only when every place its location names is clearly outside the US. A US
place or "Remote" in the US stays; "Remote" alone, no location, or a place
Scout cannot read ALSO stays and its row says ``location_unclear`` (the
"unclear location" label). ON by default for a US setup (the shared
``find-jobs.json`` countries hold the US), OFF otherwise; ``us_only`` says
either, for this call only (no profile setting is changed).
``counts.us_only_left_out`` is how many postings it left out.

COPIES (0.1.11.8 N2). The same company, the same title (case, spaces and
punctuation aside) AND the same description posted more than once, so that
only the location differs (typically once per country), is ONE row:
``copies`` (how many), ``locations`` / ``locations_text`` ("Remote: Estonia,
Lithuania, Latvia +4") and ``members`` (each copy's address). The
description is compared by the digest the company index keeps
(``content_sha256``). Conservative: another description (another team under
the same title) is another row, and a posting with NO stored description is
never merged; two companies or two titles never merge, nor a removed copy
with a live one. Two cities of one country merge. Collapsed BEFORE the page
and the counts: ``matched`` and every other count are rows, a page of 50 is
50 jobs. The row is ONE canonical job (``canonical_job.pick_canonical``: its
US posting when it has one, else the earliest posted, then the posting id):
its state, its rank and its address are the row's, and Assess, Mark applied
and the job page act on it. Any copy with an application makes the row
applied. "Assess these" by a filter takes the canonical job only: one model
call for a job, not one per country. Only the copies the other filters
select are a row's copies. A search that NAMES its postings (``jobs``) is
exact: neither US only nor the collapse applies. ``collapse=False`` lists
every posting.

LABELS (data_labels, P4): like ``scout new``, no response mixes. A response
holds posting text and what a model derived from it (``public-untrusted``)
next to ids, counts, codes and the profile tags, and nothing the user wrote.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
import threading
import uuid

from . import fit as fit_rules
from . import postings, run_history
from .assess_causes import failure_lines
from .evaluated_models import notice_lines
from .assess_preview import model_input_summary, summary_lines
from .data_labels import ENVELOPE_KEY, PUBLIC_UNTRUSTED, UNTRUSTED_TEXT_RULE, labels_envelope
from .find_jobs.canonical_job import canonical_order
from .find_jobs.job_copies import (
    PLACE_OTHER,
    PLACE_UNCLEAR,
    PLACE_US,
    US_ONLY_RULE,
    copy_key,
    distinct_locations,
    locations_text,
    place_of,
    us_only_default,
)
from .pipeline.busy import LiveBatch, assess_batch
from .pipeline.store import (
    EPHEMERAL_PROFILE,
    LEASE_ASSESS_BATCH,
    RENEW_SECONDS,
    PipelineStore,
    PipelineStoreError,
    PostingRecord,
    RunAssessment,
)
from .postings import PostingModelError, PostingModelPreparing, PostingText, ProfileView
from .scout_new import (
    APPLIED_COMMAND,
    FIRST_USE_DAYS,
    POSTINGS_LABELS,
    STOPPED_CANCELLED,
    _assess,
    _calls,
    _grouped,
    _row_json,
    _score,
    _shown,
    batch_date,
    check_response,
    divider_text,
    _ranking,
    in_order,
    posted_text,
    split_low_rank,
    top_ranked_batch,
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

#: The orders (0110-10-14). ``fit`` is the grid's own (``scout_new.order_key``, then the newest seen) and the default.
SORT_FIT = "fit"
SORT_NEWEST_POSTED = "newest_posted"
SORTS = (SORT_FIT, SORT_NEWEST_POSTED)

#: The state filters. ``assessed`` is any posting with an assessment; ``recommended`` the Scout label.
STATE_ASSESSED = "assessed"
STATE_RECOMMENDED = "recommended"
#: 0.1.11.3 (item 12): ``applied`` keeps the postings an application event put in an application state (applied and
#: beyond). It is a filter over the events, never a row state: the assessment state of a row is untouched.
#: 0.1.11.5: and only this filter (or naming the posting) lists such a posting: ``_hidden``.
STATE_APPLIED = "applied"
# ``has_gap`` (0.1.11 N3, OD1): matched by verdict, held by the gate (``job_state.HAS_GAP``).
# ``thin_posting`` (0.1.11.2): matched by verdict on no row about the job (``fit.THIN_POSTING``): never in ``matched``.
_ROW_STATES = frozenset({
    "not_assessed", "needs_answers", "matched", "has_gap", "not_a_match", "tailored", fit_rules.WEAK_FIT, fit_rules.THIN_POSTING,
})
#: 0.1.11.2: ``ranked_low`` (``fit.RANKED_LOW``) keeps only the not-assessed postings ranked below the weak-fit rank.
STATES = frozenset(_ROW_STATES | {STATE_ASSESSED, STATE_RECOMMENDED, STATE_APPLIED, fit_rules.RANKED_LOW})

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
_NOT_ASSESSED = "not_assessed"
#: 0.1.11.8 N2: the grid's labels plus the locations of a row's copies (posting text, like ``location``).
_ROWS_LABELS = {
    **POSTINGS_LABELS,
    "/rows/*/locations/*": PUBLIC_UNTRUSTED,
    "/rows/*/locations_text": PUBLIC_UNTRUSTED,
    "/rows/*/members/*/location": PUBLIC_UNTRUSTED,
}


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


@dataclass(frozen=True, slots=True)
class _Place:
    """What the company index says of one posting, for US only and the copies."""

    title: str
    company: str
    location: str
    #: ``job_copies.place_of``: ``us``, ``unclear`` or ``other``.
    place: str
    #: The digest of the title and the description (``None``: no description stored, never merged).
    content: str | None
    posting_id: str


#: ``(home, board) -> (the company file's stamp, {job: its place})``.
_PLACES: dict[tuple[str, str], tuple[object, dict[str, _Place]]] = {}
_PLACES_LOCK = threading.Lock()
_PLACES_BOARDS = 4096


def _places(home_root: Path, rows: Iterable[PostingRecord]) -> dict[str, _Place]:
    """``job -> its place`` (title, company, location, where, description digest) from the company index.

    One company file per board that has a row here, read once and kept while the file's stamp (mtime, size) does not
    move, so a server reads a board again only after an update wrote it. A board whose file cannot be read gives
    nothing: its postings are kept by US only and are rows of their own.
    """

    from .find_jobs.company_index import CompanyIndex
    from .find_jobs.contracts import FindJobsContractError, normalize_url

    boards = sorted({row.board for row in rows})
    index = CompanyIndex.for_home(Path(home_root))
    found: dict[str, _Place] = {}
    for board in boards:
        try:
            ats, slug = postings.split_board(board)
            stat = postings._stat(index.path(ats, slug))
        except ValueError:
            continue  # not a board the index can name: it has no file
        key = (str(home_root), board)
        with _PLACES_LOCK:
            kept = _PLACES.get(key)
        if kept is None or kept[0] != stat:
            entry = index.read(ats, slug)
            facts: dict[str, _Place] = {}
            if entry is not None:
                company = entry.company if type(entry.company) is str else ""
                for posting_id, posting in entry.postings.items():
                    try:
                        job = normalize_url(posting.url)
                    except FindJobsContractError:
                        continue
                    facts[job] = _Place(
                        posting.title, company, posting.location, place_of(posting.location, posting.countries),
                        posting.content_sha256 or None, posting_id,
                    )
            kept = (stat, facts)
            with _PLACES_LOCK:
                if len(_PLACES) >= _PLACES_BOARDS and key not in _PLACES:
                    del _PLACES[next(iter(_PLACES))]
                _PLACES[key] = kept
        found.update(kept[1])
    return found


def us_only_setting(home_root: Path, target: Path) -> bool:
    """0.1.11.8 N1: the list's US-only default: ON when the shared ``find-jobs.json`` countries hold the US. A read."""

    try:
        return us_only_default(postings._shared_config(Path(home_root), Path(target)).countries)
    except (PostingModelError, ValueError, OSError):
        return False  # no readable setup: nothing says this is a US setup


def _posted(row: PostingRecord) -> str:
    """The date the 7 / 30 days and "newest posted" judge: the day the posting went up; for a board that gives none, when it was first seen."""

    return row.published_at or row.first_seen


def _in_window(row: PostingRecord, window: str | None, since: str, moment: datetime) -> bool:
    if window is None:
        return True
    if window == WINDOW_NEW:
        return row.first_seen > since
    edge = postings.stamp(moment - timedelta(days=WINDOWS[window] or 0)) or ""
    return _posted(row) >= edge


def _applications(resolved: object) -> dict[str, dict[str, object]]:
    """0.1.11.3 (item 12): job identity -> ``{status, since}`` of every job an application event put in an application state.

    ONE read of the committed events per request (``job_state.read_application_events``), joined to the rows by job
    identity; a row never reads the events. A display read: events that cannot be read label nothing.
    """

    from .find_jobs.job_state import current_application_event, read_application_events

    if resolved is None:
        return {}
    try:
        events = read_application_events(resolved)
    except Exception:  # noqa: BLE001 - a label read: events that cannot be read hide nothing
        return {}
    found: dict[str, dict[str, object]] = {}
    for job, group in events.items():
        event = current_application_event(group)
        if event is not None:
            since = event.get("occurred_at")
            found[job] = {"status": str(event["event_kind"]), "since": since if isinstance(since, str) else None}
    return found


def _wanted(
    row: PostingRecord, states: Sequence[str], setting: fit_rules.FitSetting = fit_rules.DEFAULT_SETTING,
    applications: Mapping[str, object] | None = None,
) -> bool:
    if not states:
        return True
    for state in states:
        if state == STATE_APPLIED and applications is not None and row.job in applications:
            return True
        if state == fit_rules.RANKED_LOW and fit_rules.is_ranked_low(row.state, row.rank_score, setting):
            return True
        if state == STATE_ASSESSED and row.state != _NOT_ASSESSED:
            return True
        if state == STATE_RECOMMENDED and row.label == STATE_RECOMMENDED:
            return True
        if state == "tailored" and row.tailored:
            return True  # 0110-8-12: a tailored resume is a flag beside the verdict state, not a state
        if state == row.state:
            return True
    return False


def _hidden(row: PostingRecord, states: Sequence[str], named: bool, applications: Mapping[str, object] | None = None) -> bool:
    """What a list leaves out unless it is asked for (its state filter, or the posting named).

    0110-10-02: a weak fit (the ``weak_fit`` state). 0.1.11.5: a posting with an application (the ``applied`` state);
    the ``applied`` filter lists every one of them, a weak fit too.
    """

    if named:
        return False
    if applications is not None and row.job in applications:
        return STATE_APPLIED not in states
    return row.state == fit_rules.WEAK_FIT and fit_rules.WEAK_FIT not in states


class _Selection:
    """One refreshed read of the read model with the call's filters applied: every matching posting, in order."""

    def __init__(
        self, home_root: Path, target: Path, store: PipelineStore, *, profile_ids: Sequence[str], query: str | None,
        states: Sequence[str], window: str | None, removed: bool | None, jobs: Sequence[str] | None, moment: datetime,
        model_wait: float | None = None, sort: str = SORT_FIT, us_only: bool = False, collapse: bool = False,
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
        # 0.1.11.6: ``removed`` None (a read of named postings) takes a posting whether its board still lists it or not.
        groups = _grouped(row for row in rows if removed is None or (row.removed_at is not None) == removed)
        if self.hidden_profiles and not self.profile_ids:
            groups = {}  # only profiles with no live rows were asked for: nothing of an active profile is shown instead
        elif self.profile_ids:
            wanted = set(self.profile_ids)
            groups = {job: group for job, group in groups.items() if any(row.profile_id in wanted for row in group)}
        only = self.profile_ids[0] if len(self.profile_ids) == 1 else None
        shown = [(group, _shown(group, only)) for group in groups.values()]
        weak = fit_rules.WEAK_FIT
        setting = fit_rules.fit_setting(home_root, target)
        #: Item 12: the application of each job, read once; the "Applied" filter, the rows' badge and (0.1.11.5) what
        #: the list leaves out all use it.
        self.applications = dict(_applications(refreshed.resolved))
        applications = self.applications
        named = jobs is not None

        def low(row: PostingRecord) -> bool:
            return fit_rules.is_ranked_low(row.state, row.rank_score, setting)

        # The filters that judge ONE posting first: the window, the words, US only. Then copies become one row, and
        # the state filters judge the row.
        shown = [(group, row) for group, row in shown if _in_window(row, window, self.since, moment)]
        words = [word for word in (query or "").casefold().split() if word]
        if words:
            text = _index_words(home_root, [row for _group, row in shown])
            shown = [(group, row) for group, row in shown if all(word in text.get(row.job, "") for word in words)]
        #: 0.1.11.8: ``job -> every copy the row stands for`` (the row's own posting first), and what US only left out.
        self.copies: dict[str, list[tuple[Sequence[PostingRecord], PostingRecord]]] = {}
        self.places: dict[str, _Place] = {}
        self.us_only_left_out = 0
        if not named and (us_only or collapse) and shown:
            self.places = places = _places(home_root, [row for _group, row in shown])
            if us_only:
                # 0.1.11.8 N1: only a posting clearly outside the US is left out; one Scout cannot place (or with no file) stays.
                kept = [pair for pair in shown if pair[1].job not in places or places[pair[1].job].place != PLACE_OTHER]
                self.us_only_left_out = len(shown) - len(kept)
                shown = kept
            if collapse:
                shown = self._collapsed(shown, places, applications)
        shown = [
            (group, row) for group, row in shown
            if _wanted(row, states, setting, applications) or row.state == weak or row.job in applications
        ]
        # 0110-10-02: the weak fits the OTHER filters select (the chip's number), then only the ones asked for stay.
        # 0.1.11.5: the same for the postings with an application; a weak fit the list leaves out as applied is not
        # in the weak-fit number (its chip would not list it).
        self.applied = sum(1 for _group, row in shown if row.job in applications)
        self.weak_fit = sum(
            1 for _group, row in shown
            if row.state == weak and not (row.job in applications and _hidden(row, states, named, applications))
        )
        self.is_ranked_low = low
        shown = [
            (group, row) for group, row in shown
            if _wanted(row, states, setting, applications) and not _hidden(row, states, named, applications)
        ]
        # 0.1.11.2: the not-assessed postings ranked low are LISTED (ordered lower by their rank, never left out);
        # this is how many the list holds, for the "Ranked low (N)" divider above them.
        self.ranked_low = sum(1 for _group, row in shown if low(row))
        # 0110-8-04: the grid's one order (``scout_new.order_key``): current, stale, not assessed; verdict; rank.
        self.shown = shown = in_order(shown)
        if sort == SORT_NEWEST_POSTED:
            shown.sort(key=lambda pair: _posted(pair[1]), reverse=True)  # stable: postings of one instant keep the grid's order
        self.new = sum(1 for _group, row in shown if row.first_seen > self.since and row.removed_at is None)

    def _collapsed(
        self, shown: Sequence[tuple[Sequence[PostingRecord], PostingRecord]], places: Mapping[str, _Place],
        applications: dict[str, dict[str, object]],
    ) -> list[tuple[Sequence[PostingRecord], PostingRecord]]:
        """0.1.11.8 N2: the copies of one job (``job_copies.copy_key``) as ONE row: its canonical job.

        ``canonical_job.pick_canonical`` names it (the US posting, else the earliest posted, then the posting id).
        ``self.copies`` keeps the copies, the canonical one first. A copy with an application makes the ROW applied:
        the canonical job is given that application in this selection's view (``applications``), so the list leaves
        the row out as applied and "Applied" lists it once.
        """

        groups: dict[object, list[tuple[Sequence[PostingRecord], PostingRecord]]] = {}
        for pair in shown:
            row = pair[1]
            facts = places.get(row.job)
            key = None if facts is None else copy_key(row.board, facts.company, facts.title, facts.content, row.removed_at is not None)
            groups.setdefault(key if key is not None else row.job, []).append(pair)
        rows: list[tuple[Sequence[PostingRecord], PostingRecord]] = []
        for members in groups.values():
            if len(members) > 1:
                members = canonical_order(
                    members, us=lambda pair: places[pair[1].job].place == PLACE_US, posted=lambda pair: _posted(pair[1]),
                    posting_id=lambda pair: places[pair[1].job].posting_id,
                )
                canonical = members[0][1].job
                self.copies[canonical] = members
                if canonical not in applications:
                    applied = next((applications[pair[1].job] for pair in members if pair[1].job in applications), None)
                    if applied is not None:
                        applications[canonical] = dict(applied)
            rows.append(members[0])
        return rows

    def copies_json(self, row: PostingRecord, text: PostingText | None) -> dict[str, object]:
        """The additive keys of a row: ``location_unclear``, ``copies``, ``locations``, ``locations_text``, ``members``."""

        facts = self.places.get(row.job)
        unclear = facts is not None and facts.place == PLACE_UNCLEAR
        members = self.copies.get(row.job)
        if not members:
            place = "" if text is None else (text.location or "")
            return {
                "location_unclear": unclear, "copies": 1, "locations": distinct_locations([place]), "locations_text": place,
                "members": [{"job_identity": row.job, "job_url": text.url if text is not None else row.job, "location": place or None}],
            }
        where = [(member.job, self.places.get(member.job)) for _group, member in members]
        found = distinct_locations(place.location for _job, place in where if place is not None)
        return {
            "location_unclear": unclear, "copies": len(members), "locations": found, "locations_text": locations_text(found),
            "members": [{"job_identity": job, "job_url": job, "location": None if place is None else (place.location or None)} for job, place in where],
        }

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


@lru_cache(maxsize=1)
def _h1b_index() -> Mapping[tuple[str, str], Mapping[str, object]]:
    """``(provider, board token lower-cased)`` -> the shipped catalog's company H-1B figure, built once per process.

    0.1.11.3 (item 8): a LABEL on the row, read from the bundled catalog (no network, no per-row read). A catalog that
    cannot load gives an empty index: the figure is display-only and never breaks the list.
    """

    from .find_jobs.company_catalog import CompanyCatalogError, load_company_catalog

    try:
        index = load_company_catalog().h1b_by_board()
    except CompanyCatalogError:
        return {}
    return {(provider.value, token): figure.to_json() for (provider, token), figure in index.items() if figure.approvals > 0}


def _rows_json(
    home_root: Path, target: Path, store: PipelineStore, shown: Sequence[tuple[Sequence[PostingRecord], PostingRecord]],
    views: Sequence[ProfileView] = (), ranked_low: Callable[[PostingRecord], bool] | None = None,
    applications: Mapping[str, Mapping[str, object]] | None = None, selection: "_Selection | None" = None,
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
    h1b = _h1b_index() if shown else {}  # once per request: a row looks its board up, never reads the catalog
    for group, row in shown:
        item = None if row.state == _NOT_ASSESSED else read_quick_assessment(home_root, target, row.profile_id, row.job)
        text = texts.get(row.job)
        entry = _row_json(group, row, text, item, pending(row.profile_id, None if text is None else text.title))
        # 0.1.11.3 (item 8): LABELS only (never a filter, sort key or hold). ``sponsorship`` is what the assessment read
        # from the posting (null: not stated / not assessed); ``h1b`` the company's catalog figure (null: none held).
        stated = None if item is None else getattr(item.result, "sponsorship", None)  # type: ignore[attr-defined]
        entry["sponsorship"] = None if stated is None else stated.value
        ats, token = postings.split_board(row.board)
        figure = h1b.get((ats, token.lower()))
        entry["h1b"] = None if figure is None else dict(figure)
        # 0.1.11.3 (item 12): a LABEL (the job's latest application status and its date; null: none). Never a state.
        applied = None if applications is None else applications.get(row.job)
        entry["application"] = None if applied is None else dict(applied)
        if selection is not None:
            entry.update(selection.copies_json(row, text))  # 0.1.11.8 N2 (additive)
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
        if ranked_low is not None:
            entry["ranked_low"] = ranked_low(row)  # 0.1.11.2: not assessed and ranked below the weak-fit rank
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
    sort: str | None = None,
    jobs: Iterable[str] | None = None,
    us_only: bool | None = None,
    collapse: bool | None = None,
) -> dict[str, object]:
    """The live search, as the ``scout-postings:1`` response. See the module docstring.

    ``us_only`` (0.1.11.8 N1): ``None`` is the setup's default (ON when the
    shared config's countries hold the US). ``collapse`` (N2): the copies of
    one job are one row (``None``: on).
    Neither applies to a search that names its postings.

    ``jobs`` (0.1.11.6): only these postings, by address (raw or normalized),
    each one the store holds whatever hides it from the list: an application,
    a weak fit, a removed posting (``removed`` is not applied). ``filters.jobs``
    echoes the normalized addresses.

    ``sort`` (0110-10-14): ``fit`` (the default: the grid's order) or
    ``newest_posted`` (the day the posting went up, the newest first).

    ``model_wait`` (0110-9-01, the server's GET): how long to wait for a build
    of the posting read model; past it the rows are read as stored, or
    ``PostingModelPreparing`` is raised when there are none yet.

    Raises :class:`PostingSearchError` / ``PostingModelError`` / ``PipelineStoreError``.
    """

    from ..workpad import committed_read_cache
    from .find_jobs.contracts import FindJobsContractError
    from .find_jobs.job_state import normalize_job_identity
    from .pipeline.rank_lane import rank_status

    home_root, target = Path(home_root), Path(target)
    if window is not None and window not in WINDOWS:
        raise PostingSearchError("invalid_value", f"window must be one of: {', '.join(WINDOWS)}")
    sort = SORT_FIT if sort is None else sort
    if sort not in SORTS:
        raise PostingSearchError("invalid_value", f"sort must be one of: {', '.join(SORTS)}")
    if type(limit) is not int or not 1 <= limit <= MAX_LIMIT or type(offset) is not int or offset < 0:
        raise PostingSearchError("invalid_value", f"limit must be 1..{MAX_LIMIT} and offset 0 or more")
    wanted_states = _names(states, STATES, "state")
    wanted_profiles = _names(profile_ids, None, "profile_id")
    named: list[str] | None = None
    if jobs is not None:
        try:
            named = list(dict.fromkeys(normalize_job_identity(job) for job in jobs))
        except FindJobsContractError as exc:
            raise PostingSearchError("invalid_value", "job must be a posting URL (a job identity)") from exc
        if not named or len(named) > MAX_LIMIT:
            raise PostingSearchError("invalid_value", f"job must name 1..{MAX_LIMIT} postings")
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    us_default = us_only_setting(home_root, target)
    us_on = named is None and (us_default if us_only is None else bool(us_only))
    collapse = named is None and (True if collapse is None else bool(collapse))
    with committed_read_cache():
        store = postings.open_store(home_root, target)
        try:
            selection = _Selection(
                home_root, target, store, profile_ids=wanted_profiles, query=query, states=wanted_states, window=window,
                removed=None if named is not None else removed, jobs=named, moment=moment, model_wait=model_wait, sort=sort,
                us_only=us_on, collapse=collapse,
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
                    "removed": bool(removed), "limit": limit, "offset": offset, "sort": sort,
                    "us_only": us_on, "collapse": collapse,
                    **({} if named is None else {"jobs": named}),
                },
                # 0.1.11.8 N1 (additive): the switch as it applied, the setup's default, the rule in a sentence.
                "us_only": {"on": us_on, "default": us_default, "rule": US_ONLY_RULE},
                "anchor": {"last_checked_at": selection.anchor, "since": selection.since},
                "counts": {
                    "matched": len(selection.shown), "shown": len(page), "new": selection.new,
                    "by_state": dict(sorted(by_state.items())),
                    # 0110-10-02: the weak fits these filters select; they are in ``matched`` only when the state is asked for.
                    "weak_fit": selection.weak_fit,
                    # 0.1.11.5: the postings with an application these filters select; in ``matched`` only when the
                    # ``applied`` state is asked for.
                    "applied": selection.applied,
                    # 0.1.11.2: the not-assessed postings ranked below the weak-fit rank. They are in ``matched`` and in the rows.
                    "ranked_low": selection.ranked_low,
                    # 0.1.11.8: the postings the listed rows stand for (``matched`` when no row has a copy), and the
                    # postings US only left out.
                    "postings": sum(len(selection.copies.get(row.job, (None,))) for _group, row in selection.shown),
                    "us_only_left_out": selection.us_only_left_out,
                },
                "postings": {
                    ENVELOPE_KEY: labels_envelope(_ROWS_LABELS),
                    "rule": UNTRUSTED_TEXT_RULE,
                    "rows": _rows_json(
                        home_root, target, store, page, selection.views, selection.is_ranked_low, selection.applications, selection,
                    ),
                },
                "profiles": selection.profiles_json(),
                "rank": rank_status(home_root, target),
                # 0.1.11.2: how far the background rank is: the order (and a batch) is of what is ranked so far.
                "ranking": _ranking(store, selection.views, home_root, target, now=moment),
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
    on_live: Callable[[LiveBatch], None] | None = None,
    us_only: bool | None = None,
) -> dict[str, object]:
    """"Assess these": ask first (count and estimate), assess on approval. The ``scout-postings-assess:1`` response.

    0.1.11.5 (ASSESS-01): ``on_live`` is called once with the approved batch's marker (``pipeline.busy.LiveBatch``)
    when it is live and says its total, before the first model call: the server's background job answers its request
    then and the batch runs on (``assess_batch_job``). The batch can be cancelled through the marker
    (``pipeline.busy.request_cancel``): the calls in flight finish, what finished is kept, ``assessed.stopped`` is
    ``cancelled``. ``low_rank.included`` says what ``include_low_rank`` would do to THIS run: the low-ranked ones
    join the pool, and one approval is still the top 50 by rank of the whole pool, so ``changes_batch`` is false
    when none of them would be among the 50.

    0.1.11.8: a FILTER selects what the Jobs list shows: US only as the list
    applies it (``us_only``; ``None``: the setup's default) and one posting per
    job (the row of its copies), so a job posted once per country is one model
    call. Named postings are assessed as named.

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
    us_on = named is None and (us_only_setting(home_root, target) if us_only is None else bool(us_only))
    collapse = named is None  # a filter selects one posting per job; named postings are assessed as named
    with committed_read_cache():
        store = postings.open_store(home_root, target)
        try:
            selection = _Selection(
                home_root, target, store, profile_ids=(profile_id,) if profile_id else (), query=query, states=wanted_states,
                window=window, removed=False, jobs=named, moment=moment, model_wait=model_wait, us_only=us_on, collapse=collapse,
            )
            if selection.as_stored and _half_applied(selection, store, named, home_root, target):
                selection = _Selection(
                    home_root, target, store, profile_ids=(profile_id,) if profile_id else (), query=query, states=wanted_states,
                    window=window, removed=False, jobs=named, moment=moment, us_only=us_on, collapse=collapse,
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
            wanted, low = split_low_rank(candidates, ranks, setting, include=include_low_rank)
            # 0110-10-11: one approval assesses 50, never more; ``wanted`` is all of them, ``pairs`` the batch.
            # 0.1.11.2: the batch is the TOP 50 BY RANK (not ranked yet: after the ranked ones, the newest first).
            dates = {(row.job, row.profile_id): batch_date(row) for _group, row in selection.shown}
            pairs, later = top_ranked_batch(wanted, ranks, dates)
            low_batch, low_later = top_ranked_batch(low, ranks, dates)
            ranking = _ranking(store, selection.views, home_root, target, now=moment)
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
                (f"Assess the top {len(pairs)} by rank of {len(wanted)} postings" if later else f"Assess {len(pairs)} posting{'s' if len(pairs) != 1 else ''}")
                + (f" ({named_profiles})" if named_profiles else "") + f"? {_calls(estimate['calls'])}{cost}"
                + (f" ({later} more after these {len(pairs)})" if later else "")
            )
            body: dict[str, object] = {"approve": True}
            if named is not None:
                body["jobs"] = named
            for key, value in (("profile_id", profile_id), ("query", query), ("states", list(wanted_states) or None), ("window", window)):
                if value:
                    body[key] = value
            if again:
                body["again"] = True
            if us_only is not None and named is None:
                body["us_only"] = bool(us_only)  # the yes selects what the question counted
            if include_low_rank:
                body["include_low_rank"] = True
            low_rank: dict[str, object] | None = None
            if low:
                # 0.1.11.5: what the yes below really assesses: the top of the WHOLE pool, the low-ranked ones in it.
                pool, _none = split_low_rank(candidates, ranks, setting, include=True)
                with_low, with_low_later = top_ranked_batch(pool, ranks, dates)
                joined = len(set(with_low) - set(pairs))
                low_estimate = _estimate(low_batch, home_root, target)[1]
                low_tokens = low_estimate["tokens"]
                low_cost = f", ~{low_tokens / 1000:.0f}k tokens" if isinstance(low_tokens, (int, float)) and low_tokens >= 1000 else ""
                one = len(low) == 1
                low_rank = {
                    "kind": "assess_low_rank", "skipped": len(low), "min_rank": setting.assess_min_rank, "estimate": low_estimate,
                    "batch": len(low_batch), "more_after": low_later,
                    "included": {
                        "pool": len(pool), "batch": len(with_low), "more_after": with_low_later, "low_ranked_in_batch": joined,
                        "changes_batch": set(with_low) != set(pairs), "estimate": _estimate(with_low, home_root, target)[1],
                    },
                    "text": (
                        f"{len(low)} low-ranked {'one is' if one else 'ones are'} skipped (rank below {setting.assess_min_rank}); "
                        f"assess {f'the top {len(low_batch)} by rank of ' if low_later else ''}{'that' if one else 'those'} too? "
                        f"{_calls(low_estimate['calls'])}{low_cost}" + (f" ({low_later} more after these {len(low_batch)})" if low_later else "")
                    ),
                    "yes": {"api": {"method": "POST", "path": "/api/postings/assess", "body": {**body, "include_low_rank": True}}},
                }
            question = {
                "kind": "assess_these", "selected": len(selection.shown), "to_assess": len(wanted), "already_current": current,
                "low_rank_skipped": len(low), "batch": len(pairs), "more_after": later,
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
                        seconds = estimate["seconds"]
                        live.begin([job for job, _owner in pairs], estimate_seconds=seconds if isinstance(seconds, (int, float)) else None)
                        if on_live is not None:
                            on_live(live)  # 0.1.11.5: the batch is live and says its total; the server answers its request now
                        # The audit of the batch (DESIGN 10.2): who approved, when, how many, the estimate. Never pending.
                        approval_id = store.create_approval(
                            trigger="assess_these", jobs=len(pairs), est_calls=int(estimate["calls"]),  # type: ignore[call-overload]
                            est_tokens=int(tokens) if isinstance(tokens, (int, float)) else None, profile_id=profile_id,
                        )
                        store.decide_approval(approval_id, approved=True, decided_by=decided_by)
                        renewer.start()
                        texts = postings.posting_texts(home_root, [row for _group, row in selection.shown])
                        assessed = _assess(pairs, texts, home_root=home_root, target=target, config=config, live=live,
                            estimate_seconds=seconds if isinstance(seconds, (int, float)) else None,
                        )
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
            # 0110-10-13: what the question's postings would send, by category (ids, labels, counts; no text). Only while
            # nothing was assessed: the low-ranked ones when only their question is left.
            summary: dict[str, object] | None = None
            asked = pairs or low_batch
            if status != STATUS_ASSESSED and asked:
                stored_text = {(row.job, row.profile_id): row.listing_known for _group, row in selection.shown}
                summary = model_input_summary(
                    home_root=home_root, target=target, pairs=asked, profiles=selection.views, model_target=model,
                    without_text=sum(1 for pair in asked if not stored_text.get(pair, False)), resolved=selection.resolved,
                )
            chosen = set(pairs)
            page = [pair for pair in selection.shown if (pair[1].job, pair[1].profile_id) in chosen][:MAX_LIMIT]
            response: dict[str, object] = {
                "schema_version": ASSESS_SCHEMA_VERSION,
                "status": status,
                "checked_at": postings.stamp(moment),
                "question": question if status == STATUS_ASK else None,
                "model_input_summary": summary,
                "counts": {
                    "selected": len(found), "to_assess": len(wanted), "already_current": current, "not_found": len(not_found),
                    "low_rank_skipped": len(low), "batch": len(pairs), "more_after": later,
                },
                "low_rank": low_rank,
                "ranking": ranking,
                "not_found": not_found,
                "approval": None if approval_id is None else {"id": approval_id, "decided_by": decided_by, "jobs": len(pairs)},
                "assessed": assessed,
                "postings": {
                    ENVELOPE_KEY: labels_envelope(_ROWS_LABELS),
                    "rule": UNTRUSTED_TEXT_RULE,
                    "rows": _rows_json(home_root, target, store, page, selection.views, selection=selection),
                },
                "profiles": selection.profiles_json(),
            }
            check_response(response)
            return response
        finally:
            store.close()


def applied_left_out_line(applied: object, filters: object = None) -> str | None:
    """0.1.11.5: "7 you already applied to are left out: gigai scout jobs list --state applied"; ``None`` when none is left out."""

    asked = isinstance(filters, Mapping) and STATE_APPLIED in (filters.get("states") or ())
    if type(applied) is not int or applied < 1 or asked:
        return None
    return f"{applied} you already applied to {'are' if applied != 1 else 'is'} left out: {APPLIED_COMMAND}"


def us_only_left_out_line(counts: Mapping[str, object]) -> str | None:
    """0.1.11.8 N1: "12 outside the US are left out (US only): --no-us-only"; ``None`` when US only left nothing out."""

    left = counts.get("us_only_left_out")
    if type(left) is not int or left < 1:
        return None
    return f"{left} outside the US {'are' if left != 1 else 'is'} left out (US only): --no-us-only"


def copies_line(counts: Mapping[str, object]) -> str | None:
    """0.1.11.8 N2: "The 40 rows stand for 61 postings: ..."; ``None`` when no row has a copy."""

    rows, all_ = counts.get("matched"), counts.get("postings")
    if type(rows) is not int or type(all_) is not int or all_ <= rows:
        return None
    return f"The {rows} row{'s' if rows != 1 else ''} stand{'s' if rows == 1 else ''} for {all_} postings: the same job posted more than once is one row (--no-collapse lists each)."


def ranking_line(ranking: object) -> str | None:
    """0.1.11.2: "Ranking is still running: 120 of 173 ranked. ..." while the background rank has postings left; else ``None``."""

    if not isinstance(ranking, Mapping) or not ranking.get("in_progress"):
        return None
    rows = [item for item in ranking.get("by_profile") or () if isinstance(item, Mapping)]
    ranked, total = sum(int(item["ranked"]) for item in rows), sum(int(item["total"]) for item in rows)
    return (
        f"Ranking is still running: {ranked} of {total} ranked. The order, and the top 50 by rank, are of what is ranked so far; "
        "a posting not ranked yet comes after the ranked ones."
    )


def render(response: Mapping[str, object]) -> str:
    """A search or "assess these" response as the terminal shows it: counts, the question, one line per posting."""

    labels = {item["profile_id"]: item["label"] for item in response["profiles"]}  # type: ignore[union-attr]
    counts = response["counts"]
    assert isinstance(counts, Mapping)
    lines: list[str] = []
    if response["schema_version"] == ASSESS_SCHEMA_VERSION:
        question, assessed = response.get("question"), response.get("assessed")
        summary = response.get("model_input_summary")
        if isinstance(summary, Mapping):
            lines.extend(summary_lines(summary))  # 0110-10-13: what would be sent, above the question
        if isinstance(question, Mapping):
            lines.append(str(question["text"]))
            running = ranking_line(response.get("ranking"))
            if running:
                lines.append(f"  {running}")
            lines.append("  Nothing was assessed. Yes: run the same command with --yes.")
        elif isinstance(assessed, Mapping):
            if assessed.get("stopped") == STOPPED_CANCELLED:  # 0.1.11.5
                lines.append(f"Cancelled: {assessed.get('not_started', 0)} not started. What finished is kept; the same command again takes the rest.")
            lines.append(f"Assessed {assessed['assessed']} of {assessed['requested']}." + (f" Fetched {assessed['fetched_on_demand']} missing description(s) first." if assessed.get("fetched_on_demand") else ""))
            from .find_jobs.posting_live import closed_skipped_text

            lines.extend(filter(None, [closed_skipped_text(assessed)]))  # 0.1.11.4 R1
            for item in assessed["failed"]:  # type: ignore[union-attr]
                lines.append(f"  not assessed ({item['error_code']}{': ' + str(item['reason']) if item.get('reason') else ''}): {item['job_identity']}")
            lines.extend(failure_lines(assessed["failed"]))  # 0110-10-13: each typed cause once, with its facts and next action
            from .quick_assess import requirements_note_lines

            lines.extend(requirements_note_lines(assessed))  # GUARDFIX
            lines.extend(notice_lines(assessed))  # MODELPIN
            if counts.get("more_after"):  # 0110-10-11
                lines.append(f"{counts['more_after']} more not assessed yet: 50 at a time. Run the same command again for the next 50.")
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
        left_out = applied_left_out_line(counts.get("applied"), response.get("filters"))
        if left_out:
            lines.append(left_out)
        lines.extend(filter(None, [us_only_left_out_line(counts), copies_line(counts)]))
        running = ranking_line(response.get("ranking"))
        if running:
            lines.append(running)
    listing = response["postings"]
    assert isinstance(listing, Mapping)
    filters = response.get("filters")
    by_rank = not isinstance(filters, Mapping) or filters.get("sort", SORT_FIT) == SORT_FIT
    us_only = isinstance(filters, Mapping) and filters.get("us_only") is True
    before: Mapping[str, object] | None = None
    for row in listing["rows"]:  # type: ignore[union-attr]
        divider = divider_text(before, row, counts) if by_rank else None  # 0.1.11.2: "Ranked low (N)", in the rank order only
        if divider:
            lines.append(divider)
        before = row
        tags = ", ".join(str(labels.get(item["profile_id"], item["profile_id"])) for item in row["profiles"])
        gap = f" · {row['minor_gap_text']}" if row.get("minor_gap_text") and not row.get("thin_posting") else ""  # 0110-10-03
        copies = row.get("copies")
        places = f" ({row['locations_text']}; {copies} copies)" if type(copies) is int and copies > 1 else ""
        if us_only and row.get("location_unclear"):
            places += " (unclear location)"  # 0.1.11.8 N1: US only kept it without knowing where it is
        lines.append(f"{row['company_name'] or row['company'] or '?'}: {row['title'] or row['job_identity']}{places} [{tags}] {row['score_text']}{gap}")
        posted = posted_text(row)  # 0110-10-14: "posted 2026-09-24" | "updated ..." | "first seen ..."
        lines.append(f"  {posted + ' · ' if posted else ''}{row['job_identity']}")
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
    "SORTS",
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
    "us_only_setting",
]
