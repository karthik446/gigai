# Spike 9 — Local search/retrieval capability sourcing

**Requested:** 2026-09-20. **Status:** Research recorded 2026-09-21; no
provider signup, API key activation, paid call, ATS polling or scraping is
authorized by this brief.

**Current evidence pointer:** [S09 research record](evidence/S09-local-search-retrieval-capability-sourcing-research.md)
supersedes the earlier "not started" index status. It records documented
provider claims plus a clearly labelled small query addendum; it does not prove
indexing/freshness latency, sustained coverage, or a durable right to store
returned results. Those remain implementation/proof decisions requiring
separate authorization.

**Question:** A local-first, installable Scout still needs to find jobs on the
public web. Local inference (Qwen/Ollama) does not itself supply search or page
fetch. What capability should provide it, at what cost, and how does a local
harness wire a model to it safely?

## Motivation and scope

The [Scout search-first direction](../directions/scout-search-first-local-applications.md)
states search/fetch is "a separate capability with its own availability, access
terms, quotas and possible charges; local inference alone does not supply it,"
and flags that private preferences must not leak into search queries. This
spike answers the operator's direct question: "for local + search — Exa API
key needed? or Tavily or whatever — how do we provide those capabilities?"

This is a completed landscape survey to pick a direction, not a procurement
decision or runtime acceptance.
The likely outcome the operator already anticipates is "cost-zero except
search" — this spike should confirm or correct that, not assume it.

The operator's real question is discovery speed: how quickly can Scout find a
newly posted, relevant job, not just whether a search API can find jobs at
all. Prioritize investigation accordingly — freshness/latency-to-discovery is
the primary axis, general search-API keyword coverage is secondary.

## Investigate

