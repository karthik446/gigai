# Exa Agent spike (S23, stage 1)

Research-only. No product code. See the operator-approved brief at
`.orchestrator/workers/specs/scout-exa-agent-discovery.brief.txt` and the
dispatched task at `.orchestrator/workers/specs/exa-agent-spike.txt` for the
full instructions this directory answers.

Deliverable doc:
[`docs/development/v0.1.9/spikes/S23-scout-setup-interview-exa-agent.md`](../../docs/development/v0.1.9/spikes/S23-scout-setup-interview-exa-agent.md).

## Layout

- `spend_guard.py` — the spend tally/cap enforcement (`research/exa_agent_spike/spend.jsonl`,
  hard $2/run and $30/total caps, refuses to start a call that would break
  either).
- `redact.py` — strips the API key/auth headers from a raw response before
  it is ever written to disk.
- `run_agent.py` — builds the query from the interview-answer template,
  calls `POST https://api.exa.ai/agent/runs` (create) then polls
  `GET https://api.exa.ai/agent/runs/{id}` to completion, tallies spend,
  saves a redacted fixture.
- `check_boards.py` — for every company/careers_url an agent run returned,
  resolves the board URL and polls it with Scout's existing
  `gigai.scout.find_jobs.ats_board_clients` (free, no Exa spend) to test
  whether it's a Greenhouse/Ashby/Lever board with >=1 posting matching the
  configured roles in the US.
- `metrics.py` — computes the per-run metrics table (cost, latency,
  companies returned, % new, % US, % sponsorship-with-grounding, % boards
  resolving, cost per new usable board) from a fixture + its `check_boards`
  output.
- `spend.jsonl` — the running spend tally (one JSON line per Exa call:
  timestamp, run label, effort, cap, estimated/reported cost, cumulative).
  Never contains the API key.
- `fixtures/` — one redacted raw agent response per run label, named
  `<run_label>.json`. Used by stage-2 tests (no live calls in tests).

## Key handling

`EXA_API_KEY` is read via `gigai.secrets_store.get("EXA_API_KEY")` (falling
back from `os.environ` first, matching `exa_client.py`'s
`_require_api_key`). The key is never printed, logged, echoed, or written to
a fixture; `redact.py` strips the `Authorization`/`x-api-key` request
headers before any response is persisted, and `run_agent.py` never logs the
outgoing headers dict.

## Running a call

```sh
uv run python research/exa_agent_spike/run_agent.py --help
```

Every invocation appends to `spend.jsonl` and refuses to start a call whose
worst-case cost would exceed the per-run ($2) or total ($30) cap. See the
S23 doc for which runs were actually executed and their results.
