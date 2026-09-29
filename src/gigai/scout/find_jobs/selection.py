"""Pure, I/O-free candidate diversity selection for Scout find-jobs.

B2 (0.1.8.1 live UAT): a naive first-N-in-batch-order cap let one board's
postings fill the entire assess run (5/5 picks were ClickHouse, 3 sharing a
title). This module is the reusable fix: given every role-matched candidate
row and a target cap, it dedupes near-identical postings, caps how many one
company can contribute, and fills the remaining cap round-robin across
companies so no single board can dominate the selection.

uat-bug-010 (UAT N13): the pre-rank has to reach that selection. The
caller used to sort its rows by score and hand them over, and every step
here then re-sorted by date, so the assess cap went to the newest postings
rather than the best fits. The scores are now an input (``rank_scores``):
``rank_rows`` is the one ordering (rank score, then newest) shared by the
per-company cap and company visiting order below and by acquire's import
bound (uat-bug-011), so the rows a run imports and the rows it assesses are
ranked the same way.

SCOPE-ADD-3 C1: the scores come from the run's own model ranking pass
(``model_rank``), sealed as ``AcquireOutput.rank_scores``. A score that
names a blocker (``mismatch_flags``) is DEMOTED, never dropped: scored
unblocked rows first (best score first), then unscored rows, then blocked
rows (best score first) -- ``model_rank.ordering_key``'s tiers.

Deliberately isolated here, mirroring ``filters.py``'s own reasoning, so
whichever node wires this in (today: ``market_acquisition.py``'s selection
loop) calls the same pure logic the tests exercise directly -- no I/O, no
contract/dataclass coupling beyond the narrow ``Candidate`` protocol below.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Protocol

from .filters import location_countries

DEFAULT_PER_COMPANY_CAP = 2

_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_RE = re.compile(r"\s+")


class Candidate(Protocol):
    """The narrow read-only shape this module needs from a posting row.

    Any ``PostingRow``-like object satisfies this structurally; the module
    never imports the frozen contract so it stays reusable and independently
    testable with plain fixtures.
    """

    @property
    def normalized_url(self) -> str: ...

    @property
    def company(self) -> str: ...

    @property
    def title(self) -> str: ...

    @property
    def location(self) -> str: ...

    @property
    def published_at(self) -> str | None: ...


class Score(Protocol):
    """The narrow read-only shape this module needs from a rank score.

    ``rank_contracts.RankScore`` (the sealed ``AcquireOutput.rank_scores``
    shape) satisfies this structurally. ``score`` is ``None`` for a row the
    pass never scored (a budget stop, a failed batch); such a row ranks with
    the rows that have no score entry at all. An optional ``mismatch_flags``
    (the ranker's blockers), read with ``getattr``, demotes a scored row
    below every unscored one (SCOPE-ADD-3 C1).
    """

    @property
    def normalized_url(self) -> str: ...

    @property
    def score(self) -> int | None: ...


DropReason = str  # "duplicate" | "company_cap" | "over_cap"


@dataclass(frozen=True)
class SelectionResult:
    """The diverse subset chosen for assessment, and why every other row was dropped.

    ``selected`` preserves the candidates in ``rows`` order restricted to the
    kept set (callers that need round-robin order can zip against
    ``selected_order``). ``dropped`` maps every non-selected candidate's
    ``normalized_url`` to one drop reason.
    """

    selected: tuple[Candidate, ...]
    dropped: dict[str, DropReason]


def normalize_title(title: str) -> str:
    """Case/whitespace/punctuation-insensitive normalization for title comparison.

    "Senior Software Engineer - Cloud Infrastructure" and "senior software
    engineer, cloud infrastructure!" normalize identically.
    """

    folded = title.casefold()
    no_punct = _PUNCTUATION_RE.sub(" ", folded)
    return _WHITESPACE_RE.sub(" ", no_punct).strip()


def _normalize_company(company: str) -> str:
    return _WHITESPACE_RE.sub(" ", company.casefold()).strip()


def _country_bucket(location: str) -> frozenset[str]:
    """The recognized country set for a location, or a sentinel for "unknown".

    Two postings with genuinely unrecognized/ambiguous locations are still
    treated as comparable (both bucket to the same sentinel) rather than
    silently never deduping just because neither location parses -- the
    dedupe key already requires the same company + normalized title, so this
    only widens matching among postings that were already near-identical.
    """

    countries = location_countries(location or "")
    return frozenset(countries) if countries else frozenset({"__unknown__"})


def _dedupe_key(row: Candidate) -> tuple[str, str, frozenset[str]]:
    return (
        _normalize_company(row.company or ""),
        normalize_title(row.title or ""),
        _country_bucket(row.location or ""),
    )


def duplicate_key(row: Candidate) -> tuple[str, str, frozenset[str]]:
    """What makes two postings near-identical here: company, normalized title, country (uat-bug-042 reuses it)."""

    return _dedupe_key(row)


def _sort_key_newest_first(row: Candidate) -> str:
    # ISO 8601 timestamps sort lexicographically; a missing/unparseable
    # published_at sorts last (oldest) rather than raising.
    published = row.published_at
    return published if isinstance(published, str) and published else ""


@dataclass(frozen=True)
class _Scored:
    score: float
    blocked: bool


def _scores_by_url(rank_scores: Sequence[Score]) -> Mapping[str, _Scored]:
    """``normalized_url -> (score, blocked)`` for the rows the pass actually scored."""

    scores: dict[str, _Scored] = {}
    for item in rank_scores:
        score = item.score
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            scores[item.normalized_url] = _Scored(score, bool(getattr(item, "mismatch_flags", ())))
    return scores


def _score_key(row: Candidate, scores: Mapping[str, _Scored]) -> tuple[int, float]:
    # Ascending: scored unblocked rows first, best score first; then unscored
    # rows; then blocked rows, best score first (demoted, never dropped).
    scored = scores.get(row.normalized_url)
    if scored is None:
        return (1, 0)
    return (2 if scored.blocked else 0, -scored.score)


def _rank(
    rows: Sequence[Candidate],
    scores: Mapping[str, _Scored],
    imported_before: Collection[str] = (),
) -> list[Candidate]:
    # Stable passes, least significant first: newest first, then (only for
    # the import cap) rows never imported before ahead of the ones that
    # were, then (only when any row is scored) by score. Without scores and
    # without ``imported_before`` the order is the date order this module
    # has always used, ties in ``rows`` order.
    ordered = sorted(rows, key=_sort_key_newest_first, reverse=True)
    if imported_before:
        ordered.sort(key=lambda row: row.normalized_url in imported_before)
    if scores:
        ordered.sort(key=lambda row: _score_key(row, scores))
    return ordered


def rank_rows(
    rows: Sequence[Candidate],
    rank_scores: Sequence[Score] = (),
    *,
    imported_before: Collection[str] = (),
) -> list[Candidate]:
    """``rows`` best first: rank score descending, then newest, then ``rows`` order.

    A row with no score (no entry, or an unscored entry) ranks after every
    scored unblocked row and before every blocked one (a score with
    ``mismatch_flags``: demoted, never dropped). With no scores at all this
    is newest first. Every row comes back; the caller slices what it needs.

    ``imported_before`` (the import cap's rotation, orchestrator decision
    2026-09-27): the ``normalized_url`` of every posting an earlier run
    already imported. Between two rows the score does not separate -- the
    same score, or no score at all -- the one never imported before goes
    first, then the newest. So without scores, consecutive runs over more
    rows than the cap import different slices until every row has had its
    turn; with scores the rank order stands and only its ties rotate. The
    selection for assessment never passes it (acquire and assess's
    recompute must rank alike).
    """

    return _rank(rows, _scores_by_url(rank_scores), imported_before)


def select_for_assessment(
    rows: Sequence[Candidate],
    *,
    cap: int,
    per_company: int | None = DEFAULT_PER_COMPANY_CAP,
    rank_scores: Sequence[Score] = (),
) -> SelectionResult:
    """Pick up to ``cap`` diverse candidates from ``rows``.

    Steps, each deterministic given the same input:

    1. Dedupe same company + normalized title (+ same-country locations):
       within each duplicate group, keep only the newest (by
       ``published_at``); every other member of the group is dropped as
       ``"duplicate"``. Scores play no part: near-identical postings score
       alike, and the newest is the one still open.
    2. Per-company cap: among the deduped survivors, keep at most
       ``per_company`` per company, best ranked first (``rank_rows``: rank
       score with blocked rows demoted, then newest); the rest are dropped
       as ``"company_cap"``.
    3. Round-robin fill: companies are visited in a fixed order (by each
       company's best score, best first; then by its newest surviving row,
       newest first; then by company name) and one row is taken per company
       per pass until ``cap`` is reached or every survivor has been taken.
       Anything left over once ``cap`` is reached is dropped as
       ``"over_cap"``.

    ``rank_scores`` is optional. With none (or none that carry a score) the
    ranking is by date alone, exactly as before uat-bug-010.

    ``per_company=None`` (uat-bug-042: a run whose cap is "all") keeps every
    deduped row of every company: step 2 drops nothing.

    ``rows`` with ``cap <= 0`` selects nothing (every row is ``"over_cap"``).
    Order within ``rows`` only breaks a tie between rows with the same score
    and the same ``published_at``, so two callers that must agree (acquire's
    selection and assess's recompute) pass their rows in the same order.
    """

    scores = _scores_by_url(rank_scores)
    dropped: dict[str, DropReason] = {}

    groups: dict[tuple[str, str, frozenset[str]], list[Candidate]] = {}
    for row in rows:
        groups.setdefault(_dedupe_key(row), []).append(row)

    deduped: list[Candidate] = []
    for members in groups.values():
        ordered = sorted(members, key=_sort_key_newest_first, reverse=True)
        deduped.append(ordered[0])
        for loser in ordered[1:]:
            dropped[loser.normalized_url] = "duplicate"

    by_company: dict[str, list[Candidate]] = {}
    for row in deduped:
        by_company.setdefault(_normalize_company(row.company or ""), []).append(row)

    capped_by_company: dict[str, list[Candidate]] = {}
    for company, members in by_company.items():
        ordered = _rank(members, scores)
        keep = len(ordered) if per_company is None else per_company
        capped_by_company[company] = ordered[:keep]
        for loser in ordered[keep:]:
            dropped[loser.normalized_url] = "company_cap"

    # Deterministic company visiting order: each company's newest surviving
    # row decides its place (newest company first), ties broken by the
    # normalized company name ascending -- sort ascending by (name) first,
    # then stably sort by newest-first descending, so a reverse sort on the
    # timestamp never also flips the name tiebreak. With scores, one more
    # stable pass puts the company with the best score first: a company's
    # first row is its best ranked one.
    company_order = sorted(capped_by_company)
    company_order.sort(
        key=lambda name: max(_sort_key_newest_first(row) for row in capped_by_company[name]),
        reverse=True,
    )
    if scores:
        company_order.sort(key=lambda name: _score_key(capped_by_company[name][0], scores))

    selected: list[Candidate] = []
    cap = max(cap, 0)
    round_index = 0
    remaining = {name: list(members) for name, members in capped_by_company.items()}
    while len(selected) < cap and any(remaining.values()):
        progressed = False
        for company in company_order:
            if len(selected) >= cap:
                break
            queue = remaining.get(company) or []
            if round_index < len(queue):
                selected.append(queue[round_index])
                progressed = True
        round_index += 1
        if not progressed:
            break

    selected_urls = {row.normalized_url for row in selected}
    for company, members in remaining.items():
        for row in members:
            if row.normalized_url not in selected_urls and row.normalized_url not in dropped:
                dropped[row.normalized_url] = "over_cap"

    # Preserve the caller's original row order for `selected` so downstream
    # consumers that zip against acquire's own row ordering stay stable.
    ordered_selected = tuple(row for row in rows if row.normalized_url in selected_urls)
    return SelectionResult(ordered_selected, dropped)


def selection_limits(selection_cap: int | str) -> tuple[int, int | None]:
    """``(cap, per_company)`` for a run's ``selection_cap`` (uat-bug-042).

    A number keeps today's selection: that many, at most
    :data:`DEFAULT_PER_COMPANY_CAP` per company. ``"all"`` is every new
    posting: up to ``contracts.ASSESS_ALL_CEILING``, no per-company cap
    (duplicates are still dropped). Acquire's selection and assess's
    recompute both call this, so the two agree.
    """

    from .contracts import is_assess_all, selection_cap_limit

    if is_assess_all(selection_cap):
        return selection_cap_limit(selection_cap), None
    return selection_cap_limit(selection_cap), DEFAULT_PER_COMPANY_CAP


__all__ = [
    "Candidate",
    "DEFAULT_PER_COMPANY_CAP",
    "Score",
    "SelectionResult",
    "duplicate_key",
    "normalize_title",
    "rank_rows",
    "select_for_assessment",
    "selection_limits",
]
