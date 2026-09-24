# S24 — Company discovery bake-off: Exa Websets vs OpenAI web_search vs Tavily vs H-1B baseline

**Requested:** 2026-09-24, from a dispatched task
(`.orchestrator/workers/specs/discovery-bakeoff.txt`) following the
operator's review of [S23](S23-scout-setup-interview-exa-agent.md): the
operator judged S23's Exa Agent (`low` effort) too unreliable to build on
alone (identical inputs returned 6 companies once, 0 the next time; 1 run
in 4 found nothing usable) and asked for other discovery approaches
compared on the same task, same UAT answers, same exclusion list.
**Status:** Research recorded 2026-09-24. SPIKE ONLY: scripts under
`research/discovery_bakeoff/`, no product code under `src/` added or
changed. Budget: operator authorized $10 total (separate from S23's
$0.834), $2/call hard cap. Actual spend: **$0.2957** (7 failed attempts
reserved worst-case but were never billed -- OpenAI does not charge for a
`429`-rejected request); worst-case reserved total **$3.06**, both well
under the $10 cap; no single call's worst-case reservation exceeded $0.30
(well under the $2/call cap).
**READ vs EXECUTED:** Exa Websets' endpoint/pricing pages, OpenAI's
`web_search` tool guide/pricing/models pages, and Tavily's
search/extract/credits pages are **READ** (URLs cited inline below, all
fetched 2026-09-24). Every cost, latency, company count, novelty, board
usability, and grounding number in Results is **EXECUTED**: live calls to
Exa's Websets API (confirming the 401 plan-gate), OpenAI's Responses API
(3 successful runs + 7 failed rate-limited attempts), Tavily's
search+extract API (3 runs), a full local scan + aggregation of the DOL
H-1B LCA disclosure workbook, a 200-employer live ATS board-slug probe, and
live `WebFetch` spot-checks of grounding sources.

## Problem

S23 showed Exa's Agent API can find genuinely new, sponsorship-evidenced,
board-verified companies very cheaply in its best case ($0.0042/usable
board) -- but r1's replication showed the *same* query/schema/effort
returns 6 companies once and 0 the next time, and a "week 2" simulation
split 2/2 usable vs 0/2 usable on back-to-back runs. The operator does not
want to commit Scout's weekly discovery step to a single provider whose
repeatability is this unproven. This spike runs the same task -- staff/
senior backend roles, US, remote or Denver CO, visa sponsorship required,
the same 23-company exclusion list -- through three more providers plus a
free non-agent baseline, to see whether another approach is more reliable,
cheaper, or usefully combinable with S23's findings.

## Operator decisions (recorded; not re-opened here)

- Candidates are fixed by the operator: Exa Websets, OpenAI Responses API
  `web_search` + structured output, Tavily (search + extract), and a DOL
  H-1B LCA disclosure-data baseline. No other provider, and no Claude web
  search (operator has no key for it).
- Budget: $10 total for this bake-off, $2/call hard cap, tracked in
  `research/discovery_bakeoff/spend.jsonl`. Spending the full $10 was
  explicitly not required.
- 3 repeatability runs per paid candidate; the H-1B baseline runs once (the
  source file is static for a given fiscal-year/quarter snapshot) plus one
  board-probe pass.
- Same task for every candidate: UAT answers (staff/senior backend, US,
  remote or Denver CO, visa sponsorship required = yes), the 23-entry
  exclusion list (UAT's Coupang/ClickHouse/Gen Digital + every company
  found across S23's fixtures, reused verbatim from
  `research/exa_agent_spike/inputs/exclusion_week2.json`), and S23's strict
  schema fields wherever the provider supports structured output.
- Every key read via `gigai.secrets_store.get(...)` (env first, then
  `~/.gigai/.env`); never printed, logged, or written to a fixture. A
  missing key skips that candidate with an explicit note, not a silent
  guess.

## Tasks

1. Read each provider's current docs for endpoint, request/response shape,
   pricing, and cost controls.
2. Reuse S23's spend-guard/redaction/board-check pattern (`spend_guard.py`,
   `redact.py`, `check_boards.py` copied into
   `research/discovery_bakeoff/`, not rewritten) with this bake-off's own
   $2/call, $10-total caps and its own `spend.jsonl`.
