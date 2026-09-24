# exa-agent-spike worker status

**Task:** task_67c090b8c2c5 (dispatch ctx_0d8622652611). STAGE 1 spike of
`.orchestrator/workers/specs/scout-exa-agent-discovery.brief.txt`: learn the
Scout setup-interview questions, the answers->query template, the output
schema, and the default effort/cap for a weekly Exa Agent company-discovery
run. Docs + scripts only; no product code under `src/`.

**Status:** Done. Deliverable:
[`docs/development/v0.1.9/spikes/S23-scout-setup-interview-exa-agent.md`](../../docs/development/v0.1.9/spikes/S23-scout-setup-interview-exa-agent.md)
(spike number confirmed S23 via `orca orchestration ask`, not S22 -- S22 is
taken by the historical `S22-01` phase-3 spike). One S23 bullet added to
`docs/development/v0.1.9/spikes/README.md`.

## Spend (final; matches `research/exa_agent_spike/spend.jsonl`, recounted)

- **Actual spend: $0.759** total across 5 live Agent API runs (Exa's own
  `costDollars.total` per run: $0.025 + $0.025 + $0.025 + $0.100 + $0.584).
- **Worst-case reserved: $2.20** (the spend guard's pre-call reservation
  sum; conservative -- includes one failed-request $0.025 reservation from
  a 400 caused by a request-shape bug, fixed before the retry).
- **Max single-run worst-case: $2.00** (the one `auto`-effort run, capped
  explicitly via `budget.maxCostDollars`).
- Both well under the $30 total cap and the $2 per-run cap; no run refused.

## What was built (owned files only)

- `research/exa_agent_spike/`: `spend_guard.py` (cap enforcement + tally),
  `redact.py` (credential stripping), `run_agent.py` (Agent API client),
  `check_boards.py` (free ATS-board usability check, reusing
  `gigai.scout.find_jobs.ats_board_clients`), `inputs/` (query/schema/
  exclusion-list files), `fixtures/` (5 redacted agent-run fixtures + 5
  board-check outputs), `spend.jsonl`, `README.md`.
- `docs/development/v0.1.9/spikes/S23-scout-setup-interview-exa-agent.md`
  (full ticket format: Status/Problem/Operator decisions/Tasks/Acceptance/
  Investigate/Results/Recommendation/Open questions/Non-claims/Change log).
- `docs/development/v0.1.9/spikes/README.md`: one S23 bullet appended.

## Key findings (see the S23 doc for full detail)

1. **Decisive primary-metric result:** low effort + strict schema + a query
   that explicitly asks for the ATS board *root* URL (not "careers page")
   scored **$0.0042/new usable board**, 100% usable, 100% new -- the same
   query at effort `auto` (capped $2) scored $0.146/board, and a naive
   first-phrasing attempt scored $0.025/board with only 20% board
   usability. Recommend `low` effort as the stage-2 default.
2. Live correction to the Agent API request shape: `input.exclusion`
   entries must be objects (`{"company": name}`), not bare strings -- a
   first live call got a 400 on this.
3. Live-fetched 4 grounding `source` URLs: 3/4 real and substantiated,
   1/4 was a dead link (404) cited as sponsorship evidence -- stage 2
   should verify grounding URLs resolve before showing them to the
   operator.
4. Found (and fixed, in the spike's own script) a real gap in Scout's
   `list_greenhouse_board`: it never populates `PostingRow.countries`
   (only Lever/Ashby do), which had silently inflated a US-match count from
   5 to 30 before the fix.

## What's left

Stage 2 (implementation) is a separate, operator-reviewed packet per the
brief -- not started. Open questions (scale beyond ~15 companies/run,
whether `auto`'s worse $/board holds across more than one run,
`output.grounding`'s own citation shape, the untested "always watch"/
"stage/size" interview fields) are recorded in the S23 doc's Open
questions section.

## r1 (coordinator review), task_c66feb7acb03 / ctx_81f9e22bb467

**Status:** Done. Fixed the arithmetic error the coordinator flagged
("100x-plus" -> actually ~35x, 0.146/0.0042 ~ 34.8, matching the
Recommendation's already-correct "3x-35x"), softened "decisive" language,
and ran 3 more live replication runs of the recommended cell (v4 query,
strict schema, `low` effort) to check repeatability, all within the $0.15
r1 cap.

### r1 spend (final; matches `research/exa_agent_spike/spend.jsonl`, recounted)

- **r1 incremental actual spend: $0.075** (3 x $0.025 low-effort runs),
  under the $0.15 cap.
- **New grand total (8 runs): $0.834 actual**, **$2.275 worst-case
  reserved**, max single-run worst-case still $2.00 -- both well under $30.
- Caught and fixed a bookkeeping bug during r1 itself: a backfill script
  double-recorded 3 `reported: true` actual-cost lines for the 3 new runs
  (they'd already been recorded once by `run_agent.py`'s own
  now-fixed-mid-spike cost-recording code). Found via recount (11 reported
  lines for 8 runs -- too many), fixed by removing the 3 duplicate lines
  from `spend.jsonl` before finalizing the doc's totals. Corrected actual
  total: $0.834 (was briefly, incorrectly, $0.909 before the dedup).

### r1 replication findings

1. **Exact repeat (same 3-entry exclusion list):** identical query/schema/
   effort/exclusion returned **0 companies** the second time (vs. 6 the
   first time), same $0.025 cost either way. The recommended cell is **not
   deterministic**.
2. **Week-2 simulation A** (exclusion list grown to 23 entries: UAT's 3 +
   all 20 companies found across every prior run): 2 new companies, both
   100% usable (real, currently-pollable boards with a matching US role).
3. **Week-2 simulation B** (same 23-entry list, independent run): 2
   different new companies, 0 overlap with A or the exclusion list, but
   **0/2 usable** -- both board URLs resolved fine but had zero current
   postings at all (a freshness gap, not a bad URL).
4. **Blended across all 4 low/strict/v4 runs:** $0.10 total, 10 companies,
   8 usable -> **$0.0125/usable board** blended, vs. the original single
   run's $0.0042 headline -- the headline was the best of four outcomes,
   not the typical one. `low` is still cheapest of the effort levels tested
   (even blended), but the product should expect variance, not a fixed
   yield per weekly run (S23 doc Recommendation point 8).

Doc updated: Results (new rows + a Repeatability subsection), Spend
reconciliation, Recommendation (points 3 and 8), Open questions,
Non-claims, Change log. All new fixtures grepped clean of
`authorization`/`x-api-key`/`api_key`; no literal key found anywhere under
`research/exa_agent_spike/` (checked programmatically, key never printed).
