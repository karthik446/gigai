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
  A THIN posting (``fit.is_thin_posting``: a match read from fewer than 4
  requirement rows) comes after all of that: below every other assessed
  posting and every posting not assessed yet, the thin ones among themselves
  in this same order.
- RANKED LOW (0.1.11.2, ``fit.is_ranked_low``): a posting not assessed yet
  whose known rank score is below ``fit.weak_fit_below_rank`` is ORDERED
  lower by that rank, never left out: its row says ``ranked_low: true``,
  ``counts.ranked_low`` counts them and the table prints a plain
  "Ranked low (N)" line above them (:func:`divider_text`). The rank order
  itself puts them after the other ranked postings not assessed yet and
  before the ones not ranked yet; :func:`order_key` needs no rule for it.
- WEAK FIT (0110-10-02, ``fit.py``): a needs-answers posting with few
  requirements met AND a low rank has the state ``weak_fit``. It is left out
  of the rows listed here (``counts.weak_fit`` says how many, the message how
  to list them), it is never one of the postings that "still need attention",
  and its row asks no question (``open_questions`` is empty).
- ONE ROW A JOB (0.1.11.9 NEW1): the same company, title and description posted
  more than once (only the location differs: once per country, or per city)
  is ONE new job, by the Jobs list's rule (``job_copies.copy_key``,
  ``canonical_job.pick_canonical``): the row is its canonical posting (the US
  one, else the earliest posted, then the posting id) and lists every
  location (``copies``, ``locations``, ``locations_text``, ``members``). Every
  count is of JOBS: ``counts.new``, the three questions, ``by_profile`` (a job
  is tagged with the roles that found ANY copy); ``counts.postings`` says how
  many postings the new jobs stand for. A batch asks for one assessment a job.
- US ONLY (0.1.11.9 NEW1, the Jobs list's switch and default:
  ``posting_search.us_only_setting``, ON for a US setup): a posting clearly
  outside the US is left out before the copies become one row, so a job
  posted only outside the US is not new, not counted and never in a batch
  (the old-assessment question too); ``counts.us_only_left_out`` says how
  many JOBS that left out, ``us_only`` in the response ``on``, ``default`` and
  the ``rule``. ``us_only=False`` (``--no-us-only``) shows them; a question's
  yes then carries the same switch.
