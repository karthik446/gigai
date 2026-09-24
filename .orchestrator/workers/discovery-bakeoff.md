# Worker: discovery-bakeoff (S24)

**Status:** Done. Deliverable:
[`docs/development/v0.1.9/spikes/S24-company-discovery-bakeoff.md`](../../docs/development/v0.1.9/spikes/S24-company-discovery-bakeoff.md).

## Spend (final, recounted from `research/discovery_bakeoff/spend.jsonl`)

- **Actual spend: $0.2957** of the $10 cap (2.96%).
- **Worst-case reserved: $3.06** of the $10 cap (30.6%) -- the gap is 7
  failed OpenAI attempts that each reserved $0.20 worst-case but were
  never billed (OpenAI doesn't charge for a `429`-rejected request).
- **Max single call reservation: $0.30** (Tavily) -- well under the
  $2/call cap. No call ever exceeded it.

## What ran (READ vs EXECUTED both cited in the doc)

1. **Exa Websets** -- blocked. `401 Unauthorized` ("Your team does not
   have access to the API. Upgrade to a Pro plan to get access.") on
   every endpoint tried. $0 cost, confirmed live, twice.
2. **OpenAI Responses API `web_search`** (`gpt-6-luna`, cheapest model
   supporting the tool + structured output) -- 3 successful runs (2, 2, 4
   companies; 5 unique, all new, 88% usable, 100% grounded), $0.0253/new-
   usable-board blended. Hit the account's 200k-TPM rate limit repeatedly
   on the first attempt of each run (query is 45-75k input tokens); fixed
   by widening retry backoff from `retry-after+1s`/4 attempts to
   `retry-after+45s`/6 attempts.
3. **Tavily** (search + extract, no agent endpoint) -- 3 runs, 0 new-and-
   usable companies across all 3 (only hit: Instrumentl, already
   excluded). Tavily's plain search mostly returns third-party aggregator
   pages, not ATS board roots, for this query.
4. **H-1B baseline** (DOL FY2026 Q3 LCA disclosure data, 251,850,891
   bytes, downloaded to gitignored `research/discovery_bakeoff/h1b_scratch/`,
   never committed) -- 19,796 certified H-1B software/backend-SOC
   employers extracted; top-200 sample committed (92KB). Slug-guess board
   probe found 3/200 usable (Roblox, Airbnb, Coinbase), all new, $0, 286s.
   Found and fixed a case-sensitivity bug in the slug-normalizing regex
   before the full probe ran.

## Recommendation (from the doc)

No single winner: H-1B baseline (free) + OpenAI `web_search` (novelty/
precision, ~$0.025/new-usable-board) run together; Websets needs an
operator plan-upgrade decision before it can be evaluated at all; Tavily
not recommended as currently implemented for this task.

## Files touched (all within owned scope)

- `research/discovery_bakeoff/**` (new: scripts, fixtures, inputs,
  h1b_sample, spend.jsonl, README; h1b_scratch/ gitignored, not tracked)
- `docs/development/v0.1.9/spikes/S24-company-discovery-bakeoff.md` (new)
- `docs/development/v0.1.9/spikes/README.md` (one bullet added)
- `.gitignore` (one line: `research/discovery_bakeoff/h1b_scratch/`)

No product code under `src/` touched. No git add/commit/stash/reset/clean
run. No key ever printed/logged (verified programmatically against all
three loaded key values across every file in `research/discovery_bakeoff/`).

## Open follow-ups (not blocking; noted in the doc's Open questions)

- OpenAI's 3-run repeatability (no zero-company run) is a small sample --
  same size as S23's original pass before its own r1 replication found a
  spread. Worth another 2-3 runs before leaning on it hard.
- Exa Websets pricing/quality entirely unknown -- needs an operator
  decision on whether to upgrade the Exa plan.
- H-1B baseline's 3/200 hit rate could likely improve by re-ranking away
  from raw case-count (which favors mega-corps/outsourcing firms) toward
  a startup/mid-size-employer signal -- not built, flagged as next step.
