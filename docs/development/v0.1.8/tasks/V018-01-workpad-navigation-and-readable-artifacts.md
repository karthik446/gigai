# V018-01 — Understandable workpads and readable artifacts

**Release:** v0.1.8  
**Status:** Requested; design and implementation not started. The readable
navigation/detail slice maps into the first v0.1.8 local tracking UI; this task
does not make storage migration a prerequisite.  
**Requested:** 2026-09-08, after inspecting the real G44 review artifacts.  
**Sequence:** After the S11 foundation and shared-interface freeze, alongside
the first vertical slice after v0.1.7 Scout; coordinate with [accessibility
Spike 1](../spikes/README.md#spike-1--user-accessibility-onboarding-and-interaction).

## User problem

The current user-facing experience exposes paths dominated by opaque names
such as `projects/project_c5c…/gigs/gig_84d…/runs/run_…/model-invocations/inv_…`.
A user cannot tell which project, Gig, task, or reviewer they are opening.
Reaching the answer requires understanding internal storage and manually
following multiple unrelated identifiers.

The review response is also JSON containing another JSON document as an
escaped `output_text` string, for example:

```json
{"output_text":"{\"findings\":[{\"criterion_id\":\"criterion_requirements\"}]}"}
```

Indenting the outer JSON does not solve this: the actual answer remains an
escaped string. A successful agent workflow must leave results a person can
find and read without decoding envelopes or opening several evidence files.

## Required outcome

Make the project/Gig/workpad model understandable and provide readable outputs
by default. This is a storage/navigation cleanup as well as an artifact-formatting
task, not merely shorter labels over an unexplained hierarchy.

## First-slice mapping

For the v0.1.8 Scout slice, this task owns the readable navigation and detail
concerns used by the essential local tracking surface:

- a basic job table/list with filters and immediate new/pending/failed
  visibility;
- a detail view linking the source posting, selected resume revision,
  requirements/evidence, gaps, questions and actions; and
- readable display of separate assessment state and explicit user tracking
  state (`shortlisted`, `applied`, `interviewing`, `rejected`, `archived`).

Status changes must call the existing validated operations. Finalizing or
tailoring a resume never creates an application event, and the surface remains
usable for browsing and tracking updates with inference disabled. Reuse the
existing local storage/projection and simplest suitable interface. React,
FastAPI, a new frontend framework, hosting, multi-tenant expansion and a
canonical storage migration are not part of this task's first-slice boundary.
The [v0.1.8 roadmap](../roadmaps/v0.1.8-scout-search-first-roadmap.md) owns the
cross-packet integration gate.

### 1. Names and a simpler navigation model

- Explain and justify the distinction between a project, its bound repository
  or directory, a Gig, its workpad, its graphs, and its Runs. Identify redundant
  layers and decide whether to simplify, hide, or retain each one.
- Show meaningful project and Gig names, graph/task names, dates, reviewer
  names, and outcome summaries in normal CLI output, listings, reports, links,
  and agent handoffs. Full IDs remain inspectable technical identifiers, not
  the primary user-facing navigation.
- Provide an obvious entry point from the current project to its Gigs, from a
  Gig to its recent work, and from a Run to inputs, outputs, reviewers, and
  decisions. Default artifact links should open the readable result.
- Design human-readable directory names, aliases, or an index as appropriate;
  explicitly decide canonical storage versus display paths. Naming collisions,
  renamed projects/Gigs, moves, duplicate names, and multiple instances must
  remain unambiguous without routinely demanding a full UUID from the user.
- Preserve portability and user ownership. Do not introduce a hosted service
  or require an HTTP API to navigate local work.

An illustrative experience is `GigAI repository → G44 contract review → latest
review → Claude / Luna / final decisions`, not four UUID lookups. This example
does not freeze a particular directory layout or command syntax.

### 2. Readable artifacts, not escaped payloads

- Provide a clearly named human-readable result for each reviewer and an
  overall review summary, using Markdown and/or the simple local HTML surface.
  Include the reviewed subject, reviewer, findings, severity, supporting source
  locations, verification, adjudication, and final disposition with backlinks.
- For a JSON model response, expose the validated decoded payload as properly
  indented JSON in a clearly distinguished derived view. Never require users
  to unescape `output_text` to read findings. Plain-text outputs remain readable
  text; malformed or ambiguous JSON is shown honestly, not silently repaired.
- Keep requests, responses, and invocation metadata discoverable under advanced
  evidence/details. Label what was sent, what was returned, and what GigAI
  validated; do not conflate provider output with the system's final decision.
- A review with three emitted findings and three rejections should read as
  **three raised, three rejected, none accepted**, not simply “three findings”
  or “clean.” Show no-findings and failed/blocked cases just as clearly.
- Preserve original journaled provider bytes, digests, and sealed snapshots.
  Readable JSON/Markdown/HTML are derived artifacts with source links; formatting
  historical evidence must not invalidate its signatures or change its meaning.

### 3. Safe cleanup and compatibility

- Inventory existing storage and callers before choosing a migration: CLI
  selectors, binding files, journal references, imports/exports, reports,
  user-customized Gig tools/UI, and docs links.
- Define a previewable, recoverable migration if canonical paths change.
  Preserve stable identity and old evidence references; interrupted migration,
  collision, rollback/recovery, and mixed old/new layouts need explicit handling.
- Do not delete historical Runs or merge distinct projects/Gigs as cosmetic
  cleanup. Keep private material private and reject unsafe traversal/symlink
  targets and unsafe HTML/link content in generated navigation.
- Clearly separate generated views from user-editable source so regeneration
  cannot overwrite customization. Existing Scout storage/authority contracts
  remain in force until a separately reviewed replacement is accepted.

## Acceptance checks

1. From a normal project entry point, a user finds the named Gig, latest Run,
   Claude/Luna responses, and final decisions without copying opaque IDs or
   consulting source code.
2. The real G44 correction/re-review scenario is readable end to end: Claude's
   three concerns, Luna's no-findings response, Terra's checks, and Sol's three
   rejections are distinguishable and linked. Use sanitized fixtures for
   automated tests; private artifacts are not exported implicitly.
3. Structured output is available as readable indented JSON and a readable
   report; the normal experience contains no escaped JSON blob masquerading
   as the report. Unicode, multiline text, malformed output, and empty findings
   are covered.
4. Original response and sealed-input bytes/digests are unchanged by rendering
   or navigation cleanup. Derived views identify their exact source revision.
5. Duplicate names, renames, moves, fresh initialization, existing workpads,
   interrupted migration, and portable copies preserve identity and history.
6. Generated views preserve user customization and pass link/path safety checks.

## Deliverables

A reviewed storage/navigation design and caller map; migration plan where
needed; implementation; automated compatibility/rendering tests; and a short
human walkthrough using the review scenario above. Connect the work to the
onboarding spike without waiting for an elaborate TUI or frontend framework.

This task is recorded only. It does not rename, migrate, delete, reformat, or
rewrite any current private workpad or alter v0.1.7 Scout delivery.