- ALREADY APPLIED (0.1.11.5): a new posting with an application (applied and
  every later application state, a rejected or withdrawn one too) is left
  out of what is new: of ``counts.new``, the rows, the questions and every
  batch. ``counts.applied`` says how many and the message how to list them
  (``gigai scout jobs list --state applied``).
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
- 50 AT A TIME (0110-10-11, the operator's rule; 0.1.11.2: by rank): every yes
  acts on the TOP 50 BY RANK and never more (:data:`BATCH_LIMIT`,
  :func:`top_ranked_batch`: the best rank score first; postings not ranked
  yet after the ranked ones, the newest first). Each question says
  the real total and the 50 ("re-assess the top 50 by rank of 422? ~50 calls ...
  (372 more after these 50)"), its estimate is the batch's, and it carries
  ``batch`` and ``more_after`` beside the total. After a yes that left some,
  ``assessed`` / ``reassessed`` also carry ``more_after`` and ``next`` (the
  call for the next 50; neither key is there when the batch was all of them).
  One call with both yeses (``assess`` and ``reassess_stale``) still makes
  at most 50 model calls: the new postings first, the old assessments in
  what is left of the 50. ``--process`` runs at most 50 pipeline steps a
  call (the offer then also carries ``steps`` and ``batch``).
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
days. ``first_seen`` is Scout's OWN first sighting (0110-10-11, checked): the
time the sources update that first read the posting wrote its company's index
file (``company_index.observe_company``), never a date of the board's; a later
change to the posting, or a description filled in later, keeps it. And "new"
counts only the postings an active profile matches NOW, while an update's
"N new" counts every posting on every board: so "nothing new" after an update
that stored hundreds is what the design gives, and the message says so. A plain call moves the anchor to the time it read the postings, AFTER
the response is built and checked: a call that fails leaves it where it was.
``peek``, a ``profile_id`` call and the ``yours`` call never move it; nor does a
call made with ``advance=False`` (0110-10-11: the terminal's prompts. The CLI
holds the anchor while it asks and moves it with :func:`settle_anchor` once
the run has done its work, so a run abandoned at a prompt consumes nothing). ``since``
selects a window again (the yes, or the ``yours`` call, after a call that
already moved the anchor). ``mark_all_seen`` moves the same anchor.

AN ASKING CALL IS A PREVIEW (0.1.10.11 NA, :func:`is_preview`): a response
that carries the assess-new question (``status: "ask"``) never moves the
anchor, with a terminal or without one (``gigai scout new --json``, a pipe).
It used to move it as soon as the response was built, so the next step the
docs give, a bare ``gigai scout new --yes --json``, found "nothing new" and
assessed nothing. The anchor moves when the question has been ANSWERED, which
is every response that is not a preview: the yes (``assess=True``, ``--yes``),
the no (``assess=False``, ``--no-assess``: what ``question.no`` names), and a
response that had nothing to ask. Asked twice, a preview shows the same
postings (before the first answer ever, "new" stays the last 7 days).

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
from .evaluated_models import notice_lines
from .find_jobs.job_copies import US_ONLY_RULE
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
#: 0110-10-11 (the operator's rule): every batch offer and every batch command acts on 50 at a time, never more (0.1.11.2: the top 50 by rank).
BATCH_LIMIT = 50
UNMET_SHOWN = 4
EVIDENCE_SHOWN = 3
DESCRIPTION_CHARS = 400
PIPELINE_COMMAND = "gigai scout new --process"
#: 0110-10-11: what "nothing new" means, and why it can follow an update that stored hundreds of new postings.
NOTHING_NEW_MEANS = "no posting that matches your profiles was first stored by Scout"
UPDATE_COUNTS_ALL = "(A sources update counts every new posting on every board, whatever its title.)"
WEAK_FIT_COMMAND = "gigai scout jobs list --state weak_fit"
#: 0.1.11.5: how to list the postings with an application, which every other list leaves out.
APPLIED_COMMAND = "gigai scout jobs list --state applied"
NO_US_ONLY_COMMAND = "gigai scout new --no-us-only"

#: States that still want something from the user (a posting Scout labelled recommended is left out by the query).
_ATTENTION_STATES = ("needs_answers", "matched", "assessed", "tailored", "not_assessed")
_NOT_ASSESSED = "not_assessed"
_NOT_STARTED = "not_started"
#: 0.1.11.5 (ASSESS-01): ``stopped`` of a batch someone cancelled; what finished is kept, the rest was never started.
STOPPED_CANCELLED = "cancelled"
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
    # 0.1.11.9 NEW1: one row a job: the locations of its copies.
    "/rows/*/locations/*": PUBLIC_UNTRUSTED,
    "/rows/*/locations_text": PUBLIC_UNTRUSTED,
    "/rows/*/members/*/location": PUBLIC_UNTRUSTED,
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

    # 0.1.11.2: a thin posting (fewer than 4 requirement rows, or none about the job) has no share to show: its rank.
    if row.reqs_total and not fit_rules.is_thin_posting(row.state, row.reqs_total):
        return round(100 * (row.reqs_met or 0) / row.reqs_total), "assessment"
    if row.rank_score is not None:
        return row.rank_score, "rank"
    return None, None


GROUP_CURRENT = "current"
GROUP_STALE = "stale"
GROUP_NOT_ASSESSED = "not_assessed"
_GROUP_ORDER = {GROUP_CURRENT: 0, GROUP_STALE: 1, GROUP_NOT_ASSESSED: 2}
#: Inside the assessed groups: matched, needs answers, anything else, weak fit, not a match, and last a thin posting
#: (0.1.11.2: a match on no requirement at all; ``pipeline.store._POSTING_ORDER`` has the same numbers).
_VERDICT_ORDER = {"matched": 0, "needs_answers": 1, fit_rules.WEAK_FIT: 3, "not_a_match": 4, fit_rules.THIN_POSTING: 5}
_VERDICT_WORDS = {
    fit_rules.THIN_POSTING: fit_rules.THIN_LABEL,
    "matched": "Matched", "needs_answers": "Needs your answers", fit_rules.WEAK_FIT: "Weak fit", "not_a_match": "Not a match",
    "has_gap": "Has a gap",  # 0.1.11 N3 (OD1): sorted with "anything else", after Needs your answers
}
_STALE_WORDS = {
    "posting_changed": "posting changed",
    "older_prompt": "older prompt",
    "settings_changed": "settings changed",
    "story_bank_changed": "answers changed",
    "resume_changed": "resume changed",
}
_RECOMMENDED = "recommended"
#: 0110-10-14: what a board means by the date stored as ``published_at`` (``ats_board_clients.PUBLISHED_FIELDS``):
#: Greenhouse's ``first_published``, Lever's ``createdAt`` and Ashby's ``publishedAt`` are the day the posting went up.
#: Served beside the date (``published_kind``) so a last change is never shown as "posted": a provider that can only
#: give its last change is listed here as ``updated``, and one nobody listed reads ``updated`` too.
#: 0.1.11.8: the six new feeds date a posting the same way (``providers.published_kinds()`` is this table built from the
#: registry; the test compares them). Pinpoint's feed carries no date at all, so its postings are undated and its
#: entry is never read; it is listed so that every board kind says what its date would mean.
PUBLISHED_KINDS = {
    "greenhouse": "posted",
    "lever": "posted",
    "ashby": "posted",
    "workable": "posted",
    "rippling": "posted",
    "gem": "posted",
    "recruitee": "posted",
    "pinpoint": "posted",
    "breezy": "posted",
}


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


def order_key(row: PostingRecord) -> tuple[int, int, int, int, int, int]:
    """What the grid is ordered by before recency (0110-8-04, 0110-10-02); ``pipeline.store._POSTING_ORDER`` is the same key in SQL.

    Thin postings last (0.1.11.2: a "fit 100%" read from 2 requirements says
    too little to list above a real match or a ranked posting). Then the
    freshness group, the Scout label ``recommended`` first inside it, the
    verdict, then the real fit: the fit number (the share of requirements
    met, must-haves weighted), then the rank score. A not-assessed row has
    no fit number, so its group is ordered by rank.
    """

    group = sort_group(row)
    verdict = 0 if group == GROUP_NOT_ASSESSED else _VERDICT_ORDER.get(row.state, 2)
    found = fit_of(row)
    return (
        1 if fit_rules.is_thin_posting(row.state, row.reqs_total) else 0,
        _GROUP_ORDER[group], 0 if row.label == _RECOMMENDED else 1, verdict,
        -(found if found is not None else -1), -(row.rank_score if row.rank_score is not None else -1),
    )


def in_order(shown: Iterable[tuple[Sequence[PostingRecord], PostingRecord]]) -> list[tuple[Sequence[PostingRecord], PostingRecord]]:
    """``(tags, row)`` pairs in the grid's order: :func:`order_key`, then the newest first, then the URL."""

    ordered = sorted(shown, key=lambda pair: pair[1].job)
    ordered.sort(key=lambda pair: pair[1].first_seen, reverse=True)  # stable: equal stamps keep the URL order
    ordered.sort(key=lambda pair: order_key(pair[1]))
    return ordered


def divider_text(before: Mapping[str, object] | None, row: Mapping[str, object], counts: Mapping[str, object]) -> str | None:
    """0.1.11.2: the plain divider line a list in rank order prints above ``row`` (``before``: the row above it, if any).

    "Ranked low (N)" above the first ranked-low row (``row["ranked_low"]``; N is ``counts["ranked_low"]``), and
    "Not ranked yet" where the postings with no rank score start right below them. A divider only says where a part
    of the list starts: every row under it is a row like any other.
    """

    low, was_low = row.get("ranked_low") is True, before is not None and before.get("ranked_low") is True
    if low and not was_low:
        return f"Ranked low ({counts.get('ranked_low') or 0})"
    if was_low and not low and row.get("state", _NOT_ASSESSED) == _NOT_ASSESSED and row.get("rank_score") is None:
        return "Not ranked yet"
    return None


def stale_label(row: PostingRecord) -> str | None:
    """"old assessment: older prompt" for a stale row; ``None`` for a current or a not-assessed one."""

    if row.state == _NOT_ASSESSED or row.stale_code is None:
        return None
    return f"old assessment: {_STALE_WORDS.get(row.stale_code, row.stale_code.replace('_', ' '))}"


def posting_dates(row: PostingRecord, text: PostingText | None = None) -> dict[str, object]:
    """A posting's dates, each under its own name (0110-10-14).

    ``published_at`` is the BOARD's date, the one the 7 / 30 days window judges (``None`` when the board gives none),
    and ``published_kind`` what the board means by it (:data:`PUBLISHED_KINDS`; a board kind this table does not know
    is ``updated``, the weaker claim). ``updated_at`` is the board's last change to the posting (``None`` when it
    gives none, or when ``text``, the posting as the index holds it, was not read): a second date, never the first.
    ``first_seen_at`` is when Scout first stored the posting: what "new since" judges.
    """

    kind = None if row.published_at is None else PUBLISHED_KINDS.get(row.board.partition(":")[0], "updated")
    return {
        "published_at": row.published_at, "published_kind": kind,
        "updated_at": text.updated_at if text is not None else None, "first_seen_at": row.first_seen,
    }


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
        # 0.1.11.2: a match read from fewer than 4 requirement rows says so instead of "Matched · fit 100%" (``fit.py``).
        thin = fit_rules.is_thin_posting(row.state, row.reqs_total)
        verdict = fit_rules.THIN_LABEL if thin else _VERDICT_WORDS.get(row.state, "Assessed")
        old = stale_label(row)
        parts = [f"{verdict} ({old})" if old else verdict]
        found = None if thin else fit_of(row)
        if found is not None:
            parts.append(f"fit {found}%")
        if row.reqs_total and row.state != fit_rules.THIN_POSTING:  # the thin STATE has no row about the job to count
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


def _shown(group: Sequence[PostingRecord], profile_id: str | None = None) -> PostingRecord:
    """The row a job is shown by: its best tag's (``match_rank`` 1), whatever role a call filters by.

    0.1.11.9: a role TAGS a job, it selects no record. A job has one assessment and one resume, so every tag's row
    carries the same verdict; what differs is each role's rank score, and the best tag is the best-ranked one. So the
    state shown is the job's, and its weak-fit state is the best rank's. ``profile_id`` is accepted and not used:
    until 0.1.11.9 a call that named one role showed the job by that role's own row.
    """

    del profile_id
    return group[0]


def tag_owner(group: Sequence[PostingRecord], row: PostingRecord, only: str | None) -> str:
    """The role whose TAG a per-tag fact of a served row is about: the one role the call filters by when it tags the
    job, else the best tag's. (A filter never changes the job's assessment or state: those are the job's.)"""

    return only if only is not None and any(item.profile_id == only for item in group) else row.profile_id


def tags_json(group: Sequence[PostingRecord], labels: Mapping[str, str] | None = None) -> list[dict[str, object]]:
    """0.1.11.9: the roles that TAG a job (the saved searches that found it), best first, each with its own rank score.

    Derived from the read model's ``(job, role)`` rows when a response is built: nothing is stored per job. ``label``
    is the role's name (its id when the caller has no names at hand).
    """

    names = labels or {}
    return [
        {"profile_id": item.profile_id, "label": names.get(item.profile_id, item.profile_id), "match_rank": item.match_rank, "rank_score": item.rank_score}
        for item in group
    ]


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
    labels: Mapping[str, str] | None = None,
) -> dict[str, object]:
    """One posting of the grid. Posting text and what a model derived from it only: nothing of the user's.

    0.1.11.9: ``tags`` are the roles that found the job (:func:`tags_json`; ``labels``: role id -> name).
    ``profile_id`` is the best tag's id and ``profiles`` the same tags as before 0.1.11.9: kept for a reader that
    knows them, they select nothing.
    """

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
        **posting_dates(row, text),  # 0110-10-14: published_at (the day it went up), published_kind, updated_at, first_seen_at
        "removed_at": row.removed_at,
        "profile_id": row.profile_id,
        "tags": tags_json(group, labels),
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
        # 0.1.11.2: a match read from fewer than 4 requirement rows (``fit.is_thin_posting``): "thin posting", never "Matched".
        "thin_posting": fit_rules.is_thin_posting(row.state, row.reqs_total),
        # 0110-10-02: the one fit number of the row (must-haves weighted); null when not assessed, and for a thin
        # posting (``thin_posting`` true: not enough requirements to score, so no percentage anywhere).
        "fit": None if fit_rules.is_thin_posting(row.state, row.reqs_total) else fit_of(row),
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
    live: LiveBatch | None = None, progress: "BatchProgress | None" = None, estimate_seconds: float | None = None,
) -> dict[str, object]:
    """Assess each ``(job, profile)`` through the job page's path, from the stored posting text.

    0.1.11.5 (ASSESS-01): the batch can be CANCELLED (``pipeline.busy.request_cancel``, from any process). No
    further model call starts; the calls in flight finish and every result is stored as usual. ``stopped`` is then
    :data:`STOPPED_CANCELLED`, the postings never started are counted in ``not_started`` (they are not failures) and
    ``assessed`` is what finished. The marker says how far the batch is (``pipeline.busy.batch_status``);
    ``estimate_seconds`` is what the question estimated for it. A batch interrupted from the keyboard ends the model
    processes it started (each is its own session and would otherwise be left running).

    0110-8-02: a posting with NO stored description is fetched on demand, ONE request for that posting alone
    (``job_input.fetch_missing_description``: public board API, the existing body cap, paced like the board clients), before it is
    given up on. ``fetched_on_demand`` counts the descriptions that way; a posting whose text cannot be had stays
    ``job_text_unavailable`` (or ``job_fetch_failed``) with a named ``reason``. 0110-10-11: a posting the model
    answered for and a guard refused (``posting_requirements_unreadable``) carries the guard as its ``reason``
    (``quick_assess.POSTING_UNREADABLE_REASONS``).

    ``live``: the caller already marked the batch live (``pipeline.busy``,
    "assess these") and this keeps its marker fresh; without it the batch is
    marked here for as long as it runs.
    """

    from .find_jobs import job_input
    from .find_jobs.assess_all import FATAL_CODES, assess_concurrency
    from .find_jobs.assess_contracts import ORIGIN_JOB_PAGE, AssessJobInput, AssessRequest, AssessResumeInput, ResolvedJob
    from ..workpad import committed_read_cache
    from .find_jobs.market_acquisition import AcquireLimits
    from .assessment_basis import assessment_notice
    from .find_jobs.job_key import job_key
    from .find_jobs.posting_live import ERROR_POSTING_CLOSED, jobs_liveness
    from .quick_assess import ERROR_NOT_STORED, QuickAssessError, run_quick_assessment

    closed: set[str] = set()
    marked = nullcontext(live) if live is not None else assess_batch(home_root, target)

    failed: list[dict[str, object]] = []
    stop: list[str] = []
    fetched: list[str] = []
    thin: dict[str, dict[str, object]] = {}  # GUARDFIX: the assessments stored with a requirements note, by job
    notices: dict[str, dict[str, object]] = {}  # MODELPIN: one per model that is not an evaluated one, by model
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
        job, profile_id = pair
        if not stop and live.cancelled():
            stop.append(STOPPED_CANCELLED)  # 0.1.11.5: no further model call; the ones in flight finish
        begun = not stop and job not in closed
        if begun:
            live.started(profile_id)
        done: list[tuple[str, str | None] | None] = []
        try:
            done.append(assess_one(pair))
            return done[0]
        finally:
            unstarted = bool(done) and done[0] is not None and done[0][0] == _NOT_STARTED
            live.finished(job, assessed=bool(done) and done[0] is None, counted=not unstarted, was_started=begun)
            live.beat()
            if progress is not None:
                progress.done()

    def assess_one(pair: tuple[str, str]) -> tuple[str, str | None] | None:
        job, profile_id = pair
        if job in closed:
            return (ERROR_POSTING_CLOSED, None)  # 0.1.11.4 R1: its board no longer lists it; no model call
        if stop:
            return (_NOT_STARTED, None)
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
            return (exc.code, exc.reason)  # 0110-10-11: which rule refused, for a code two rules share
        # 0110-8-09: "assessed" means a record the grid will read, under THIS job and profile; anything else is a named failure.
        # 0.1.11.9 RB2: a copy of a job posted once per country is assessed as the job: its record names the posting
        # the job's records are kept under (``job_key``), and every copy's row reads it.
        if (
            stored.job.job_identity not in (job, job_key(home_root, target, job, board=text.board))
            or stored.resume.profile_id != profile_id or not Path(stored.stored_path or "").is_file()
        ):
            return (ERROR_NOT_STORED, None)
        if stored.requirements_note is not None:
            thin[job] = {"job_identity": job, "profile_id": profile_id, "text": stored.requirements_note}
        notice = assessment_notice(stored)
        if notice is not None:
            notices[notice.model] = notice.to_json()
        return None

    # PL5: the batch is live work the pipeline's runner yields to (DESIGN 7), like an "assess all" batch.
    try:
        with marked as live:
            # 0.1.11.4 R1: only the postings of THIS batch are asked about (one request each, none within the hour);
            # a closed one is marked removed and not assessed. A board that does not answer changes nothing.
            closed.update(job for job, answer in jobs_liveness(home_root, target, [job for job, _profile in pairs]).items() if answer.closed)
            live.begin([job for job, _profile in pairs], estimate_seconds=estimate_seconds)
            if progress is not None:
                progress.start()
            with ThreadPoolExecutor(max_workers=max(1, assess_concurrency()), thread_name_prefix="scout-new-assess") as pool:
                try:
                    outcomes = list(pool.map(one, pairs))
                except KeyboardInterrupt:
                    # 0.1.11.5: Ctrl-C reaches this thread only. Nothing further starts, and the model processes this
                    # process started are ended (each is its own session: the terminal's signal never reaches them).
                    from ..adapters.process import terminate_children

                    stop.append("interrupted")
                    terminate_children()
                    raise
    finally:
        for client in clients:
            client.close()  # type: ignore[attr-defined]
    cancelled = bool(stop) and stop[0] == STOPPED_CANCELLED
    not_started = 0
    for (job, profile_id), outcome in zip(pairs, outcomes):
        if outcome is not None:
            code, reason = outcome
            if cancelled and code == _NOT_STARTED:
                not_started += 1  # a cancelled batch: never started is not a failure
                continue
            failure: dict[str, object] = {"job_identity": job, "profile_id": profile_id, "error_code": code}
            if reason is not None:
                failure["reason"] = reason
            failure.update(cause_fields(code))  # 0110-10-13: did a model call start, may it have used tokens, what next
            failed.append(failure)
    batch: dict[str, object] = {
        "requested": len(pairs), "assessed": len(pairs) - len(failed) - not_started, "failed": failed,
        "stopped": stop[0] if stop else None, "fetched_on_demand": len(fetched),
    }
    if cancelled:
        batch["not_started"] = not_started  # omitted unless the batch was cancelled
    if closed:
        batch["closed_skipped"] = len({job for job, _profile in pairs if job in closed})  # omitted when no posting of the batch was closed
    if thin:
        batch["requirements_notes"] = [thin[job] for job in sorted(thin)]  # omitted when every answer read enough requirements
    if notices:
        batch["model_notices"] = [notices[model] for model in sorted(notices)]  # omitted when every assessment was by an evaluated model
    return batch


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


