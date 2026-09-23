# S13 — Existing job-discovery solutions research

## Ticket

**Status:** Research recorded 2026-09-22.  
**Requested:** 2026-09-22. **Scope:** v0.1.8 spike; no new release gate.  
**Execution:** Research complete; no implementation, provider change, or
Scout behavior change is authorized by this ticket.

**Current evidence pointer:** [S13 research record](evidence/S13-existing-job-discovery-solutions-research.md)
(revised twice same-day after review — see its "Revision notes" section)
surveys eight candidates, including two genuinely installable libraries
missed in the first pass: `ats-scrapers` (pip-installable; its live
`BaseScraper.afetch()`/`fetch()` classes return a typed `list[Job]` model
and cover Greenhouse/Lever/Ashby — kept distinct from its separate hosted
`search()` DataFrame interface, which queries a pre-aggregated snapshot, not
live per-employer data) and `JobSpy` (pip-installable, covers LinkedIn/
Indeed-class boards, not ATS platforms). Neither was installed or run —
suitability is unverified pending a real evaluation, though a source-based
adapter sketch and rough integration-effort estimate for `ats-scrapers`'s
live scrapers is recorded. For deduplication: adapt `job-finder`'s
multi-pass approach now; embedding/vector-search is a documented but
unverified-effectiveness alternative technique, not confirmed working, and
not the only technique handling non-exact matches. For staleness:
`job-finder`'s apply-link probe-and-classify logic (`staleness.py`) is a
concrete, reusable technique beyond TTL/diffing, missed in the first pass.

**Problem:** [S09](S09-local-search-retrieval-capability-sourcing.md) already
surveyed search/ATS-feed providers, pricing, freshness/indexing-latency claims,
ToS constraints, and harness tool-call precedent for job discovery. What it did
not do is survey existing *implementations* — concrete open-source projects,
libraries, or products that already do posting discovery, dedup, or
staleness detection end to end. Designing Scout's discovery tools without that
check risks rebuilding something a maintained project already does.

**Intended behavior:** Building on S09's provider/ATS survey (do not
re-survey it), find concrete existing implementations — projects, libraries,
scrapers, or aggregators — that already perform job-posting discovery,
deduplication, or staleness detection, and assess what Scout could reuse
versus what remains custom work. Sample-resume gathering is secondary: note
prior art only if found without expanding the survey to cover it in depth.

**Proposed tasks (refine before execution):**

- Identify concrete open-source projects or libraries that implement
  job-posting discovery, aggregation, deduplication, or staleness/freshness
  detection (not general search providers — those are S09's scope).
- For each candidate, record: what it does, sources/providers it integrates
  with, output contract (schema/format returned), maintenance status
  (last commit, issue activity), license, and rough integration effort into
  Scout's current tool shape.
- Distinguish reuse-as-dependency, reuse-as-reference-implementation (adapt
  the approach, not the code), and no-fit.
- Note, without expanding scope, any existing prior art for sample-resume
  gathering if it surfaces during the above search.
- Summarize which of Scout's *intended* discovery-tool capabilities (see
  Framing below) already have a strong existing implementation to build on,
  and which remain genuinely custom.

**Acceptance / attached evidence:** A short written survey listing each
candidate project/library found, its maintenance/license/integration-effort
assessment, and a reuse/adapt/no-fit recommendation per Scout capability. No
code change, dependency addition, or tool-binding change follows from this
spike alone; adoption of any finding is a separate, explicit decision.

**Evidence now:** See [S13 research record](evidence/S13-existing-job-discovery-solutions-research.md)
above. This ticket authorizes research only; no code change follows from it.

## Framing — intended, not existing, capabilities

Scout's discovery approach as discussed is **intended/planned**, not
established fact from this research: web search and ATS polling as discovery
tools, plus a proposed (not yet built) sample-resume-gathering capability.
This ticket does not claim any of these are currently implemented in Scout;
verify actual implementation status against the codebase separately if that
distinction matters for scoping a task, rather than assuming it from this
document or the source discussion.

## Conversation notes

Condensed from the 2026-09-22 discussion in
[1.8-chat-09-21-26.md](../1.8-chat-09-21-26.md) (see "Next discussion topics —
spikes not yet created"). The operator asked for two spikes from that
discussion and requested this simpler one be created first; the companion
tools/goal-unit composition spike is tracked separately (see Related work).

The operator's framing: check how others already solve discovering postings
before adding more capability — a research-before-build step, not a request
to change search providers now. Follow-up review corrected this ticket's
first draft on two points: it must build on S09 rather than repeat its
provider/ATS survey, and it must not assert that Scout's discovery tools are
already implemented (see Framing above).

## Related work and open placement

- [S09 local search/retrieval capability sourcing](S09-local-search-retrieval-capability-sourcing.md)
  is the prerequisite provider/ATS-feed/pricing/freshness survey. S13 does not
  repeat it — S13's distinct question is which existing *implementations*
  (projects/libraries) Scout can reuse, not which providers/capabilities
  exist.
- [1.8-chat-09-21-26.md](../1.8-chat-09-21-26.md) records the full discussion
  this spike was drawn from, including the Gig/goal-graph/tool definitions
  that motivate treating "discover postings" as a replaceable tool contract
  rather than a fixed implementation.
- The companion spike — tools and goal-unit composition into traversable node
  graphs — is the second topic from the same discussion; create it separately.

No implementation, provider adoption, or Scout tool-binding change is
authorized by recording this ticket.
