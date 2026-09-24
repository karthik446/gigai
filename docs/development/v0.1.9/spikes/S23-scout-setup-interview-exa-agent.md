# S23 — Scout setup interview + Exa Agent company discovery (stage 1 spike)

**Requested:** 2026-09-24, from the operator-approved brief
`.orchestrator/workers/specs/scout-exa-agent-discovery.brief.txt` (STAGE 1 of
a two-stage plan; stage 2 is an implementation packet, only after the
operator reviews this spike).
**Status:** Research recorded 2026-09-24; coordinator-reviewed r1 (same
date) added 3 replication runs and corrected an arithmetic error (see
Change log). Documentation + scripts only; no product code under `src/` was
added or changed. Live Exa spend was authorized by the operator (brief:
"$20-30 of live Exa spend to learn the right questions"; dispatched task
cap: $2/run, $30 total, r1's replication cap: $0.15) and was used: **8**
live Agent API runs total (5 initial + 3 r1 replication), actual spend
**$0.834** (see Results), reserved worst-case **$2.275** (both well under
the $30 cap; every run's worst case was <= $2, the per-run cap).
**READ vs EXECUTED:** the Agent API's endpoint, request/response shape,
pricing, and effort levels below are READ from
[`https://exa.ai/docs/reference/agent-api-guide`](https://exa.ai/docs/reference/agent-api-guide)
and [`https://exa.ai/pricing`](https://exa.ai/pricing) (both fetched
2026-09-24), cross-checked against one field-shape correction found by
EXECUTING a live call (see Investigate §1). The metrics table is EXECUTED:
eight live `POST https://api.exa.ai/agent/runs` calls plus free ATS-board
polls via `gigai.scout.find_jobs.ats_board_clients`, plus three live
`WebFetch` spot-checks of grounding-source URLs. All scripts and fixtures
are under `research/exa_agent_spike/`.

## Problem

Scout's `acquire` step already finds ATS boards via Exa search and polls
them for postings, but two things are unsolved: (1) there's no operator
setup interview to drive what Scout looks for -- roles, location,
sponsorship need, and company exclude/watch lists are not captured anywhere
today; and (2) discovering genuinely **new** companies (not already
watchlisted) with real visa-sponsorship evidence is not something Exa's
plain `/search` endpoint does well -- sponsorship is unknown on 69/69 rows
per `scout-uat-0181-bugs.brief.txt` B3. Exa's separate Agent API is built
for exactly this ("build lists from open-ended criteria... with citations")
but its request/response shape, cost behavior, and actual output quality
for this use case were unverified. This spike answers: what questions
should the setup interview ask, what query/schema should the weekly
discovery run send, and does it actually find usable boards cheaply.

## Operator decisions (recorded; not re-opened here)

- Setup interview asks the operator questions **once**; the answers drive
  the product. Fields: roles/level (+ titles to avoid), countries +
  remote/hybrid/onsite + city, needs-visa-sponsorship (plain yes/no, hard
  filter), companies to exclude, companies to always watch, company
  stage/size + industries in/out, must-have/deal-breaker stack, cadence +
  budget.
- The weekly Exa Agent run discovers **new** companies only; the
  known-company list is passed via the Agent API's exclusion input so the
  agent skips them. Daily/on-click ATS polling is unchanged; posting text
  still comes only from the ATS APIs, never from the agent.
- Budget: operator authorized $20-30 of live spend for this spike
  ("we can burn 20-30$ and learn the right way"); the dispatched task added
  a stricter per-run cap ($2) and running tally file, both honored here
  (actual total spent: $0.759).
- Monitors (Exa's scheduled/webhook product) are explicitly out of scope:
  webhook delivery needs a public endpoint and gigai is local-only.

## Tasks

1. Verify the Agent API from Exa's docs: endpoint, request body
   (`input.data`/`input.exclusion`, `outputSchema`, effort levels, cost
   cap), async polling/SSE, `output.grounding`.
2. Draft the setup-interview question list with the operator's UAT answers
   as defaults.
3. Vary query phrasings x schema shapes x effort; run live, under the spend
   guard.
4. For every returned company, check board usability for free via Scout's
   existing ATS clients (no extra Exa spend).
5. Compute per-run metrics, primary metric cost-per-new-usable-board.
6. Save every raw response as a redacted fixture for stage-2 tests.

## Acceptance criteria

- Every claim below cites a URL or a command/script actually run.
- The metrics table's totals reconcile against `spend.jsonl` (recounted in
  Results, not assumed).
- Every fixture under `research/exa_agent_spike/fixtures/` is grepped clean
  of `Authorization`/`x-api-key`/`api_key` header material (one benign
  false-positive substring match found and shown to be safe -- see
  Non-claims).
- No single run's worst-case cost exceeded $2; total worst-case reserved
  spend did not exceed $30.

## Investigate

### 1. Agent API shape (verified against the docs, corrected once live)

- **Endpoint:** `POST https://api.exa.ai/agent/runs` (create),
  `GET https://api.exa.ai/agent/runs/{id}` (poll).
  Source: [agent-api-guide](https://exa.ai/docs/reference/agent-api-guide).
- **Auth:** `Authorization: Bearer $EXA_API_KEY` (the docs also list
  `x-api-key: $EXA_API_KEY` as accepted; this spike used
  `Authorization: Bearer`, matching the docs' own quickstart curl example).
- **Request body:** `{"query": str, "effort": "minimal"|"low"|"medium"|"high"|"xhigh"|"auto"|"max",
  "outputSchema": <JSON Schema>, "input": {"data": [...], "exclusion": [...]},
  "budget": {"maxCostDollars": float}}`. Field is **`outputSchema`**
  (camelCase), not `output_schema` -- the docs' own example body uses
  `outputSchema` verbatim.
- **`input.exclusion` shape correction (found live, not in the docs'
  prose):** each exclusion entry must be an **object**, not a bare string.
  The docs' own example shows `{"animal": "goat"}`-style entries but never
  states this is a hard requirement. This spike's first live call sent
  `"exclusion": ["Coupang", "ClickHouse", "Gen Digital"]` and got back a
  `400 INVALID_REQUEST` with
  `"Expected object, received string"` at `input.exclusion[0..2]`
  (`research/exa_agent_spike/run_agent.py`'s `_build_body`, comment cites
  this). Fixed to `{"company": name}` per excluded company, which the API
  accepted. **This is the one place the docs alone were insufficient and a
  live call was needed to get the request right** -- record this for
  stage 2's implementation.
- **Cost/budget:** fixed efforts have an exact price:
  `minimal $0.012, low $0.025, medium $0.10, high $0.50, xhigh $1.00`
  (matches the brief's FACTS and
  [exa.ai/pricing](https://exa.ai/pricing) exactly). `auto`/`max` are
  metered (`$0.10/ACU + $0.005/search`), default caps `$5`/`$20`, overridable
  via `budget.maxCostDollars` (range $1-$100). Every live `auto`/`max` call
  this spike made set `budget.maxCostDollars` explicitly to `$2.0`, the
  spike's own per-run cap.
- **Async pattern:** create returns `{"id", "status": "queued"}`; poll until
  a terminal status (`completed`, `failed`, `cancelled`); docs suggest ~4s
  poll intervals (this spike's `run_agent.py` uses that interval, 300s
  timeout). SSE (`stream: true` / `Accept: text/event-stream`) is
  documented as an alternative; not used here -- polling is simpler for a
  one-shot script and measures the same cost/latency/output.
- **Output:** `output.text` (prose answer), `output.structured`
  (schema-validated per `outputSchema`), `output.grounding` (field-level
  citations -- present but this spike relied on the schema's own `source`
  field plus spot-fetching sources, not on parsing `output.grounding`'s
  internal shape, since the per-company `source` field already gives a URL
  per row and was sufficient for the usability check). `costDollars` is
  **an object** (`{"agentCompute", "emails", "phoneNumbers", "search",
  "total"}`), not a plain number -- another live correction:
  `run_agent.py` originally read `costDollars` as a number and silently
  skipped recording the actual cost for every run until this was caught
  and fixed (see Non-claims).

### 2. Setup-interview question list (final wording, with UAT defaults)

Drafted from the brief's field list; defaults shown are the operator's UAT
config (`run_d73cb030`) and are pre-filled, not forced -- the interview
still asks so the operator can change them.

| # | Question | Default (from UAT) |
| - | --- | --- |
| 1 | What roles/level are you targeting? (free text, e.g. "staff backend", "senior backend") | `staff backend`, `senior backend` |
| 2 | Any job titles to avoid? (free text, optional) | *(none set)* |
| 3 | Which countries should postings be in? (ISO country codes) | `US` |
| 4 | Remote-only, hybrid, onsite, or any? | remote or Denver, CO |
| 5 | If not remote-only, what city/area? | `Denver, CO` |
| 6 | Do you need visa sponsorship? (yes/no -- hard filter) | `yes` |
| 7 | Any companies to exclude (not interested / current employer)? | `Coupang`, `ClickHouse`, `Gen Digital` |
| 8 | Any companies to always watch regardless of other filters? | *(none set)* |
| 9 | Preferred company stage/size? Industries to include/exclude? | *(none set)* |
| 10 | Any must-have or deal-breaker tech stack? | *(none set)* |
| 11 | How often should discovery run, and what's an acceptable Exa spend per run? | weekly, <= $2/run (this spike's recommendation -- see Recommendation) |

### 3. Answers -> query template

The winning query (`low_strict_v4_boardurl`, see Results) was built by
explicitly instructing the agent to find the ATS **board root URL**, not a
job-posting or aggregator link -- the first, more naive phrasing
(`low_strict_v1`) asked for "careers page or job board URL" and got back
mostly job-aggregator links (`offerpilotai.com`, a direct job-posting URL),
only 1/5 of which resolved to a real, pollable ATS board. Template (`{}` =
interview answer):

```text
Find {countries} companies hiring for {roles} roles ({remote_clause}) that
use Greenhouse, Lever, or Ashby as their applicant tracking system AND are
documented to sponsor {countries} work visas (H-1B). Only return companies
where you can identify the company's OWN job board base URL on one of these
three platforms (e.g. https://boards.greenhouse.io/<company>,
https://jobs.lever.co/<company>, or https://jobs.ashbyhq.com/<company> --
the board root, not a link to a specific job posting or a third-party
aggregator like LinkedIn, Indeed, or another job site). Skip a company if
you cannot find its own board URL on one of those three ATS platforms. For
each company give: the company name, the exact board URL, the ATS
provider, and visa sponsorship evidence with a source URL (an H-1B filing
record, careers page, or job posting). Exclude: {exclusion_list}.
```

Full text used: `research/exa_agent_spike/inputs/query_v4_board_url_explicit.txt`.
Two other phrasings were tried (`query_v1_direct.txt`,
`query_v2_sponsorship_first.txt`, `query_v3_board_first.txt` -- v2/v3
drafted but not run live, see "skipped cells" below) to sanity-check
wording sensitivity; only v1 and v4 were actually executed, since v4's
"board root, not a job posting" instruction was the single change that
moved usable-board yield from 20% to 100% and further wording tweaks
looked unlikely to teach more per dollar.

### 4. Output schema (strict, recommended) vs loose

Strict (recommended, `research/exa_agent_spike/inputs/schema_strict.json`):

```json
{
  "type": "object",
  "properties": {
    "companies": {
      "type": "array",
      "maxItems": 15,
      "items": {
        "type": "object",
        "properties": {
          "company": { "type": "string" },
          "careers_url": { "type": "string", "format": "uri" },
          "ats_provider": { "type": "string", "enum": ["greenhouse", "lever", "ashby", "other", "unknown"] },
          "board_token": { "type": "string" },
          "sponsorship": { "type": "string", "enum": ["yes", "no", "unknown"] },
          "sponsorship_evidence": { "type": "string" },
          "source": { "type": "string", "format": "uri" }
        },
        "required": ["company", "careers_url", "ats_provider", "sponsorship", "sponsorship_evidence", "source"]
      }
    }
  },
  "required": ["companies"]
}
```

Loose (`research/exa_agent_spike/inputs/schema_loose.json`) asked for just
`{name, notes}` with `notes` as free prose. Result: the loose schema's
`notes` field *did* contain a source URL and sponsorship evidence in
prose (confirmed by reading `low_loose_v4.json`'s output), but as
unstructured text -- extracting `careers_url`/`source` back out needs a
regex or a second LLM pass. The strict schema's `source` field is directly
usable input to a "spot-check a sample of sources" step with no extra
parsing, and returned 3x more companies in the same run (6 vs 2, likely
because a fixed strict schema constrains the agent's search space less
ambiguously than open-ended prose -- not independently confirmed beyond
this single paired comparison).

### 5. Board usability check (free, no Exa spend)

For every company an agent run returned, `check_boards.py` resolves
`careers_url`/`source`-adjacent board URL via
`gigai.scout.find_jobs.contracts.parse_board_url` and polls it with
`gigai.scout.find_jobs.ats_board_clients.ATSBoardClients` (the same public
Greenhouse/Lever/Ashby REST APIs Scout's `acquire` step already calls) --
free, no Exa spend, uses `config.roles` to filter matching postings exactly
as `matches_roles` does in product code.

**Bug found and fixed during this spike:** the first version of
`check_boards.py` treated a `PostingRow` with no parsed `countries` as
"assume US" (`not row.countries or "US" in row.countries`). Reading
`ats_board_clients.py` directly shows `list_greenhouse_board` never
populates `PostingRow.countries` (only Lever's and Ashby's listers parse
geography) -- so this inflated Grafana Labs' "US-matching" count to 30 (its
entire board, including Germany/Ireland/Spain/Sweden/UK-remote roles) before
the fix. Fixed to fall back to a conservative substring match against the
free-text `location` field for rows with no parsed countries (see
`check_boards.py`'s `_row_is_us`), which reduced Grafana's US-matching count
from 30 to 5 -- still usable (>=1 match), but an honest number instead of a
wrong one. **This is a real gap in Scout's own Greenhouse country parsing**,
not just a spike-script bug; stage 2 (or a follow-up ticket) should decide
whether `list_greenhouse_board` should also populate `countries` from
`location` text the way this spike's fallback does.

### 6. Grounding spot-check (live fetches, not assumed)

Per the acceptance criteria ("% sponsorship claims with a real grounding
source (spot-check a sample of sources by fetching them)"), 4 `source` URLs
from `low_strict_v4_boardurl` were fetched live with `WebFetch`:

| Company | Source URL | Result |
| --- | --- | --- |
| Palantir Technologies | `ellis.com/visa-sponsors/palantir-technologies-inc/h1b` | **Real, substantiated** -- 697 LCAs, detailed DOL filing data |
| NimbleRx | `ellis.com/visa-sponsors/nimblerx-inc/h1b` | **Real, substantiated** -- 13 LCAs, filing detail |
| Profound | `tryprofound.com/careers/9b429...` | **Real** -- live posting states "happy to support visa sponsorship for qualified international candidates" (posting is titled Fullstack, not specifically Backend -- a minor role-title mismatch, not a fabrication) |
| Grafana Labs | `bluedoor.sh/data/h1b-jobs/companies/grafana-labs` | **Broken -- HTTP 404.** The agent cited a source URL that does not resolve. |

3/4 (75%) of spot-checked sources were real and substantiated; 1/4 (25%)
was a dead link cited as evidence. This is a genuine finding for stage 2:
**grounding sources need to be verified (at minimum, an HTTP HEAD/GET
check) before being shown to the operator or merged into the watchlist as
"evidence," since the agent can cite a URL that doesn't resolve.**

## Results (metrics table)

Eight live runs total: 5 from the initial pass (varying query x schema x
effort) plus 3 replication runs added in r1 (coordinator review) to check
how much the recommended cell's numbers actually repeat, rather than resting
the recommendation on one run. `$/new usable board` is the PRIMARY metric.
"new %" = share of returned companies not already in that run's exclusion
list (recounted against each fixture and its exclusion input, not assumed).
"ground %" = share of returned companies whose schema included a non-empty
grounding text AND source URL (loose schema's prose-embedded source didn't
count as a *structured* field, hence 0% there even though the information
was present -- see Investigate §4).

| Run label | Query | Schema | Effort | Exclusion list | Actual cost | Latency | Companies | New % | Usable boards | Usable % | Grounded % | $ / new usable board |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `low_strict_v1` | v1 (naive) | strict | low | 3 (UAT) | $0.025 | 37.7s | 5 | 100% | 1 | 20% | 100% | $0.0250 |
| `low_strict_v4_boardurl` | **v4 (board-URL-explicit)** | strict | low | 3 (UAT) | $0.025 | 29.5s | 6 | 100% | 6 | 100% | 100% | **$0.0042** |
| `low_loose_v4` | v4 | loose | low | 3 (UAT) | $0.025 | 21.7s | 2 | 100% | 2 | 100% | 0%* | $0.0125 |
| `medium_strict_v4` | v4 | strict | medium | 3 (UAT) | $0.100 | 66.6s | 2 | 100% | 2 | 100% | 100% | $0.0500 |
| `auto_strict_v4_cap2` | v4 | strict | auto (cap $2) | 3 (UAT) | $0.584 | 128.7s | 6 | 100% | 4 | 67% | 100% | $0.1460 |
| `low_strict_v4_boardurl_repeat1` (r1) | v4 | strict | low | 3 (UAT, same as above) | $0.025 | 91.4s | **0** | -- | 0 | -- | -- | **n/a (no results)** |
| `low_strict_v4_week2_a` (r1) | v4 | strict | low | 23 (UAT 3 + all 20 companies found so far) | $0.025 | 25.2s | 2 | 100% | 2 | 100% | 100% | $0.0125 |
| `low_strict_v4_week2_b` (r1) | v4 | strict | low | 23 (same as week2_a) | $0.025 | 50.3s | 2 | 100% | 0 | 0% | 100% | **inf (no usable board)** |

\* loose schema's evidence/source were present as prose inside `notes`, not
as a separate structured field -- see Investigate §4.

### Repeatability (r1, coordinator-requested)

The recommended cell (v4 query, strict schema, `low` effort) had been run
**once** before r1 (6 companies, 6 usable, $0.0042/board). r1 repeated it 3
more times under the same guards:

- **Repeat 1 (identical inputs):** the exact same query, schema, effort,
  and exclusion list returned **zero companies** the second time
  (`stopReason: "schema_satisfied"`, `output.text`: "No company met all of
  the stated constraints with sufficiently explicit evidence as of
  2026-09-24. The strongest sponsorship matches were outside the allowed
  geography... while remote matches either lacked explicit employer
  sponsorship or did not clearly have a staff/senior backend title."). Same
  $0.025 cost as the first run -- Exa charges for a completed run
  regardless of result count. **The run is not deterministic: identical
  inputs produced 6 companies once and 0 companies the next time.**
- **Week-2 simulation A** (exclusion list grown to the UAT 3 plus all 20
  companies any run had found so far, sent as `input.exclusion` objects):
  2 new companies (VRChat, Docker), both resolved to real, currently
  pollable boards with >=1 matching US posting -- 100% usable, $0.0125/new
  usable board.
- **Week-2 simulation B** (same 23-entry exclusion list as A, a second
  independent run): 2 different new companies (Turnkey, Lively), zero
  overlap with A's results or with the exclusion list (genuine novelty),
  but **0/2 resolved to a currently usable board** -- both board URLs
  resolved fine (real Ashby/Greenhouse boards, no HTTP error) but each had
  **zero** postings at all right now (`posting_count: 0`), not just zero
  matching the configured roles. Read directly as: the agent's knowledge of
  what's currently open lags reality, or these companies' boards emptied
  out between whenever Exa indexed them and today.
- **Blended across all 4 low/strict/v4-query runs:** $0.10 total cost, 10
  companies returned, 8 usable boards -> **$0.0125/usable board** blended
  -- still strong, but 3x worse than the single best run's $0.0042 headline,
  and one of the four runs (25%) returned nothing usable at all. The
  headline $0.0042 number was the best of four outcomes, not the typical
  one.

**Spend reconciliation** (recounted directly from
`research/exa_agent_spike/spend.jsonl`, not assumed; includes r1's 3
replication runs):

- Worst-case reserved total: **$2.275** (sum of all `reported: false`
  lines' final `cumulative_dollars`).
- Actual reported total (sum of `reported: true` `cost_dollars` lines,
  Exa's own `costDollars.total` per run): **$0.834**
  (0.025 + 0.025 + 0.025 + 0.100 + 0.584 + 0.025 + 0.025 + 0.025 = 0.834).
- Largest single run's worst-case reservation: **$2.00** (`auto`, capped at
  the spike's own $2 per-run limit) -- no run exceeded the $2 cap.
- r1's incremental actual spend: **$0.075** (3 low-effort runs at $0.025
  each), under r1's own $0.15 cap.
- Both totals are well under the $30 total cap.

**Skipped cells and why:** the brief allowed 3-4 phrasings x 2 schemas x 3
efforts (up to 24 cells) but said "not every cell is required." This spike
ran 5 of those cells and stopped once the primary metric showed roughly a
35x spread (auto at $0.146/board vs low-board-url-explicit at
$0.0042/board -- 0.146/0.0042 ~ 34.8) with no sign that spending more would
change the recommendation. That spread is what a single run of each cell
showed, not a result replicated across repeated runs -- see the r1
replication in Results, which repeats the recommended cell 3 more times to
check how much this varies before leaning further on "decisive":
- `query_v2_sponsorship_first.txt` and `query_v3_board_first.txt` were
  drafted (interview-answer variations) but not run live -- v4's
  "board root, not a job posting" instruction was the one change that
  mattered (20% -> 100% usable-board yield); v2/v3 vary sponsorship-first
  vs board-first framing, which looked unlikely to move the metric further
  for the cost of two more live calls.
- `high`/`xhigh` effort and `max` (metered, $20 default cap, beta header
  required) were not run: `medium` already cost 4x `low` for the *same*
  2-company result as `medium`'s neighbor cells, and `auto` (metered,
  cheaper in practice than `medium`+`high` combined at $0.584) already
  showed metered effort returns *more* companies than fixed `medium`/`high`
  but at *worse* $/usable-board than `low` -- spending $0.50-$1.00 more on
  `high`/`xhigh`/`max` to confirm that trend was judged not worth it against
  "you do NOT have to spend the budget. Stop and report once the
  questions/schema/effort are clear."
- The loose-schema x `medium`/`auto` cells were skipped once `low_loose_v4`
  showed the loose schema's structural downside (Investigate §4) applies at
  any effort level -- it's a schema-shape property, not something effort
  would fix.

## Recommendation

For stage 2's weekly discovery run:

1. **Schema: strict**, not loose. `careers_url`, `ats_provider`,
   `sponsorship`, `sponsorship_evidence`, and `source` as separate typed
   fields, matching the brief's suggested shape almost exactly (this spike
   added `board_token` as optional, which the agent left empty in every
   run -- the agent infers ATS provider and board URL but not a separate
   token; product code can derive `board_token` from `careers_url` via the
   existing `parse_board_url`, so drop the schema field and derive it
   instead).
2. **Query: explicitly ask for the ATS board root URL**, not "careers page"
   or "job board URL" -- the single highest-leverage wording change found
   (20% -> 100% usable-board yield, same cost). Use the template in
   Investigate §3.
3. **Default effort: `low`** ($0.025/run). Across all 4 runs of this exact
   cell (1 original + 3 r1 replications), `low` still beat every other
   effort tested on *blended* cost-per-usable-board ($0.0125 blended vs
   `medium`'s $0.0500 and `auto`'s $0.1460, both single runs) -- but r1's
   replication also showed `low` itself is noisy run-to-run: 6 companies/6
   usable once, then 0/0, then 2/2, then 2/0, on otherwise-identical or
   incrementally-adjusted inputs. **Recommend defaulting to `low` on cost
   grounds, but do not plan the product around a fixed
   "N usable boards per run" expectation** -- see point 8 below on cadence.
   `auto` returned the same company count as `low` in one single paired run
   but at 23x the cost with a worse usable-board rate (67% vs 100%);
   plausibly `auto`'s larger search budget pulls in some companies whose
   current postings don't match the role, not a pattern confirmed across
   more than one `auto` run. Keep the operator-visible manual "try harder"
   option that uses `auto` capped at $2 for a company the operator
   specifically wants more effort spent finding.
4. **Cap: $2/run hard ceiling**, `budget.maxCostDollars: 2.0` set explicitly
   whenever `auto`/`max` is used (never rely on Exa's own $5/$20 defaults).
   At the recommended `low` effort this cap is never approached (`low` costs
   $0.025, 1.25% of the cap) -- it exists to bound the "try harder" option
   in point 3.
5. **Verify every grounding `source` URL resolves** (HTTP GET/HEAD) before
   showing sponsorship evidence to the operator or merging a company into
   the watchlist -- Investigate §6 found a live 404 cited as evidence in
   this spike's own sample.
6. **`list_greenhouse_board` should populate `PostingRow.countries`** the
   way Lever's and Ashby's listers do (Investigate §5) -- not required for
   stage 2 to ship, but the current gap means Greenhouse-board US-matching
   silently falls back to free-text `location` parsing, which is weaker
   than Lever/Ashby's structured geography.
7. **`input.exclusion` entries must be objects** (`{"company": name}`), not
   bare strings -- a real integration detail for stage 2's client code,
   found only by a live 400 (Investigate §1).
8. **Weekly cadence: run it, but expect variance, not a guaranteed hit.**
   r1's 2 "week 2" simulations (exclusion list grown to 23 entries) each
   found exactly 2 new companies, matching the brief's "the win is novelty"
   framing -- growing the exclusion list did not shrink results to zero the
   way a naive dedup-by-search-space model might predict. But usability
   swung hard between the two: 2/2 usable in one run, 0/2 usable in the
   other (both boards resolved but had zero current postings -- a
   freshness gap, not a bad URL). Combined with the identical-input repeat
   returning 0 companies, **a single weekly `low`-effort run should be
   treated as "check for new candidates," not "guaranteed to add N
   companies to the watchlist."** If the product needs a steadier weekly
   yield, running `low` 2x (cost: $0.05/week, still trivial against the
   $2/run cap) and merging results would very likely raise the odds of at
   least one non-empty, high-usability week over a single run, though this
   spike did not test running twice back-to-back to confirm that directly.

## Open questions

- Does yield/cost hold at a larger scale (this spike's `maxItems: 15` schema
  cap and small per-run company counts, 0-6, may not represent a
  `numResults`-style larger pull)? Not tested -- would cost more per run to
  find out and the brief said not to spend the budget just to learn this.
- Does `auto`'s worse $/usable-board hold across multiple runs, or was this
  one run unlucky (e.g. it happened to surface 2 companies whose postings
  had since closed)? Only one `auto` run was made; a second would clarify
  but wasn't judged worth $0.50-$2 more spend given that r1 already showed
  `low` itself (the cheaper cell) varies run to run by more than that
  spread -- more `auto` runs would need to be compared against more `low`
  runs to mean anything, which gets expensive fast.
- Given r1's 4-run spread for `low` (6/6, 0/0, 2/2, 2/0 companies/usable),
  how many runs would it take to get a stable read on the *typical*
  $/usable-board, and does running `low` more than once per week (point 8
  in Recommendation) actually raise the odds of a non-empty week, or does
  the same-day non-determinism seen in the identical-input repeat mean
  back-to-back runs return correlated (not independent) results? Untested.
- `output.grounding`'s own structured citation shape (separate from the
  schema's `source` field) was never inspected -- this spike relied on the
  schema-level `source` field, which was sufficient for the usability
  check, but stage 2 may still want to read `output.grounding` if it needs
  citation spans within `sponsorship_evidence`, not just a source URL.
- The interview's "companies to always watch" and "stage/size + industries"
  fields (#8-#9 in the question list) were drafted but never exercised
  against a live query -- no UAT default exists for them, so this spike
  couldn't test how they'd shape the query template.

## Non-claims

- This spike does not claim the recommended query/schema/effort is optimal
  -- only that, blended across 4 runs of the recommended cell, it cost less
  per usable board than the single `medium` and `auto` runs tested. r1
  found this result is noisy run-to-run (see Results §"Repeatability"): the
  single best run ($0.0042/board) is not the typical run
  ($0.0125/board blended across 4). A larger sample could change the
  picture further, and "decisive" in the original spike's framing should be
  read as "clearly cheaper on the runs actually made," not "reliably
  reproduces the headline number every time."
- Does not claim `auto`/`max` are worse in general -- only that this
  spike's single `auto` run, at this company-discovery task, scored worse
  on cost-per-usable-board than the *blended* `low` result did.
- Does not implement the setup interview, the query builder, the exclusion-
  list plumbing, or any product-code change. Stage 2 is a separate,
  operator-reviewed packet.
- Does not claim every fixture is free of any conceivable leak beyond what
  was checked: fixtures were grepped for `authorization`, `x-api-key`,
  `api_key` (case-insensitive) after redaction. One match was found
  (`low_strict_v1.json`, the substring "employment **authorization**"
  inside a sponsorship-evidence quote from a real job posting, not a
  credential) and confirmed benign by inspection -- see the grep output
  referenced in the Change log.
- `run_agent.py` had a bug in its first cut (fixed mid-spike, see
  Investigate §1) that silently failed to record `costDollars` because the
  field turned out to be an object, not a number; the initial 5 runs'
  actual costs were backfilled from their saved fixtures after the fix, not
  lost, but this shows the first cut of the cost-tracking code was wrong
  before it was corrected. r1's 3 additional runs recorded correctly from
  the start (r1 also found and removed 3 accidental duplicate `reported`
  lines this backfill step had written on top of `run_agent.py`'s own
  recording -- `spend.jsonl` was deduplicated before this doc's totals were
  finalized). The $0.834 total in Results is a direct recount of
  `spend.jsonl` as of r1, not a running estimate -- but a stage-2
  implementation making many more runs than this spike's 8 should not
  assume $0.834/8 ~= $0.10/run is a stable per-run average, given the
  Repeatability section's spread (some runs cost $0.025 for zero results).

## Change log

- 2026-09-24: initial spike, 5 live Agent API runs
  (`low_strict_v1`, `low_strict_v4_boardurl`, `low_loose_v4`,
  `medium_strict_v4`, `auto_strict_v4_cap2`), free ATS-board checks for
  every returned company, 4 live grounding-source spot-fetches, doc
  written. Coordinator corrected the spike number from S22 (already used by
  the historical `S22-01` phase-3 spike) to S23 mid-dispatch; this doc uses
  S23 throughout.
- 2026-09-24 (r1, coordinator review): fixed an arithmetic error --
  "100x-plus spread" (auto $0.146 vs low $0.0042) was actually ~35x
  (0.146/0.0042 ~ 34.8), consistent with the Recommendation section's
  already-correct "3x-35x"; softened "decisive" to what a small number of
  runs can actually support. Added 3 live replication runs of the
  recommended cell (v4 query, strict schema, `low` effort), $0.075 total,
  under r1's $0.15 cap: one exact repeat (same 3-entry exclusion list --
  returned 0 companies, vs the original run's 6, showing the result is not
  deterministic) and two "week 2" simulations (exclusion list grown to 23
  entries: UAT's 3 plus all 20 companies any run had found -- one returned
  2/2 usable new companies, the other 2 new companies but 0/2 usable
  because both boards had zero current postings). Updated Results with a
  Repeatability subsection and the blended $0.0125/usable-board figure
  across all 4 low/strict/v4 runs, updated the Recommendation (added point
  8 on weekly cadence expectations), Open questions, and Non-claims.
  Reconciled totals: 8 runs, $0.834 actual, $2.275 worst-case reserved, no
  run over $2, both figures recounted directly from `spend.jsonl` after
  removing 3 accidental duplicate `reported` lines a backfill step had
  written (see Non-claims).
