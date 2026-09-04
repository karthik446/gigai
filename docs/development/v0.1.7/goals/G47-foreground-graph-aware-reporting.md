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

- a Find report with Exa research policy, sealed query brief, each bounded
  Codex/Claude research action, provider usage, citation/snapshot provenance,
  as-of/retrieval time, result/no-match/failure state, lead count,
  deduplication evidence, and safe lead summaries; and
- a Tailor report with selected lead-binding/input identity, source evidence,
  factuality/gap status, and output artifacts.

## Determinism, redaction, and source labels

`report-projection.schema.json` has schema ID
`urn:gigai:schema:report-projection:1`. Every projection records `renderer_id`,
`renderer_version`, `projection_schema_version`, the exact authoritative source
artifact references/digests, and a canonical projection digest. The source list
is ordered by artifact path then digest; candidates/promotions are ordered by
their stable IDs; diagnostics are ordered by stable diagnostic code then event
sequence. No rebuild-time timestamp, random ID, locale-dependent formatting,
or filesystem enumeration order appears in the canonical body.

For the same renderer ID/version and authoritative inputs, JSON, Markdown, and
HTML canonical bytes must rebuild byte-for-byte identically using UTF-8 and LF
line endings. A display-only `rendered_at` value, when needed, lives outside the
canonical artifact and is excluded from its digest. A changed renderer or schema
version creates a new projection identity rather than silently changing a prior
report.

A Find projection exposes a source only as `source_label` with a normalized
HTTPS host and an optional bounded title. It may include a private-local
`display_url` only when it is the policy-validated canonical HTTPS origin/path
with query/fragment/credentials stripped; it is never fetched during rendering.
The raw provider URL remains a private evidence artifact. Committed evidence
contains neither `display_url` nor `source_label`, only the permitted sanitized
IDs/digests/counts from G45.2.

## Boundary

All projections are derived from authoritative journal/workpad/Plan/Run
artifacts. Reading or rebuilding them does not allocate a Run, refresh a
source, invoke a provider, re-rank a lead, infer application status, write a
target repository, or modify durable evidence.

HTML is a static local artifact. G47 does not add an HTTP service, background
worker, scheduler, retry loop, current-state database, or a competing approval,
consent, or lifecycle surface.

## Acceptance evidence

Tests and UAT prove byte-identical canonical rebuilds from a completed
foreground Run, distinguish v1/v2 and selected graphs, preserve raw-private
artifact redaction, disclose research/read failure honestly, and link an
explicit lead-input binding without claiming an automatic journey. They cover
renderer-version change, ordering mutation, timestamp exclusion, URL-label
normalization/redaction, and missing optional HTML. CLI/JSON/Markdown/HTML
projections remain read-only and tolerate missing optional HTML without changing
authoritative Run state.

## v0.1.8 handoff

v0.1.8 may add graph-specific schedules and an append-only application
pipeline. Those features consume G47 projections/evidence but require their
own activation, recovery, deduplication, schedule-consent, and state-transition
contracts. G47 itself never starts unattended work.
