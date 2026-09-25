"""Pure, I/O-free candidate diversity selection for Scout find-jobs.

B2 (0.1.8.1 live UAT): a naive first-N-in-batch-order cap let one board's
postings fill the entire assess run (5/5 picks were ClickHouse, 3 sharing a
title). This module is the reusable fix: given every role-matched candidate
row and a target cap, it dedupes near-identical postings, caps how many one
company can contribute, and fills the remaining cap round-robin across
companies so no single board can dominate the selection.

Deliberately isolated here, mirroring ``filters.py``'s own reasoning, so
whichever node wires this in (today: ``market_acquisition.py``'s selection
loop) calls the same pure logic the tests exercise directly -- no I/O, no
contract/dataclass coupling beyond the narrow ``Candidate`` protocol below.
"""

from __future__ import annotations

from collections.abc import Sequence
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


def select_for_assessment(
    rows: Sequence[Candidate],
    *,
    cap: int,
    per_company: int = DEFAULT_PER_COMPANY_CAP,
) -> SelectionResult:
    """Pick up to ``cap`` diverse candidates from ``rows``.

    Steps, each deterministic given the same input:

    1. Dedupe same company + normalized title (+ same-country locations):
       within each duplicate group, keep only the newest (by
       ``published_at``); every other member of the group is dropped as
       ``"duplicate"``.
    2. Per-company cap: among the deduped survivors, keep at most
       ``per_company`` per company (newest first); the rest are dropped as
       ``"company_cap"``.
    3. Round-robin fill: companies are visited in a fixed order (by each
       company's newest surviving row, newest first, ties broken by company
       name) and one row is taken per company per pass until ``cap`` is
       reached or every survivor has been taken. Anything left over once
       ``cap`` is reached is dropped as ``"over_cap"``.

    ``rows`` with ``cap <= 0`` selects nothing (every row is ``"over_cap"``).
    Order within ``rows`` never matters to the result; only ``published_at``
    and company identity do, so re-running on a reordered but identical batch
    is a no-op.
    """

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
        ordered = sorted(members, key=_sort_key_newest_first, reverse=True)
        capped_by_company[company] = ordered[:per_company]
        for loser in ordered[per_company:]:
            dropped[loser.normalized_url] = "company_cap"

    # Deterministic company visiting order: each company's newest surviving
    # row decides its place (newest company first), ties broken by the
    # normalized company name ascending -- sort ascending by (name) first,
    # then stably sort by newest-first descending, so a reverse sort on the
    # timestamp never also flips the name tiebreak.
    company_order = sorted(capped_by_company)
    company_order.sort(key=lambda name: _sort_key_newest_first(capped_by_company[name][0]), reverse=True)

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
    "SelectionResult",
    "normalize_title",
    "select_for_assessment",
]
