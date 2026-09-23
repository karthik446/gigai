# Handoff — 2026-09-22 — Gig-composition discussion and S12/S13/S15 spikes

**For:** next Claude CLI session on this branch (`karthik446/gigai-v0.1.8`).
**From:** session ending 2026-09-22, working in this same worktree.
**Read this first**, then follow the pointers below rather than
re-deriving anything — the underlying spike/evidence docs are the source
of truth; this file is a map to them plus what's still open.

## What happened this session

The operator opened a discussion about what a "Gig" is (goal graphs, tools,
goal units) and asked for it recorded, then requested four spikes from that
discussion. All four spike tickets exist now, three have completed research
with one round of operator-caught corrections each, and one (S12) has its
first task closed with the remaining three tasks still open.

Read the full discussion first if you want the product framing:
[1.8-chat-09-21-26.md](1.8-chat-09-21-26.md).

## Spike status (all in `docs/development/v0.1.8/spikes/`)

| Spike | Status | What it found |
| --- | --- | --- |
| **S13** — existing job-discovery solutions | **Done.** Research recorded, corrected twice after review. | Surveyed 8 candidates. Two are genuinely installable libraries missed in the first pass: `ats-scrapers` (its live `BaseScraper.afetch()`/`fetch()` classes return a typed `list[Job]` and cover Greenhouse/Lever/Ashby — kept distinct from its separate hosted `search()` DataFrame interface, which queries a pre-aggregated snapshot, not live per-employer data) and `JobSpy` (LinkedIn/Indeed-class boards, not ATS). Neither installed or run — suitability unverified. `job-finder`'s ordered dedup pass sequence and its `staleness.py` apply-link probe (404/410/closed-text = expired; 403/429/5xx/timeout = inconclusive, stays live) are concrete, adoptable techniques. Full detail + integration sketch: [evidence/S13-existing-job-discovery-solutions-research.md](spikes/evidence/S13-existing-job-discovery-solutions-research.md). |
| **S15** — goal/tool-unit composition research | **Done.** Research recorded, corrected after review. | Traced `scout_materialization.py:226`'s `_compiled_snapshot` directly: **Scout's bundled compiler** (not a codebase-wide claim) emits one single-goal, zero-edge graph per selector, `tools` hard-coded to `[]`, literal `executor` inlined every time — not composition from a reusable catalog. `graph_set.py` only validates an already-built document. Surveyed LangGraph (has both a real tool object via `ToolNode`/`BaseTool` and an independent task unit via the Functional API's `@task`), Temporal (`Activity` — reusable/idempotent, but that doesn't establish versioning/interchangeability), Anthropic's tool-use API (thinnest tool contract: name/description/input_schema), and CrewAI (`Task` — closest precedent for a goal unit definable independent of graph position). No superiority ranking between GigAI's schema and any framework is claimed. Sketch-level composition proposal and S14 handoff note included. Full detail: [evidence/S15-goal-and-tool-unit-composition-research.md](spikes/evidence/S15-goal-and-tool-unit-composition-research.md). |
| **S12** — auditable graph traversal | **Task 1 closed 2026-09-22.** Tasks 2–4 open. | Task 1 (map `find-jobs` onto existing graph/execution capabilities), built on S15, is done: [evidence/S12-scout-graph-mapping.md](spikes/evidence/S12-scout-graph-mapping.md). Key finding: `run.py:3438`'s `_validate_scheduler_policy` rejects recovery edges, non-automatic dependency edges, parallel goals, and operator-gated goals — schema support and runtime support are not the same thing. But plain sequential multi-goal execution is not an open question: existing test coverage (`tests/behaviors/runtime_run_authority/test_g14_scheduler.py`, read from source, not executed this session) asserts a 2-goal automatic-dependency-edge graph running end to end via `launch_run`. The narrower open question is whether Scout's specific proposed stages (discover → extract requirements → assess → present) and a custom typed decision outcome (vs. the hard-coded `COMPLETE` observed in the traced execution path) are supported — **this does not block scoping tasks 2–4**, it's just unverified. See "Next tasks" below. |
| **S14** — schema inventory and consolidation audit | **Not started.** No dependency, no gate. | Ticket only: [S14-schema-inventory-and-consolidation-audit.md](spikes/S14-schema-inventory-and-consolidation-audit.md). `src/gigai/schemas/` has 85 files (82 `.schema.json`), several concepts with multiple packaged versions (`model-invocation` v1/v2/v3, `external-recording-*` ×2, etc.) — ticket requires evidence-based classification (accepted-by-current-validator / legacy-read-only / no-evident-reader) before any redundancy claim, no assumptions. |

