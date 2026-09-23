# S13: Existing job-discovery solutions — research

**Date:** 2026-09-22 (revised same-day after review; see "Revision notes"
at the end for what changed and why).
**Scope:** v0.1.8 research only. Every project below was assessed by reading
its own GitHub repository page/README/PR/source file content (fetched
directly, dates and file paths noted per source); no code was cloned,
installed, or run, and no library's actual runtime behavior was measured.
No provider signup, ATS polling, or scraping was performed. This builds on
[S09](../S09-local-search-retrieval-capability-sourcing.md)'s
provider/ATS/pricing/freshness survey — that ground is not re-covered here.
**Question:** Given S09 already identifies which providers and ATS feeds
exist, are there existing open-source implementations that already build
posting discovery, deduplication, or staleness detection on top of them —
and does Scout gain anything by reusing or studying them versus building its
discovery tools from scratch?

## Summary

Two genuinely installable, importable libraries were found —
**JobSpy** (`pip install python-jobspy`) and **ats-scrapers**
(`pip install ats-scrapers`) — which changes the survey's earlier
conclusion that nothing packaged existed. Their suitability for Scout is
**not yet verified**: neither was installed or run, so claims below about
their APIs and coverage are documentation-sourced, not tested. Beyond these
two, the remaining candidates are standalone single-operator scripts, not
packages, and reuse-as-dependency does not apply to them.

- **JobSpy** scrapes LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter,
  Bayt, Naukri, and BDJobs. It does **not** cover Greenhouse, Lever, or
  Ashby — it is complementary to, not a substitute for, S09's ATS-feed
  finding.
- **ats-scrapers** actually documents **two distinct interfaces, not one**:
  live per-vendor scraper classes (`GreenhouseScraper("anthropic").fetch()`
  / `afetch()`, returning `list[Job]` — a typed Pydantic model, source read
  directly from `scrapers/base.py` and `models.py`) covering Greenhouse,
  Lever, Ashby, and several ATS platforms S09 did not cover (Workday,
  SmartRecruiters, SuccessFactors, and others); and a separate `search()`
  function that queries the project's own pre-built, hosted Parquet/CSV
  dataset snapshot and returns a pandas DataFrame, not a live scrape. These
  serve different purposes and should not be described as one interface.
  The live scraper classes are the closest thing found to a reusable
  discovery dependency and need a real evaluation (install, run against one
  company, inspect actual output) before any
  reuse decision — see Open questions.

On deduplication, `job-finder`'s multi-pass approach already includes a
fuzzy/non-exact matching step (title+company similarity), so it is
inaccurate to describe embedding-based matching as the only technique
handling non-exact duplicates; it is a second, more general technique for
the same problem, not the sole one. Separately, the embedding project's own
repository states no benchmark or evaluation was run — it should be
described as a documented implementation of a plausible technique, with its
actual effectiveness unverified, not as "real and working."

On staleness, `job-finder` implements a concrete, non-trivial check beyond
simple TTL or diffing: it probes each posting's apply link and classifies
the response, treating `404`/`410` or specific closed-posting body text as
confirmed-expired, while `403`, `429`, `5xx`, and timeouts are treated as
inconclusive (kept live) rather than expired. This is a real, reusable
staleness technique this survey's first pass missed.

## Candidates surveyed

### Installable libraries (suitability unverified — documentation only)