def _ranking(
    store: PipelineStore, views: Sequence[ProfileView], home_root: Path, target: Path, *, now: datetime | None = None,
) -> dict[str, object]:
    """How far the background rank is, per active profile: ``ranked`` of ``total`` live matches the rank lane will rank.

    0.1.11.2: the lane ranks only the postings that went up in the last :data:`FIRST_USE_DAYS` days (the rule of
    ``pipeline.rank_lane._unranked``: :func:`batch_date` after the window's start). An older posting with no score
    stays "not ranked yet" for good, so it is not counted: ``total`` is the ranked postings plus the unranked ones
    inside the window, and ``in_progress`` ends when the lane has nothing left to rank.

    ``stale_resume`` (0.1.11.2, ``postings.rank_resume_stale``): a profile's resume changed after its postings were
    ranked (per profile, and true at the top when it is for any). The scores are stored per resume digest, so those
    postings read "not ranked yet" until they are ranked again: the Jobs page offers "Re-rank". Never a model call.
    """

    from .pipeline.rank_lane import _window_start, rank_status

    since = _window_start((now or datetime.now(UTC)).astimezone(UTC))  # the lane's own window and its own rule
    stale = postings.rank_resume_stale(store, views)
    by_profile = []
    for view in views:
        # 0.1.11.3: both counts are of the window the line names ("last 7 days"), so "Re-rank" asks about the same
        # postings: a ranked posting older than the window is no longer counted.
        ranked, total = store.rank_counts(view.profile_id, since)  # two counts: a poll reads no rows
        by_profile.append({"profile_id": view.profile_id, "ranked": ranked, "total": total, "stale_resume": stale[view.profile_id]})
    try:
        enabled = bool(rank_status(home_root, target)["enabled"])
    except (PipelineStoreError, OSError, ValueError):  # a display read: a setting that cannot be read is "not ranking"
        enabled = False
    return {
        "enabled": enabled,
        "in_progress": enabled and any(item["ranked"] < item["total"] for item in by_profile),
        "window_days": FIRST_USE_DAYS,
        "stale_resume": any(stale.values()),
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
    # 0110-10-11: one --process runs at most 50 steps; the offer says so when more than that wait.
    capped = len(waiting) > BATCH_LIMIT
    ask = f"process the next {BATCH_LIMIT} of {len(waiting)} steps now? ~{calls} calls in all, at most {BATCH_LIMIT} a run" if capped else f"process now? {_calls(calls)}"
    return {
        "waiting": jobs,
        "awaiting_approval": len(gated),
        "approvals": approvals,
        "est_calls": calls,
        **({"steps": len(waiting), "batch": BATCH_LIMIT} if capped else {}),
        "command": PIPELINE_COMMAND,
        "text": f"{jobs} waiting{need}, {ask}",
    }


def process_waiting(home_root: Path, target: Path, *, config: object | None = None, decided_by: str = "operator") -> dict[str, object]:
    """The yes to the pipeline offer: approve what waits for an approval, then run the waiting steps once.

    ``{approved: [{id, jobs}], drain: scout-pipeline-drain:1}``: ids, codes
    and counts. The drain is the pipeline's own (``run_once``): switched off,
    or yielding to live work, it runs nothing and says so; the daily cap of
    model calls holds, so a job over it waits for the next day. 0110-10-11: one
    call runs at most :data:`BATCH_LIMIT` steps; the rest wait for the next call
    (or the background runner, under the same daily cap).
    """

    from .pipeline.runner import run_once
    from .pipeline.triggers import approve_all

    approved = approve_all(home_root, target, decided_by=decided_by)
    drain = run_once(home_root, target, config=config, max_steps=BATCH_LIMIT)
    return {
        "approved": [{"id": item["id"], "jobs": item["decided_jobs"]} for item in approved],
        "drain": drain.to_json(),
    }


# --- the response ---------------------------------------------------------------------------


def _when(value: str) -> str:
    """An instant as a person reads it, in the local time zone: ``Tue 14:02``-like, with the date."""

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone().strftime("%a %d %b %H:%M")


def _calls(count: object) -> str:
    """``~1 call`` / ``~12 calls``: an estimate's call count as a question says it."""

    return f"~{count} call{'' if count == 1 else 's'}"


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


def _us_flag(us_only: bool | None) -> str:
    """`` --us-only`` / `` --no-us-only`` when the call said either; ``""`` for the setup's default."""

    return "" if us_only is None else (" --us-only" if us_only else " --no-us-only")


def _answers(since: str, profile_id: str | None, *, flag: str, body: Mapping[str, object], us_only: bool | None = None) -> dict[str, object]:
    """How a question is answered: the CLI command and the API call, for the same window (and profile).

    ``us_only`` (0.1.11.9 NEW1): what the call that asked said of US only, so the yes selects what the question counted.
    """

    profile = f" --profile {profile_id}" if profile_id is not None else ""
    api_body = {
        **body, "since": since, **({"profile_id": profile_id} if profile_id is not None else {}),
        **({"us_only": bool(us_only)} if us_only is not None else {}),
    }
    return {
        "cli": f"gigai scout new {flag} --since {since}{profile}{_us_flag(us_only)}",
        "api": {"method": "POST", "path": "/api/new", "body": api_body},
    }


def _question(
    pairs: Sequence[tuple[str, str]], new_count: int, views: Sequence[ProfileView], since: str, *, home_root: Path, target: Path,
    profile_id: str | None = None, low_rank: int = 0, us_only: bool | None = None,
) -> tuple[dict[str, object], str]:
    """The approval question: ids and numbers, and the sentence (it names the profile tags).

    ``low_rank`` (0110-10-02): how many new postings the assess threshold holds back; they are their own question.
    """

    size = min(len(pairs), BATCH_LIMIT)  # 0110-10-11, 0.1.11.2: a yes assesses the top 50 by rank, never more
    model, found = _estimate(size, home_root=home_root, target=target)
    by_profile = _by_profile(pairs, views)
    labels = {view.profile_id: view.label for view in views}
    named = ", ".join(f"{labels[item['profile_id']]} {item['count']}" for item in by_profile)  # type: ignore[index]
    plural = "s" if new_count != 1 else ""
    across = f" across {len(by_profile)} profiles ({named})" if len(by_profile) > 1 else (f" ({named})" if named else "")
    separate = f"{low_rank} low-ranked {'one is' if low_rank == 1 else 'ones are'} a separate question"
    if size < len(pairs):
        ask = f"Assess {_newest_words(size, len(pairs))} not assessed yet{f' ({separate})' if low_rank else ''}?"
    elif low_rank:
        ask = f"Assess {len(pairs)} of them ({separate})?"
    else:
        ask = "Assess them?" if len(pairs) == new_count else f"Assess the {len(pairs)} not assessed yet?"
    sentence = (
        f"{new_count} new job{plural}{across}. {ask} {_calls(found['calls'])}{_tokens(found['tokens'])}"
        f"{_more_words(size, len(pairs))}"
    )
    question = {
        "kind": "assess_new",
        "new": new_count,
        "to_assess": len(pairs),
        "batch": size,
        "more_after": len(pairs) - size,
        "low_rank_skipped": low_rank,
        "by_profile": by_profile,
        "model_target": model,
        "estimate": found,
        "yes": _answers(since, profile_id, flag="--yes", body={"assess": True}, us_only=us_only),
        "no": _answers(since, profile_id, flag="--no-assess", body={"assess": False}, us_only=us_only),
        "text": sentence,
    }
    return question, sentence


def _stale_question(
    pairs: Sequence[tuple[str, str]], views: Sequence[ProfileView], since: str, *, home_root: Path, target: Path,
    profile_id: str | None = None, low: Sequence[tuple[str, str]] = (), min_rank: int = 0, us_only: bool | None = None,
) -> dict[str, object]:
    """0110-8-08: the postings that have only an old assessment, as their own question. Never answered by the assess-new yes.

    0110-10-02: ``low`` are the ones below the assess threshold. They are left out of the yes and counted
    (``low_rank_skipped``); when ONLY they are left, the question is about them and its yes names ``--include-low-rank``.
    """

    asked = pairs or low
    size = min(len(asked), BATCH_LIMIT)  # 0110-10-11, 0.1.11.2: a yes re-assesses the top 50 by rank, never more
    model, found = _estimate(size, home_root=home_root, target=target)
    have = "has" if len(asked) == 1 else "have"
    those = "that one" if len(asked) == 1 else "those"
    newest = _newest_words(size, len(asked))
    cost = f"{_calls(found['calls'])}{_tokens(found['tokens'])}{_more_words(size, len(asked))}"
    if pairs:
        flag, body = "--reassess-stale", {"assess": False, "reassess_stale": True}
        text = f"{len(pairs)} {have} only an old assessment; re-assess{' ' + newest if newest else ''}? {cost}"
        if low:
            more = "is" if len(low) == 1 else "are"
            text += f" ({len(low)} more {more} low-ranked, rank below {min_rank}, and left out; add --include-low-rank to include them)"
    else:
        flag, body = "--reassess-stale --include-low-rank", {"assess": False, "reassess_stale": True, "include_low_rank": True}
        text = (
            f"{len(low)} low-ranked (rank below {min_rank}) {have} only an old assessment and {'is' if len(low) == 1 else 'are'} left out; "
            f"re-assess {f'the top {size} by rank of ' if size < len(low) else ''}{those} too? {cost}"
        )
    return {
        "kind": "reassess_stale",
        "to_reassess": len(pairs),
        "batch": size,
        "more_after": len(asked) - size,
        "low_rank_skipped": len(low),
        "by_profile": _by_profile(asked, views),
        "model_target": model,
        "estimate": found,
        "yes": _answers(since, profile_id, flag=flag, body=body, us_only=us_only),
        "text": text,
    }


def _low_rank_question(
    low: Sequence[tuple[str, str]], views: Sequence[ProfileView], since: str, setting: fit_rules.FitSetting, *, home_root: Path,
    target: Path, profile_id: str | None = None, us_only: bool | None = None,
) -> dict[str, object]:
    """0110-10-02: the new postings below the assess threshold, as their own question. Never answered by a plain yes."""

    size = min(len(low), BATCH_LIMIT)  # 0110-10-11, 0.1.11.2: its batch is the top 50 by rank too
    model, found = _estimate(size, home_root=home_root, target=target)
    one = len(low) == 1
    return {
        "kind": "assess_low_rank",
        "skipped": len(low),
        "batch": size,
        "more_after": len(low) - size,
        "min_rank": setting.assess_min_rank,
        "by_profile": _by_profile(low, views),
        "model_target": model,
        "estimate": found,
        "yes": _answers(since, profile_id, flag="--yes --include-low-rank", body={"assess": True, "include_low_rank": True}, us_only=us_only),
        "text": (
            f"{len(low)} low-ranked {'one is' if one else 'ones are'} skipped (rank below {setting.assess_min_rank}); "
            f"assess {f'the top {size} by rank of ' if size < len(low) else ''}{'that' if one else 'those'} too? {_calls(found['calls'])}{_tokens(found['tokens'])}"
            f"{_more_words(size, len(low))}"
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


def batch_date(row: PostingRecord) -> str:
    """What "newest" means for a batch: the day the posting went up; for a board that gives none, when Scout first saw it."""

    return row.published_at or row.first_seen


def newest_batch(
    pairs: Sequence[tuple[str, str]], dates: Mapping[tuple[str, str], str], *, limit: int | None = None,
) -> tuple[list[tuple[str, str]], int]:
    """0110-10-11: ``(the newest ``limit`` of ``pairs``, how many are left for later)``. ``dates`` is :func:`batch_date` per pair.

    ``limit`` is :data:`BATCH_LIMIT` unless given. The newest first; postings of one instant in the order of their
    URL, so the same call picks the same batch.
    """

    limit = BATCH_LIMIT if limit is None else limit
    ordered = sorted(pairs)
    ordered.sort(key=lambda pair: dates.get(pair, ""), reverse=True)  # stable
    return ordered[:limit], max(0, len(ordered) - limit)


def top_ranked_batch(
    pairs: Sequence[tuple[str, str]], ranks: Mapping[tuple[str, str], int | None], dates: Mapping[tuple[str, str], str], *,
    limit: int | None = None,
) -> tuple[list[tuple[str, str]], int]:
    """0.1.11.2: ``(the top ``limit`` of ``pairs`` BY RANK, how many are left for later)``: what a yes assesses.

    The best rank score first; postings with one score the newest first (:func:`batch_date`), then by URL, so the
    same call picks the same batch. A posting not ranked yet comes after every ranked one, the newest first: with
    nothing ranked this is :func:`newest_batch`. ``limit`` is :data:`BATCH_LIMIT` unless given.
    """

    limit = BATCH_LIMIT if limit is None else limit
    ordered = sorted(pairs)
    ordered.sort(key=lambda pair: dates.get(pair, ""), reverse=True)  # stable
    ordered.sort(key=lambda pair: (0, -score) if (score := ranks.get(pair)) is not None else (1, 0))  # stable
    return ordered[:limit], max(0, len(ordered) - limit)


def _newest_words(size: int, total: int) -> str:
    """"the top 50 by rank of 422" when a batch is less than all of them; ``""`` when it is all."""

    return f"the top {size} by rank of {total}" if size < total else ""


def _more_words(size: int, total: int) -> str:
    return f" ({total - size} more after these {size})" if size < total else ""


def _current(row: PostingRecord) -> bool:
    return row.state != _NOT_ASSESSED and row.stale_code is None


def _new_pairs(groups: Mapping[str, Sequence[PostingRecord]], profile_id: str | None) -> list[tuple[str, str]]:
    """0110-8-01: the new postings nothing has assessed, ONE pair a job: ``(job, the role recorded on its assessment)``.

    0.1.11.9: that role is the job's best tag. ``profile_id`` only filters ``groups`` (the caller's read): a job
    that is assessed is never a pair again because another role tags it too.
    """

    del profile_id
    pairs = []
    for group in groups.values():
        row = _shown(group)
        if all(item.state == _NOT_ASSESSED for item in group):
            pairs.append((row.job, row.profile_id))
    return sorted(pairs)


def _one_a_job(rows: Iterable[PostingRecord], home_root: Path, target: Path) -> list[PostingRecord]:
    """0.1.11.9 RB2: ``rows`` with ONE row a job: the copies of a job posted once per country share one assessment
    (``find_jobs.job_key``), so a batch asks for it once. The posting the job's records are kept under is the one
    kept when it is among them, else the first by address. Rows of jobs with no copy pass as they are."""

    from .find_jobs.job_key import job_key

    kept: dict[str, PostingRecord] = {}
    for row in sorted(rows, key=lambda item: item.job):
        key = job_key(home_root, target, row.job, board=row.board)
        if key not in kept or row.job == key:
            kept[key] = row
    return list(kept.values())


_Pair = tuple[Sequence[PostingRecord], PostingRecord]


class _JobRows:
    """0.1.11.9 NEW1: postings as ONE ROW A JOB, by the Jobs list's own code (``posting_search._Selection``).

    ``rows`` are ``(the row's tags, its canonical posting's row)`` in the order given (a job stands where its first
    posting stood); ``left_out`` is how many JOBS US only left out (every posting of the job is clearly outside the
    US); ``applied`` how many jobs an application on any copy left out; ``postings`` how many postings ``rows`` stand
    for. ``json(row, text)`` is the row's additive keys (``copies``, ``locations``, ``locations_text``, ``members``,
    ``location_unclear``). ``profile_id`` keeps the jobs that role tags (any copy), as the list's role filter does.
    """

    def __init__(
        self, home_root: Path, pairs: Sequence[_Pair], *, us_only: bool, applications: Mapping[str, Mapping[str, object]],
        profile_id: str | None = None,
    ) -> None:
        from . import posting_search as lists
        from .find_jobs.job_copies import PLACE_OTHER

        def collapsed(of: Sequence[_Pair]) -> tuple[list[_Pair], "lists._Selection", dict[str, dict[str, object]]]:
            # The list's rule lives on its selection (``_collapsed`` fills ``copies``, ``copies_json`` reads ``places``):
            # the same two methods are used here on a selection that holds nothing else, so the rule is written once.
            view = object.__new__(lists._Selection)
            view.copies, view.places = {}, places
            held = {job: dict(found) for job, found in applications.items()}
            rows = view._collapsed(of, places, held) if of else []
            if profile_id is not None:
                rows = [(group, row) for group, row in rows if any(item.profile_id == profile_id for item in group)]
            return rows, view, held

        places = lists._places(home_root, [row for _group, row in pairs]) if pairs else {}
        every = list(pairs)
        kept = [pair for pair in every if pair[1].job not in places or places[pair[1].job].place != PLACE_OTHER] if us_only else every
        rows, self._view, held = collapsed(kept)
        self.left_out = len(collapsed(every)[0]) - len(rows) if len(kept) != len(every) else 0
        self.applied = sum(1 for _group, row in rows if row.job in held)
        self.rows: list[_Pair] = [(group, row) for group, row in rows if row.job not in held]
        self.postings = sum(len(self._view.copies.get(row.job, (None,))) for _group, row in self.rows)

    def json(self, row: PostingRecord, text: PostingText | None) -> dict[str, object]:
        return self._view.copies_json(row, text)


def _kept_by_us_only(home_root: Path, rows: Sequence[PostingRecord]) -> list[PostingRecord]:
    """``rows`` without the postings clearly outside the US (the list's US only: one Scout cannot place stays)."""

    from . import posting_search as lists
    from .find_jobs.job_copies import PLACE_OTHER

    places = lists._places(home_root, rows) if rows else {}
    return [row for row in rows if row.job not in places or places[row.job].place != PLACE_OTHER]


def _applications(home_root: Path, applied: Iterable[str]) -> dict[str, dict[str, object]]:
    """Each applied job AND its copies (``job_key.copies_of``: only the boards of the jobs applied to are read)."""

    from .find_jobs.job_key import copies_of

    found: dict[str, dict[str, object]] = {}
    for job in sorted(applied):
        for copy in copies_of(home_root, job):
            found.setdefault(copy, {})
    return found


def _stale_rows(store: PipelineStore, profile_id: str | None) -> list[PostingRecord]:
    """0110-8-08: the live postings with an assessment and no CURRENT one under any matching profile, each as the row of the profile whose old assessment is shown."""

    rows = []
    # 0.1.11.9: ``profile_id`` filters by tag; a job is judged by EVERY tag's row (one current row is a current job).
    found = store.postings(profile_id=profile_id)
    if profile_id is not None and found:
        found = store.postings(jobs={row.job for row in found})
    for group in _grouped(found).values():
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


def is_preview(response: Mapping[str, object]) -> bool:
    """0.1.10.11 NA, the ONE place that says a question is still open: this response is a PREVIEW.

    A response that carries the assess-new question (``status: "ask"``) shows
    what is new and asks; nothing is decided yet, so it must not consume
    "new": the anchor stays (``anchor.advances`` is false), and the same call
    again, or a bare ``--yes`` after it, measures from the same time. Every
    other response is ANSWERED and moves the anchor as a plain call does: the
    yes that assessed, the explicit no (``assess=False``, ``--no-assess``) and
    a response with nothing to ask. ``stale_question``, ``low_rank_question``
    and the pipeline offer do not make a preview: a plain yes never answers
    them, and each names its own call (with ``--since``).
    """

    return response.get("status") == STATUS_ASK


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
    advance: bool = True,
    us_only: bool | None = None,
) -> dict[str, object]:
    """What is new since the last check, as the ``scout-new:1`` response. See the module docstring.

    ``us_only`` (0.1.11.9 NEW1): the Jobs list's switch. ``None`` is the setup's
    default (ON when the shared config's countries hold the US); either value
    is carried by every question's yes.

    ``advance=False`` (0110-10-11): this call does not move the anchor,
    whatever else it is (``anchor.advances`` is false). For a caller that asks
    the user something before the run is done; it moves the anchor itself
    with :func:`settle_anchor` when the run has done its work. A response
    that asks (``status: "ask"``) is a preview and never moves it, whatever
    ``advance`` says (:func:`is_preview`).

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
    unless ``include_low_rank``, and acts on the top :data:`BATCH_LIMIT`
    (50) of them by rank, never more. ``progress`` gets
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
            include_low_rank=include_low_rank and not yours, advance=advance, us_only=us_only,
        )


def scout_new_yours(
    home_root: Path, target: Path, *, profile_id: str | None = None, since: str | None = None, now: datetime | None = None,
    model_wait: float | None = None, us_only: bool | None = None,
) -> dict[str, object]:
    """The user's own evidence of what matches, for the postings ``scout_new`` lists with the same filters.

    User-private only: never a posting's title, company or text (a posting is
    named by its job identity). Never assesses, never moves the anchor.
    """

    return scout_new(
        home_root, target, profile_id=profile_id, peek=True, assess=False, since=since, now=now, yours=True, model_wait=model_wait,
        us_only=us_only,
    )


def _scout_new(
    home_root: Path, target: Path, *, profile_id: str | None, peek: bool, assess: bool | None, since: str | None,
    now: datetime | None, config: object | None, yours: bool, process: bool = False, decided_by: str = "operator",
    reassess_stale: bool = False, progress: Callable[[str], None] | None = None, model_wait: float | None = None,
    build_progress: Callable[[str, int, int], None] | None = None, include_low_rank: bool = False, advance: bool = True,
    us_only: bool | None = None,
) -> dict[str, object]:
    from .posting_search import us_only_setting

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

        # 0.1.11.5: a posting with an application is left out of what is new, of its counts and of every batch
        # (``gigai scout jobs list --state applied`` lists them). The events are read once per request.
        # 0.1.11.9 NEW1: an application on ANY copy of a job is the job's.
        applied = _applications(home_root, _applied(refreshed.resolved))
        applied_new = 0
        # 0.1.11.9 NEW1: the Jobs list's US only, by its default; and one row a job (:class:`_JobRows`).
        us_default = us_only_setting(home_root, target)
        us_on = us_default if us_only is None else bool(us_only)
        jobs: _JobRows | None = None

        def read_new() -> dict[str, list[PostingRecord]]:
            """The new JOBS: ``canonical posting -> the row's tags`` (the canonical posting's own first)."""

            nonlocal applied_new, jobs
            # Every role's new postings: a role filter judges the ROW (the roles that found any copy), as the list's does.
            found = _grouped(store.postings(since=since_at))
            jobs = _JobRows(
                home_root, [(group, _shown(group)) for group in found.values()], us_only=us_on, applications=applied,
                profile_id=profile_id,
            )
            applied_new = jobs.applied
            return {row.job: list(group) for group, row in jobs.rows}

        setting = fit_rules.fit_setting(home_root, target)

        def to_assess() -> tuple[list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]], list[tuple[str, str]]]:
            """``(new to assess, new low-ranked, stale to re-assess, stale low-ranked)`` as the stores are now (0110-10-02)."""

            ranks = {(row.job, row.profile_id): row.rank_score for group in groups.values() for row in group}
            # 0.1.11.9 RB2: one model call a JOB: the copies of a job posted once per country are one pair.
            first = {(row.job, row.profile_id): row for group in groups.values() for row in group}
            new_pairs = sorted((row.job, row.profile_id) for row in _one_a_job((first[pair] for pair in _new_pairs(groups, profile_id)), home_root, target))
            new_keep, new_low = split_low_rank(new_pairs, ranks, setting, include=include_low_rank)
            stale = [row for row in _stale_rows(store, profile_id) if row.job not in applied]
            old = _one_a_job(_kept_by_us_only(home_root, stale) if us_on else stale, home_root, target)
            old_ranks = {(row.job, row.profile_id): row.rank_score for row in old}
            old_keep, old_low = split_low_rank(list(old_ranks), old_ranks, setting, include=include_low_rank)
            # 0110-10-11, 0.1.11.2: what the batch of 50 a yes acts on is ordered by: the rank score, then the newest.
            dates.clear()
            dates.update({(row.job, row.profile_id): batch_date(row) for group in groups.values() for row in group})
            dates.update({(row.job, row.profile_id): batch_date(row) for row in old})
            batch_ranks.clear()
            batch_ranks.update(ranks)
            batch_ranks.update(old_ranks)
            return new_keep, new_low, old_keep, old_low

        dates: dict[tuple[str, str], str] = {}
        batch_ranks: dict[tuple[str, str], int | None] = {}
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
        # 0110-10-11: a yes acts on 50, never more; what is left is said with the call for the next 50.
        # 0.1.11.2: the 50 are the TOP 50 BY RANK (:func:`top_ranked_batch`), no longer the newest.
        # One call that carries BOTH yeses still makes at most 50 model calls: the new postings first, the old
        # assessments in what is left of the 50 (none left: the stale question stays, with its own call).
        new_batch, new_later = top_ranked_batch(pairs, batch_ranks, dates)
        room = BATCH_LIMIT - len(new_batch) if approved_new else BATCH_LIMIT
        stale_batch, stale_later = top_ranked_batch(stale_pairs, batch_ranks, dates, limit=room)
        approved_stale = bool(stale_batch) and reassess_stale
        low_flag = " --include-low-rank" if include_low_rank else ""
        low_body = {"include_low_rank": True} if include_low_rank else {}
        if progress is not None and (approved_new or approved_stale):
            # A long batch is never silent, and a moving count is explained: how far the background rank is.
            progress(f"ranking: {_ranking_line(_ranking(store, views, home_root, target, now=moment), labels)}")
        if approved_new:
            assessed = batch(new_batch, [group[0] for group in groups.values()], done_word="assessed", doing_word="assessing")
            if new_later:  # only then: a batch that was all of them answers what it always did
                assessed["more_after"] = new_later
                assessed["next"] = _answers(since_at, profile_id, flag=f"--yes{low_flag}", body={"assess": True, **low_body}, us_only=us_only)
        elif pairs and assess is None:
            status = STATUS_ASK
        if approved_stale:
            # 0110-8-08: only on its own yes. Each posting for the profile whose old assessment it has.
            reassessed = batch(
                stale_batch, store.postings(jobs={job for job, _owner in stale_batch}), done_word="re-assessed", doing_word="re-assessing"
            )
            if stale_later:
                reassessed["more_after"] = stale_later
                reassessed["next"] = _answers(
                    since_at, profile_id, flag=f"--reassess-stale{low_flag}", body={"assess": False, "reassess_stale": True, **low_body},
                    us_only=us_only,
                )
        if assessed is not None or reassessed is not None:
            postings.refresh(home_root, target, store=store, now=moment)
            groups = read_new()
            pairs, low_pairs, stale_pairs, low_stale = to_assess()  # what is still not assessed: the failures
        if status == STATUS_ASK:
            question, sentence = _question(
                pairs, new_count, views, since_at, home_root=home_root, target=target, profile_id=profile_id, low_rank=len(low_pairs),
                us_only=us_only,
            )
        stale_question = (
            _stale_question(
                stale_pairs, views, since_at, home_root=home_root, target=target, profile_id=profile_id, low=low_stale,
                min_rank=setting.assess_min_rank, us_only=us_only,
            )
            if stale_pairs or low_stale else None
        )
        low_rank_question = (
            _low_rank_question(low_pairs, views, since_at, setting, home_root=home_root, target=target, profile_id=profile_id, us_only=us_only)
            if low_pairs else None
        )

        weak_fit = ranked_low = 0
        assert jobs is not None
        us_left_out, copies_of_new = jobs.left_out, jobs.postings
        if groups:
            shown = in_order((group, _shown(group, profile_id)) for group in groups.values())
            message = f"{new_count} new job{'s' if new_count != 1 else ''} since {_when(since_at)}."
            if copies_of_new > len(groups):
                # 0.1.11.9 NEW1: the count is of jobs; the postings behind them are said once.
                message += f" They stand for {copies_of_new} postings: the same job posted more than once is one row that lists its locations."
            # 0110-10-02: a weak fit is not listed; the count and how to list them are said instead.
            weak_fit = sum(1 for _group, row in shown if row.state == fit_rules.WEAK_FIT)
            shown = [(group, row) for group, row in shown if row.state != fit_rules.WEAK_FIT]
            # 0.1.11.2: a posting ranked low is listed like any other, lower by its rank; this is the divider's number.
            ranked_low = sum(1 for _group, row in shown if fit_rules.is_ranked_low(row.state, row.rank_score, setting))
            if len(shown) > NEW_ROWS_LIMIT:
                shown = shown[:NEW_ROWS_LIMIT]
                message += f" Showing the first {NEW_ROWS_LIMIT}: assessed ones first, by fit, then by rank."
            if weak_fit:
                message += (
                    f" {weak_fit} weak fit{'s are' if weak_fit != 1 else ' is'} not listed (few requirements met and a low rank):"
                    f" {WEAK_FIT_COMMAND}"
                )
        else:
            best = store.postings_by_score(states=_ATTENTION_STATES, profile_id=profile_id, limit=ATTENTION_LIMIT * 3)
            tags = _grouped(store.postings(jobs={row.job for row in best}))
            # 0.1.11.9: each job by its best tag's row (a role filter picks the jobs, never the row). NEW1: one row a
            # job here too, US only applied, a job with an application on any copy left out.
            jobs = _JobRows(
                home_root, [(group, _shown(group)) for group in (tags.get(row.job, [row]) for row in best)], us_only=us_on,
                applications=applied,
            )
            shown = [(group, row) for group, row in jobs.rows[:ATTENTION_LIMIT] if row.state != fit_rules.WEAK_FIT]
            # 0110-10-11: what "new" counts is said, so "nothing new" after an update that stored hundreds of postings
            # is not a riddle: new is a posting your profiles MATCH that Scout first stored after the time named.
            if source == SINCE_FIRST_USE:
                message = f"Nothing new in the last {FIRST_USE_DAYS} days: {NOTHING_NEW_MEANS} in them. {UPDATE_COUNTS_ALL}"
            else:
                message = f"Nothing new since your last check ({_when(since_at)}): {NOTHING_NEW_MEANS} after it. {UPDATE_COUNTS_ALL}"
            if shown:
                message += f" Top {len(shown)} that still need your attention:"

        if applied_new:
            message += (
                f" {applied_new} new job{'s' if applied_new != 1 else ''} you already applied to"
                f" {'are' if applied_new != 1 else 'is'} left out: {APPLIED_COMMAND}"
            )
        if us_left_out:
            message += (
                f" {us_left_out} new job{'s' if us_left_out != 1 else ''} posted only outside the US"
                f" {'are' if us_left_out != 1 else 'is'} left out (US only): {NO_US_ONLY_COMMAND}"
            )

        texts = postings.posting_texts(home_root, [row for _group, row in shown])
        rows_json: list[dict[str, object]] = []
        evidence: list[dict[str, object]] = []
        from .quick_assess import read_quick_assessment

        pending = postings.TagPending(home_root, views)
        for group, row in shown:
            item = None if row.state == _NOT_ASSESSED else read_quick_assessment(home_root, target, row.profile_id, row.job)
            text = texts.get(row.job)
            rows_json.append(_row_json(group, row, text, item, pending(tag_owner(group, row, profile_id), None if text is None else text.title), labels))
            rows_json[-1]["ranked_low"] = fit_rules.is_ranked_low(row.state, row.rank_score, setting)  # 0.1.11.2
            rows_json[-1].update(jobs.json(row, text))  # 0.1.11.9 NEW1 (additive): copies, locations, members, location_unclear
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
        hint_profile = (f" --profile {profile_id}" if profile_id is not None else "") + _us_flag(us_only)
        query = f"since={since_at}" + (f"&profile_id={profile_id}" if profile_id else "") + ("" if us_only is None else f"&us_only={int(bool(us_only))}")
        per_profile: dict[str, int] = {}
        for group in groups.values():
            for row in group:
                per_profile[row.profile_id] = per_profile.get(row.profile_id, 0) + 1
        # 0.1.10.11 NA: a response that asks is a preview; the question is not answered yet, so "new" is not consumed.
        advances = advance and not peek and profile_id is None and not process and not is_preview({"status": status})
        response: dict[str, object] = {
            "schema_version": SCHEMA_VERSION,
            "status": status,
            "since": since_at,
            "since_source": source,
            "checked_at": checked_at,
            "peek": bool(peek),
            "profile_id": profile_id,
            # 0.1.11.9 NEW1: the Jobs list's switch, as it is for this response.
            "us_only": {"on": us_on, "default": us_default, "rule": US_ONLY_RULE},
            "anchor": {"last_checked_at": None if anchor is None else anchor.last_checked_at, "advances": advances},
            "counts": {
                # 0.1.11.9 NEW1: every count here is of JOBS (the copies of a job are one).
                "new": new_count,
                # How many postings the new jobs stand for, and how many new jobs US only left out.
                "postings": copies_of_new,
                "us_only_left_out": us_left_out,
                "to_assess": len(pairs),
                "low_rank_skipped": len(low_pairs),
                "only_stale": len(stale_pairs) + len(low_stale),
                "weak_fit": weak_fit,
                # 0.1.11.5: the new postings with an application: left out of ``new``, of the rows and of every batch.
                "applied": applied_new,
                # 0.1.11.2: the new postings not assessed yet and ranked below ``fit.weak_fit_below_rank``. Listed, never left out.
                "ranked_low": ranked_low if groups else sum(1 for row in rows_json if row["ranked_low"]),
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
            "ranking": _ranking(store, views, home_root, target, now=moment),
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


def settle_anchor(home_root: Path, target: Path, checked_at: str) -> str:
    """0110-10-11: move the anchor to ``checked_at``, the time a call made with ``advance=False`` read the postings.

    What the terminal does once its run has done its work (every question
    answered and acted on). A run that is abandoned at a prompt never gets
    here, so the next run still measures "new" from the anchor it found. The
    anchor never moves back. Returns the anchor as it is now.
    """

    at = postings.stamp(checked_at)
    if at is None:
        raise ScoutNewError("invalid_value", "checked_at must be the checked_at of a scout new response")
    store = PipelineStore(pipeline_path(Path(home_root), Path(target)))
    try:
        return store.advance_anchor(at, set_by="scout_new").last_checked_at
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

    from .find_jobs.posting_live import closed_skipped_text

    closed = closed_skipped_text(batch)  # 0.1.11.4 R1: said the same way for either batch
    return (f" Fetched {batch['fetched_on_demand']} missing description(s) first." if batch.get("fetched_on_demand") else "") + (f" {closed}" if closed else "")


def _next_lines(batch: Mapping[str, object], what: str) -> list[str]:
    """0110-10-11: after a batch of 50 that left some: how many, and the call for the next 50."""

    following = batch.get("next")
    if not batch.get("more_after") or not isinstance(following, Mapping):
        return []
    return [f"{batch['more_after']} more {what}: 50 at a time. Next: {following['cli']}"]


def _failure_code(item: Mapping[str, object]) -> str:
    return f"{item['error_code']}{': ' + str(item['reason']) if item.get('reason') else ''}"


def _note_lines(batch: Mapping[str, object]) -> list[str]:
    from .quick_assess import requirements_note_lines

    return requirements_note_lines(batch)


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
        lines.extend(_note_lines(assessed))  # GUARDFIX
        lines.extend(notice_lines(assessed))
        lines.extend(_next_lines(assessed, "not assessed yet"))
    reassessed = response.get("reassessed")
    if isinstance(reassessed, Mapping):
        lines.append(f"Re-assessed {reassessed['assessed']} of {reassessed['requested']}." + _fetched_note(reassessed))
        for item in reassessed["failed"]:  # type: ignore[union-attr]
            lines.append(f"  not re-assessed ({_failure_code(item)}): {item['job_identity']}")
        lines.extend(failure_lines(reassessed["failed"]))
        lines.extend(_note_lines(reassessed))  # GUARDFIX
        lines.extend(notice_lines(reassessed))
        lines.extend(_next_lines(reassessed, "with only an old assessment"))
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
        counts = response.get("counts")
        switch = response.get("us_only")
        us_only = isinstance(switch, Mapping) and switch.get("on") is True
        before: Mapping[str, object] | None = None
        for row in rows:
            divider = divider_text(before, row, counts if isinstance(counts, Mapping) else {})  # 0.1.11.2: "Ranked low (N)"
            if divider:
                lines.extend([divider, rule])
            before = row
            tags = ", ".join(str(labels.get(item["profile_id"], item["profile_id"])) for item in row["profiles"])
            details = [f"{row['company_name'] or '?'}: {row['title'] or row['job_identity']}", str(row["work_mode"])]
            copies = row.get("copies")
            if type(copies) is int and copies > 1:
                details.extend([str(row["locations_text"]), f"{copies} postings"])  # 0.1.11.9 NEW1: one row a job
            if us_only and row.get("location_unclear"):
                details.append("unclear location")  # US only kept it without knowing where it is
            if row["salary"]:
                details.append(str(row["salary"]))
            if posted_text(row):
                details.append(posted_text(row))  # 0110-10-14
            details.append(f"[{tags}]")
            if row.get("tag_pending"):
                details.append("tag pending")  # 0110-8-05: matched by a generic title's words; its function tag is not known yet
            # One part per line (the column is narrow): the verdict, "old assessment: ...", "N of M requirements", the rank.
            score = str(row["score_text"]).split(" · ") + ([str(row["minor_gap_text"])] if row.get("minor_gap_text") and row.get("state") == "matched" and not row.get("thin_posting") else [])
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
    "BATCH_LIMIT",
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
    "is_preview",
    "mark_all_seen",
    "newest_batch",
    "top_ranked_batch",
    "batch_date",
    "divider_text",
    "order_key",
    "posted_text",
    "posting_dates",
    "process_waiting",
    "render",
    "response_labels",
    "score_text",
    "scout_new",
    "scout_new_yours",
    "settle_anchor",
    "sort_group",
    "split_low_rank",
    "stale_label",
]
