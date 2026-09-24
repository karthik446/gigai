# GigAI Changelog

This is the external, capability-focused history of GigAI. It describes what
an operator can do, not how the implementation works. The internal technical
history is kept in the maintainers' local notes, outside this repo.

Goal labels are milestone references, not package-version numbers. Goal order,
phase order, and release order are deliberately different; release notes must
not be inferred from a Goal number.

## Unreleased capability milestones

Backfill from the accepted Goal completion audits is intentionally tracked in
the internal changelog first. Entries added here must describe only a verified,
operator-visible capability and must link to the relevant release or evidence.

### Added

<!--
External entry shape:

#### GNN — Capability name

- What an operator can now do.
- Important user-visible boundary or limitation.

Do not include commit IDs, schema field names, test counts, or implementation
mechanics here. Those belong in the internal changelog.
-->

## Released versions

### 0.1.8.1

- Assess now sends the model the real posting text and a real assessment
  prompt (with the output schema and examples) instead of the title alone.
- Assess tolerates sloppy model output (normalizes odd field shapes) and
  isolates a bad answer to that one posting instead of failing the whole
  run; when a posting can't be assessed, the recorded cause explains why.
- Raw Exa and applicant-tracking-board responses are now stored per run for
  debugging and as test fixtures.
- Acquire applies an explicit country filter and prefers a job board's own
  posting over an Exa search result for the same job.
- Adds a visa-sponsorship filter to find-jobs.
- Adds search and filters to the Scout UI.
- Fixes the release pipeline: the GitHub Release now publishes right after
  PyPI, and PyPI/TestPyPI clean-install checks run as non-blocking
  post-publish checks with a bounded wait for the index instead of racing
  it.

### 0.1.8

- Adds Scout's `find-jobs` workflow, GigAI's first shipped Gig: acquire public
  postings from Exa search and the Greenhouse/Lever/Ashby applicant-tracking
  boards through an auto-managed watchlist; assess each posting with a
  requirements-by-resume matrix that surfaces suggestions and open questions,
  defaulting to a local model with explicit hosted-model targets available;
  and present results through a localhost API and Vite UI that asks for
  explicit consent before any network call or hosted-model use.
- Moves the Scout package to `gigai.scout` so it imports and packages as a
  self-contained Gig built on GigAI core. Core still imports Scout in places;
  removing those so core never imports a Gig is planned for v0.1.9.
- Reorganizes the test suite into behavior-grouped directories (S11) with a
  `make test` runner that separates source, behavior, and wheel-resource
  suites.
- Fixes release CI's setup verifier and workflow model-target wiring, and adds
  an interpreter safety guard to the wheel-resource test lane.

### 0.1.7

- Adds the bundled Scout authoring source, local record workflow, bounded public
  acquisition import, and rebuildable local report surface.
- Adds resumable interview and private-transfer preparation plus a local runtime
  comparison workflow with explicit synthetic/offline boundaries.

### 0.1.5

- Adds a browser-first local setup flow for GigAI's private workspace,
  workpads, model choices, and machine-wide role defaults.
- Adds adaptive Gig-definition interviews that turn an operator's intent and
  selected local context into a reviewable proposal before approval.
- Adds explicit proposal feedback, revision, approval, rejection, Run
  inspection, and recurring/comparison command flows around local Gig state.
- Keeps the workpad, proposal history, approved versions, and review evidence
  under the operator's selected local home.

### 0.1.4

- Adds the model-facilitated Gig builder for UAT: GigAI can guide an operator
  through a Gig definition, ask bounded adaptive follow-up questions, build a
  reviewable proposal, and require explicit approval before sealing it.
- This release is an alpha UAT candidate; configured live model families and
  real operator workflows remain subject to the G24/G26 UAT gate.

### 0.1.3

Release-specific capability notes will be reconciled from the G12 release
evidence and the verified capability inventory.

## Deferred and not advertised

This section records capability families that research or implementation
documents explicitly do not advertise as shipped. It prevents a feasibility
spike or roadmap item from becoming an external support claim by implication.
