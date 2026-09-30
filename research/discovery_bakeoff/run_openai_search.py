"""Call OpenAI's Responses API web_search tool for one discovery run, under the spend guard.

Verified live 2026-09-24 against https://developers.openai.com/api/docs/guides/tools-web-search,
https://developers.openai.com/api/docs/guides/structured-outputs, and
https://developers.openai.com/api/docs/pricing (READ), cross-checked by one live
smoke-test call (EXECUTED, see ``spend.jsonl`` run_label ``smoketest_luna``).

- Endpoint: ``POST https://api.openai.com/v1/responses``.
- Tool: ``{"type": "web_search"}`` (current name; ``web_search_preview`` is the
  legacy alias, not used here).
- Model: ``gpt-6-luna`` -- the cheapest model that supports both the
  ``web_search`` tool and strict structured output ($0.10/1M input,
  $0.50/1M output tokens, vs ``gpt-6-sol`` at $2/$10 and ``gpt-6-astra`` at
  $10/$50 per the pricing page). Confirmed live: a smoke-test call returned
  a grounded, cited answer (``output_types: reasoning, web_search_call,
  message``) for ~$0.011 all-in.
- Structured output: ``text.format = {"type": "json_schema", "name": ...,
  "schema": <JSON Schema, additionalProperties:false, all fields required>,
  "strict": true}``.
- Pricing: web_search tool call is $10.00/1000 calls ($0.01/call) *plus*
  token costs at the model's normal per-token rate (search-result content
  counts as input tokens). No separate "search content" fee at gpt-6-luna's
  non-reasoning-preview tier per the pricing page.
- Auth: ``Authorization: Bearer $OPENAI_API_KEY``.
- Sync: the Responses API call is synchronous (no polling needed) --
  ``status: "completed"`` comes back directly in the POST response for a
  non-background request.

Usage:
    uv run python research/discovery_bakeoff/run_openai_search.py \
        --run-label openai_run1 --query-file query.txt --schema-file schema.json \
        [--cap-dollars 2.0]

Never logs the API key or an unredacted raw response (redact.py strips
credential-shaped keys/values before anything is saved).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))  # allow `python run_openai_search.py` directly
from redact import redact  # noqa: E402
from spend_guard import SpendCapExceeded, check_and_reserve, record_actual  # noqa: E402

from gigai import secrets_store  # noqa: E402

RESPONSES_URL = "https://api.openai.com/v1/responses"
MODEL = "gpt-6-luna"
# Pricing per token, from https://developers.openai.com/api/docs/pricing (read 2026-09-24).
INPUT_COST_PER_TOKEN = 0.10 / 1_000_000
OUTPUT_COST_PER_TOKEN = 0.50 / 1_000_000
WEB_SEARCH_CALL_COST = 0.01  # $10.00 / 1000 calls, non-reasoning-preview tier
# Worst-case reservation for one call: generous token ceiling (32k in + 4k out)
# plus up to 3 web_search sub-calls (the model may search multiple times per
# turn) -- this is a conservative upper bound reserved BEFORE the call, not
# the typical actual cost.
WORST_CASE_COST_DOLLARS = 32_000 * INPUT_COST_PER_TOKEN + 4_000 * OUTPUT_COST_PER_TOKEN + 3 * WEB_SEARCH_CALL_COST

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _require_api_key() -> str:
    import os

    api_key = os.environ.get("OPENAI_API_KEY") or secrets_store.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit(
            "OPENAI_API_KEY is not set (checked os.environ and ~/.gigai/.env via gigai.secrets_store)"
        )
    return api_key


def _actual_cost(usage: dict) -> float:
    input_tokens = usage.get("input_tokens", 0)
    output_tokens = usage.get("output_tokens", 0)
    return input_tokens * INPUT_COST_PER_TOKEN + output_tokens * OUTPUT_COST_PER_TOKEN


def run(run_label: str, query: str, output_schema: dict, cap_dollars: float | None) -> Path:
    """Execute one guarded Responses API call with web_search; saves a redacted fixture."""

    worst_case = cap_dollars if cap_dollars is not None else WORST_CASE_COST_DOLLARS
    decision = check_and_reserve("openai_web_search", run_label, worst_case)
    print(
        f"[spend guard] {run_label}: reserved ${decision.worst_case_cost_dollars:.4f} "
        f"(cumulative ${decision.cumulative_before_dollars:.4f} -> ${decision.cumulative_after_dollars:.4f})",
        file=sys.stderr,
    )

    api_key = _require_api_key()
    body = {
        "model": MODEL,
        "input": query,
        "tools": [{"type": "web_search"}],
        "text": {
            "format": {
                "type": "json_schema",
                "name": "companies_result",
                "schema": output_schema,
                "strict": True,
            }
        },
    }
    started = time.monotonic()
    with httpx.Client(timeout=300.0) as client:
        response = None
        max_attempts = 6
        for attempt in range(max_attempts):
            response = client.post(
                RESPONSES_URL,
                json=body,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            if response.status_code != 429:
                break
            # The account's 200k TPM limit is shared across this script's own
            # prior (possibly client-timed-out-but-server-completed) attempts
            # -- wait the server's own retry-after PLUS a fixed buffer well
            # past a full rolling-minute window, not just retry-after+1s,
            # since a short wait was observed to still collide with the same
            # window in practice (see the S24 doc's OpenAI candidate notes).
            retry_after = float(response.headers.get("retry-after", "10"))
            wait_seconds = retry_after + 45.0
            print(
                f"[retry] {run_label}: 429 tokens-per-minute limit, waiting {wait_seconds:.1f}s "
                f"(attempt {attempt + 1}/{max_attempts})",
                file=sys.stderr,
            )
            time.sleep(wait_seconds)
        assert response is not None
        response.raise_for_status()
        result = response.json()
    latency_seconds = time.monotonic() - started

    web_search_calls = sum(1 for o in result.get("output", []) if o.get("type") == "web_search_call")
    usage = result.get("usage", {})
    token_cost = _actual_cost(usage)
    actual_cost = token_cost + web_search_calls * WEB_SEARCH_CALL_COST
    record_actual("openai_web_search", run_label, actual_cost)

    # Extract the structured message content (the schema-validated JSON) for
    # convenience -- the full redacted response is saved too.
    structured_text = None
    for o in result.get("output", []):
        if o.get("type") == "message":
            for c in o.get("content", []):
                if c.get("type") == "output_text":
                    structured_text = c.get("text")

    fixture = {
        "run_label": run_label,
        "model": MODEL,
        "latency_seconds": round(latency_seconds, 3),
        "web_search_calls": web_search_calls,
        "usage": usage,
        "actual_cost_dollars": round(actual_cost, 6),
        "request": redact({"model": MODEL, "input": query, "tools": [{"type": "web_search"}]}),
        "response": redact(result),
        "structured_output": json.loads(structured_text) if structured_text else None,
    }
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = FIXTURES_DIR / f"{run_label}.json"
    fixture_path.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[saved] {fixture_path} (actual cost ${actual_cost:.4f})", file=sys.stderr)
    return fixture_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--query-file", required=True, type=Path)
    parser.add_argument("--schema-file", required=True, type=Path)
    parser.add_argument("--cap-dollars", type=float, default=None, help="worst-case reservation; defaults to a conservative estimate")
    args = parser.parse_args()

    query = args.query_file.read_text(encoding="utf-8").strip()
    output_schema = json.loads(args.schema_file.read_text(encoding="utf-8"))

    try:
        run(args.run_label, query, output_schema, args.cap_dollars)
    except SpendCapExceeded as exc:
        raise SystemExit(f"REFUSED: {exc}") from None


if __name__ == "__main__":
    main()