## A note on how these were corrected (read before trusting any claim in them)

Every one of S13/S15/S12's evidence docs was reviewed by the operator and
had genuine errors caught and fixed — this is normal, expected, and
recorded transparently in each doc's own "Revision notes" section at the
bottom. Read those sections; they tell you exactly what changed and why, so
you don't have to guess which parts of the document to trust more. In
order of correction:

- **S13** (2 rounds): missed two installable libraries in the first pass;
  overclaimed embedding-based dedup as "real and working" without
  verification; conflated `ats-scrapers`'s two distinct interfaces
  (live scraper classes vs. hosted dataset `search()`) in the second round.
- **S15** (1 round): wrongly claimed LangGraph has no tool/task abstraction
  (it has `ToolNode` and the Functional API's `@task`); made unsupported
  "GigAI's schema is richer than all four frameworks" claims; scoped a
  Scout-compiler-specific finding as if it were codebase-wide; overclaimed
  Temporal's retry/idempotency discipline as establishing tool
  interchangeability (it addresses execution reliability only).
- **S12** (2 rounds): conflated schema support with runtime execution
  support (the scheduler actually rejects several schema-valid shapes);
  conflated Scout's six independent selectors with the proposed multi-stage
  pipeline; asserted proposed stages were "internal to" the current single
  node's execution without having traced that execution; in fixing the
  schema/runtime conflation, overcorrected into treating "can any
  multi-node graph run" as fully open when existing test coverage already
  answers that; and used "already-passing test" language for a test that
  was only read from source, not executed this session — corrected to
  "existing test coverage."

**Lesson for whoever picks this up:** when citing runtime/test behavior,
say what was actually done (read from source vs. executed) — don't imply a
test run happened when only the source was read.

## Next tasks (in rough priority order)

1. **S12 tasks 2–4** (documentation only unless the operator explicitly
   authorizes more):
   - **Task 2**: Fully design where typed, evidence-backed decisions select
     the next graph edge. Task 1's mapping already sketched this using the
     Jev/TypeSafe research and the schema's existing `outcomes`/
     `on_outcomes` fields — see
     [evidence/S12-scout-graph-mapping.md](spikes/evidence/S12-scout-graph-mapping.md)'s
     "Where typed, evidence-backed decisions could select an edge" section.
     Needs firming up, still contingent on verifying a goal's execution can
     actually emit a custom `outcome` (currently only `COMPLETE`/`FAILED`
     observed in `run.py`).
   - **Task 3**: The three-worker Orca acknowledgment check. **Do not run
     this without an explicit, separate operator go-ahead** — S12's own
     ticket gates it, and this handoff does not constitute that
     authorization.
   - **Task 4**: Record concrete proposed system-behavior changes with
     evidence, per S12's acceptance criteria. Depends on tasks 2–3 landing
     first.
2. **S14** — independent of the above, can be picked up any time. Follow
   its own ticket's evidence-based classification requirement strictly
   (no "redundant"/"safe to retire" claims without a cited reader/writer
   trail — this was a specific correction applied to S13/S15 too, so
   holds the same bar here from the start).
3. If S12/S14 raise a genuine runtime question (e.g. "can a goal emit a
   custom outcome"), that would need actual code investigation (likely
   more of `run.py`'s execution paths, e.g. `_execute_deterministic` and
   whatever calls it) — not another documentation-only pass assuming the
   answer either way.

## Conventions to keep following

- **Ticket format**: short Jira-style ticket (problem, intended behavior,
  tasks, acceptance criteria, evidence) at the top of each spike file;
  conversation notes and detail below. See any `S##-*.md` file in
  `spikes/` for the pattern.
- **Evidence docs** live in `spikes/evidence/S##-<topic>.md`, linked from
  the parent ticket's "Current evidence pointer" line and from the spikes
  [README.md](spikes/README.md) index entry — both need updating whenever
  a spike's evidence changes.
- **Relative links from `spikes/evidence/*.md`**: this directory is 5
  levels below the repo root counting the file itself
  (`docs/development/v0.1.8/spikes/evidence/`), so links to `src/gigai/...`
  need `../../../../../src/gigai/...` (5×`../`), links to sibling ticket
  files in `spikes/` need `../S##-....md` (1×`../`), and links to
  `docs/development/v0.1.8/*.md` need `../../` (2×`../`). Getting this
  wrong was a repeated error this session — verify with a shell `[ -f ... ]`
  check before trusting a link, not just by eyeballing dot-counts.
- **Research-only spikes never authorize implementation.** Every ticket in
  this set says so explicitly; don't let a clean finding read as permission
  to build.
- **rtk / CLAUDE.md**: this environment has an `rtk` wrapper hook that
  rewrites some shell commands; if a `find`/`grep` with compound flags
  fails oddly, use `rtk proxy <cmd>` to bypass filtering (used throughout
  this session for `find -not -path` style commands).

## Files touched this session

- `docs/development/v0.1.8/1.8-chat-09-21-26.md` (appended discussion)
- `docs/development/v0.1.8/spikes/S13-existing-job-discovery-solutions-research.md` (new)
- `docs/development/v0.1.8/spikes/S14-schema-inventory-and-consolidation-audit.md` (new)
- `docs/development/v0.1.8/spikes/S15-goal-and-tool-unit-composition-research.md` (new)
- `docs/development/v0.1.8/spikes/S12-gig-graph-traversal-and-auditable-execution.md` (updated — dependency note, task-1 closure)
- `docs/development/v0.1.8/spikes/evidence/S13-existing-job-discovery-solutions-research.md` (new)
- `docs/development/v0.1.8/spikes/evidence/S15-goal-and-tool-unit-composition-research.md` (new)
- `docs/development/v0.1.8/spikes/evidence/S12-scout-graph-mapping.md` (new)
- `docs/development/v0.1.8/spikes/README.md` (updated — S12/S13/S14/S15 index entries)
- This file (new)

Nothing else in the working tree was touched by this session; the
pre-existing `M`/`D`/`??` git status from before this session started is
unrelated to this work.

## Addendum — follow-up session, same day

- **S12 task 2 documented:** [evidence/S12-decision-edge-design.md](spikes/evidence/S12-decision-edge-design.md).
  It resolves the "can a goal emit a custom outcome" question from code:
  contract and router support it (`validators.py:667-684`, `run.py`
  `_ready_goals`/`_blocked_by_terminal_outcome`; the
  `test_g14_scheduler.py` routing tests were **executed** this time: 10
  passed). But no producer emits anything besides `COMPLETE`/`FAILED`. A
  branch that isn't taken is recorded `blocked`, which makes the run
  `blocked`, and OR-joins are unsupported. Candidate changes D1–D6 are
  recorded for task 4. The S12 ticket and spikes README are updated.
- Task 3 (Orca) is still gated; task 4 still waits on task 3.
- **S14 audit documented:** [evidence/S14-schema-inventory-audit.md](spikes/evidence/S14-schema-inventory-audit.md).
  All 82 schemas are classified with citations; 76 are validated against
  their file by current code. None of the 12 multi-version families is legacy: writers
  still emit v1 by payload shape, and the external-recording CLI defaults
  to `--protocol-version 1`. Seven are flagged for review. None lacks a
  reader or writer, but three have hand-rolled code contracts that disagree
  with the packaged file (corrected after operator review). The privacy guard (`package_privacy.py:39-51`)
  lists only v1 external-recording schemas; this was found by reading and
  needs an operator decision. The S14 ticket and spikes README are updated.
- **Operator review round (same day):**
  - S14's count was corrected to 76.
  - Four "tests-only/unreferenced" schemas were traced to hand-rolled code
    contracts. `handoff-frontmatter` output fails its schema when
    `artifact_refs` is present (executed).
  - S12 D1–D6 are relabeled as alternatives, shape-dependent or optional,
    not all necessary.
  - Abstention no longer implies operator gating.
  - The "only body text changes" claim is withdrawn.
- **S12 task 4 documented:** [evidence/S12-proposed-system-behavior-changes.md](spikes/evidence/S12-proposed-system-behavior-changes.md).
  Section C (notifications and resume) intentionally has no conclusions.
- **S14 follow-up (F6):** one test file was run into a scratch basetemp
  (3 passed) and scripts were run over its workpads:
  - 0 of 28 journal handoffs conform to `handoff-frontmatter`.
  - The privacy guard misses renamed v2 records (confirmed at the guard; no
    leak demonstrated).
  - `occurrence declare` refuses v2 Gigs with a misleading error (read).
  - Tailor-selection output conforms (one shape).
- **Follow-up tickets recorded** in `docs/development/followups/`:
  CONTRACT-01 (handoff contract), PRIVACY-01 (v2 guard gap), OCCURRENCE-01
  (unsupported-version error) and CONTRACT-02 (tailor write validation).
  None is authorized, and their evidence isn't yet operator-reviewed.
- **S12 task 3 run** (operator go-ahead 2026-09-22):
  [evidence/S12-orca-ack-check.md](spikes/evidence/S12-orca-ack-check.md),
  run `run_ef1121d3c864`.
  - 3 ACKs and 3 DONEs, none missing or duplicated, and no polling.
  - Not exercised: blocking resume (the workers finished before the waits
    began), the Orca-unreachable path, and token usage.
  - Task 4's section C is filled in (N1–N5).
  - All three workers were released. The inbox is fully acked, and 0
    terminals remain reclaimable.
- **N3 blocking-resume check run** (operator-authorized):
  [evidence/S12-orca-blocking-resume-check.md](spikes/evidence/S12-orca-blocking-resume-check.md),
  run `run_c7a2891bce10`.
  - A blocked wait of 76.4 s returned within the second of `worker_done`.
  - The coordinator resumed 8.2 s later.
  - The ACK heartbeat triggered one unplanned coordinator turn through
    Orca's terminal nudge (N6).
  - Sanitized receipt JSONs now exist for both Orca checks.
  - N2 stays a proposal.
- **Second review round:**
  - D3 now carries D1's guarantees: validate the label, reject undeclared
    labels, preserve producer and evidence identity.
  - PRIVACY-01's acceptance covers all five v2 families plus v1 and a
    non-private negative case.
  - S14's executed checks are reproducible from
    `spikes/evidence/S14-repro/` (preserved scripts, exact commands,
    sanitized `results.txt`, re-run and matched).
  - S14 F2's stale "not traced" text is replaced with its F6 follow-up.
- **Still open:** operator review of S12 tasks 2–4 (including N1–N6), S14
  and the follow-up tickets. The Orca-unreachable path (ORCA-01) is still
  untested. The operator deferred it until Luna's timing
  run finishes. When it runs, fill in task 4's section C from its results.
- Files touched in this follow-up: the two new evidence docs above, the
  S12 and S14 tickets, `spikes/README.md` and this file.
