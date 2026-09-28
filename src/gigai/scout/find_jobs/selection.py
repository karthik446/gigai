"""Pure, I/O-free candidate diversity selection for Scout find-jobs.

B2 (0.1.8.1 live UAT): a naive first-N-in-batch-order cap let one board's
postings fill the entire assess run (5/5 picks were ClickHouse, 3 sharing a
title). This module is the reusable fix: given every role-matched candidate
row and a target cap, it dedupes near-identical postings, caps how many one
company can contribute, and fills the remaining cap round-robin across
companies so no single board can dominate the selection.

uat-bug-010 (UAT N13): the Jev pre-rank has to reach that selection. The
caller used to sort its rows by score and hand them over, and every step
here then re-sorted by date, so the assess cap went to the newest postings
rather than the best fits. The scores are now an input (``rank_scores``):
``rank_rows`` is the one ordering (Jev score, then newest) shared by the
per-company cap and company visiting order below and by acquire's import
bound (uat-bug-011), so the rows a run imports and the rows it assesses are
ranked the same way.

Deliberately isolated here, mirroring ``filters.py``'s own reasoning, so
whichever node wires this in (today: ``market_acquisition.py``'s selection
loop) calls the same pure logic the tests exercise directly -- no I/O, no
contract/dataclass coupling beyond the narrow ``Candidate`` protocol below.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    """The narrow read-only shape this module needs from a Jev rank score.

    ``jev_contracts.RankScore`` satisfies this structurally. ``score`` is
    ``None`` for a row Jev never scored (past the cost cap, or a failed
    call); such a row ranks with the rows that have no score entry at all.
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


def _sort_key_newest_first(row: Candidate) -> str:
    # ISO 8601 timestamps sort lexicographically; a missing/unparseable
    # published_at sorts last (oldest) rather than raising.
    published = row.published_at
    return published if isinstance(published, str) and published else ""


def _scores_by_url(rank_scores: Sequence[Score]) -> Mapping[str, float]:
    """``normalized_url -> score`` for the rows Jev actually scored."""

    scores: dict[str, float] = {}
    for item in rank_scores:
        score = item.score
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            scores[item.normalized_url] = score
    return scores


def _score_key(row: Candidate, scores: Mapping[str, float]) -> tuple[int, float]:
    # Ascending: scored rows first, best score first; unscored rows last.
    score = scores.get(row.normalized_url)
    return (1, 0) if score is None else (0, -score)


def _rank(rows: Sequence[Candidate], scores: Mapping[str, float]) -> list[Candidate]:
    # Two stable passes: newest first, then (only when any row is scored)
    # by score. Without scores the second pass is skipped, so the order is
    # the date order this module has always used, ties in ``rows`` order.
    ordered = sorted(rows, key=_sort_key_newest_first, reverse=True)
    if scores:
        ordered.sort(key=lambda row: _score_key(row, scores))
    return ordered


def rank_rows(rows: Sequence[Candidate], rank_scores: Sequence[Score] = ()) -> list[Candidate]:
    """``rows`` best first: Jev score descending, then newest, then ``rows`` order.

    A row with no score (no entry, or an unscored entry) ranks after every
    scored row. With no scores at all this is newest first. Every row comes
    back; the caller slices what it needs.
    """

    return _rank(rows, _scores_by_url(rank_scores))


def select_for_assessment(
    rows: Sequence[Candidate],
    *,
    cap: int,
    per_company: int = DEFAULT_PER_COMPANY_CAP,
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
       ``per_company`` per company, best ranked first (``rank_rows``: Jev
       score, then newest); the rest are dropped as ``"company_cap"``.
    3. Round-robin fill: companies are visited in a fixed order (by each
       company's best score, best first; then by its newest surviving row,
       newest first; then by company name) and one row is taken per company
       per pass until ``cap`` is reached or every survivor has been taken.
       Anything left over once ``cap`` is reached is dropped as
       ``"over_cap"``.

    ``rank_scores`` is optional. With none (or none that carry a score) the
    ranking is by date alone, exactly as before uat-bug-010.

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
        capped_by_company[company] = ordered[:per_company]
        for loser in ordered[per_company:]:
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


__all__ = [
    "Candidate",
    "DEFAULT_PER_COMPANY_CAP",
    "Score",
    "SelectionResult",
    "normalize_title",
    "rank_rows",
    "select_for_assessment",
]
