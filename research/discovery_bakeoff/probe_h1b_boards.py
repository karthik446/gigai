"""Slug-guess Greenhouse/Lever/Ashby board tokens for H-1B sample employers, verified live.

No agent, no search API -- this is the H-1B baseline's own board-discovery
step (task's candidate 4: "probe Greenhouse/Ashby/Lever boards for each
[employer] (slug guesses from the company name, verified by a real board
poll)"). For each employer name in the derived sample
(``research/discovery_bakeoff/h1b_sample/top200_employers.json``), this
script:

1. Generates a small set of plausible board-token slugs from the employer
   name (lowercase, strip legal suffixes like "Inc"/"LLC"/"Corp"/"Ltd",
   strip punctuation, join with no separator and with hyphens -- the two
   conventions Greenhouse/Lever/Ashby tokens actually use in practice, per
   the fixtures already collected in ``research/exa_agent_spike/fixtures/``
   e.g. ``boards.greenhouse.io/grafanalabs``,
   ``jobs.ashbyhq.com/nimblerx``).
2. Polls each (provider, slug) guess against the real public API via
   ``gigai.scout.find_jobs.ats_board_clients.ATSBoardClients`` -- exactly
   ``check_boards.py``'s own client, reused, not reimplemented -- and keeps
   only guesses that resolve (HTTP 200, valid board) AND have >=1 posting
   matching the configured roles in the US (reusing ``check_boards.py``'s
   ``_row_is_us`` logic, imported directly).
3. Records cost as $0 (these are the same free public ATS REST endpoints
   S23 and this bake-off's ``check_boards.py`` already call -- no Exa/
   OpenAI/Tavily spend). Still writes to ``spend.jsonl`` with
   ``cost_dollars: 0.0`` so the tally file has a complete record of every
   external call this bake-off made, not just the paid ones.

Slug-guessing is inherently lossy (a wrong guess -> 404, silently skipped,
not a false claim) -- this script reports exactly how many of the top-N
employers it tried, how many guesses were attempted, and how many resolved,
so the "effort/cost to do this at scale" metric the task asks for is a real
count, not an estimate.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from check_boards import _row_is_us  # noqa: E402
from spend_guard import check_and_reserve, record_actual  # noqa: E402

from gigai.scout.find_jobs.ats_board_clients import ATSBoardClientError, ATSBoardClients  # noqa: E402
from gigai.scout.find_jobs.contracts import FindJobsConfig, ModelTarget, SourceToggles  # noqa: E402

_CLIENTS = ATSBoardClients()
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|incorporated|llc|l\.l\.c|corp|corporation|co|company|ltd|limited|llp|lp|group|holdings?|technologies|technology|solutions|services|the)\b\.?",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+", re.IGNORECASE)

PROVIDERS = ("greenhouse", "lever", "ashby")


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


def guess_slugs(employer_name: str) -> list[str]:
    """Generate a small set of plausible board-token slugs from an employer name."""

    base = employer_name
    # Drop a "d/b/a X" or parenthetical clause -- keep the primary name.
    base = re.split(r"\bd/b/a\b|\(", base, maxsplit=1, flags=re.IGNORECASE)[0]
    stripped = _LEGAL_SUFFIXES.sub("", base).strip()
    candidates = set()
    for text in {base.strip(), stripped}:
        if not text:
            continue
        no_sep = _NON_ALNUM.sub("", text).lower()
        hyphenated = _NON_ALNUM.sub("-", text).strip("-").lower()
        if no_sep:
            candidates.add(no_sep)
        if hyphenated:
            candidates.add(hyphenated)
    return sorted(candidates)


@dataclass(frozen=True)
class ProbeResult:
    employer_name: str
    slugs_tried: int
    provider: str | None
    board_token: str | None
    posting_count: int
    matching_role_us_count: int
    is_usable_board: bool


def probe_employer(client: httpx.Client, employer_name: str, roles: tuple[str, ...]) -> ProbeResult:
    slugs = guess_slugs(employer_name)
    config = _minimal_config(roles)
    tried = 0
    for slug in slugs:
        for provider in PROVIDERS:
            tried += 1
            try:
                rows = _CLIENTS.list_board(client, provider, slug, config)
            except ATSBoardClientError:
                continue
            except Exception:
                continue
            if rows:
                us_matches = sum(1 for row in rows if _row_is_us(row))
                return ProbeResult(
                    employer_name=employer_name,
                    slugs_tried=tried,
                    provider=provider,
                    board_token=slug,
                    posting_count=len(rows),
                    matching_role_us_count=us_matches,
                    is_usable_board=us_matches >= 1,
                )
    return ProbeResult(
        employer_name=employer_name,
        slugs_tried=tried,
        provider=None,
        board_token=None,
        posting_count=0,
        matching_role_us_count=0,
        is_usable_board=False,
    )


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-file", required=True, type=Path)
    parser.add_argument("--out-file", required=True, type=Path)
    parser.add_argument("--top-n", type=int, default=50, help="how many top employers (by case count) to probe")
    parser.add_argument("--roles", nargs="+", default=["staff backend", "senior backend"])
    args = parser.parse_args()

    sample = json.loads(args.sample_file.read_text(encoding="utf-8"))
    employers = sample["employers"][: args.top_n]

    decision = check_and_reserve("h1b_board_probe", f"probe_top{args.top_n}", worst_case_cost_dollars=0.0)
    print(f"[spend guard] free (public ATS APIs), reserved $0 (cumulative stays ${decision.cumulative_after_dollars:.4f})", file=sys.stderr)

    started = time.monotonic()
    results: list[ProbeResult] = []
    with httpx.Client(timeout=20.0) as client:
        for e in employers:
            results.append(probe_employer(client, e["employer_name"], tuple(args.roles)))
    elapsed = time.monotonic() - started
    record_actual("h1b_board_probe", f"probe_top{args.top_n}", 0.0)

    usable = [r for r in results if r.is_usable_board]
    total_slug_attempts = sum(r.slugs_tried for r in results)
    args.out_file.write_text(
        json.dumps(
            {
                "employers_probed": len(results),
                "usable_boards_found": len(usable),
                "total_slug_attempts": total_slug_attempts,
                "elapsed_seconds": round(elapsed, 2),
                "results": [asdict(r) for r in results],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        f"{len(usable)}/{len(results)} usable boards, {total_slug_attempts} slug attempts, "
        f"{elapsed:.1f}s -> {args.out_file}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
