"""Merge OpenAI + H-1B candidates: dedupe, exclude, board-check, verify, watchlist.

Steps (task contract, step 4):
1. Union both sources' ``Candidate`` rows.
2. Dedupe by ``(provider, board_token)`` (derived from ``careers_url`` via
   ``parse_board_url`` -- S23 Recommendation point 1) and by normalized
   company name.
3. Drop exclusions (the watchlist + ``prefs.exclude_companies``).
4. Board-check each survivor with the real ATS APIs (>=1 posting matching
   ``prefs.roles`` in ``prefs.countries``) -- S23 Investigate §5's
   ``check_boards.py`` logic, reused.
5. Verify every *cited* sponsorship-evidence ``source_url`` actually
   resolves (HTTP HEAD, falling back to GET on a HEAD failure -- some sites
   block HEAD but allow GET) -- S23 Recommendation point 5 (a live 404 was
   cited as evidence in S23's own sample). An empty ``source_url`` is not a
   dead citation: the board is kept, marked ``evidence_verified=False``,
   with no HTTP call (held-review-002).
6. Add each usable, board-checked board to the Scout watchlist.
   ``WatchlistEntry``/``WatchlistFirstSeen`` are the versioned
   ``scout-watchlist:1`` journal contract (Amendment 02) and this task must
   not change them (coordinator decision, Option B, recorded in
   ``.orchestrator/workers/s2a-discovery-engine.md``): no evidence field
   exists on ``WatchlistEntry``, and ``SourceKind`` has no
   ``openai_web_search``/``h1b`` value. ``first_seen.source_kind`` is set
   to ``ats`` (the board was confirmed by our own ATS poll before being
   added, same as any other watchlist entry) with the true discovery
   origin recorded in the sidecar evidence file
   (``storage.evidence_path``), not in the watchlist itself.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable

import httpx

from ..ats_board_clients import ATSBoardClientError, ATSBoardClients
from ..contracts import (
    ATSProvider,
    FindJobsConfig,
    ModelTarget,
    SourceKind,
    SourceToggles,
    WatchlistEntry,
    WatchlistFirstSeen,
    parse_board_url,
)
from ..watchlist import add_to_watchlist, list_active
from .prefs import DiscoveryPrefs
from .storage import atomic_write, evidence_path
from .types import Candidate

_CLIENTS = ATSBoardClients()

_US_LOCATION_RE = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?)\b"
    r"|remote\s*-\s*us\b"
    r"|,\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b",
    re.IGNORECASE,
)


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _row_is_us(row: object) -> bool:
    countries = getattr(row, "countries", None)
    if countries:
        return "US" in countries
    location = getattr(row, "location", "") or ""
    return bool(_US_LOCATION_RE.search(location))


def normalize_company(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", name.lower())


@dataclass(frozen=True)
class MergedBoard:
    """One board that survived merge; ``to_json`` matches ``DiscoveryResult.new_boards``."""

    company: str
    provider: str
    board_token: str
    careers_url: str
    sponsorship: str
    sponsorship_evidence: str
    evidence_source_url: str
    evidence_verified: bool
    found_by: tuple[str, ...]
    matching_us_postings: int

    def to_json(self) -> dict[str, object]:
        return {
            "company": self.company,
            "provider": self.provider,
            "board_token": self.board_token,
            "careers_url": self.careers_url,
            "sponsorship": self.sponsorship,
            "sponsorship_evidence": self.sponsorship_evidence,
            "evidence_source_url": self.evidence_source_url,
            "evidence_verified": self.evidence_verified,
            "found_by": list(self.found_by),
            "matching_us_postings": self.matching_us_postings,
        }


def _group_by_board_then_company(candidates: list[Candidate]) -> dict[str, list[Candidate]]:
    """Group candidates that share a (provider, board_token) OR a normalized company name.

    Both sources can find the same company via different URLs (e.g. OpenAI
    cites a job-posting-shaped URL, H-1B derives the board root directly);
    grouping by *either* key -- with a normalized-company alias merged into
    whichever board-key group it first touches -- keeps a single company
    from producing two ``MergedBoard`` rows while still unioning every
    source's ``found_by`` onto the one group that survives.
    """

    board_key_by_company: dict[str, str] = {}
    groups: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        parsed = parse_board_url(candidate.careers_url)
        board_key = f"{parsed[0]}:{parsed[1]}" if parsed is not None else None
        company_key = normalize_company(candidate.company)

        group_key = board_key or board_key_by_company.get(company_key) or f"company:{company_key}"
        if board_key is not None:
            board_key_by_company.setdefault(company_key, board_key)
        groups.setdefault(group_key, []).append(candidate)
    return groups


def _is_excluded(company: str, exclusions: set[str]) -> bool:
    return normalize_company(company) in exclusions


def _verify_source_url(client: httpx.Client, url: str) -> bool:
    if not url:
        return False
    try:
        response = client.head(url, timeout=15.0, follow_redirects=True)
        if response.status_code < 400:
            return True
        # Some sites reject HEAD but allow GET (S23 found this pattern).
        response = client.get(url, timeout=15.0, follow_redirects=True)
        return response.status_code < 400
    except httpx.HTTPError:
        return False


def _minimal_config(roles: tuple[str, ...]) -> FindJobsConfig:
    return FindJobsConfig(
        roles=roles,
        merged_queries=(),
        location=None,
        remote=False,
        published_after=None,
        sources=SourceToggles(exa=False, ats=True, hiringcafe=False),
        default_assess_cap=10,
        default_model_target=ModelTarget.OLLAMA_LOCAL,
        countries=("US",),
        visa_sponsorship_required=False,
    )


def merge_and_verify(
    *,
    client: httpx.Client,
    all_candidates: list[Candidate],
    prefs: DiscoveryPrefs,
    exclusions: set[str],
    on_progress: Callable[[dict], None] | None = None,
) -> tuple[list[MergedBoard], dict[str, int]]:
    """Dedupe/exclude/board-check/verify. Returns (usable boards, skip-reason counts)."""

    skipped: dict[str, int] = {}

    def _skip(reason: str) -> None:
        skipped[reason] = skipped.get(reason, 0) + 1

    config = _minimal_config(prefs.roles or ("software engineer",))

    survivors: list[Candidate] = []
    for candidate in all_candidates:
        if _is_excluded(candidate.company, exclusions):
            _skip("excluded")
            continue
        survivors.append(candidate)

    grouped = _group_by_board_then_company(survivors)

    by_key: dict[str, list[Candidate]] = {}
    for group in grouped.values():
        # Each group's own board_token comes from whichever member(s) parsed
        # as an ATS URL; a group with none parseable is dropped here (once
        # per group, not once per member, so "unparseable" is counted
        # per surviving company, not inflated by duplicate sources).
        parsed = next((parse_board_url(c.careers_url) for c in group if parse_board_url(c.careers_url) is not None), None)
        if parsed is None:
            _skip("unparseable_board_url")
            continue
        key = f"{parsed[0]}:{parsed[1]}"
        by_key.setdefault(key, []).extend(group)

    boards: list[MergedBoard] = []
    for index, (key, group) in enumerate(sorted(by_key.items())):
        provider, board_token = key.split(":", 1)
        if on_progress is not None:
            on_progress({"stage": "merge_board_check", "company": group[0].company, "index": index, "of": len(by_key)})
        try:
            rows = _CLIENTS.list_board(client, provider, board_token, config)
        except ATSBoardClientError as exc:
            _skip(f"board_unresolvable:{exc.code}")
            continue
        us_matches = sum(1 for row in rows if _row_is_us(row))
        if us_matches < 1:
            _skip("no_matching_us_postings")
            continue

        # held-review-002: verification is only for a *cited* source URL. A
        # member with an empty source_url (openai_source's documented rule:
        # when sponsorship isn't required the model may answer with no
        # evidence/source at all, and merge only verifies a URL when one is
        # present) is kept with ``evidence_verified=False`` and costs no HTTP
        # call. Only a group whose every citation is dead is dropped as
        # ``evidence_unverifiable`` (S23's live-404 case).
        cited = [c for c in group if c.source_url]
        uncited = [c for c in group if not c.source_url]
        primary = next((c for c in cited if _verify_source_url(client, c.source_url)), None)
        evidence_verified = primary is not None
        if primary is None and uncited:
            primary = uncited[0]
        if primary is None:
            _skip("evidence_unverifiable")
            continue

        boards.append(
            MergedBoard(
                company=primary.company,
                provider=provider,
                board_token=board_token,
                careers_url=primary.careers_url,
                sponsorship=primary.sponsorship,
                sponsorship_evidence=primary.sponsorship_evidence,
                evidence_source_url=primary.source_url,
                evidence_verified=evidence_verified,
                found_by=tuple(sorted({c.found_by for c in group})),
                matching_us_postings=us_matches,
            )
        )
    return boards, skipped


def _load_evidence(home_root: Path, target: Path) -> dict[str, dict[str, object]]:
    path = evidence_path(home_root, target)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _save_evidence(home_root: Path, target: Path, evidence: dict[str, dict[str, object]]) -> None:
    atomic_write(evidence_path(home_root, target), json.dumps(evidence, indent=2, sort_keys=True).encode("utf-8"))


def evidence_for(*, home_root: Path, target: Path) -> dict[tuple[str, str], dict[str, object]]:
    """Read helper for S2-B: ``{(provider, board_token): evidence_dict}``."""

    raw = _load_evidence(home_root, target)
    result: dict[tuple[str, str], dict[str, object]] = {}
    for key, value in raw.items():
        provider, _, board_token = key.partition(":")
        result[(provider, board_token)] = value
    return result


def add_usable_boards_to_watchlist(
    *,
    home_root: Path,
    target: Path,
    discovery_id: str,
    boards: list[MergedBoard],
    gig_id: str | None = None,
    batch_id: str | None = None,
) -> list[MergedBoard]:
    """Add each board to the watchlist (contract-untouched) + record evidence in the sidecar."""

    existing = {(e.provider.value, e.board_token) for e in list_active(home_root, target, gig_id)}
    evidence = _load_evidence(home_root, target)
    added: list[MergedBoard] = []
    observed_at = _now()
    batch = batch_id or discovery_id

    for board in boards:
        if (board.provider, board.board_token) in existing:
            continue
        entry = WatchlistEntry(
            watchlist_id=f"watchlist_{uuid.uuid4()}",
            provider=ATSProvider(board.provider),
            board_token=board.board_token,
            company=board.company,
            state="active",
            first_seen=WatchlistFirstSeen(
                source_kind=SourceKind.ATS,  # board confirmed by our own ATS poll; see module docstring
                source_url=board.careers_url,
                query_key=f"discovery:{discovery_id}",
                batch_id=batch,
                observed_at=observed_at,
            ),
        )
        add_to_watchlist(entry, home_root, target, gig_id)
        evidence[f"{board.provider}:{board.board_token}"] = {
            "company": board.company,
            "sponsorship": board.sponsorship,
            "sponsorship_evidence": board.sponsorship_evidence,
            "evidence_source_url": board.evidence_source_url,
            "evidence_verified": board.evidence_verified,
            "found_by": list(board.found_by),
            "matching_us_postings": board.matching_us_postings,
            "first_found_at": observed_at,
            "last_confirmed_at": observed_at,
            "discovery_id": discovery_id,
        }
        added.append(board)

    # A rediscovered board (already on the watchlist) still gets its
    # evidence refreshed (last_confirmed_at bumped, first_found_at kept).
    for board in boards:
        key = f"{board.provider}:{board.board_token}"
        if key in evidence and board not in added:
            evidence[key]["last_confirmed_at"] = observed_at

    _save_evidence(home_root, target, evidence)
    return added


__all__ = [
    "MergedBoard",
    "add_usable_boards_to_watchlist",
    "evidence_for",
    "merge_and_verify",
    "normalize_company",
]
