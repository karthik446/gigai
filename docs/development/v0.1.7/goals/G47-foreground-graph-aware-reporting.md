# G47 — Foreground Graph-Aware Reporting

**Version:** v0.1.7
**Status:** Proposed — contract defined; not activated
**Depends on:** G43.2, G45, G45.2, G46, G46.1, and G46.2
**Defers:** workers, schedules, application-pipeline state, and unattended work

## Outcome

G47 makes completed foreground Runs inspectable through rebuildable CLI, JSON,
Markdown, and optional static HTML projections. It reports the exact Gig
version, Graph Set, selected graph, Plan, direct-consent Run, sources, effects,
usage, terminal state, artifacts, and graph-specific provenance without
becoming a second authority or starting any work.

For the Job Search Lifecycle UAT, it can render:

- a Find report with source policy, query-preview digest, as-of/retrieval time,
  result/no-match/failure state, lead count, deduplication evidence, and safe
  lead summaries; and
- a Tailor report with selected lead-binding/input identity, source evidence,
  factuality/gap status, and output artifacts.

## Boundary

All projections are derived from authoritative journal/workpad/Plan/Run
artifacts. Reading or rebuilding them does not allocate a Run, refresh a
source, invoke a provider, re-rank a lead, infer application status, write a
target repository, or modify durable evidence.

HTML is a static local artifact. G47 does not add an HTTP service, background
worker, scheduler, retry loop, current-state database, or a competing approval,
consent, or lifecycle surface.

## Acceptance evidence

Tests and UAT prove that reports rebuild identically from a completed
foreground Run, distinguish v1/v2 and selected graphs, preserve raw-private
artifact redaction, disclose source/read failure honestly, and link an explicit
lead-input binding without claiming an automatic journey. CLI/JSON/Markdown/HTML
projections remain read-only and tolerate missing optional HTML without changing
authoritative Run state.

## v0.1.8 handoff

v0.1.8 may add graph-specific schedules and an append-only application
pipeline. Those features consume G47 projections/evidence but require their
own activation, recovery, deduplication, schedule-consent, and state-transition
contracts. G47 itself never starts unattended work.
