"""Tavily search + extract for one discovery run, under the spend guard.

Verified live 2026-09-24 against
https://docs.tavily.com/documentation/api-reference/endpoint/search,
https://docs.tavily.com/documentation/api-reference/endpoint/extract, and
https://docs.tavily.com/documentation/api-credits (READ).

Tavily has no agent/list-building endpoint (unlike Exa's Agent API or
Websets) -- this script assembles the company list itself: it runs a
``search`` call, then ``extract``s each result URL's raw content, then a
company/board/sponsorship record is derived from that content by simple
text heuristics (looking for a Greenhouse/Lever/Ashby URL in the extracted
text and an H-1B/sponsorship keyword near the company name). This is
weaker than a provider that returns a schema-validated list directly
(Exa Agent, OpenAI web_search+structured-output) -- Tavily's contribution
is raw search+extract, not judgment, and that gap is a genuine per-
candidate finding for the bake-off doc, not hidden.

- Search endpoint: ``POST https://api.tavily.com/search``.
  Body: ``{"query": str, "search_depth": "basic"|"advanced", "max_results":
  int, "include_answer": bool, "include_raw_content": bool}``.
  Pricing: basic = 1 credit ($0.008), advanced = 2 credits ($0.016) per
  call (pay-as-you-go rate from the docs).
- Extract endpoint: ``POST https://api.tavily.com/extract``.
  Body: ``{"urls": [str, ...], "extract_depth": "basic"|"advanced"}``.
  Pricing: basic = 1 credit per 5 successful URLs ($0.0016/URL), advanced =
  2 credits per 5 ($0.0032/URL).
- Auth: ``Authorization: Bearer $TAVILY_API_KEY``.
- Sync: both endpoints are synchronous HTTP calls, no polling.

This script uses ``search_depth: "advanced"`` (better content-token
coverage, still $0.016/call) and ``extract_depth: "basic"`` (cheapest;
"advanced" adds little for board-URL/sponsorship-keyword detection on
plain job-board and H-1B-tracker pages).

Usage:
    uv run python research/discovery_bakeoff/run_tavily_search.py \
        --run-label tavily_run1 --query-file query.txt --max-results 10

Never logs the API key or an unredacted raw response.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))  # allow `python run_tavily_search.py` directly
from redact import redact  # noqa: E402
from spend_guard import SpendCapExceeded, check_and_reserve, record_actual  # noqa: E402

from gigai import secrets_store  # noqa: E402

SEARCH_URL = "https://api.tavily.com/search"
EXTRACT_URL = "https://api.tavily.com/extract"
SEARCH_DEPTH = "advanced"
EXTRACT_DEPTH = "basic"
SEARCH_COST = 0.016  # advanced, pay-as-you-go
EXTRACT_COST_PER_URL = 0.0016  # basic, per successful URL (1 credit / 5 URLs)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

_ATS_URL_RE = re.compile(
    r"https?://(?:boards\.greenhouse\.io/[a-zA-Z0-9_-]+"
    r"|jobs\.lever\.co/[a-zA-Z0-9_-]+"
    r"|jobs\.ashbyhq\.com/[a-zA-Z0-9_-]+)"
)
_SPONSORSHIP_RE = re.compile(r"\b(h-?1b|visa sponsorship|sponsor.{0,20}visa|work authorization)\b", re.IGNORECASE)


def _require_api_key() -> str:
    import os

    api_key = os.environ.get("TAVILY_API_KEY") or secrets_store.get("TAVILY_API_KEY")
    if not api_key:
        raise SystemExit(
            "TAVILY_API_KEY is not set (checked os.environ and ~/.gigai/.env via gigai.secrets_store)"
        )
    return api_key


def _derive_companies(search_results: list[dict], extracted: dict[str, str]) -> list[dict]:
    """Heuristically derive {company, careers_url, sponsorship_evidence, source} from raw content."""

    companies = []
    for result in search_results:
        url = result.get("url", "")
        title = result.get("title", "")
        content = extracted.get(url, "") or result.get("content", "")
        ats_match = _ATS_URL_RE.search(content) or _ATS_URL_RE.search(url)
        sponsorship_match = _SPONSORSHIP_RE.search(content)
        if not ats_match:
            continue
        companies.append(
            {
                "company": title.split(" - ")[0].split(" | ")[0].strip() or title,
                "careers_url": ats_match.group(0),
                "ats_provider": (
                    "greenhouse" if "greenhouse" in ats_match.group(0)
                    else "lever" if "lever" in ats_match.group(0)
                    else "ashby" if "ashbyhq" in ats_match.group(0)
                    else "unknown"
                ),
                "sponsorship": "yes" if sponsorship_match else "unknown",
                "sponsorship_evidence": (
                    content[max(0, sponsorship_match.start() - 80): sponsorship_match.end() + 80]
                    if sponsorship_match else ""
                ),
                "source": url,
            }
        )
    return companies


def run(run_label: str, query: str, max_results: int, cap_dollars: float | None) -> Path:
    """Execute one guarded Tavily search + extract; saves a redacted fixture."""

    # Worst case: 1 search call + up to max_results extract calls.
    worst_case = cap_dollars if cap_dollars is not None else SEARCH_COST + max_results * EXTRACT_COST_PER_URL
    decision = check_and_reserve("tavily", run_label, worst_case)
    print(
        f"[spend guard] {run_label}: reserved ${decision.worst_case_cost_dollars:.4f} "
        f"(cumulative ${decision.cumulative_before_dollars:.4f} -> ${decision.cumulative_after_dollars:.4f})",
        file=sys.stderr,
    )

    api_key = _require_api_key()
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    import time

    started = time.monotonic()
    with httpx.Client(timeout=60.0) as client:
        search_response = client.post(
            SEARCH_URL,
            json={
                "query": query,
                "search_depth": SEARCH_DEPTH,
                "max_results": max_results,
                "include_answer": False,
                "include_raw_content": True,
            },
            headers=headers,
        )
        search_response.raise_for_status()
        search_result = search_response.json()
        results = search_result.get("results", [])

        urls = [r.get("url") for r in results if r.get("url")]
        extracted: dict[str, str] = {}
        extract_result = {}
        if urls:
            extract_response = client.post(
                EXTRACT_URL,
                json={"urls": urls, "extract_depth": EXTRACT_DEPTH},
                headers=headers,
            )
            extract_response.raise_for_status()
            extract_result = extract_response.json()
            for item in extract_result.get("results", []):
                extracted[item.get("url", "")] = item.get("raw_content", "")

    latency_seconds = time.monotonic() - started

    successful_extracts = len(extract_result.get("results", [])) if urls else 0
    actual_cost = SEARCH_COST + (successful_extracts / 5.0) * (EXTRACT_COST_PER_URL * 5)
    record_actual("tavily", run_label, actual_cost)

    companies = _derive_companies(results, extracted)

    fixture = {
        "run_label": run_label,
        "search_depth": SEARCH_DEPTH,
        "extract_depth": EXTRACT_DEPTH,
        "latency_seconds": round(latency_seconds, 3),
        "actual_cost_dollars": round(actual_cost, 6),
        "request": redact({"query": query, "max_results": max_results}),
        "search_response": redact(search_result),
        "extract_response": redact(extract_result),
        "derived_companies": companies,
    }
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = FIXTURES_DIR / f"{run_label}.json"
    fixture_path.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"[saved] {fixture_path} (actual cost ${actual_cost:.4f}, "
        f"{len(companies)} companies derived from {len(results)} search results)",
        file=sys.stderr,
    )
    return fixture_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--query-file", required=True, type=Path)
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--cap-dollars", type=float, default=None)
    args = parser.parse_args()

    query = args.query_file.read_text(encoding="utf-8").strip()

    try:
        run(args.run_label, query, args.max_results, args.cap_dollars)
    except SpendCapExceeded as exc:
        raise SystemExit(f"REFUSED: {exc}") from None


if __name__ == "__main__":
    main()