3. Run each paid candidate 3x under the spend guard; run the H-1B baseline
   once (deterministic) plus a board-probe pass.
4. For every company any candidate returned, check board usability for
   free via Scout's existing ATS clients (no extra spend).
5. For the H-1B baseline, slug-guess Greenhouse/Lever/Ashby board tokens
   from employer names and verify each guess with a real board poll.
6. Compute per-run metrics (cost, latency, companies, % new, % usable, %
   grounded, $/new-usable-board, run-to-run overlap) from rows, not
   assumed; recount totals against `spend.jsonl`.
7. Spot-check a sample of sponsorship-evidence source URLs live.

## Acceptance criteria

- Every claim below cites a URL or a command/script actually run.
- The comparison table's totals reconcile against `spend.jsonl` (recounted
  in Results, not assumed).
- Every fixture under `research/discovery_bakeoff/fixtures/` is grepped
  clean of `authorization`/`x-api-key`/`api_key` header material (11 benign
  false-positive matches found -- all the substring "authorization" inside
  "work authorization"/"employment authorization" in scraped job-posting
  prose, the same pattern S23 already documented; confirmed no actual key
  value from any of the three loaded keys appears in any file under
  `research/discovery_bakeoff/`, checked programmatically against the
  loaded secret values themselves, not just by eye).
- No single call's worst-case reservation exceeded $2; total worst-case
  reserved spend did not exceed $10.
- No large data file is git-tracked (the ~252MB H-1B workbook lives in the
  gitignored `research/discovery_bakeoff/h1b_scratch/`; only a 92KB, 200-row
  derived sample plus a 48KB board-probe result are committed).

## Investigate

### 1. Exa Websets -- blocked, $0 cost