| Project | What it documents | Sources/ATS covered | Output contract (per README) | License | Maintenance signal | Assessment |
| --- | --- | --- | --- | --- | --- | --- |
| [speedyapply/JobSpy](https://github.com/speedyapply/JobSpy) | `pip install -U python-jobspy`; `scrape_jobs(site_name, search_term, location, distance, results_wanted, job_type, is_remote, easy_apply, hours_old, proxies, ...)` returning one row per job | LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter, Bayt, Naukri, BDJobs — **not** Greenhouse/Lever/Ashby | Documented fields: title, company, location (country/city/state), job URL, description, job type, salary (interval/min/max/currency), date posted, remote flag, plus site-specific fields (e.g. LinkedIn level) | MIT | 4,300+ stars, 353 commits, 865 forks, 47 open issues, 18 open PRs — high visible activity; exact last-commit date not confirmed in this pass | **Needs evaluation, not yet a fit-or-no-fit call.** Complementary to S09's ATS-feed finding (different sources entirely), not a replacement. If Scout wants big-board coverage (LinkedIn/Indeed-class sites) alongside ATS polling, this is the one concrete candidate; requires installing and inspecting real output before any adoption decision, and its ToS/scraping-legality posture for the big boards it covers was not assessed here (S09 flagged general job-board scraping legal exposure; this library's own stance on that was not checked). |
| [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers) — **live scraper classes** | `pip install "ats-scrapers[scrapers]"`; per-vendor classes subclass `BaseScraper` (source: `scrapers/base.py`) — `async def afetch(self) -> list[Job]` ("return all currently active jobs for this company"), with `fetch()` as a sync wrapper. Example: `GreenhouseScraper("anthropic").fetch()` | Greenhouse, Lever, Ashby, Workday, SmartRecruiters, SuccessFactors, Oracle, iCIMS, ADP Workforce Now, HERP Hire, HRMOS, Keka, Paycom, Softgarden, Workable, Personio; first-party APIs (Amazon, Apple, Google, TikTok, Uber); public sources (EURES, Welcome to the Jungle, others) | `list[Job]` — a Pydantic model (source: `models.py`) with typed fields: `global_id`, `url`, `title`, `company`, `ats_type`, `ats_id`, `location`, `country_iso`, `region`, `lat`/`lon`, `is_remote`, `salary_currency`/`salary_period`/`salary_summary`/`salary_min`/`salary_max`, `experience`, `employment_type`, `department`, `team`, `requisition_id`, `apply_url`, `commitment`, `description`, `posted_at`, `fetched_at`, `application_deadline`, `language`, plus a `raw` dict for provider-specific passthrough | MIT | 500+ commits, 158 stars, 42 forks, open PRs present; exact last-commit date not confirmed in this pass | **This is the reusable-dependency candidate.** Live, per-company fetch — matches Scout's actual need (poll specific watchlisted employers) far better than the hosted dataset below. See "Integration sketch" for a concrete adapter shape and effort estimate. Still needs a real install-and-run pass to confirm the adapters work as documented; not yet verified. |
| [kalil0321/ats-scrapers](https://github.com/kalil0321/ats-scrapers) — **hosted dataset `search()`** | `pip install ats-scrapers` (add `[parquet]` for full-dataset queries); `search(query, location, ats, limit, remote, salary_min)` queries a **pre-built, already-aggregated snapshot** the project hosts (base install: per-source CSV slices; `[parquet]` extra: full hosted Parquet snapshot at a [published manifest URL](https://storage.stapply.ai/jobhive/v1/manifest.json)) | Same vendor list as above, pre-scraped by the project itself, not fetched live by the caller | `pandas.DataFrame` — a snapshot query result, not a live per-company fetch | MIT | Same repository/activity as above | **Different fit than the live scrapers — not a live-discovery tool.** This is closer to a static dataset lookup than a discovery mechanism Scout would poll; useful only if Scout wanted a broad, already-aggregated snapshot rather than fresh per-watchlist-employer data, which is not what S09's freshness-focused framing calls for. Do not conflate this with the live scraper classes above — they solve different problems within the same package. |

### Reference-only implementations (not packaged; approach may be adapted, code would not be imported)

| Project | What it does | Sources integrated | Output/storage | License | Maintenance | Reuse assessment |
| --- | --- | --- | --- | --- | --- | --- |
| [BenAttanasio/job-finder](https://github.com/BenAttanasio/job-finder) | Scheduled scrape → dedup → Claude-scored profile match → SQLite → email digest + Flask dashboard | JSearch (Google Jobs via RapidAPI), Adzuna, Greenhouse/Lever/Ashby public boards, FinOps Foundation board, Claude server-side web search, direct employer pages | SQLite database (specific table/column schema not read in this pass — "SQLite" names the storage engine, not the output contract); Flask dashboard | MIT | Active — most recent commit 2026-09-13 (9 days before this survey), ~13 commits, 2 contributors | **Reuse-as-reference-implementation.** Closest end-to-end shape to Scout's own discovery→assess flow. Two specific techniques worth adapting directly (not importing): the ordered dedup pass sequence (below) and the apply-link staleness probe in `staleness.py` (below). A concrete integration path — pinning the exact commit read, and mapping its SQLite columns to fields Scout would need — is unresearched; see Open questions. |
| [Feashliaa/job-board-aggregator](https://github.com/Feashliaa/job-board-aggregator) | Daily multithreaded ETL pulling 7 ATS platforms into chunked gzip JSON, served via a static frontend with client-side filtering | Greenhouse, Lever, Ashby, BambooHR, iCIMs, Paylocity, Workday | Chunked gzip JSON files (~25k jobs/chunk) + manifest; per-job fields include title, company, location, ATS source, experience-level classification, application-status tracking (exact schema/field names not read from source in this pass) | Code: MIT. Curated company dataset: **CC BY-NC 4.0 (non-commercial)** — a real license constraint if Scout ever reused the *data*, not just the approach | Active — 420 commits, daily GitHub Actions refresh | **Reuse-as-reference-implementation for scale/format and staleness-pruning pattern only.** Its `merge_data.py` dedup-on-merge and 30-day-old pruning are documented techniques, but the project targets 1M+ postings across 20k companies — well beyond Scout's current per-user watchlist scope, so its 30-day figure is a scale-driven choice, not a number to copy without justification. |
| [adgramigna/job-board-scraper](https://github.com/adgramigna/job-board-scraper) ("Levergreen") | Daily Scrapy-based scrape of Greenhouse/Lever (README also names Ashby/Rippling) → S3 raw HTML → Postgres → dbt transforms → Airtable → Softr frontend | Greenhouse, Lever (Ashby/Rippling named but not detailed in README) | Postgres via a multi-stage ETL (S3 → dbt → Airtable); exact table schema not read in this pass | MIT | 406 commits; exact last-commit date not confirmed in this pass | **No-fit as a dependency; reference only for its S3-cache-to-avoid-rescraping pattern.** Heavier multi-vendor-SaaS pipeline than Scout needs; the one transferable idea is caching raw fetches to avoid re-hitting a source twice in a day. |
| [bennnnnn/recall PR #1382](https://github.com/bennnnnn/recall/pull/1382) | A single PR adding normalized title+company cross-source dedup on top of existing URL-hash dedup, within a broader "recall" job-tracking app | Not detailed (PR-scoped); broader app aggregates multiple job boards | Not detailed (PR-scoped) | Not confirmed in this pass | Unclear — single PR reviewed, not the whole repo's activity | **Reference for one dedup technique only.** Moving from URL-hash-only to normalized title+company matching, with cross-source matches updating an existing record rather than duplicating it, is a small, adaptable technique. |
| [arasgungore/job-posting-duplicate-detection](https://github.com/arasgungore/job-posting-duplicate-detection) | Documents using Sentence-Transformer embeddings plus Milvus vector search to flag semantically similar postings; source-agnostic (reads a CSV) | None (source-agnostic; takes any job-postings CSV) | Milvus vector index | MIT | Low — 7 commits total, explicitly presented as a completed take-home ML exercise; no evident ongoing maintenance, and the repository does not report having been benchmarked or evaluated for accuracy | **Documented technique, effectiveness unverified — not "real and working."** This is a second technique addressing non-exact/near-duplicate matching (title+company fuzzy matching, in `job-finder`'s later pass, is the first); it is not the only candidate solving that problem, and this project's own material does not demonstrate it working correctly, only that it implements the approach. Running Milvus is also disproportionate infrastructure for Scout's current per-user watchlist scale. Worth revisiting only if a fuzzy-matching approach proves insufficient in practice, and only after independently verifying the technique's accuracy. |
| [emredurukn/awesome-job-boards](https://github.com/emredurukn/awesome-job-boards) | Curated link list of job boards/ATS platforms | N/A (index, not code) | N/A | Not applicable | Not assessed for reuse (not an implementation) | **Not a candidate implementation** — useful only as a discovery aid for further sourcing. |

## Specific reusable techniques found

### Deduplication (job-finder)

Per its own description, dedup runs as ordered passes: exact `job_id` match,
then exact canonical apply URL with tracking parameters stripped, then fuzzy
match on normalized title plus company, then near-identical titles whose
companies are compatible. The third and fourth passes are themselves
non-exact/fuzzy matching — the same class of problem the embedding-based
project addresses with a different technique, not a problem unique to
embeddings.

### Staleness (job-finder, `staleness.py`)

Read directly from
[the source file](https://github.com/BenAttanasio/job-finder/blob/main/staleness.py):
apply links are probed and classified into two outcomes.

- **Confirmed expired:** HTTP `404` or `410`, or response body text matching
  a pattern for explicit closure language (e.g. "no longer
  available/accepting/active", "this job/position/posting/listing has been
  expired/closed/filled" and near variants).
- **Inconclusive, treated as still live:** HTTP `403` (bot-wall protection),
  `429` (rate limiting), `5xx` server errors, or network timeouts/connection
  failures. The design is deliberately conservative — ambiguous responses
  default to "not dead" so flaky hosts or anti-bot measures don't falsely
  expire a real listing.

This is concrete, reusable behavior beyond both TTL-based pruning and
simple content-diffing, and beyond what S09 established (ATS-provided
timestamps, or caller-side hash/diff). It is a candidate technique for
Scout's own staleness handling regardless of which discovery library, if
any, Scout ultimately uses.

## Integration sketch and effort estimate — shortlisted libraries

This is a source-based sketch of what wiring each shortlisted library into
Scout would look like, and a rough effort estimate. It is a design sketch
for review, not a build plan; no code is written or run as part of this
spike.

### `ats-scrapers` live scraper classes (the shortlisted candidate)

**What Scout would call:** one `BaseScraper` subclass per watchlisted
employer's ATS platform, e.g. `GreenhouseScraper(board_token).fetch()` /
`await ...afetch()`, per company on the user's watchlist — directly
parallel to how S09 already frames ATS polling (per-employer, per-platform,
on a fixed interval).

**Adapter needed:** a thin translation layer mapping the library's `Job`
Pydantic model to whatever internal posting representation Scout's
discovery tool contract expects (not yet defined in this survey; see S15
for the goal/tool-unit shape). Concretely:

- `Job.global_id` / `Job.ats_id` → Scout's posting identity field, usable
  directly as (or as an input to) the exact-ID dedup pass already
  recommended above.
- `Job.url` / `Job.apply_url` → feeds both the canonical-URL dedup pass and
  the `job-finder`-style staleness probe. Being a typed `HttpUrl` only
  confirms well-formed URL shape; it says nothing about the value's source,
  provenance, or whether it's the right URL to poll under Scout's own fetch
  rules — that validation would still be Scout's responsibility.
- `Job.posted_at` / `Job.fetched_at` → these describe *when the posting was
  published* and *when this library last observed it*, not whether it was
  *edited*. Neither is an edit-detection timestamp: `fetched_at` only marks
  observation time, and `posted_at` is a publication date. Detecting an
  in-place edit still needs either a documented vendor update field (e.g.
  Greenhouse's `updated_at`, per S09) or a caller-side content hash/diff, if
  the library surfaces the raw vendor field at all — not established here
  and not something these two fields alone provide.
- `Job.raw` → an escape hatch for any vendor-specific field the typed model
  doesn't surface, which is useful given how much per-vendor variation S09
  already documented (e.g. Ashby's `publishedAt` semantics).

**Rough effort estimate:** small if the library works as documented — one
adapter function mapping ~20 typed `Job` fields to Scout's own schema, plus
error handling for whatever the library does on a network failure/rate
limit (not documented in the material read; would need to be observed in
an actual run). This estimate is contingent on the still-unverified claim
that `afetch()`/`fetch()` behave as the source-level docstrings say; it
could be materially larger if the library's error handling, rate-limiting,
or auth requirements (if any exist beyond what was read) turn out to need
extra work Scout would otherwise not have written by hand-rolling against
S09's endpoints directly.

**What would NOT need building** if this library works as documented:
Scout would not need to write or maintain its own Greenhouse/Lever/Ashby
JSON-parsing code, which is otherwise the fallback S09 and this survey both
point to.

### `ats-scrapers` hosted `search()` (not shortlisted for live discovery)

No adapter sketch is offered here: as noted above, this queries the
project's own pre-aggregated snapshot rather than performing a live
per-employer fetch, so it does not serve Scout's watchlist-freshness need
and is not a candidate for the same integration path.

### `JobSpy`

No adapter sketch is offered for the same reason discovery via `JobSpy`
was deprioritized above: it does not cover Greenhouse/Lever/Ashby at all,
so it would supplement, not implement, Scout's ATS-polling capability. If
Scout later wants big-board coverage (LinkedIn/Indeed-class), a similar
sketch (map its documented per-job fields — title, company, location,
description, salary, date posted, remote flag — into Scout's schema) would
be the same shape of work, but that is a separate capability decision, not
part of this ATS-polling assessment.

## Sample-resume gathering — no prior art surfaced

No candidate found addresses sample-resume gathering. This was a secondary,
non-expanded part of the search per the ticket's scope; absence of a hit
here is not evidence of absence generally.

## Recommendation per Scout capability

| Scout capability (intended, not yet confirmed built) | Recommendation |
| --- | --- |
| Web search discovery | S09's provider survey remains the operative research; no candidate here changes that. |
| ATS polling (Greenhouse/Lever/Ashby) | **Evaluate `ats-scrapers`'s live scraper classes (`BaseScraper.afetch()`/`fetch()` → `list[Job]`) before hand-rolling** — not its separate hosted-dataset `search()`, which queries a pre-aggregated snapshot and does not serve live per-employer polling. See "Integration sketch" for a concrete adapter shape and effort estimate against the typed `Job` model. Not yet verified to work correctly or produce trustworthy output — install it and check its real behavior against one board before deciding to adopt or bypass it. `job-finder` and `job-board-aggregator` show hand-rolled fetch-and-parse is a viable fallback if the live scrapers don't hold up. |
| Big-board discovery (LinkedIn/Indeed-class, outside S09's ATS/general-search scope) | `JobSpy` is a concrete, popular, installable option if Scout wants this coverage; requires its own legal/ToS check (not done here) and a real evaluation before adoption. |
| Deduplication | **Adapt now:** `job-finder`'s ordered pass sequence (exact ID → canonical URL → fuzzy title+company → compatible-company near-match) and the `recall` PR's normalized title+company technique are concrete and directly portable into Scout's own dedup logic. **Defer, and verify before relying on:** embedding/vector-search matching (`arasgungore`) is a documented, unverified-effectiveness technique for the same non-exact-match problem the fuzzy pass already partly covers; revisit only if fuzzy matching proves insufficient, and confirm its accuracy independently first. |
| Staleness/freshness detection | **Adapt now:** `job-finder`'s apply-link probe-and-classify logic (confirmed-dead status codes/text vs. inconclusive-stays-live) is a concrete technique beyond what S09 or the rest of this survey found. `job-board-aggregator`'s 30-day prune is a scale-driven housekeeping number, not a technique to copy without justification for Scout's own scale. |
| Sample-resume gathering | No prior art found; still an open, unresearched gap. |

## Open questions

- **Neither `JobSpy` nor `ats-scrapers` was installed or run.** All claims
  about their APIs, coverage, and output fields are sourced from their own
  README/documentation as rendered on GitHub, not verified execution. The
  concrete next step, if either is a serious candidate, is: install it,
  pin the exact version/commit evaluated, run it against one real
  source (e.g. one Greenhouse board for `ats-scrapers`), and record the
  actual output fields and any errors — not just the documented ones.
- `job-finder`'s SQLite schema and `job-board-aggregator`'s/
  `adgramigna`'s exact JSON/Postgres field names were not read from source
  in this pass; only their READMEs' prose descriptions were used. A closer
  read of the actual schema (or a pinned source revision) is needed before
  scoping any adapter Scout would need to consume their output shape, if
  reuse ever goes beyond technique-borrowing.
- `adgramigna/job-board-scraper`'s and both libraries' exact last-commit
  dates were not confirmed; if any is of real interest, verify current
  activity before citing it as "actively maintained."
- This search was not exhaustive — GitHub/web search surfaces popular and
  recently-active repositories more than obscure or older ones; the two
  installable libraries found this revision were missed in the first pass,
  which is itself evidence the search method has real recall gaps.

No implementation, dependency addition, or Scout tool-binding change follows
from this research; adopting any technique or library above is a separate,
explicit decision requiring its own verification first.

## Revision notes (same-day correction)

The first version of this document concluded "nothing found is an
importable library" and "reuse-as-dependency is not realistic for any
candidate found." That conclusion was wrong: `JobSpy` and `ats-scrapers` are
both real, documented, pip-installable packages and were missed by the
original search terms. It also claimed the staleness survey found nothing
beyond TTL/diffing, when `job-finder`'s own `staleness.py` (not read in the
first pass) implements a more specific technique. It also called the
embedding-based dedup approach "real and working" without verification, and
described it as the sole technique for non-exact matching when the
fuzzy-matching pass already in this document's own dedup table contradicted
that. This revision corrects all four points; nothing in the original
document's ATS/pricing/provider-adjacent claims (which came from S09, not
this document's own research) was affected.

**Second correction pass (same day):** the first revision conflated
`ats-scrapers`'s two distinct interfaces — its live per-vendor scraper
classes (`BaseScraper.afetch()`/`fetch()` returning `list[Job]`, read
directly from `scrapers/base.py` and `models.py`) and its separate
hosted-dataset `search()` function (querying a pre-aggregated snapshot,
returning a DataFrame) — as if they were one thing described loosely as
"returns pandas DataFrames." They are now split into two table rows. This
pass also adds a source-based integration sketch and rough effort estimate
for the shortlisted live-scraper interface, since the ticket asked for
rough integration effort and the first two revisions had deferred that
entirely to a hypothetical future install-and-run step. No installation or
live execution was performed to write the sketch — it is derived from the
library's own class/model source code as fetched and read directly.
