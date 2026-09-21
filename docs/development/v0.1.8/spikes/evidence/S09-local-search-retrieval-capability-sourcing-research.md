# S09: Local search/retrieval capability sourcing — research

**Date:** 2026-09-21
**Scope:** v0.1.8 research only; no new provider signup or scraping was performed. Every pricing/freshness/coverage/free-tier claim in the main body below is documented from a provider's own primary materials (or, where marked, a primary legal filing or a primary ATS docs page), not measured. No ATS polling and no job-posting bulk fetch was executed to produce this report. One exception, added 2026-09-21 and separately authorized: a small addendum (end of document) reports 3 live trial queries per provider run against Tavily and Exa using the operator's own pre-existing API keys, explicitly labeled as measured rather than documented, with keys never stored in the repository.
**Question:** For a local-first, installable Scout, what capability supplies web discovery/fetch (since local inference does not), at what cost, under what legal exposure, and how should a local harness wire a model to it safely?

## Executive summary

Local inference (Qwen/Ollama) supplies reasoning, not retrieval. Three genuinely separate capabilities are needed — **discovery** (learning a posting exists), **fetching** (retrieving its content once a URL is known), and **change detection** (noticing a known posting changed) — and no single product in this survey covers all three well. [Ollama tool-calling docs](https://docs.ollama.com/capabilities/tool-calling) (accessed 2026-09-21) confirm local models can be wired to arbitrary tools including a search function, but Ollama itself ships no search or fetch capability.

The operator's "cost-zero except search" instinct is **directionally correct, and stronger than assumed for the ATS leg, with one general-search provider's own numbers plausibly reaching sustained free use too**: employer/ATS feeds (Greenhouse, Lever, Ashby) are genuinely free, unauthenticated, primary-documented, keyless JSON APIs for discovery+fetch(+partial change-detection) on a user-curated watchlist — this is the strongest, cheapest, lowest-restriction-per-docs-reviewed path this survey found, confirmed against each platform's own docs. General search-API discovery is more mixed: Exa, Brave, and SerpApi's free tiers are modest trial-credit allowances (hundreds/month or a one-time few-thousand-credit grant) that plausibly run out under sustained "tens of searches/day" use; but Tavily's own documented 1,000-credits/month, 1-credit-per-basic-search free tier arithmetically covers up to ~33 basic searches/day for a full 30-day month without exceeding the free allowance (30/day × 30 days = 900 credits, under the 1,000 ceiling) — so at least one general-search provider's own published numbers do plausibly sustain realistic daily volume at $0, not just a one-time trial; this is a documented-tier calculation, not a measured result (Section 9). SerpApi's "$0/month, 250 searches/month" free plan is real per its own pricing page, but SerpApi is currently the defendant in an active Google lawsuit (filed 2025-12-19, alleging DMCA circumvention and unlicensed resale of Google Search content) that materially raises risk of relying on it, independent of price. [Google's own statement](https://blog.google/technology/safety-security/serpapi-lawsuit/) (accessed 2026-09-21).

No documented indexing-latency SLA (a number like "indexed within N minutes of publish") was found in any general search provider's primary docs. Exa's own docs describe "coverage refreshed continuously... timing varies by source" — an explicit non-commitment, not a number. [Exa Data Index](https://exa.ai/docs/reference/the-exa-index) (accessed 2026-09-21). This is the single most consequential gap for the operator's actual question (how fast can Scout learn of a new posting) and is exactly why ATS-feed polling — where the employer's own system is the source of truth for what it has published, rather than a third party's re-crawled index — is the stronger lead for the freshness objective, while general search stays a complementary broad-discovery/backstop layer. This is not itself a freshness guarantee: ATS-feed polling bounds only the delay between **a posting becoming visible in the feed endpoint and Scout's next successful poll detecting it** — not the separate, unmeasured delay between an employer publishing a role internally and that posting actually appearing in the feed, and not any interval where polling itself fails (rate-limited, network error, outage). Both gaps are real and neither was measured in this pass; Section 9's smallest-experiment table names what would close them.

Recommendation (detail in the final section): prototype **ATS-feed polling** (Greenhouse `boards-api.greenhouse.io`, Lever `api.lever.co`, Ashby `api.ashbyhq.com`) against a small user-curated employer watchlist as the primary, free, lower-legal-risk-per-docs-reviewed discovery+fetch(+partial change-detection) path; add **one general search API with a documented no-scrape/licensed-index stance and a real free tier** (Brave Search API or Tavily, both discussed below) as a bring-your-own-key backstop for employers outside the watchlist; treat SerpApi as **not recommended** pending the Google litigation outcome despite its attractive free tier and native Google-Jobs endpoint. Note "ToS-clean" is not established here: only each ATS vendor's own developer docs and `robots.txt` were reviewed (Section 2), not the vendor's full terms-of-service/EULA document — see Section 7 and the open questions for what remains unverified.

## 1. Discovery, fetching, and change detection — kept distinct

| Capability | What it means here | Covered by |
| --- | --- | --- |
| **Discovery** | Learning a new, previously-unseen posting exists | General search APIs (Exa/Tavily/Brave/SerpApi/You.com/Kagi) via keyword queries; ATS feed polling (new ID appears in a company's `jobs` list); a user manually pasting a URL |
| **Fetching** | Retrieving a known posting's actual content once its URL/ID is known | ATS APIs return full content in the same call (Greenhouse `?content=true`, Lever `description`/`descriptionPlain` fields, Ashby `descriptionHtml`/`descriptionPlain`); Exa/Tavily/You.com offer full-page-content search results or a separate Contents/Extract endpoint; Brave Search API returns snippets, not full content — fetching a matched URL is a separate step outside Brave's product; SerpApi returns a structured snippet/summary per job (`jobs_results[].description`), not guaranteed full posting text |
| **Change detection** | Noticing a known posting was edited, closed, or reposted | Greenhouse: documented `updated_at` field per job — [Job Board API docs](https://docs.greenhouse.io/job-board.html) (accessed 2026-09-21). Ashby: documents only `publishedAt`, defined as "ISO DateTime when the job was last published" — **not** a general edit timestamp; a correction to an earlier internal draft of this report, which incorrectly said `publishedAt`/`updatedAt` — [Ashby Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api) (accessed 2026-09-21) documents no `updatedAt` field, so detecting an in-place edit to an already-published Ashby posting requires a caller-side content hash/diff, same as Lever, not a provider-supplied signal. Lever: **no documented timestamp field** in the public postings response schema — [Lever postings-api README](https://github.com/lever/postings-api) (accessed 2026-09-21); change detection against Lever would require a caller-side hash/diff of the response body, not a provider-supplied signal. None of the general search APIs document a change-detection primitive; a caller would need to re-search/re-fetch and diff, which is discovery+fetch reused for a different purpose, not a distinct provider capability. |

No general search provider markets or documents a change-detection feature distinct from re-running a search or re-fetching a URL. This confirms the direction doc's framing: change detection is presently a caller-side responsibility (diff on re-poll), available cheaply only where the source (ATS) exposes an update timestamp.

## 2. Employer/ATS feed and watchlist angle

This is the strongest finding of the survey. Three of the four named ATS platforms document a public, unauthenticated, keyless JSON endpoint for reading a specific employer's published postings — confirmed against each vendor's own docs, not third-party scraper listings.

### Greenhouse

- Endpoint: `GET https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs`, with `?content=true` to include full descriptions, departments, and offices. `GET .../jobs/{job_id}` fetches one posting.
- Authentication: none required for any GET endpoint; explicitly documented as public. Only the application-submission `POST` requires Basic Auth.
- Change detection: job objects carry a documented `updated_at` field.
- robots.txt at `boards-api.greenhouse.io/robots.txt` (fetched directly 2026-09-21): `User-agent: * / Disallow: /embed/` — the JSON API paths used here are not disallowed.
- Source: [Greenhouse Job Board API docs](https://docs.greenhouse.io/job-board.html) (accessed 2026-09-21).

### Lever

- Endpoint: `GET https://api.lever.co/v0/postings/{site}?mode=json` (global) or `api.eu.lever.co` (EU); `GET .../{site}/{posting-id}` for one posting. Query filters: `team`, `department`, `location`, `commitment`, `level`, `skip`, `limit`.
- Authentication: none for GET; POST (apply) requires an API key.
- Response includes `description`/`descriptionPlain`/`descriptionBody`, `hostedUrl`, `applyUrl`, and optional `salaryRange` — a full-content fetch in the same call as discovery.
- Change detection: **not documented** — no `updatedAt`/`createdAt` field appears in the response schema in the official repo.
- Rate limit: documented only for the application-POST path (429 above 2 req/s); no documented limit for the public GET/list endpoint.
- robots.txt at `api.lever.co/robots.txt` (fetched directly 2026-09-21): `Allow: / / Crawl-delay: 1` — a blanket allow with a 1-second crawl delay.
- Source: [official `lever/postings-api` GitHub repository](https://github.com/lever/postings-api) (accessed 2026-09-21).

### Ashby

- Endpoint: `GET https://api.ashbyhq.com/posting-api/job-board/{job_board_name}?includeCompensation=true`.
- Authentication: none documented.
- Response includes `descriptionHtml`/`descriptionPlain`, `publishedAt` ("ISO DateTime when the job was last published"), `employmentType`, `workplaceType`, address fields, and compensation fields when requested — full content plus a documented last-publication timestamp in one response. `publishedAt` signals *when the posting was last published*, not a general-purpose edit/update timestamp; it does not by itself confirm whether the description text was edited since. No `updatedAt` field is documented.
- No filtering/search parameter is documented; the endpoint returns the whole board per request.
- Rate limits: not documented for this public posting endpoint in Ashby's own docs (secondary sources report an unofficial ~100 req/min ceiling observed in practice, not a vendor commitment — treat as undocumented).
- Source: [Ashby Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api) (accessed 2026-09-21).

### Workday

- No primary "public API for job listings" documentation was found. Workday's own developer-facing REST APIs (e.g. Staffing) are for authenticated internal HR integration, not public careers-page consumption. Every path described for reading a `myworkdayjobs.com` careers site's JSON is a reverse-engineered, undocumented endpoint that the company's own careers page happens to call client-side — this is inferred from third-party tooling, not confirmed from a Workday primary source, and should be labeled accordingly: **Workday has no documented public job-feed API**; treat any Workday polling as scraping a private/undocumented endpoint until a Workday primary source says otherwise.

### Net assessment for the ATS angle

For a user-curated watchlist of specific employers, polling Greenhouse/Lever/Ashby's own JSON endpoints on a fixed interval gives:
- **Discovery**: new job ID appears in the list response — detection latency after the posting becomes visible in the feed is bounded by the poll interval (assuming a successful poll), not by an opaque index-refresh SLA. This does not bound the separate, unmeasured delay between an employer publishing a role internally and that posting appearing in the feed at all, and does not account for a failed/rate-limited/errored poll — see the executive summary's freshness caveat above.
- **Fetching**: full content in the same response (Greenhouse with `?content=true`, Lever and Ashby by default) — no separate fetch-and-parse step, no HTML scraping.
- **Change detection**: real, general-purpose edit detection only for Greenhouse (`updated_at`). Ashby's `publishedAt` marks last-publication time, not general edits — treat Ashby the same as Lever and rely on a caller-side content hash/diff to detect an in-place edit.
- **Cost**: $0, no signup, no API key, at any of these three vendors' documented terms.
- **Legal posture (docs/robots.txt only, not a full ToS review)**: robots.txt for Greenhouse and Lever's JSON endpoints explicitly permits automated access at the paths used, and each vendor's own developer docs affirmatively describe the endpoint as public. This is the least-restrictive posture found among everything surveyed — but it is based only on developer documentation and robots.txt, not a full read of each vendor's terms-of-service/EULA document, so "ToS-clean" should not be asserted as an established conclusion (see Section 7 and the open questions).

This is plausibly faster, cheaper, and more reliable than general search for roles at employers the user already cares about, exactly as the direction doc anticipated — but it only covers employers on the ATS platforms it targets and only employers the user has proactively added to a watchlist; it does not discover roles at unwatched employers or on platforms outside Greenhouse/Lever/Ashby. It is complementary to, not a replacement for, general search.

## 3. Candidate search providers

All figures below are from each provider's own pricing/docs pages, accessed 2026-09-21, unless marked otherwise.

### Exa

- **Pricing**: Search $7/1,000 requests (note: one Exa-owned page surfaced a conflicting $5/1,000 figure in a promotional blog context found via search — the pricing page itself, fetched directly, states $7/1,000 for standard Search; treat $7/1,000 as the pricing-page-of-record figure and the $5 figure as unverified/promotional). Contents (full-page fetch) $1/1,000 pages. Answer $5/1,000. Deep Search $12–15/1,000. Results beyond the first 10 per request: +$1/1,000. [Exa Pricing](https://exa.ai/pricing) (accessed 2026-09-21).
- **Free tier**: new accounts get "$20 in free credits (around 2,800 searches)"; Free Tier accounts additionally get "$10 in credits every month" ongoing. A no-signup MCP server and an in-browser playground exist for zero-key trial. [Exa Pricing](https://exa.ai/pricing).
- **Rate limits**: 10 requests/second on Search, 100/s on Contents, 10/s on Answer for standard accounts (documented via Exa's own enterprise-comparison material referencing these ceilings); custom QPS for Enterprise.
- **Freshness/coverage**: Exa's own Data Index page states coverage is "refreshed continuously... timing varies by source and how often a page changes" — an explicit non-quantified claim, i.e., Exa does **not** publish an indexing-latency number. The Contents API's `maxAgeHours` parameter lets a caller request a fresher live fetch instead of the cached indexed copy, which is itself evidence the indexed copy can be stale by an unspecified amount. No job-board-specific coverage claim was found in primary docs. [Exa Data Index](https://exa.ai/docs/reference/the-exa-index), [Exa FAQ](https://exa.ai/docs/reference/faqs) (accessed 2026-09-21).
- **ToS on caching/storage**: Exa's Terms of Service (PDF; could not be machine-extracted by the tooling used here, so this is drawn from a third-party legal summary and should be independently re-verified before any implementation relies on it) reportedly grants Exa a broad license over user input/output and separately reportedly restricts persistent caller-side caching of results without written preapproval. This is **not independently confirmed against the raw ToS text** in this research pass — flagged as a gap requiring direct re-read of `https://exa.ai/assets/Exa_Labs_Terms_of_Service.pdf` before any storage-dependent design is finalized.
- **Response shape**: Search returns snippets/highlights by default; the Contents endpoint (separate call, or bundled via `contents` parameter) returns full page text/markdown. So Exa spans discovery (Search) and fetching (Contents) as two billable products.
- **Privacy**: Exa documents an opt-available Zero Data Retention mode across Search/Answer/Deep Research where query data is deleted post-search; absent ZDR, query data is used to improve/train models. [Exa privacy policy summary via Exa's own ZDR blog post](https://exa.ai/blog/zdr-search-engine) (accessed 2026-09-21) — the ZDR blog post is Exa's own material, used here as primary.

### Tavily

- **Pricing**: credit-based. Basic search = 1 credit, Advanced search = 2 credits, per request. Pay-as-you-go overage $0.008/credit. [Tavily API Credits docs](https://docs.tavily.com/documentation/api-credits) (accessed 2026-09-21).
- **Free tier**: "Researcher" plan, 1,000 API credits/month, no credit card required, resets on the 1st of each month. At realistic Scout volume (tens of searches/day ≈ 300–900 basic-search credits/month), the free tier plausibly covers daily use if kept to basic search and stays under ~1,000 credits/month — this is the most promising free tier of the general-search providers for sustained (not one-time) use. [Tavily Pricing](https://www.tavily.com/pricing) (accessed 2026-09-21).
- **Rate limits**: not quantified on the public pricing page; "higher rate limits" gated behind paid plans, implying the free tier has an unstated (lower) rate ceiling.
- **Freshness/coverage**: no indexing-latency figure found in Tavily's own docs in this pass.
- **ToS on caching/storing**: Tavily's Platform Terms of Service (fetched directly) explicitly prohibit transferring/sharing/sublicensing API access to a third party, prohibit using the service to build a competing product, and grant Tavily a broad license (Section 9.2) to "use, access, view, store, copy, display, create derivative works of... Customer Input." The Acceptable Use Policy (fetched directly) separately prohibits using the *Services themselves* — i.e., Tavily's own site/product — "to access, use, scrape, extract, harvest, or index the Services... by automated means, except as expressly permitted." Neither document contains an explicit blanket ban on a customer caching *returned* search results locally, but neither grants an explicit right to bulk-export or resell them either; this should be read as permissive-by-omission rather than an affirmative caching right. [Tavily Platform Terms of Service](https://www.tavily.com/terms), [Tavily Acceptable Use Policy](https://www.tavily.com/acceptable-use-policy) (both accessed 2026-09-21).
- **Response shape**: search results include content snippets; an `include_raw_content` option and separate Extract/Crawl endpoints provide full page content as a distinct, separately-billed step.
- **Privacy — flag**: Tavily's own privacy policy states query data "may" be shared with third-party search index providers "in limited situations where [Tavily's] own search index is unable to retrieve the requested content," governed thereafter by that third party's own privacy policy. This is a genuine, provider-documented exposure path even for a non-personalized query: the query can be forwarded to an unnamed third party outside Tavily's own privacy commitments. [Tavily Privacy Policy summary](https://www.tavily.com/privacy) (accessed 2026-09-21) — flagged for re-verification against the raw policy text before relying on Tavily for anything privacy-sensitive.

### Brave Search API

- **Pricing/free tier — changed recently**: Brave's own pricing page (fetched directly 2026-09-21) states the Search plan costs $5.00/1,000 requests with "$5 monthly credits" auto-applied — i.e., there is no longer a pure zero-cost unlimited-up-to-N-queries free plan; the "free tier" is now a $5/month credit allowance (≈1,000 queries/month at $5/1k) requiring a card on file. Secondary sources report Brave removed a prior 2,000-queries/month no-card free tier around 2026-02-12; this date/change is not independently confirmed against a Brave changelog in this pass and should be treated as a claimed, not verified, transition date.
- **Rate limit**: documented at 50 requests/second for the Search plan (per Brave's own pricing/documentation page).
- **Freshness/coverage**: no indexing-latency figure found in Brave's primary docs in this pass.
- **ToS on caching/storing — clearest restriction found among general search providers**: Brave's Terms of Service (fetched directly) state customers may not "store, cache, or create a database of Search Results, in whole or in part, other than transient storage required for operation of Customer Applications," may not "redistribute, resell, or sublicense the Search Results," and may not use results "to create, evaluate, train, re-train, fine-tune, benchmark or otherwise improve artificial intelligence models" without a separate storage-rights or AI-training-rights plan. [Brave Search API Terms of Service](https://api-dashboard.search.brave.com/documentation/resources/terms-of-service) (accessed 2026-09-21). This is more explicit than Exa's or Tavily's language and should be the template for what "acceptable local caching" needs to look like for this provider: transient/operational only, no durable local job-listing database built from Brave results without a storage-rights plan.
- **Response shape**: web search results (snippets/metadata); Brave also offers a separate "Answers" (LLM-generated grounded answer) product at $4/1,000 queries plus token cost. Fetching full page content is not part of the Search product; that remains a separate step against the discovered URL.
- **Privacy**: Brave's own material states it does not collect identifiers linking a query to an individual/device and takes the position query data is not personal data under GDPR; Zero Data Retention is available as a custom Enterprise option, implying non-Enterprise accounts are on a documented default retention window (12 months from account deletion, per Brave's own privacy-notice material) rather than zero retention by default.

### SerpApi

- **Pricing/free tier**: "Free" plan, $0/month, 250 searches/month, 50/hour throughput, all search engines including Google Jobs. [SerpApi Pricing](https://serpapi.com/pricing) (accessed 2026-09-21). At tens of searches/day this free tier is tight but not obviously insufficient (250/month ≈ 8/day average) — usable only for light, non-daily-continuous polling.
- **Rate limit**: every plan caps throughput at 20% of monthly volume per hour; Free plan = 50/hour.
- **Job-board coverage**: SerpApi documents a dedicated **Google Jobs API** (`engine=google_jobs`) and a **Google Jobs Listing Results API**, returning structured `jobs_results` with title, company, location, posting-age string (e.g. "4 days ago"), description, and application links — genuinely job-postings-shaped output, the most job-specific product surveyed among general-purpose providers.
- **Freshness**: SerpApi's own docs state identical queries are cached for 1 hour and cached hits don't count against quota; no indexing-latency figure (how fresh Google's own underlying Jobs index is) was found in SerpApi's primary docs — that freshness bound is inherited from Google Jobs, which SerpApi does not control or document.
- **Legal posture — load-bearing flag**: Google (the underlying index SerpApi scrapes to build its Jobs/Search products) filed a federal lawsuit against SerpApi on 2025-12-19 alleging DMCA circumvention of anti-scraping protections and unlicensed resale of Google-sourced content; SerpApi has denied the allegations and filed a motion to dismiss in February 2026. [Google's own blog post](https://blog.google/technology/safety-security/serpapi-lawsuit/) (accessed 2026-09-21). This is Google's own primary statement of its position, not a third-party rumor, though it is one party's allegation, not an adjudicated finding — the case is active/unresolved as of this report's access date. Regardless of outcome, this is a live legal-risk signal specifically for SerpApi's core product (scraping Google Search/Jobs), and a real reason SerpApi is a weaker recommendation than its price/free-tier numbers alone would suggest.
- **ToS on caching/storing**: SerpApi's terms (per its own legal-documents index, cross-checked via search) prohibit reproducing, duplicating, copying, selling, or reselling any part of the Service; a "ZeroTrace Mode" paid feature offers non-storage of query parameters/files. Caching for billing purposes (successful vs. failed/cached-query accounting) is described in SerpApi's own docs.

### You.com

- **Pricing**: Web Search API $5.00/1,000 calls, flat regardless of result count up to 100 results/call, with full page content bundled into every result at no extra charge (i.e., You.com's Search product spans both discovery and fetching in one billed call, unlike Exa/Tavily/Brave where content is a separate line item). Contents-only extraction (when a URL is already known) is $1.00/1,000 pages. [You.com Pricing](https://you.com/pricing) (accessed 2026-09-21).
- **Free tier**: "$100 free credit" for new accounts across paid APIs, plus a documented no-key **100 queries/day** Web Search free tier. This is the most generous *sustained daily* free allowance found among general search providers in this survey (100/day covers "tens of searches/day" comfortably), if the no-API-key claim holds in practice — this specific claim (100/day, no key) came through search-result synthesis rather than a directly fetched primary page in this pass and should be re-verified directly against `you.com/pricing` or `docs.you.com` before being load-bearing.
- **Rate limits**: 10 requests/second documented across `/v1/search`, `/v1/contents`, `/v1/answer`, `/v1/research`.
- **Freshness**: supports a `freshness` filter (day/week/month/year/custom range) on queries — a filtering capability, not an indexing-latency SLA.
- **ToS/privacy**: You.com advertises SOC 2 certification with zero-data-retention options; specific caching/downstream-use contractual language was not independently fetched and confirmed from You.com's raw ToS in this pass — flagged as unverified and requiring direct ToS re-read before relying on it.

### Kagi (Search API)

- **Pricing**: $12/1,000 requests, pay-as-you-go, invoiced every 30 days or at $100 usage. [Kagi API Pricing](https://kagi.com/api/pricing) (accessed 2026-09-21).
- **Free tier/trial**: the pricing page itself advertises a free-trial signup path ("Sign up for free"), but no free-tier request quota (e.g., "N free queries/month") was found in primary Kagi docs in this pass — unlike Exa/Tavily/Brave/You.com/SerpApi, Kagi does not appear to publish a standing free monthly allowance, only a trial-credit mechanism whose size was not confirmed. Flagged as the weakest-documented free tier among the providers checked.
- **Response shape/freshness/rate limits/ToS on caching**: Kagi's own Search API overview page, fetched directly, does not state response shape, rate limits, freshness claims, or caching/storage terms on the page reviewed; the full reference lives at `kagi.com/api/docs`, which was not separately fetched in this pass. Kagi is therefore the **least-verified** provider in this survey and should not be treated as ruled in or out pending a direct read of its full API reference.

## 4. Cost-zero reality check

**Direct answer: partially confirmed, with an important correction to the operator's framing.** The pattern is not "everything local is free, search is the one paid leg" as a monolith — it splits by discovery method:

- **ATS-feed-watchlist discovery+fetch+change-detection is genuinely $0/month**, no signup, no key, at documented terms (Greenhouse, Lever, Ashby), for any employer on those platforms that the user adds to a watchlist. This is real, free, sustained capacity — not a trial credit that runs out.
- **General search-API discovery is mixed, not uniformly unreliable, at sustained "tens of searches/day" volume**: Exa's and Brave's advertised free allowances are credit-based and modest ($10–20/mo in credits, or "$5 credit ≈ 1,000 queries/mo" for Brave); SerpApi's free 250/month is real but throughput-capped and now carries the added SerpApi/Google legal-risk flag; Kagi publishes no standing free quota found in this pass. **Tavily's own documented terms (1,000 credits/month, no card, 1 credit/basic search) arithmetically cover up to ~33 basic searches/day sustained for a 30-day month (30/day × 30 = 900, under the 1,000 ceiling) — plausibly sufficient for realistic Scout volume, not merely a one-time trial credit.** You.com's documented $100 signup credit is real but one-time; its claimed 100/day no-key tier is unverified in this pass (Section 3) and should not be relied on until re-confirmed. Tavily is therefore the clearest documented-tier case for sustained free general-search use at this report's access date — this is a documented-tier-vs-usage-pattern calculation, not a measured result, since no multi-day live-volume trial was run (the smoke trial in the addendum below covers only 3 queries).
- So the corrected statement is: **search does not have to be the one paid leg at low volume if ATS-feed watchlist coverage is prioritized and a Tavily- or You.com-shaped free tier is used as backstop for the general-search leg; it very plausibly becomes a paid leg only if usage grows past a few hundred general-search queries/month, or if the operator wants Google-native Jobs coverage via SerpApi/paid Deep-Search-grade freshness.** This is a documented-tier read, not a measured proof of sufficiency — see Section 9 for the smallest experiment that would confirm it.

## 5. Harness precedent — concrete mechanics

### DeepSeek (primary docs)

DeepSeek's tool-calling contract (`api-docs.deepseek.com/guides/tool_calls/`, fetched directly 2026-09-21) is OpenAI-compatible:

- **Tool definition schema**:
  ```json
  {
    "type": "function",
    "function": {
      "name": "get_weather",
      "description": "Get weather of a location, the user should supply a location first.",
      "parameters": {
        "type": "object",
        "properties": {
          "location": {"type": "string", "description": "The city and state, e.g. San Francisco, CA"}
        },
        "required": ["location"]
      }
    }
  }
  ```
- **Model's tool call**: returned as a structured `tool_calls` entry with an `id` used to correlate the eventual result.
- **Result hand-back**: a new message `{"role": "tool", "tool_call_id": "<tool.id>", "content": "<result>"}` is appended before the follow-up request.
- **Truncation/dedup**: DeepSeek's own docs do **not** document a truncation, summarization, or deduplication strategy for long tool results — this is left to the caller. DeepSeek does document (per search-derived, not directly fetched, secondary confirmation) up to 128 parallel tool calls per turn on V4 Pro/Flash and a "strict mode" beta that enforces stricter JSON-Schema conformance on tool arguments; neither of those specifics were independently confirmed against DeepSeek's raw docs page in this pass beyond the tool_calls page above and should be re-verified if load-bearing.

### Ollama (primary docs, the brief's named reference)

`docs.ollama.com/capabilities/tool-calling` (fetched directly 2026-09-21) documents the same shape as DeepSeek's (OpenAI-style `type: "function"` tool definitions), plus:

- Result hand-back message: `{"role": "tool", "tool_name": "function_name", "content": "result_value"}` — note Ollama's own docs use `tool_name` where DeepSeek's OpenAI-compatible shape uses `tool_call_id`; a GigAI wrapper targeting both backends needs to normalize this field rather than assume one shape.
- Multi-turn loop: initial request with tools → model returns `tool_calls` → harness executes the real function(s) and appends `tool`-role result messages → resubmit → repeat until the model returns no further `tool_calls`. Ollama's docs explicitly suggest telling the model "it is in a loop and can make multiple tool calls," implying the loop-termination and turn-budget logic is the caller's responsibility, not a built-in guardrail.
- Streaming: partial `thinking`/`content`/`tool_calls` fields must be accumulated across chunks before the follow-up call is built.
- No truncation/dedup guidance is documented by Ollama either — consistent with DeepSeek, this is left entirely to the caller/harness, reinforcing that GigAI's own wrapper (not the model runtime) must own result truncation, deduplication, and re-fetch avoidance.

### Open local-agent-plus-search precedent (leads only, used to point at concrete mechanics, not cited as authority)

Search surfaced several small open-source projects wrapping a search backend (often SearXNG) with a dedup/truncation/caching layer in front of a tool-calling loop — e.g. a FastAPI wrapper around SearXNG reported to add deduplication, content extraction, and caching before returning results to the model, and a separate lightweight tool reported to merge multi-source results, dedupe by URL, and cap to `max_results` before returning to the agent. These are useful as **existence proof of the pattern** (a thin dedup/truncate/cache layer between the raw search API and the model's tool-result message is standard practice, not a novel design choice for GigAI) — none were verified as authoritative or production-grade in this pass, and none should be cited as a specification; they are leads pointing at a pattern this report's own proposed tool-call shape (Section 6) already follows independently from DeepSeek's and Ollama's own primary docs.

## 6. Privacy boundary

The direction doc's concern is specific: an overly personalized query (salary target, sponsorship requirement, other private constraint) must not leak to a third-party search provider, and no provider's logging/ToS should create exposure even for a plain, non-personalized query.

**Findings against that specific risk:**

- **Tavily** is the one provider in this survey whose own privacy policy documents an affirmative onward-sharing path: query data "may" be forwarded to an unnamed third-party search-index provider when Tavily's own index can't answer the query, "governed by [that third party's] privacy policy" thereafter. Even a scrupulously non-personalized acquisition query passed to Tavily can end up logged by an unnamed downstream party outside Tavily's own stated privacy commitments. **Flagged as an exposure risk** for any query, not just personalized ones — re-verify against Tavily's raw privacy policy before relying on it for anything privacy-adjacent.
- **Exa** documents an opt-in Zero Data Retention mode; absent opting in, query data is used to improve/train Exa's own models — a same-vendor use, not a documented third-party forward, but still retained/used beyond the single request by default.
- **Brave** states by default it does not collect identifiers linking a query to a person/device and treats query data as non-personal under GDPR in its own stated position; a documented default retention window (reported as 12 months from account deletion) applies outside the paid Zero-Data-Retention Enterprise option.
- **SerpApi** offers a paid "ZeroTrace Mode" implying the default mode does retain query parameters/data.
- **You.com** and **Kagi**: no privacy/logging specifics were independently confirmed from primary sources in this pass beyond marketing claims of SOC 2/zero-retention options — flagged unverified.

**Design implication (independent of any one provider's ToS, and the safest posture regardless of which provider is chosen):** the acquisition query sent to any general search API must be constructed from public, non-identifying terms only (role title, skill keywords, location, employer name) — never from the user's private preference record (salary floor, sponsorship need, personal constraints). Those private fields are used only in the local assessment step, after a posting is already fetched, and are never serialized into a provider-bound HTTP request. ATS-feed polling sidesteps this risk category entirely for its covered employers, since the request is just "list this employer's jobs" with no query content derived from the user at all — another argument for weighting the ATS-feed path first.

## 7. Legal/ToS boundary on job boards

This is a documentation survey, not a legal opinion; genuine uncertainty is flagged rather than resolved.

**Search-API-mediated discovery vs. direct fetch, compared:**

- When a general search API (Exa, Tavily, Brave, You.com, Kagi) indexes a job-board page and returns it as a search result, the provider — not Scout — is the party doing the crawling/indexing, under whatever crawling agreement or robots.txt posture that provider maintains with the job board. Scout, as the API's customer, inherits the provider's ToS (caching/resale/storage restrictions, Section 3) but not directly the job board's scraping restrictions, because Scout itself never issued a request to the job board.
- When Scout (or a tool it calls) fetches a job-posting URL **directly** — e.g., to get the full description after a search API returned only a snippet, or after ATS-feed discovery returned a `hostedUrl`/`applyUrl` — Scout becomes the direct requester and is subject to that job board's own ToS/robots.txt.

**Major job boards' own ToS language on this, from primary sources:**

- **LinkedIn**: User Agreement (Section 8.2, per direct quotation found via search and consistent across multiple secondary summaries, not independently re-fetched from linkedin.com in this pass) prohibits use of "software, scripts, robots, crawlers... to scrape or copy profiles and information of others," and separately prohibits bots/unauthorized automated methods generally. This is a clear, explicit no-scraping stance; direct-fetch of LinkedIn job postings is contractually disallowed regardless of how the URL was discovered. LinkedIn has litigated this position (the hiQ Labs case), though that case's outcome turned on CFAA (a criminal-adjacent statute), not on whether the ToS prohibition itself was enforceable as a contract matter — breach-of-contract exposure from violating the User Agreement is a live and distinct risk from CFAA, per the same legal commentary. **Recommendation: do not direct-fetch LinkedIn job postings; LinkedIn discovery via a general search API result snippet is a materially different, lower-risk posture than Scout fetching the LinkedIn URL itself, but LinkedIn's ToS should be treated as prohibiting the latter.**
- **Indeed**: Terms of Service (fetched directly, `indeed.com/legal`) explicitly prohibit automating the *application* process outside Indeed's own vendor tooling, and separately restrict scraping of job-seeker profile data. The excerpt fetched did not surface an equally explicit blanket prohibition on scraping *job postings themselves* (as distinct from applications or profiles) in the section reviewed — **genuine uncertainty flagged**: Indeed's full ToS is longer than what was fetched here, and a general "unauthorized automated access" clause likely exists elsewhere in the document; do not treat the absence of a quote in this pass as evidence Indeed permits posting-scraping. Re-read the full document before any direct-fetch design targeting Indeed.
- **Greenhouse/Lever/Ashby (as ATS platforms, not job boards)**: these are not job boards in the LinkedIn/Indeed sense — they are the vendor infrastructure an employer chooses, and each vendor's own developer docs affirmatively document the public read endpoint as intended for public consumption (Section 2). This is the least-restrictive posture found in the survey by the measures actually reviewed here: the vendor itself publishes and documents the endpoint as public, and Greenhouse's/Lever's robots.txt (fetched directly) does not disallow it. This does not constitute a full ToS/EULA clearance — none of the three vendors' complete terms-of-service documents were reviewed in this pass, only developer docs and robots.txt, so a residual unknown remains (flagged in the open questions).
- **Workday**: no comparable affirmative "this is public and intended for external consumption" statement was found from Workday itself; treat direct-fetch of `myworkdayjobs.com` content as unresolved/uncertain rather than confirmed-safe.

**Net**: search-API-mediated discovery is the lower-risk path for boards with an explicit no-scrape ToS (LinkedIn); direct fetch is comparatively low-risk and vendor-endorsed for Greenhouse/Lever/Ashby; direct fetch of Indeed or Workday postings sits in a genuinely unresolved zone under this pass's research depth and should not be treated as cleared.

## 8. Installable/local-first fit

- **Bring-your-own-API-key pattern**: every general search provider surveyed (Exa, Tavily, Brave, SerpApi, You.com, Kagi) uses a standard header/bearer API-key auth model suitable for a locally-stored key (e.g., in a local config file or OS keychain), consistent with how other local-LLM tools already ask users to supply their own provider keys. No provider surveyed requires server-side OAuth or a hosted callback that would be awkward for a purely local install.
- **Degradation behavior (must not silently hide jobs, per the direction doc)**: none of the providers surveyed publish guidance on this — it is entirely a GigAI-side design obligation. The concrete implication for the harness: a missing/invalid search-API key must not cause acquisition to silently return zero results indistinguishable from "no jobs found." It should surface as a distinct, visible state ("general search unavailable: no/invalid API key configured") separate from "search ran and found nothing," and — critically — ATS-feed watchlist polling should continue to function with **no API key at all**, since Greenhouse/Lever/Ashby need none; a missing general-search key should degrade Scout to watchlist-only discovery, not to zero discovery.
- **No-signup/anonymous free tier for first-run trial**: Exa documents a no-key MCP server and an in-browser playground; SerpApi's $0/month tier still requires account signup (not fully anonymous, but no payment); Tavily's free tier requires signup but explicitly no card. **None of the general search providers surveyed offer a fully anonymous, zero-signup, production-usable API-key-free trial** for first-run use inside an installed app — the closest is Exa's playground/MCP path, which is not the same as a first-run API key. By contrast, **ATS-feed watchlist polling requires no signup, no key, and no trial period at all** and is the only path in this survey suitable for genuine zero-friction first-run trial before a user commits to any account.

## Provider comparison table

| Provider | Capability covered | Free tier (documented) | Pricing (documented) | Rate limit (documented) | Indexing-latency claim | ToS: caching/storage | Legal/ToS risk flag |
| --- | --- | --- | --- | --- | --- | --- | --- |
| **Greenhouse** (`boards-api.greenhouse.io`) | Discovery + fetch + general-edit change detection (`updated_at`) | No key/signup required; no documented request-volume cap found, but no rate limit is documented either — treat as undocumented, not confirmed-unlimited | $0 | Undocumented | N/A — polling interval is the bound | No caching/storage terms found; full ToS/EULA not reviewed in this pass, only developer docs + robots.txt (see below) | robots.txt allows the API paths used; least-restrictive per docs/robots.txt reviewed (not a full ToS review) |
| **Lever** (`api.lever.co`) | Discovery + fetch; **no** documented change-detection field | No key/signup required for GET; no documented request-volume cap on the list/GET endpoint (only the apply-POST path has a documented rate limit) — treat as undocumented, not confirmed-unlimited | $0 | 2 req/s documented for apply-POST only; list/GET undocumented | N/A | No caching/storage terms found; full ToS/EULA not reviewed in this pass, only developer docs + robots.txt (see below) | robots.txt blanket-allows with 1s crawl-delay; low risk per docs/robots.txt reviewed |
| **Ashby** (`api.ashbyhq.com`) | Discovery + fetch; **no** general-edit change detection (`publishedAt` = last-publication time only, not an update timestamp) | No key/signup required; no documented request-volume cap found (an unofficial ~100/min ceiling is reported by secondary sources, not a vendor commitment) — treat as undocumented, not confirmed-unlimited | $0 | Undocumented (unofficial ~100/min reported, unverified) | N/A | No caching/storage terms found; full ToS/EULA not reviewed in this pass, only developer docs (see below) | Docs describe the endpoint as public, but no full ATS terms-of-service document was reviewed in this pass — "public read endpoint" is confirmed; broader legal clearance is not (see below) |
| **Workday** | None confirmed | N/A | N/A | N/A | N/A | N/A | No primary public-API doc found; treat as unresolved/scraping |
| **Exa** | Discovery (Search) + fetch (Contents, separate billed step) | ~2,800 one-time credits + $10/mo ongoing | $7/1k search, $1/1k content | 10 req/s search | "Refreshed continuously... varies by source" — explicitly not quantified | Broad license claimed in ToS; persistent-caching restriction reported but not independently confirmed from raw ToS text | Low-medium; re-verify ToS PDF directly |
| **Tavily** | Discovery (search) + fetch (Extract/Crawl, separate) | 1,000 credits/mo, no card | $0.008/credit (~1 credit/basic search) | Undocumented on free tier | Not found | No blanket ban found; broad license to Tavily over input | Query-forwarding-to-third-party privacy flag (Section 6) |
| **Brave Search API** | Discovery (snippets only); fetch is a separate external step | $5 credit/mo (~1,000 queries) after Feb-2026 tier change (change date unverified) | $5/1k | 50 req/s | Not found | Explicit: no persistent storage/caching without a storage-rights plan; no resale | Clearest, most restrictive ToS language on caching — plan accordingly |
| **SerpApi** | Discovery + fetch (Google Jobs engine returns descriptions) | 250/mo, $0 | $25–$2,750+/mo paid tiers | 50/hour on free tier (20% of monthly volume/hour on all tiers) | 1-hour result cache documented; underlying Google index freshness not documented by SerpApi | Reproduction/resale prohibited in SerpApi's own terms; ZeroTrace Mode = paid no-storage option | **Active Google lawsuit (filed 2025-12-19, unresolved)** alleging DMCA circumvention/unlicensed resale — material risk independent of price |
| **You.com** | Discovery + fetch bundled in one call (full content included) | $100 signup credit; claimed 100/day no-key tier (unverified in this pass) | $5/1k (search+content bundled) | 10 req/s | `freshness` filter exists; no latency SLA found | SOC2/ZDR claimed; raw ToS not independently fetched | Unverified ToS; otherwise no flag found |
| **Kagi (Search API)** | Discovery (unclear if snippets or full content — not confirmed) | Trial signup only; no standing free quota found | $12/1k | Not found | Not found | Not found | Least-verified provider in this survey — do not treat as cleared or ruled out |

## Cost-zero-except-search: direct answer

**Not accurate as a monolithic claim; accurate in a narrower, corrected form — and the ATS leg is a genuine extension of "free," not just a partial exception.** ATS-feed-watchlist discovery and fetch are genuinely free, keyless, and sustained per each platform's own documentation (Section 2) for all three vendors; general-purpose edit-change-detection is documented only for Greenhouse (`updated_at`) — Ashby and Lever both require a caller-side content hash/diff for that specific sub-capability. So the "local is free" story extends further than the operator's original framing implied — discovery for watchlisted employers is free too, not just inference. General search is more mixed than "the one paid leg": Tavily's own documented 1,000-credits/month no-card tier arithmetically covers ~33 basic searches/day for a full month (Section 4), a genuinely sustained allowance, not a trial credit; You.com's advertised no-key daily tier is unverified pending direct re-fetch and should not be relied on yet. SerpApi's $0/250-per-month tier is real on paper but is paired with an unresolved Google lawsuit against SerpApi's core scraping method, a risk that argues against it regardless of price. No provider in this survey publishes a documented indexing-latency number, so **the operator's real question — how fast can Scout learn of a new posting via general search — is not answered by any provider's pricing/free-tier documentation and cannot be resolved by reading docs alone** (Section 9 names the smallest experiment to check this; the live smoke trial in the addendum below is a small step toward it, not a substitute).

## Proposed tool-call shape for wiring a local model to the chosen provider(s)

Following the OpenAI-compatible shape both DeepSeek and Ollama document (Section 5), normalized across the two observed result-hand-back field-name variants (`tool_call_id` vs `tool_name`):

```json
{
  "type": "function",
  "function": {
    "name": "discover_postings_by_watchlist",
    "description": "List currently published postings for one watchlisted employer via its ATS's public read API. Returns only public, employer-published fields. Does not accept or use any private user preference data.",
    "parameters": {
      "type": "object",
      "properties": {
        "employer_id": {"type": "string", "description": "Watchlist entry id (maps internally to ats_platform + board_token/site/job_board_name)"}
      },
      "required": ["employer_id"]
    }
  }
}
```

```json
{
  "type": "function",
  "function": {
    "name": "discover_postings_by_search",
    "description": "Run one public-job-search query against the configured general search provider. Query text is constructed by the harness from an allowlist of public fields (role title, skill keywords, employer name, location); the harness rejects/rebuilds any candidate query containing terms outside that allowlist before it is sent to the provider — this description documents the enforced behavior, and is not itself the enforcement mechanism.",
    "parameters": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "location": {"type": "string"},
        "max_results": {"type": "integer", "default": 10}
      },
      "required": ["query"]
    }
  }
}
```

```json
{
  "type": "function",
  "function": {
    "name": "fetch_posting_content",
    "description": "Fetch full content for one already-discovered posting URL/id. Distinct from discovery; never invents a URL not previously returned by discover_postings_*.",
    "parameters": {
      "type": "object",
      "properties": {
        "posting_ref": {"type": "string", "description": "Opaque id/URL returned by a prior discover_postings_* call"}
      },
      "required": ["posting_ref"]
    }
  }
}
```

Result hand-back (harness-normalized; the wrapper translates to whichever of `tool_call_id`/`tool_name` the target runtime expects):

```json
{
  "role": "tool",
  "tool_call_id": "<id>",
  "content": {
    "source": "greenhouse|lever|ashby|exa|tavily|brave|serpapi|...",
    "results": [
      {"posting_ref": "...", "title": "...", "employer": "...", "location": "...", "posting_time_raw": "...|unknown", "first_seen_at": "...", "snippet_or_content": "...", "content_complete": true}
    ],
    "truncated": false,
    "dedup_removed_count": 0
  }
}
```

Harness-owned responsibilities (per Section 5's finding that neither DeepSeek's nor Ollama's docs specify these — GigAI's wrapper must):
- **Dedup**: drop postings whose `posting_ref` (or ATS `id`) was already surfaced in a prior run within the retention window, before the result ever reaches the model.
- **Truncation**: cap `snippet_or_content` to a fixed budget before it enters the model's context; record `truncated: true` and retain the untruncated raw response in the evidence/source-snapshot store (per the Jev research's evidence-vs-explanation separation) rather than losing it.
- **Re-fetch avoidance**: `fetch_posting_content` checks the source-snapshot store for a recent fetch of the same `posting_ref` before issuing a new HTTP request.
- **Provider fallback surfacing**: if `discover_postings_by_search` cannot run because no/invalid API key is configured, the tool must return an explicit `{"error": "search_unavailable", "reason": "missing_or_invalid_api_key"}` result rather than an empty `results: []` — so the model (and ultimately the dashboard) can distinguish "no jobs found" from "search capability unavailable," satisfying the direction doc's "must not hide jobs" requirement.

## Privacy-separation design

1. **Two distinct request paths; schema alone is necessary but not sufficient enforcement.** `discover_postings_by_watchlist` and `discover_postings_by_search` are the only functions permitted to reach a network boundary during acquisition, and neither declares a preferences/assessment object as an input parameter. That schema shape prevents the model from passing a *separate, named* preferences field, but it does **not** by itself stop private content from ending up inside the free-text `query` string — a model (or a careless prompt/harness bug) can still write "python developer, must sponsor visa, $180k+" into `query`, and a schema with no preferences field cannot detect or block that. Approving field *categories* (role title, skill keywords, location — point 3) is the same gap one level down: an allowlisted `location` field with an unconstrained value can still hold `"anywhere, must sponsor visa"`. Real enforcement requires the harness to (a) source query values only from a separately maintained, user-approved **public search settings record** — not the model's free-text choice and not the private preferences record — and (b) validate the resulting query against that same approved-values list before it is ever sent to a provider. The schema shape and the field-category allowlist are both useful first lines, neither is the guarantee; the approved-values record is.
2. **Assessment is a separate, later, purely-local step, and private preferences are structurally unavailable to query construction, not merely unused by convention**: the preferences record (salary target, sponsorship requirement, location constraints, other private conditions) is passed only as an input to the local assessment call, which takes an already-fetched posting plus that preferences record and produces the requirement matrix/proposal — this step makes no outbound network call to any search/fetch provider. The query-construction code path (point 3) that builds `discover_postings_by_search`'s request must not have the preferences record in scope at all — not filtered, not read-then-discarded, simply never passed in — so a bug or prompt-injection attempt in that path has no private data available to leak in the first place. This mirrors the direction doc's "acquire, then assess independently" sequencing and the Jev research's separation of typed decision from evidence input.
3. **Query construction from an approved public search-settings record, not an allowlist of field names alone**: `discover_postings_by_search`'s query string is built by the harness from a separately maintained, user-approved settings record (e.g. approved role titles, approved skill keywords, approved locations — each an explicit value the user reviewed and accepted, not a category the model can fill in freely) — never by string-interpolating the full preferences object, and never by accepting an arbitrary model-authored value for an allowlisted field name. This closes the specific gap where a field-name allowlist alone (role title/skill keywords/location as categories) would still permit private text inside an unconstrained value for one of those fields. A prompt-injected or model-hallucinated attempt to "search more precisely using the user's salary requirement" cannot produce a query containing that data, both because the harness — not the model — controls query construction, and because the private preferences record is never in scope for that code path (point 2) to pull from even accidentally.
4. **Provider selection informed by Section 6's flags**: given Tavily's documented third-party query-forwarding path, a provider used for anything beyond bare public-role-keyword search should default to Brave (explicit non-personal-data stance, clearer ToS) or ATS-feed-only discovery where possible; Tavily remains usable for the non-sensitive keyword-only query path described in point 3, since the query itself never contains private data regardless of downstream forwarding — but this should be re-confirmed once Tavily's raw privacy policy is directly re-read (flagged in Section 3).
5. **No silent hosted fallback**: if the local model is unavailable, acquisition (ATS polling, general search) must not fall back to any hosted inference/assessment provider carrying private preference data — this spike found no evidence any surveyed search provider offers or requires model inference on private input, and none should be given access to it.

## Recommendation

**Prototype, in this priority order:**

1. **ATS-feed watchlist polling** (Greenhouse `boards-api.greenhouse.io`, Lever `api.lever.co`, Ashby `api.ashbyhq.com`) as the primary discovery+fetch(+general-edit change-detection for Greenhouse only; content hash/diff for Lever and Ashby) path for employers the user adds to a watchlist. Free, keyless, lower legal risk per the docs/robots.txt actually reviewed (not a full ToS clearance), bounded-latency-by-poll-interval **for detecting a posting once it appears in the feed** — this controls detection delay after publication, not the delay between an employer publishing internally and the posting becoming visible in the feed at all, which this survey did not measure. Directly relevant to the operator's stated priority (freshness) in a way no general search API's undocumented indexing latency is, but not itself proof of freshness (see Section 9 and the open questions).
2. **One general search API as a bring-your-own-key backstop** for employers/roles outside the watchlist. Between the providers surveyed, **Brave Search API** (clearest, most explicit ToS on caching/no-resale, documented non-personal-data privacy stance) and **Tavily** (best-documented sustained free tier at realistic volume, but flagged for its third-party query-forwarding privacy note) are the two strongest candidates; neither is a clean unconditional win, so prototyping should compare both against the same fixture rather than picking one from docs alone.
3. **Do not prototype with SerpApi** despite its attractive free tier and native Google-Jobs product, pending resolution of the active Google litigation against SerpApi's core scraping method — this is a documented, cited, unresolved legal-risk flag, not a documentation gap that further reading would close.
4. **Kagi and You.com are under-verified in this pass** (Kagi's full API reference and You.com's raw ToS were not directly fetched) — neither ruled in nor out; a follow-up documentation pass (not a live-query experiment) could close this gap cheaply before any prototyping decision that depends on them.
5. **Workday is not currently viable** for the ATS-feed approach — no primary public-API documentation was found; any Workday coverage would currently mean fetching an undocumented endpoint, which this spike does not recommend building on without a Workday primary source clarifying its status.

This is not "none fit" — the ATS-feed path is a strong, documented, zero-cost fit for its scope, and at least one general-search backstop (Brave or Tavily) is plausibly workable within a real free tier at the stated usage scale. The genuine gap is that **no provider's documentation resolves the freshness/indexing-latency question that most drives the operator's actual objective** — that gap is inherent to what documentation can show and is exactly what Section 9 below scopes as follow-up experimentation.

## Smallest experiment to verify each load-bearing documented claim

Each item below is future work requiring separate authorization; none was run to produce this report.

| Claim this spike relied on | Smallest experiment to verify it |
| --- | --- |
| ATS-feed polling gives faster/cheaper discovery than general search for watchlisted employers | Poll one Greenhouse and one Lever board every N minutes for a fixed window (e.g. 48–72 hours) alongside a matched general-search query for the same employer/role; compare time-to-first-appearance for any new posting that appears during the window. Needs no signup for the ATS leg; needs one provider's free-tier key for the search leg. |
| No general search provider documents a quantified indexing-latency SLA | Not verifiable by further reading — this is a negative-finding claim (absence of a documented number) already confirmed by direct primary-doc review in this pass; no experiment closes it further, only a provider publishing new docs would. |
| Tavily's 1,000-credits/month free tier "plausibly survives" realistic daily Scout volume | Run a small number of representative daily-volume queries (e.g., 10–30/day) against the free-tier key for a week and track credit consumption against the 1,000/month ceiling. |
| Brave's ToS-compliant "transient storage only" caching is workable for Scout's re-fetch-avoidance need | Design and dry-run (no live calls) a caching layer that holds results only long enough to dedupe within a single acquisition run, then confirm with Brave (via their own support channel, not assumed) whether that pattern qualifies as "transient storage required for operation," before persisting anything longer-lived. |
| SerpApi's Google Jobs product remains usable despite the pending litigation | Not an experiment — track the case's docket/outcome before any adoption decision; this is a legal-status watch item, not something a technical trial resolves. |
| You.com's claimed 100/day no-key free tier | Re-fetch `you.com/pricing` and `docs.you.com` directly (a documentation re-check, not a live-query experiment) to confirm the figure before relying on it, since this pass's number came through search-result synthesis rather than a directly fetched primary page. |
| Exa's ToS persistent-caching restriction | Directly re-read the raw text of `https://exa.ai/assets/Exa_Labs_Terms_of_Service.pdf` (this pass's PDF fetch failed to extract text) before any design assumes either that caching is restricted or that it is permitted. |
| Kagi's rate limits, response shape, and caching ToS | Directly fetch and read `kagi.com/api/docs` (not attempted in this pass) before including or excluding Kagi from a shortlist. |
| Lever's lack of a change-detection timestamp field | Confirmed already from the primary GitHub docs (no experiment needed) — but worth a one-off live call (once separately authorized) to confirm the field is truly absent from the actual JSON response and not merely undocumented. |
| Indeed's and Workday's exact posting-scraping ToS posture | Directly read Indeed's full Terms of Service beyond the excerpt fetched here, and search for any Workday statement (support docs, developer portal) addressing public careers-page data — a documentation-only follow-up, not a live trial. |

## Open questions

- Does the operator want change-detection coverage badly enough to treat Lever's missing timestamp field as a blocker for that one ATS, or is a caller-side content hash sufficient?
- Should Scout's watchlist UI let a user add *any* employer's careers URL and have the harness auto-detect which ATS (Greenhouse/Lever/Ashby/other/none-detected) backs it, or should watchlist entries be added per-ATS explicitly?
- If Brave and Tavily are both prototyped as the general-search backstop, what's the actual selection rule at runtime — user choice, automatic fallback on rate-limit/error, or a documented default with an override?
- Given Tavily's third-party query-forwarding note, is a query built only from public role/skill/employer/location terms (per Section 6's proposed allowlist) sufficient mitigation, or does the direction doc's "no exposure even for non-personalized queries" bar require excluding Tavily entirely regardless of query content?
- Is there a legal/compliance appetite question the operator wants answered before any employer-ATS polling begins at all (distinct from the technical feasibility this spike confirmed), given that ATS terms were reviewed only via each vendor's own developer docs and robots.txt, not a full ToS/EULA read for Greenhouse, Lever, or Ashby?
- Does "no signal on Workday" mean deprioritize Workday-hosted employers on the watchlist entirely for v1, or is a manually-pasted-URL-only path (no polling) an acceptable interim for those employers?
- What poll interval is both technically reasonable (courteous to ATS infrastructure — Lever documents a `Crawl-delay: 1`, suggesting per-request pacing, not necessarily a polling-interval bound) and tight enough to matter for the operator's "within the first hour or two" responsiveness objective?

## Addendum: measured trial queries (2026-09-21)

Everything above this section is documented, not measured (per the report's stated scope). This addendum is the one exception: three generic, public, non-personalized queries were run live against Tavily and Exa, using the operator's own existing API keys (not newly signed up for this spike). No private preference data, no real job application, no employer-ATS polling was performed. This is a small, disposable trial — 3 queries per provider — not the fuller freshness experiment Section 9 above describes (which would need a fixed multi-day polling window and a matched ATS comparison); it verifies basic reachability, latency, and result shape only.

**Method:** `POST https://api.tavily.com/search` (`search_depth: "basic"`, 5 results) and `POST https://api.exa.ai/search` (`type: "auto"`, 5 results), for the queries "senior backend engineer remote," "python developer san francisco," and "staff software engineer new york." Full request/response timing captured; raw JSON kept only in the session scratchpad, not committed to the repository. API keys were read from a local `.env` outside the repo and deleted from disk immediately after the trial; they were never logged, printed, or written into this document or memory.

**Tavily — all 3 queries succeeded (HTTP 200):**

| Query | Latency (wall / Tavily-reported `response_time`) | Results | Content snippets included |
| --- | --- | --- | --- |
| senior backend engineer remote | 1.39s / 1.02s | 5 | Yes |
| python developer san francisco | 1.24s / 1.05s | 5 | Yes |
| staff software engineer new york | 1.38s / 1.06s | 5 | Yes |

All 15 returned results were genuine job-posting-shaped pages (aggregator listing pages — RemoteRocketship, Wellfound, Built In, Dice, Indeed, direct employer job pages like `asana.com/jobs/apply/...` and `jobs.intuit.com/job/...`). No off-topic results in this small sample. Tavily returned usable content snippets on every result, consistent with the documented product shape (Section 3).

**Exa — 2/3 succeeded, 1 transient provider error, 1 notable coverage miss:**

| Query | Latency | Result | Notes |
| --- | --- | --- | --- |
| senior backend engineer remote | 1.22s | 5 results, HTTP 200 | On-topic: direct employer ATS links (`jobs.ashbyhq.com/close/...`, `jobs.ashbyhq.com/prefect/...`), one link verified live (HTTP 200) by a follow-up fetch in this trial. `publishedDate` present on 3/5 results (e.g. `2026-09-01`, `2026-08-03`), null on 2/5. |
| python developer san francisco | 0.15s | **HTTP 503**: `"Exa is temporarily over capacity. Please retry with exponential backoff."` | A live, undocumented-in-pricing-page reliability data point: Exa's own API returned a capacity error on a plain query in this trial, not a hypothetical. Should be retried with backoff per Exa's own error message before drawing a reliability conclusion from one failure, but this is a genuine measured occurrence, not a documentation claim. |
| staff software engineer new york | 1.30s | 5 results, HTTP 200, but **off-topic** | All 5 results were LinkedIn *personal profile* pages (e.g. `linkedin.com/in/jackmaris`), not job postings — a real, measured coverage/precision miss for `type: "auto"` on this query, not something Exa's own docs (Section 3) would have predicted. This is the kind of gap the "documented vs. measured" distinction in this brief exists to catch. |

**What this trial does and does not establish:**

- **Establishes:** both providers are live and reachable with the operator's existing keys; Tavily's documented content-snippet shape held in practice on this sample; Exa's `type: "auto"` mode is not reliably job-posting-scoped — at least one of three plain role/location queries returned LinkedIn profiles instead of postings, and a separate query hit a transient 503. Ashby-hosted postings surfaced by Exa's general search do independently corroborate Section 2's ATS-feed finding: the same employer postings are reachable both via Exa's index and via Ashby's own public API, though only the latter is free/keyless/change-detection-capable.
- **Does not establish:** indexing latency (none of these postings' actual publish times were known ahead of the query, so "how fresh" cannot be measured from this trial — that needs the matched ATS-vs-search timing experiment Section 9 names, run over a multi-day window against postings of known publish time); free-tier sustainability at real daily volume (3 queries is far below the "tens of searches/day" volume the free-tier question is about); or whether the `type: "auto"` miss is representative or a one-off — a larger sample (the report's existing 20-30 case framing from the S08 methodology could be reused here) would be needed before treating Exa's job-search precision as either adequate or inadequate.
- **One concrete follow-up this trial surfaces:** if Exa is prototyped further, `type: "keyword"` or a more constrained query construction should be tried against the same LinkedIn-profile-miss case before concluding `type: "auto"` is unsuitable — this trial did not test that variant.

## Sources reviewed (primary-first)

All web sources below were accessed on 2026-09-21 unless otherwise noted. Primary sources (each provider's/vendor's own docs, pricing pages, ToS, or a company's own blog/legal statement) are listed first per provider; secondary/aggregator sources used only as leads are marked accordingly and were not cited as the source for any claim above.

**Exa**
- [Exa Pricing](https://exa.ai/pricing)
- [Exa Pricing (docs mirror)](https://exa.ai/docs/reference/pricing)
- [Exa Data Index](https://exa.ai/docs/reference/the-exa-index)
- [Exa API FAQs](https://exa.ai/docs/reference/faqs)
- [Exa Zero Data Retention blog post](https://exa.ai/blog/zdr-search-engine)
- [Exa Terms of Service (PDF; fetched but not machine-extractable in this pass)](https://exa.ai/assets/Exa_Labs_Terms_of_Service.pdf)

**Tavily**
- [Tavily Pricing](https://www.tavily.com/pricing)
- [Tavily API Credits docs](https://docs.tavily.com/documentation/api-credits)
- [Tavily Platform Terms of Service](https://www.tavily.com/terms)
- [Tavily Acceptable Use Policy](https://www.tavily.com/acceptable-use-policy)
- [Tavily Privacy Policy](https://www.tavily.com/privacy) (accessed via search synthesis; direct fetch of full text not independently performed)

**Brave Search API**
- [Brave Search API Pricing](https://api-dashboard.search.brave.com/documentation/pricing)
- [Brave Search API Terms of Service](https://api-dashboard.search.brave.com/documentation/resources/terms-of-service)
- [Brave: "the only search API offering true Zero Data Retention"](https://brave.com/blog/search-api-zero-data-retention/)

**SerpApi**
- [SerpApi Pricing](https://serpapi.com/pricing)
- [SerpApi Google Jobs API](https://serpapi.com/google-jobs-api)
- [Google's own statement on the SerpApi lawsuit](https://blog.google/technology/safety-security/serpapi-lawsuit/)
- (secondary, leads only, not cited as source of legal fact) reporting on the Google v. SerpApi filing via MediaPost, VKTR, IPWatchdog, Search Engine Roundtable, Search Engine Land — used only to corroborate the filing date and motion-to-dismiss timing already stated in Google's own post.

**You.com**
- [You.com Pricing](https://you.com/pricing)

**Kagi**
- [Kagi API Pricing](https://kagi.com/api/pricing)
- [Kagi Search API overview (partial; full reference at kagi.com/api/docs not separately fetched)](https://help.kagi.com/kagi/api/search.html)

**ATS platforms**
- [Greenhouse Job Board API docs](https://docs.greenhouse.io/job-board.html)
- [Greenhouse `boards-api.greenhouse.io/robots.txt`](https://boards-api.greenhouse.io/robots.txt) (fetched directly)
- [Lever `postings-api` official GitHub repository](https://github.com/lever/postings-api)
- [Lever `api.lever.co/robots.txt`](https://api.lever.co/robots.txt) (fetched directly)
- [Ashby Public Job Posting API](https://developers.ashbyhq.com/docs/public-job-posting-api)

**Job boards' ToS (scraping posture)**
- [Indeed Terms of Service / Legal](https://www.indeed.com/legal)
- LinkedIn User Agreement Section 8.2 language (via search-result synthesis; not independently re-fetched from linkedin.com in this pass — flagged)

**Harness precedent**
- [DeepSeek API: Tool Calls guide](https://api-docs.deepseek.com/guides/tool_calls/)
- [Ollama: Tool calling capability docs](https://docs.ollama.com/capabilities/tool-calling)
- (leads only, not cited as authority) several open-source search-tool-wrapper repositories surfaced via search, used only to corroborate that a dedup/truncate/cache layer between raw search results and a model's tool-result message is a common pattern.

**GigAI repository references consulted for framing (not primary external sources)**
- [Scout search-first direction](/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/docs/development/v0.1.8/directions/scout-search-first-local-applications.md)
- [S09 spike brief](/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/docs/development/v0.1.8/spikes/S09-local-search-retrieval-capability-sourcing.md)
- [Jev structured-decision research](/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/docs/development/v0.1.8/spikes/evidence/jev-typesafe-ai-structured-decisions-research.md)
- [Qwen3.8 Scout local-model pilot](/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/docs/development/v0.1.8/spikes/evidence/qwen38-scout-local-eval.md)
