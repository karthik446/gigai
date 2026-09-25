"""Check usability of agent-returned companies WITHOUT extra Exa spend.

For every company/careers_url an agent run returned, resolve the board URL
and poll it with Scout's existing ATS clients (public Greenhouse/Lever/Ashby
APIs -- free, no Exa call). A company is a "usable board" when its URL
parses as one of the three supported providers AND polling that board
returns >=1 posting matching the configured roles in the US.

Uses ``gigai.scout.find_jobs.ats_board_clients`` and
``gigai.scout.find_jobs.contracts`` exactly as Scout's product code does
(read-only import; this script writes nothing under ``src/``).

US-match caveat (found live, not assumed): Lever's and Ashby's listers
populate ``PostingRow.countries`` from parsed posting geography, but
Greenhouse's lister (``list_greenhouse_board``) never sets ``countries`` --
it only has a free-text ``location`` string (confirmed by reading
``ats_board_clients.py`` and by a live Grafana Labs board poll during this
spike, where every row had ``countries=None``). Treating an empty
``countries`` as "assume US" would have silently counted Grafana's
Germany/Ireland/Spain/Sweden/UK-remote rows as US matches. This script
instead: uses ``countries`` when the lister provides it (Lever/Ashby); for
Greenhouse (or any row with no parsed countries), does a conservative
substring check against ``location`` for "united states"/"usa"/"remote -
us"/a bare US state abbreviation pattern, and otherwise marks the row
``us_match=False`` (unverified, never assumed).
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClientError, ATSBoardClients
from gigai.scout.find_jobs.contracts import (
    FindJobsConfig,
    ModelTarget,
    SourceToggles,
    parse_board_url,
)

_CLIENTS = ATSBoardClients()

# Conservative, substring-only US-location signal for rows with no parsed
# ``countries`` (Greenhouse never sets it -- see module docstring). No
# guessing: a location string that doesn't match one of these stays
# unverified, never assumed US.
_US_LOCATION_RE = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?)\b"
    r"|remote\s*-\s*us\b"
    r"|,\s*(AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY)\b",
    re.IGNORECASE,
)


def _row_is_us(row: object) -> bool:
    countries = getattr(row, "countries", None)
    if countries:
        return "US" in countries
    location = getattr(row, "location", "") or ""
    return bool(_US_LOCATION_RE.search(location))


def _minimal_config(roles: tuple[str, ...]) -> FindJobsConfig:
    """A throwaway config carrying only what ``list_board`` reads: roles/countries."""

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


@dataclass(frozen=True)
class BoardCheckResult:
    company: str
    careers_url: str
    parses_as_ats: bool
    provider: str | None
    board_token: str | None
    resolved: bool
    error: str | None
    posting_count: int
    matching_role_us_count: int
    is_usable_board: bool


def check_one(client: httpx.Client, company: str, careers_url: str, roles: tuple[str, ...]) -> BoardCheckResult:
    parsed = parse_board_url(careers_url)
    if parsed is None:
        return BoardCheckResult(
            company=company,
            careers_url=careers_url,
            parses_as_ats=False,
            provider=None,
            board_token=None,
            resolved=False,
            error="not a recognized greenhouse/lever/ashby URL",
            posting_count=0,
            matching_role_us_count=0,
            is_usable_board=False,
        )
    provider, board_token = parsed
    config = _minimal_config(roles)
    try:
        rows = _CLIENTS.list_board(client, provider, board_token, config)
    except ATSBoardClientError as exc:
        return BoardCheckResult(
            company=company,
            careers_url=careers_url,
            parses_as_ats=True,
            provider=provider,
            board_token=board_token,
            resolved=False,
            error=f"{exc.code}: board did not resolve",
            posting_count=0,
            matching_role_us_count=0,
            is_usable_board=False,
        )
    us_matches = sum(1 for row in rows if _row_is_us(row))
    return BoardCheckResult(
        company=company,
        careers_url=careers_url,
        parses_as_ats=True,
        provider=provider,
        board_token=board_token,
        resolved=True,
        error=None,
        posting_count=len(rows),
        matching_role_us_count=us_matches,
        is_usable_board=us_matches >= 1,
    )


def check_many(companies: list[dict[str, str]], roles: tuple[str, ...]) -> list[BoardCheckResult]:
    """``companies`` is a list of ``{"company": str, "careers_url": str}`` dicts."""

    results: list[BoardCheckResult] = []
    with httpx.Client(timeout=20.0) as client:
        for entry in companies:
            company = entry.get("company", "")
            careers_url = entry.get("careers_url", "")
            if not careers_url:
                results.append(
                    BoardCheckResult(
                        company=company,
                        careers_url=careers_url,
                        parses_as_ats=False,
                        provider=None,
                        board_token=None,
                        resolved=False,
                        error="no careers_url in agent output",
                        posting_count=0,
                        matching_role_us_count=0,
                        is_usable_board=False,
                    )
                )
                continue
            results.append(check_one(client, company, careers_url, roles))
    return results


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--companies-file", required=True, type=Path, help="JSON list of {company, careers_url}")
    parser.add_argument("--roles", nargs="+", default=["staff backend", "senior backend"])
    parser.add_argument("--out-file", required=True, type=Path)
    args = parser.parse_args()

    companies = json.loads(args.companies_file.read_text(encoding="utf-8"))
    results = check_many(companies, tuple(args.roles))
    args.out_file.write_text(
        json.dumps([asdict(r) for r in results], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    usable = sum(1 for r in results if r.is_usable_board)
    print(f"{usable}/{len(results)} usable boards -> {args.out_file}", file=sys.stderr)


if __name__ == "__main__":
    main()