Read
[`websets/quickstart.md`](https://exa.ai/docs/websets/quickstart.md),
[`websets/api/websets/create-a-webset.md`](https://exa.ai/docs/websets/api/websets/create-a-webset.md),
and [`websets/api/websets/preview-a-webset.md`](https://exa.ai/docs/websets/api/websets/preview-a-webset.md)
(all fetched 2026-09-24). Endpoint: `POST https://api.exa.ai/websets/v0/websets`
(create), `POST https://api.exa.ai/websets/v0/websets/preview` (free
preview, per the docs' own description: "understand how their search will
be interpreted before committing" without creating a webset or consuming
search credits). Request body: `{"search": {"query": str, "count": int,
"entity": {"type": "company"|...}, "criteria": [{"description": str}]},
"enrichments": [...], "exclude": [...]}`. The quickstart page states
plainly: "The Websets API requires a paid Websets plan; Search API credits
and Websets credits are separate."

**Live result:** every Websets endpoint tried
(`POST /websets/v0/websets/preview`, `GET /websets/v0/websets`) returned
**`401 Unauthorized`**: `"Your team does not have access to the API.
Upgrade to a Pro plan to get access."` -- confirmed twice, on both the free
preview endpoint and the plain list endpoint, with the same
`EXA_API_KEY` that worked for S23's Agent API calls. This is a plan gate,
not a metered call -- **$0 spent**, no `spend.jsonl` line needed (a 401
before any billable work starts). Websets is excluded from the Results
table below for this reason; it cannot be run with the current account.

### 2. OpenAI Responses API `web_search` tool

Read [`developers.openai.com/api/docs/guides/tools-web-search`](https://developers.openai.com/api/docs/guides/tools-web-search)
(redirects to `developers.openai.com`),
[`.../guides/structured-outputs`](https://developers.openai.com/api/docs/guides/structured-outputs),
[`.../pricing`](https://developers.openai.com/api/docs/pricing), and
[`.../models`](https://developers.openai.com/api/docs/models) (all fetched
2026-09-24). Current tool name: `"web_search"` (`web_search_preview` is
the legacy alias). Endpoint: `POST https://api.openai.com/v1/responses`,
synchronous (no polling). Model family that supports `web_search`:
GPT-6 (`gpt-6-astra`, `gpt-6-sol`, `gpt-6-luna`), all three documented as
supporting "Functions, Web search, File search, Computer use." Pricing
(per 1M tokens, short context): `gpt-6-astra` $10/$50, `gpt-6-sol` $2/$10,
`gpt-6-luna` $0.10/$0.50 -- **`gpt-6-luna` is the cheapest model that
supports `web_search`**, chosen for that reason and confirmed live to work
(see below). `web_search` tool pricing: $10.00/1000 calls ($0.01/call)
plus search-result content billed as ordinary input tokens at the model's
rate (the non-reasoning-preview tier's $25/1000-calls-with-free-content
variant does not apply to `web_search` on a current model). Structured
output: `text.format = {"type": "json_schema", "name": str, "schema":
<JSON Schema, additionalProperties:false, every field in "required">,
"strict": true}`.

**Live smoke test** (`research/discovery_bakeoff/fixtures/` -- not saved
as a numbered run, recorded in `spend.jsonl` as `smoketest_luna`): a
trivial "say OK and search for one fact" prompt returned a grounded,
cited answer (`output` types: `reasoning`, `web_search_call`, `message`)
for $0.0109 -- confirms the tool fires and the shape is right before
spending on the real query.

**Rate-limit finding (a real, load-bearing result, not a footnote):** the
account's `gpt-6-luna` **tokens-per-minute (TPM) limit is 200,000**
(confirmed from a live `429` response body: `"Rate limit reached for
gpt-6-luna ... Limit 200000, Used 145896, Requested 72618"`). The bake-off's
query (S23's `v4` board-URL-explicit template, filled with the full
23-name exclusion list) plus `web_search`'s returned search-result content
routinely requests **45,000-75,000 input tokens per call** (confirmed:
successful runs used 47,005 / ~44,000 / ~44,000 input tokens). Two calls
this close together exhaust the 200k window. **Every first attempt at
each of the 3 real runs hit `429` at least once**; the original 4-retry
backoff (`retry-after` + 1s, observed 8-16s waits) was not enough --
`openai_run1`'s *original* attempt exhausted all 4 retries and failed
outright (see Non-claims for the full failed-attempt list). Widening the
backoff to `retry-after + 45s` and the retry ceiling to 6 attempts fixed
this: all 3 final runs succeeded, though `openai_run3` still needed 4
retries (total wall-clock ~12 minutes for that one call) before a 200.
**This is a genuine cost of using `web_search` with a long, exclusion-list-
heavy query on a low-TPM-tier account** -- a product implementation would
need either a higher-TPM tier, a shorter exclusion encoding (e.g. hashed
IDs instead of full names), or accept multi-minute latency on cold calls.

### 3. Tavily search + extract

Read [`docs.tavily.com/documentation/api-reference/endpoint/search`](https://docs.tavily.com/documentation/api-reference/endpoint/search),
[`.../endpoint/extract`](https://docs.tavily.com/documentation/api-reference/endpoint/extract),
and [`.../api-credits`](https://docs.tavily.com/documentation/api-credits)
(fetched 2026-09-24). `POST https://api.tavily.com/search`:
`{"query": str, "search_depth": "basic"|"advanced", "max_results": int,
"include_raw_content": bool}`. `POST https://api.tavily.com/extract`:
`{"urls": [str,...], "extract_depth": "basic"|"advanced"}`. Pricing
(pay-as-you-go, $0.008/credit): basic search 1 credit ($0.008), advanced
search 2 credits ($0.016); basic extract 1 credit per 5 successful URLs
($0.0016/URL), advanced extract 2 credits per 5 ($0.0032/URL). **Tavily
has no agent or list-building endpoint** -- unlike Exa's Agent API/Websets
or OpenAI's schema-validated `web_search` output, Tavily returns raw
search results and (optionally) raw extracted page content; this bake-off
assembles the company list itself (`run_tavily_search.py`'s
`_derive_companies`), matching a `boards.greenhouse.io|jobs.lever.co|
jobs.ashbyhq.com` URL pattern against each result's URL and extracted
content, and an H-1B/sponsorship keyword nearby for the evidence field.
This is materially weaker than the other two candidates -- Tavily
contributes raw retrieval, not judgment, and that gap shows directly in
Results.

**Live finding:** `search_depth: "advanced"`, `max_results: 10` on this
bake-off's query returned mostly **third-party visa-sponsorship aggregator
pages** (`migratemate.co`, `hiringfleet.com`, `jaabz.com`), not direct ATS
board links -- the same "aggregator, not board root" problem S23's
Investigate §3 found and fixed by explicit query wording for the Exa
Agent. Tavily's plain search/extract has no equivalent lever to pull:
`extract`ing those aggregator pages' raw content did not surface a direct
Greenhouse/Lever/Ashby URL in 2 of 3 runs (the aggregator pages describe a
company's sponsorship history in prose, without linking its actual ATS
board). Across all 3 runs, the *only* company this bake-off's heuristic
extracted was **Instrumentl**, found because its own job posting happened
to be the (rare) direct-Lever-URL search result -- and Instrumentl is
already in the exclusion list (found in S23), so **0/3 runs found a new,
usable-board company.**

### 4. H-1B baseline (DOL OFLC LCA disclosure data)

Source page:
[`dol.gov/agencies/eta/foreign-labor/performance`](https://www.dol.gov/agencies/eta/foreign-labor/performance)
(fetched 2026-09-24). File: FY2026 Q3 LCA Disclosure Data,
`https://www.dol.gov/media/LCA_Disclosure_Data_FY2026_Q3.xlsx`,
**251,850,891 bytes** (confirmed both via `curl -I`'s `content-length`
header and the downloaded file's actual size -- they match). Downloaded to
`research/discovery_bakeoff/h1b_scratch/` (gitignored, added to `.gitignore`
by this task, never committed). 98 columns, streamed with
`openpyxl(read_only=True)` (not loaded fully into memory -- the file is
too large for that to be comfortable).

**Filters** (`h1b_baseline.py`): `VISA_CLASS == "H-1B"`,
`CASE_STATUS == "Certified"` (an approved LCA, not merely filed --
stronger evidence than "filed"), `SOC_CODE` starting with one of 5
software/backend-adjacent 2018-SOC prefixes (`15-1252` Software
Developers, `15-1251` Computer Programmers, `15-1253` Software QA
Analysts/Testers, `15-1211` Computer Systems Analysts, `15-1299` Computer
Occupations All Other) -- chosen by a live scan of the file's first
200,000 rows' own `SOC_TITLE` values, since DOL's SOC taxonomy has no
finer "backend engineer" distinction (a real, disclosed limitation, see
Non-claims). Result: **19,796 unique employers** matched the filters;
aggregated by `EMPLOYER_NAME`, ranked by certified-case count, top 200
written to the small, git-tracked
`research/discovery_bakeoff/h1b_sample/top200_employers.json` (92KB).

**The top of that list is dominated by mega-corps and outsourcing/staffing
firms** -- Amazon.com Services LLC (7,228 cases), Google LLC (5,344),
Cognizant Technology Solutions (5,292), Infosys Limited (3,824), Microsoft
(3,638), Meta (2,961), Apple (2,457), Deloitte Consulting (1,726), Compunnel
Software Group (1,613), Tata Consultancy Services (1,492), Wipro (1,289)
-- exactly the employers Scout's watchlist model does *not* need help
finding (they're not "new" discoveries in any useful sense, and most don't
use a Greenhouse/Lever/Ashby board at all -- see below).

**Board probe** (`probe_h1b_boards.py`, free, no paid-candidate spend):
slug-guesses a board token from each employer name (strips legal suffixes
-- Inc/LLC/Corp/Ltd/Technologies/Solutions/etc -- and tries both a
no-separator and a hyphenated join), tries all 3 providers per guess,
keeps a guess only if it resolves AND has >=1 matching US posting (reusing
`check_boards.py`'s row-matching, not reimplemented). **A real bug was
found and fixed mid-run:** the slug-normalizing regex
(`[^a-z0-9]+`) was case-sensitive without `re.IGNORECASE`, so it treated
every uppercase letter in an employer name as a "separator" to strip --
`"Amazon.com Services LLC"` produced `mazoncomservices` (the leading `A`
silently dropped) instead of `amazoncomservices`. Fixed before the full
200-employer probe ran (the buggy version was only used in an early,
discarded 5-employer smoke test). **Result on the top 200 (by case
count): 3/200 usable boards** -- Roblox Corporation (`roblox`, Greenhouse,
1 matching posting), Airbnb Inc. (`airbnb`, Greenhouse, 2 matching
postings), Coinbase Inc. (`coinbase`, Greenhouse, 3 matching postings) --
**2,013 total slug attempts, 286 seconds, $0 cost.** All 3 are outside the
23-entry exclusion list (genuinely new). The 0/30 result for the top 30
employers by case count (mega-corps, outsourcing firms) then 3/170 for the
rest confirms the intuition: **the highest-sponsorship-volume employers
are the least likely to use a startup-style ATS**; a production version of
this baseline would probe further down the ranked list (by sponsorship
*consistency*, not raw volume) or filter out known staffing/outsourcing
firms first to raise the hit rate per slug-attempt.

### 5. Grounding spot-checks (live fetches, not assumed)

| Company | Source URL | Result |
| --- | --- | --- |
| Docker (OpenAI runs 1-3) | `ellis.com/visa-sponsors/docker-inc/h1b` | **Real, substantiated** -- 64 total LCA filings shown, including 5 certified in 2026, 1 in 2025; median salary and H-1B-dependent status also shown |
| Kalepa (OpenAI run 1) | `job-boards.greenhouse.io/kalepa/jobs/5995682004` (the posting itself, not the sponsorship-tracker link) | **Real** -- live posting confirms "Staff Backend Engineer - Core Product (USA), Remote," asks a sponsorship screening question; matches the model's own hedged evidence text exactly ("asks... does not clarify the company's sponsorship policy") |
| Kalepa | `myvisajobs.com/employer/kalepa/` | **Not independently verifiable** -- the site returns HTTP 403 to automated fetches (confirmed twice, different URLs, different companies below); this is a site-side bot block, not evidence the underlying data is false, but it could not be confirmed here |
| VRChat (OpenAI runs 2-3) | `jobs.lever.co/vrchat/...` (the posting itself) | **Real** -- live posting confirms "Senior / Staff Backend Engineer (API) - Economy" at VRChat, remote; posting itself does not mention sponsorship (consistent with the model citing a separate H-1B-tracker source for that claim, not the posting) |
| VRChat | `myvisajobs.com/employer/vrchat/` | **Not independently verifiable** -- same 403 as Kalepa's myvisajobs.com link |

3/5 spot-checked source URLs were independently confirmed live; 2/5
(both `myvisajobs.com`) could not be fetched due to the site blocking
automated requests -- **not** the same finding as S23's dead-404 grounding
source, and this doc does not claim those two are false, only unverified
by this method.

## Results (comparison table)

Recounted from `research/discovery_bakeoff/spend.jsonl` and each fixture +
paired `check_boards.py` output. "new %" = share of returned companies not
matching (case-insensitive substring) the 23-entry exclusion list.
"usable %" = share with >=1 matching US posting on a live board poll.
"grounded %" = share with both a non-empty sponsorship-evidence string and
a source URL. `$/new usable board` is the primary metric (blank where
undefined, i.e. 0 new-and-usable companies).

| Candidate | Run | Cost | Latency | Companies | New % | Usable | Usable % | Grounded % | $/new usable board |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Exa Websets | -- | $0.00 | -- | **blocked (401, no Pro plan)** | -- | -- | -- | -- | -- |
| OpenAI `web_search` | run1 | $0.0557 | 454s | 2 | 100% | 2 | 100% | 100% | **$0.0279** |
| OpenAI `web_search` | run2 | $0.0662 | 223s | 2 | 100% | 2 | 100% | 100% | **$0.0331** |
| OpenAI `web_search` | run3 | $0.0553 | 731s | 4 | 100% | 3 | 75% | 100% | **$0.0184** |
| OpenAI `web_search` | **blended (3 runs)** | $0.1772 | avg 469s | 5 unique / 8 raw | 100% | 7 (of 8 raw) | 88% | 100% | **$0.0253** |
| Tavily | run1 | $0.0288 | 6.5s | 1 | 0% | 1 | 100% | 0%* | n/a (0 new-and-usable) |
| Tavily | run2 | $0.0288 | 7.3s | 0 | -- | 0 | -- | -- | n/a (0 new-and-usable) |
| Tavily | run3 | $0.0288 | 12.2s | 1 | 0% | 1 | 100% | 0%* | n/a (0 new-and-usable) |
| Tavily | **blended (3 runs)** | $0.0864 | avg 8.7s | 2 raw (1 unique) | 0% | 2 | 100% | 0% | **n/a (0 new-and-usable across all 3 runs)** |
| H-1B baseline + board probe | single (deterministic) | **$0.00** | 286s (probe only) | 3 usable / 200 probed | 100% | 3 | -- (denominator is 200 probed, not 3 returned) | n/a\*\* | **$0.00 (free)** |

\* Tavily's `sponsorship_evidence` field is populated by a keyword-proximity
heuristic, not a schema-guaranteed field the way OpenAI's structured output
is -- 0% here reflects that the one non-excluded-but-usable result
(Instrumentl, both times) didn't have a sponsorship keyword within the
extracted-content window the heuristic checked, not that sponsorship
evidence doesn't exist for Instrumentl (S23's own fixtures record it).
\*\* "grounded" isn't a meaningful percentage for the H-1B baseline --
sponsorship evidence for every one of the 19,796 filtered employers is the
DOL disclosure file itself (case count + a link to the source file), which
is definitionally 100% grounded by construction, not something a spot-check
can vary run to run.

**Spend reconciliation** (recounted directly from
`research/discovery_bakeoff/spend.jsonl`):

- Total actual reported cost (sum of `reported: true` lines' real cost):
  **$0.2957** (`openai_web_search`: $0.1885 across the 1 smoke test + 1
  rate-limit probe + 3 successful runs; `tavily`: $0.1072 across 1 smoke
  test + 3 runs; `h1b_board_probe`: $0.00).
- Total worst-case reserved (sum of `reported: false` lines' cost, the
  guard's pre-call reservation): **$3.06**.
- The gap between the two ($3.06 - $0.30 = $2.76) is **7 failed OpenAI
  attempts** that each reserved a $0.20 worst-case but were never billed
  (OpenAI's API does not charge for a request rejected with `429` before
  it starts generating) -- `openai_run1` (original: client-side
  `ReadTimeout` after 120s, an insufficient timeout for this query, fixed
  to 300s), `openai_run1_retry` (`ReadTimeout` again, at 300s -- the
  actual bottleneck was TPM rate-limiting, not raw slowness, discovered
  next), `openai_run1_v2` and `openai_run1_v3` (`429`, immediate), a
  second `openai_run1` attempt and `openai_run3`'s first attempt
  (`429` after exhausting the original 4-retry/short-backoff loop). Every
  one of these is a worst-case reservation the spend guard correctly held
  before knowing the call would fail -- exactly the conservative behavior
  `spend_guard.py`'s docstring describes, and it is why the actual spend
  ($0.30) is so much lower than the reserved total ($3.06): failed calls
  reserve but don't spend.
- Largest single worst-case reservation: **$0.30** (Tavily's
  `search_cost + max_results * extract_cost_per_url` estimate) -- no call
  approached the $2/call cap.
- Both totals are well under the $10 total cap ($3.06 worst-case = 30.6%
  of the cap; $0.2957 actual = 2.96%).

## Recommendation

**No single candidate replaces S23's Exa Agent outright; the strongest
combination found is H-1B baseline (for volume, at $0) + OpenAI
`web_search` (for novelty/precision, at ~$0.025/new-usable-board) run
together, with Tavily and Websets not recommended for this task as
currently accessible.**

1. **OpenAI `web_search` + `gpt-6-luna` + strict structured output is the
   strongest paid candidate tested here.** $0.0253/new-usable-board
   blended across 3 runs (vs S23 Exa Agent's $0.0125/board blended across
   4 `low`-effort runs) -- OpenAI is *more expensive* per board than S23's
   Exa Agent blended number, but **its 3 runs never returned zero
   companies** (2, 2, 4 -- compare S23's `low` spread of 6, 0, 2, 2) and
   **Docker appeared as a usable, new company in all 3 runs**, a
   consistency S23's repeatability testing did not find in its own
   candidate. This is 3 runs, not a large sample (see Open questions), but
   it is the most repeatable of the candidates actually tested across
   both spikes.
2. **The H-1B baseline is free and deterministic and should run
   unconditionally alongside whichever paid candidate is chosen** -- it
   found 3 new, usable-board companies (Roblox, Airbnb, Coinbase) at $0,
   though the current slug-guessing approach only clears a ~1.5% hit rate
   (3/200) against the highest-case-count employers, because that segment
   is dominated by mega-corps and outsourcing firms that don't run a
   Greenhouse/Lever/Ashby board. **A production version should re-rank by
   a "likely startup/mid-size tech employer" signal** (e.g. filter out
   known staffing/outsourcing-firm name patterns, or re-rank by case count
   *and* NAICS code) rather than raw case-count, to raise the effective
   hit rate -- untested here, flagged as the clearest next improvement.
3. **Tavily, as currently implemented (basic search-result derivation, no
   agent judgment), is not recommended for this task.** All 3 runs
   together found exactly one company (Instrumentl), already excluded --
   **0 new-and-usable companies across 3 runs and $0.0864 blended spend.**
   Tavily's `search` alone returns aggregator pages, not board roots, for
   this query shape; making it competitive would need either a second LLM
   pass over the extracted content (which erodes its cost advantage over
   OpenAI's `web_search`, which already does that judgment internally) or
   a materially different query strategy not tested here (e.g. searching
   `site:boards.greenhouse.io "visa sponsorship"` directly instead of a
   natural-language query).
4. **Exa Websets cannot be evaluated with the current account** -- it
   needs a paid Websets plan separate from the Search/Agent credits
   already available. This is a decision for the operator: either
   upgrade to evaluate it (cost unknown -- the plan-gated pricing was
   never reached) or drop it from consideration. Not recommended to
   pursue without that decision, since $0 of this bake-off's budget can
   evaluate it further as configured.
5. **Budget the OpenAI candidate's real-world latency, not just its
   dollar cost:** the account's 200k TPM limit means a query this size
   (45-75k input tokens from `web_search` results) can take 4-12 minutes
   wall-clock under retry backoff when run back-to-back with other calls
   on the same account/model. A weekly cadence (S23's Recommendation
   point 8) easily tolerates this; a same-session multi-run workflow
   (this bake-off's own 3 runs) does not, and needed backoff tuning
   mid-run to succeed at all (see Investigate §2).
6. **Keep the exclusion list format provider-appropriate.** This bake-off
   reused the same 23-name plain-text exclusion list across all three
   paid candidates (interpolated into the query text) -- this worked, but
   burns real input tokens on OpenAI (part of why its per-call token
   count is high) and offers no structural guarantee the model actually
   honors it (unlike Exa Agent's typed `input.exclusion` field, per S23).
   A larger production exclusion list (hundreds of companies) would need
   a cheaper encoding for OpenAI (e.g. a compact ID list plus a lookup
   table) or a provider with a structural exclusion mechanism.

## Open questions

- Does OpenAI `web_search`'s repeatability (2, 2, 4 companies; Docker in
  all 3) hold at a larger sample, the way S23's r1 replication showed
  `low`-effort Exa Agent's apparent consistency did NOT hold past a single
  run? Only 3 runs were made here, same sample size as S23's original
  pass before r1 found the spread -- this doc does not claim OpenAI is
  reliably non-zero, only that these 3 runs were.
- Would a differently-worded Tavily query (e.g. `site:` operators, or a
  query that asks for board URLs directly rather than "companies hiring
  for X role") perform meaningfully better, or is plain search+extract
  structurally weak at this task regardless of wording? Untested --
  the bake-off's budget and the task's fixed "same query for all
  candidates" requirement didn't allow varying Tavily's query
  independently of the shared template.
- What would Exa Websets actually cost and return for this task? Entirely
  unanswered -- the account cannot reach it. A follow-up would need the
  operator to decide whether to upgrade the Exa plan before any further
  spike time is spent on it.
- Does re-ranking the H-1B sample by a startup/mid-size-employer signal
  (instead of raw case count) meaningfully raise the 3/200 hit rate, and
  by how much? Not tested -- flagged in Recommendation point 2 as the
  clearest next step, not executed here since it requires a NAICS-code or
  name-pattern heuristic this spike didn't build.
- Two `myvisajobs.com` grounding-source URLs (Kalepa, VRChat) could not be
  independently fetched (HTTP 403 to automated requests) -- are these
  genuinely blocking bots generally, or specifically this fetch method?
  Not resolved; a production implementation verifying grounding sources
  would need to know whether `myvisajobs.com` is reliably fetchable by
  whatever mechanism it uses (likely not a generic `WebFetch`-equivalent,
  if this tool's block is representative).

## Non-claims

- Does not claim OpenAI `web_search` is cheaper than Exa Agent -- on
  blended $/new-usable-board, OpenAI's $0.0253 is *worse* than S23's Exa
  Agent blended $0.0125 (though *better* than S23's single `medium`
  ($0.0500) and `auto` ($0.1460) Exa Agent cells). The claim is about
  repeatability (no zero-company run across 3 tries here, vs S23's r1
  finding a zero-company run and a zero-usable run within its own 4), not
  about raw cost.
- Does not claim Tavily is unusable in general -- only that this bake-off's
  specific implementation (basic search-result derivation with a simple
  ATS-URL/keyword heuristic, no second LLM judgment pass, the same shared
  natural-language query template used for the other candidates) found
  nothing new and usable across 3 runs on this specific task. A different
  query strategy or an added LLM-judgment step was not tested (see Open
  questions) and could perform differently.
- Does not claim the 7 failed OpenAI attempts cost nothing in a stricter
  accounting sense -- they cost real wall-clock time (most of this spike's
  duration) and each held a $0.20 worst-case reservation against the
  budget for a period, even though none was actually billed. The $0.2957
  actual-spend figure is correct for dollars charged; it is not a claim
  that the failed attempts were free of any cost.
- Does not claim the H-1B baseline's SOC-code filter is a precise
  "backend engineer" filter -- DOL's LCA data classifies by broad SOC
  occupation code, not job title; `15-1299` ("Computer Occupations, All
  Other") in particular is a catch-all that likely includes some
  non-backend roles. The filter is the closest match DOL's taxonomy
  offers, not a precise one.
- Does not claim the two unverified `myvisajobs.com` sources (Kalepa,
  VRChat) are false -- only that they could not be confirmed by this
  spike's fetch method (HTTP 403 returned both times).
- Does not claim Exa Websets would perform worse (or better) than any
  other candidate -- it was never reached past the plan-gate 401, so this
  spike has no data on it at all beyond "not accessible with the current
  account."
- Every fixture was grepped for `authorization`/`x-api-key`/`api_key`
  (case-insensitive); 11 matches, all the benign "work authorization"/
  "employment authorization" substring inside scraped job-posting prose
  (same pattern S23 documented), confirmed by inspection. A second,
  stricter check compared every file under `research/discovery_bakeoff/`
  against the three loaded key values themselves (not just header-name
  patterns) and found no raw key value in any file.

## Change log

- 2026-09-24: initial bake-off. Confirmed Exa Websets blocked (401, no Pro
  plan) at $0 cost. Ran 3 live OpenAI Responses API `web_search` +
  structured-output calls (after widening retry backoff from
  `retry-after+1s`/4 attempts to `retry-after+45s`/6 attempts to survive
  the account's 200k-TPM limit against this query's 45-75k-token size; the
  original backoff caused `openai_run1`'s and `openai_run3`'s first
  attempts to fail outright). Ran 3 live Tavily search+extract calls. Read
  and downloaded the FY2026 Q3 DOL H-1B LCA disclosure file (251,850,891
  bytes, to a gitignored scratch path), filtered to 19,796 certified
  H-1B software/backend-SOC employers, derived a 200-row sample, and
  slug-guess-probed all 200 against live Greenhouse/Lever/Ashby APIs
  (found and fixed a case-sensitivity bug in the slug-normalizing regex
  mid-spike, before the full 200-employer probe ran). Live-fetched 5
  grounding-source URLs (3 confirmed, 2 blocked by the source site's bot
  protection). Reconciled `spend.jsonl`: $0.2957 actual, $3.06 worst-case
  reserved, no call over $2, both well under the $10 cap. Doc written.
