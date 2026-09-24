"""Call Exa's Agent API for one discovery run, under the spend guard.

Endpoint verified against https://exa.ai/docs/reference/agent-api-guide and
https://exa.ai/pricing (both read 2026-09-23):

- ``POST https://api.exa.ai/agent/runs`` creates a run. Body:
  ``{"query": str, "effort": "minimal"|"low"|"medium"|"high"|"xhigh"|"auto"|"max",
  "outputSchema": <JSON Schema object>, "input": {"data": [...], "exclusion": [...]},
  "budget": {"maxCostDollars": float}}`` (``budget`` only meaningful for
  ``auto``/``max``; range $1-$100, defaults $5/$20 -- this spike always sets
  it explicitly to <= the $2 per-run cap for those two efforts).
  ``input.exclusion`` entries are free-form OBJECTS, not bare strings --
  confirmed both from the docs' own example (``{"animal": "goat"}``) and
  live: a first attempt at ``{"exclusion": ["Coupang", ...]}`` (bare
  strings) was rejected with a 400 ``INVALID_REQUEST``
  (``"Expected object, received string"`` at ``input.exclusion[0]``). This
  script sends ``{"company": name}`` per excluded company.
- Auth: ``Authorization: Bearer $EXA_API_KEY`` (the docs also list
  ``x-api-key: $EXA_API_KEY`` as an accepted alternative; this script uses
  ``Authorization: Bearer`` since that's the header shown in the docs'
  quickstart curl example).
- Async polling: the create call returns ``{"id": str, "status": "queued"}``;
  poll ``GET https://api.exa.ai/agent/runs/{id}`` (docs suggest ~4s
  intervals) until ``status`` is a terminal value (``completed``, ``failed``,
  ``cancelled``). This script polls rather than using SSE (``stream: true``)
  -- simpler for a one-shot spike script, no functional difference in what
  is measured.
- Output: ``output.text``, ``output.structured`` (schema-validated per
  ``outputSchema``), ``output.grounding`` (citations), ``costDollars``
  (actual cost).

Fixed efforts (``minimal``..``xhigh``) have an exact documented price and
need no ``budget``. ``auto``/``max`` are metered; this script always passes
``budget.maxCostDollars`` for those two, capped at
``spend_guard.PER_RUN_CAP_DOLLARS`` ($2) or below.

Usage:
    uv run python research/exa_agent_spike/run_agent.py \
        --run-label low_strict_v1 --effort low --query-file query.txt \
        --schema-file schema_strict.json [--cap-dollars 2.0]

Never logs the API key, the outgoing headers, or an unredacted response.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))  # allow `python run_agent.py` directly
from redact import redact  # noqa: E402
from spend_guard import (  # noqa: E402
    FIXED_EFFORT_COST_DOLLARS,
    METERED_EFFORTS,
    SpendCapExceeded,
    check_and_reserve,
    record_actual,
)

from gigai import secrets_store  # noqa: E402

AGENT_RUNS_URL = "https://api.exa.ai/agent/runs"
POLL_INTERVAL_SECONDS = 4.0
POLL_TIMEOUT_SECONDS = 300.0
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _require_api_key() -> str:
    import os

    api_key = os.environ.get("EXA_API_KEY") or secrets_store.get("EXA_API_KEY")
    if not api_key:
        raise SystemExit(
            "EXA_API_KEY is not set (checked os.environ and ~/.gigai/.env via gigai.secrets_store)"
        )
    return api_key


def _build_body(query: str, effort: str, output_schema: dict, exclusion: list[str], cap_dollars: float | None) -> dict:
    body: dict[str, object] = {
        "query": query,
        "effort": effort,
        "outputSchema": output_schema,
    }
    if exclusion:
        # input.exclusion entries are free-form objects, not bare strings --
        # confirmed against the docs' own example ({"animal": "goat"}), and
        # against a live 400 INVALID_REQUEST response this spike hit when it
        # first tried bare strings ("Expected object, received string" at
        # input.exclusion[0]). "company" is the domain key for this use case.
        body["input"] = {"exclusion": [{"company": name} for name in exclusion]}
    if effort in METERED_EFFORTS:
        assert cap_dollars is not None
        body["budget"] = {"maxCostDollars": cap_dollars}
    return body


def _poll(client: httpx.Client, run_id: str, api_key: str) -> dict:
    deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
    headers = {"Authorization": f"Bearer {api_key}"}
    while True:
        response = client.get(f"{AGENT_RUNS_URL}/{run_id}", headers=headers)
        response.raise_for_status()
        payload = response.json()
        status = payload.get("status")
        if status in TERMINAL_STATUSES:
            return payload
        if time.monotonic() > deadline:
            raise SystemExit(f"run {run_id} did not reach a terminal status within {POLL_TIMEOUT_SECONDS}s (last status={status!r})")
        time.sleep(POLL_INTERVAL_SECONDS)


def run(run_label: str, effort: str, query: str, output_schema: dict, exclusion: list[str], cap_dollars: float | None) -> Path:
    """Execute one guarded Agent API run; saves a redacted fixture; returns its path."""

    decision = check_and_reserve(run_label, effort, cap_dollars)
    print(
        f"[spend guard] {run_label}: reserved ${decision.worst_case_cost_dollars:.3f} "
        f"(cumulative ${decision.cumulative_before_dollars:.3f} -> ${decision.cumulative_after_dollars:.3f})",
        file=sys.stderr,
    )

    api_key = _require_api_key()
    body = _build_body(query, effort, output_schema, exclusion, cap_dollars)
    started = time.monotonic()

    with httpx.Client(timeout=30.0) as client:
        create_response = client.post(
            AGENT_RUNS_URL,
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )
        create_response.raise_for_status()
        created = create_response.json()
        run_id = created.get("id")
        if not run_id:
            raise SystemExit(f"agent run create response had no id: {redact(created)!r}")
        result = _poll(client, run_id, api_key)

    latency_seconds = time.monotonic() - started
    # costDollars is an object ({"agentCompute", "emails", "phoneNumbers",
    # "search", "total"}), confirmed live (auto_strict_v4_cap2 fixture) --
    # not a plain number as first assumed. ".total" is the actual charge.
    cost_breakdown = result.get("costDollars")
    actual_cost = cost_breakdown.get("total") if isinstance(cost_breakdown, dict) else cost_breakdown
    if isinstance(actual_cost, (int, float)):
        record_actual(run_label, effort, float(actual_cost))

    fixture = {
        "run_label": run_label,
        "effort": effort,
        "cap_dollars": cap_dollars,
        "latency_seconds": round(latency_seconds, 3),
        "request": redact({"query": query, "effort": effort, "outputSchema": output_schema, "exclusion_count": len(exclusion)}),
        "response": redact(result),
    }
    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    fixture_path = FIXTURES_DIR / f"{run_label}.json"
    fixture_path.write_text(json.dumps(fixture, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"[saved] {fixture_path}", file=sys.stderr)
    return fixture_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-label", required=True)
    parser.add_argument("--effort", required=True, choices=sorted(FIXED_EFFORT_COST_DOLLARS) + sorted(METERED_EFFORTS))
    parser.add_argument("--query-file", required=True, type=Path)
    parser.add_argument("--schema-file", required=True, type=Path)
    parser.add_argument("--exclusion-file", type=Path, default=None, help="JSON list of company names to exclude")
    parser.add_argument("--cap-dollars", type=float, default=None, help="required for auto/max; must be <= $2")
    args = parser.parse_args()

    query = args.query_file.read_text(encoding="utf-8").strip()
    output_schema = json.loads(args.schema_file.read_text(encoding="utf-8"))
    exclusion = json.loads(args.exclusion_file.read_text(encoding="utf-8")) if args.exclusion_file else []

    try:
        run(args.run_label, args.effort, query, output_schema, exclusion, args.cap_dollars)
    except SpendCapExceeded as exc:
        raise SystemExit(f"REFUSED: {exc}") from None


if __name__ == "__main__":
    main()