- **Discovery, fetching, and change detection are separate capabilities.**
  Keep them distinct throughout the survey rather than treating "search" as
  one undifferentiated capability:
  - *Discovery*: learning a new posting exists (search API query, employer/ATS
    feed, watchlist match).
  - *Fetching*: retrieving the posting's actual content once its URL is known.
  - *Change detection*: noticing a known posting was edited, closed, or
    reposted (relevant to the direction doc's "posting time may be missing,
    ambiguous, or changed by reposting" requirement).
  A provider or method may cover one, two, or all three; say which.

- **Employer/ATS feeds and watchlists, not just general search APIs.** Many
  employers publish postings through a small number of ATS platforms (e.g.
  Greenhouse, Lever, Ashby, Workday) that expose public job listing pages or
  feeds per company. Investigate whether polling a user-curated watchlist of
  specific employers' ATS pages/feeds gives faster, cheaper, more reliable
  discovery for roles the user already cares about than a general search API
  — these are plausibly complementary, not competing, discovery paths.

- **Candidate providers.** Survey at minimum Exa, Tavily, Brave Search API, and
  SerpAPI (add others found during research, e.g. You.com, Kagi's API if
  publicly documented). Substantiate each provider's claims from its own
  primary documentation (pricing page, API docs, ToS); third-party writeups
  and comparisons are useful as leads for what to check, not as the cited
  source for a claim. For each, record:
  - Pricing model and free-tier limits at realistic Scout query volume
    (e.g. tens of searches/day, not one-off demo calls).
  - Freshness and job-board coverage — does it index job postings well, or is
    it general web search that happens to find some. Look specifically for any
    documented indexing latency (how long after a page is published/changed
    the provider's index reflects it), not only breadth of coverage.
  - Rate limits, ToS terms on caching/storing results, and any restriction on
    downstream use (e.g. "no resale," "no bulk export").
  - Response shape: does it return snippets only, full page content, or
    require a separate fetch step.

- **Cost-zero reality check.** Directly examine the operator's assumption: is
  there a free-tier path that plausibly survives realistic daily Scout usage,
  or does search remain the one paid leg regardless of how local everything
  else is? State this as a documented, cited-pricing conclusion, not an
  assumption carried over from the direction doc — and not as a measured
  result, since no live query is authorized here (see "Documented estimates
  versus measured results" below).

- **Harness precedent.** Survey how existing local/agent harnesses wire a
  model to a search tool:
  - DeepSeek's public harness material (tool-call schema for search, how
    results are truncated/summarized before being handed back to the model).
  - Any other public local-agent-plus-search stack worth citing (e.g. open
    source deep-research/browsing agent projects), scoped to concrete
    mechanics: tool schema, pagination/dedup handling, result truncation,
    and how they avoid re-fetching duplicate postings.
  - Ollama's own [tool-calling docs](https://docs.ollama.com/capabilities/tool-calling)
    for how a local model would be given a search tool in the same shape.

- **Privacy boundary.** The direction doc names a real risk: an overly
  personalized search query (e.g. including salary target or a private
  constraint) leaking preference data to a third-party search provider.
  Investigate how to keep the acquisition query (public job search) separate
  from the private assessment step (which uses preferences), and whether any
  candidate provider's logging/ToS creates exposure even for non-personalized
  queries. No silent hosted fallback for private material.

- **Legal/ToS boundary on job boards.** Many job boards disallow direct
  scraping. Compare search-API-mediated discovery (provider indexes the page,
  Scout only sees search results) against direct fetch of a job posting URL
  once discovered. This is a cheap survey of each provider's and major job
  board's public ToS language, not a legal opinion — flag genuine uncertainty
  rather than asserting compliance.

- **Installable/local-first fit.** If Scout is meant to be software the user
  installs and runs themselves, the search key most likely becomes
  "bring your own API key," matching the pattern most local-LLM tools already
  use. Investigate minimal setup friction for that: where the key is stored
  locally, how a missing/invalid key degrades (acquisition should not silently
  fail per the direction doc's "must not hide jobs" requirement), and whether
  a provider offers a no-signup/anonymous free tier suitable for first-run
  trial before a user commits to an account.

## Starting points in this repository

- [Scout search-first direction](../directions/scout-search-first-local-applications.md)
  — names this exact gap ("Searching/fetching is a separate capability...
  local inference alone does not supply it") and the privacy-leak concern this
  spike must address.
- [Jev structured-decision research](evidence/jev-typesafe-ai-structured-decisions-research.md)
  — the state/evidence separation this spike's fetched content should feed
  into (`evidence_refs` distinct from a model's explanation).
- [Qwen3.8 Scout local-model pilot](evidence/qwen38-scout-local-eval.md) —
  existing supplied-source evaluation used no live search; this spike is the
  first place online discovery is examined for the local setup.

## Documented estimates versus measured results

This brief authorizes no signup, API trial, or live query. Its findings on
pricing, coverage, and indexing latency are therefore documented claims from
each provider's own primary materials, not measured results. State every
freshness, coverage, and free-tier-sufficiency claim as "documented/claimed"
rather than "measured." That documented evidence is sufficient to justify
which option(s) are worth prototyping — it does not itself prove actual
freshness, coverage, or free-tier sufficiency in practice. Where a claim
materially drives the provider choice (especially indexing latency, since
that is the operator's real question), name the smallest authorized
experiment that would verify it — e.g. a handful of live trial queries
against a provider's free tier, or polling one ATS feed for a fixed window
— as follow-up work requiring separate
authorization, not something this spike can settle by reading documentation.

## Expected output

A provider comparison table (pricing/free-tier, coverage, indexing-latency
claims, ToS, rate limits) covering both general search APIs and employer/ATS
feed or watchlist approaches, a direct answer to the cost-zero-except-search
question with citations, a proposed tool-call shape for wiring a local model
to the chosen provider(s), and an explicit privacy-separation design between
acquisition queries and private assessment data. Separate discovery, fetching,
and change-detection findings rather than reporting one blended "search"
conclusion. End with a recommendation (which provider(s)/feeds to prototype
with, or "none fit — reconsider approach"), the smallest experiment needed to
verify each load-bearing documented claim, and open questions. No signup, key
activation, paid call, or scraping follows from recording this brief.
