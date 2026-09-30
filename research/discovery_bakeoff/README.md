# Company discovery bake-off (S24)

Research-only. No product code. Compares four company-discovery approaches
on the same task S23 used (Exa Agent), after the operator judged S23's
`low`-effort Exa Agent result too unreliable to build on alone (identical
inputs returned 6 companies once, 0 the next time; 1 run in 4 found nothing
usable -- see S23's Repeatability section, kept in the maintainers' local
orchestrator docs).

Deliverable doc: S24-company-discovery-bakeoff.md (kept in the maintainers'
local orchestrator docs).

## Candidates

1. **Exa Websets** -- Exa's list-building product. **Blocked**: the
   account's `EXA_API_KEY` gets a `401` ("Your team does not have access to
   the API. Upgrade to a Pro plan to get access.") on every Websets
   endpoint tried, confirmed live at $0 cost (a plan-gate 401, not a
   metered call). See the S24 doc's Investigate section.
2. **OpenAI web_search** (Responses API, `gpt-6-luna`, structured output).
3. **Tavily** (search + extract; this bake-off assembles the company list
   itself from raw results -- Tavily has no agent/list-building endpoint).
4. **H-1B baseline** (DOL OFLC LCA disclosure data, free, deterministic) +
   a Greenhouse/Lever/Ashby slug-guess board probe.
5. Reference only: S23's Exa Agent `low` numbers (not re-run here).

## Layout

- `spend_guard.py` -- this bake-off's own spend cap/tally ($2/call,
  $10 total; separate from S23's `research/exa_agent_spike/spend.jsonl`).
- `redact.py`, `check_boards.py` -- copied verbatim from
  `research/exa_agent_spike/` (S23), reused per the task's instruction not
  to rewrite them.
- `run_openai_search.py` -- one guarded Responses API call with the
  `web_search` tool and strict JSON-schema structured output.
- `run_tavily_search.py` -- one guarded Tavily `search` + `extract` call
  pair; derives a company list from raw content with simple ATS-URL /
  sponsorship-keyword heuristics (Tavily itself does not return a
  schema-validated company list).
- `h1b_baseline.py` -- streams the DOL LCA disclosure workbook, filters to
  certified H-1B software/backend-SOC rows, aggregates by employer, writes
  the small derived sample.
- `probe_h1b_boards.py` -- slug-guesses Greenhouse/Lever/Ashby board tokens
  from H-1B sample employer names, verifies each guess with a real board
  poll (reuses `check_boards.py`'s row-matching logic).
- `metrics.py` -- shared per-run metric definitions (cost, latency,
  companies, % new, % usable, % grounded, $/new usable board), matching
  S23's definitions, recomputed from rows.
- `spend.jsonl` -- running spend tally, same two-line-per-call pattern as
  S23 (`reported: false` = worst-case reservation before the call,
  `reported: true` = actual cost after).
- `fixtures/` -- redacted raw responses per run label.
- `inputs/` -- shared query template, strict schema (Exa-shape and
  OpenAI-strict-mode shape), and the 23-entry exclusion list (UAT's
  Coupang/ClickHouse/Gen Digital + every company found across all of S23's
  fixtures, reused from `research/exa_agent_spike/inputs/exclusion_week2.json`).
- `h1b_sample/` -- small (<=200 row), git-tracked derived sample of top
  H-1B-sponsoring software/backend employers. The raw ~252MB DOL workbook
  itself lives in `h1b_scratch/` (gitignored, never committed).

## Key handling

Every key (`EXA_API_KEY`, `OPENAI_API_KEY`, `TAVILY_API_KEY`) is read via
`gigai.secrets_store.get(...)` (env first, then `~/.gigai/.env`), never
printed, logged, or written to a fixture. `redact.py` strips
credential-shaped keys/values before anything is persisted.

## Running a call

```sh
uv run python research/discovery_bakeoff/run_openai_search.py --help
uv run python research/discovery_bakeoff/run_tavily_search.py --help
uv run --with openpyxl python research/discovery_bakeoff/h1b_baseline.py --help
uv run python research/discovery_bakeoff/probe_h1b_boards.py --help
```

`h1b_baseline.py` needs `openpyxl` (not a project dependency; run via
`uv run --with openpyxl`, or `--with pandas --with openpyxl` -- neither is
added to `pyproject.toml`, this is a one-off research script).

Every paid-candidate invocation appends to `spend.jsonl` and refuses to
start a call whose worst-case cost would exceed the per-call ($2) or total
($10) cap. See the S24 doc for which runs were actually executed and their
results.
