# w0c roadmap worker handoff

state: done

output_paths:
- docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md
- docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md
- docs/development/v0.1.8/README.md
- .orchestrator/workers/w0c-roadmap.md

question_for_operator: none.

summary:
- Created the 149-line active functional roadmap with D1-D9, the Revision-1-aware four-packet split, I-0 shared contracts first, serialized I-2/run.py ownership, A-1 through A-6 acquisition packets, disjoint owned-file rows, fixture-first parallel waves, Terra/coordinator gates, exact focused commands, retained/dropped gates, risks, non-claims, code anchors, and a single 2026-09-22 change-log entry.
- Added only the requested obsolete banner under the old roadmap H1; the current old-file diff below still includes pre-existing uncommitted work, and the worker's only added hunk is `@@ -2,0 +3,2 @@`.
- Re-pointed the README Release roadmap section and amended the no-new-frontend line to record the 2026-09-22 local Vite override; no Amendment-02 file was edited.

ownership_checks:
- New roadmap: 149 lines, under the 250-line limit.
- DAG owned-file audit: no overlap found; every path from the current Amendment-02 ownership cells and the provisional Revision-1 split occurs exactly once across I-0/I-1/I-2, A-1/A-2/A-3/A-4/A-5/A-6, B-1/B-2, and C-1/C-2/C-3/C-4. Coordinator fixtures are explicitly not packet-owned product files; provisional paths remain subject to the required Amendment-02 Revision-1 reconciliation.
- README's pre-existing working-tree hunks remain untouched; this worker's README additions are only the Release roadmap re-point and the 2026-09-22 frontend-override line.
- `rtk git diff --check` for the two tracked owned files exited 0.
- No tests, full suite, wheel install, UI build, provider/model call, Exa/HiringCafe request, live Run, or network read was executed.

READ:
- AGENTS.md, /Users/kar/.codex/RTK.md, and .claude/skills/gigai-orchestrator/SKILL.md.
- Existing old roadmap, v0.1.8 README, spikes/README.md, all four Phase-2 ticket files plus ticket README, P2-FREEZE-04 Amendment 01, the on-disk Amendment 02, the P3 integration trace, and the coordinator's Revision-1 review/spec (`.orchestrator/reviews/w0-amendment-02.md`, `.orchestrator/workers/spec-w0r-amendment-02-rev.txt`).
- Source anchors cited in the new roadmap: `src/gigai/run.py:2187-2212,280-302,3438-3561`; `src/gigai/scout_acquisition_records.py:1-7,273-355,467-557`; `src/gigai/adapters/factory.py:61-121`; `src/gigai/scout_proposal_execution.py:137-155`; `src/gigai/model_execution.py:74-85,147-175,194-205,241-246`; proposal/record/selection/document/private-record/input/report/projection/workpad/loopback files at the exact ranges listed in the roadmap; and `pyproject.toml:17-31`.
- Relevant memory registry entries for this checkout were searched before repository exploration.

EXECUTED:
- Read-only `rtk rg`, `nl -ba`, `sed`, `wc`, `git status`, `git diff`, path/ownership-count checks, and `rtk git diff --check`.
- Applied only documentation edits to the three requested docs plus this worker handoff. No source, test, schema, or UI implementation was made.
- Initial Orca heartbeat/check attempts returned `runtime_unavailable`; I restarted the runtime with `rtk proxy orca open --json`, then delivered heartbeat `msg_f8d410517143`. No worker_done message has been sent yet; this handoff is prepared for the required final lifecycle message.

git diff -U0 -- docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md:
```diff
diff --git a/docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md b/docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md
index 886baa4..51fb24f 100644
--- a/docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md
+++ b/docs/development/v0.1.8/roadmaps/v0.1.8-scout-search-first-roadmap.md
@@ -2,0 +3,2 @@
+**Status: Obsolete (2026-09-22).** Superseded by [v0.1.8 find-jobs functional roadmap](v0.1.8-find-jobs-functional-roadmap.md). Kept for history; do not plan from it.
+
@@ -4,11 +6,14 @@
-**Status:** Draft, pending refinement. Activates only after v0.1.7 ships;
-recording this roadmap does not itself start implementation, a Run, or any
-schedule/model/provider activation.
-**Depends on:** v0.1.7 release completing (operator: "almost ready to be
-released" as of 2026-09-21).
-**Target:** Friday, September 25, 2026, as the bounded-release target date.
-Saturday–Sunday, September 26–27, held as contingency, not planned scope.
-This is the implementation-start-to-ship calendar, distinct from the planning
-date (2026-09-21, today) and from whenever v0.1.7 actually finishes — see
-Control 2 below for what happens if v0.1.7's completion date and this
-roadmap's assumed start drift apart.
+**Status:** Planning baseline for later v0.1.8 phases; the bounded S11 Phase 1
+groundwork packet is implemented in this checkout under an explicit operator
+authorization, and its complete test migration/runner-performance preparation
+is now in progress. The roadmap does not claim v0.1.7 shipped or activate a
+Run, schedule, model or provider.
+**Depends on:** v0.1.7 release completing. No v0.1.7 ship date is recorded or
+claimed here; later v0.1.8 implementation dates are set only after that
+release is verified. The operator-authorized S11 groundwork exception is
+recorded below and does not activate later phases.
+**Calendar:** No fixed ship date or arbitrary five-day promise. After the
+actual v0.1.7 ship date is recorded, the coordinator reassesses the phase
+calendar against the S11 measured baseline, shared-interface freeze and the
+feasibility proof. The former September 25 target is historical and
+superseded, not a current ETA.
@@ -22,0 +28,4 @@ already recorded:
+- [S11 — behavior-based test organization](../spikes/S11-behavior-based-test-organization.md)
+  — **first release foundation.** Inventory and measure the current tests and
+  verifiers, establish deterministic and separately selected evidence lanes,
+  and perform only a bounded initial migration before feature work grows.
@@ -24,5 +33,4 @@ already recorded:
-  — **partially required for this release.** A bounded Qwen-vs-Luna pilot,
-  reusing S08's frozen-case/gold-adjudication methodology at small scale, is
-  required by Day 4 to produce real assessment-quality evidence for the
-  shipped model. The full five-setup S08 comparison (GPT, Claude Sonnet,
-  and the rest) is deferred (see Scope, below).
+  — research recorded. A small Qwen-vs-Luna pilot may be a release gate only
+  after synthetic candidate inputs, frozen cases and human gold adjudication
+  are prepared; the research record does not prove runtime/model quality and
+  the full five-setup comparison remains deferred.
@@ -30,4 +38,6 @@ already recorded:
-  — **directly informs this release's acquisition design.** ATS-feed
-  watchlist polling (Greenhouse, Ashby) as the primary discovery path and
-  one general-search backstop come from this spike's findings, not a fresh
-  decision made here.
+  — research recorded and directly informs acquisition. ATS-feed watchlists
+  and one general-search backstop remain design inputs, but freshness/indexing
+  latency and durable storage rights are not established acceptance evidence.
+- [S10 — Ollama invocation and harness onboarding](../spikes/S10-ollama-invocation-and-harness-onboarding.md)
+  — research recorded. Its caller/local invocation gaps are audited during
+  groundwork; adapter or synthetic evidence is not installed/live caller proof.
@@ -35,2 +45 @@ already recorded:
-  — tracked as a parallel, non-blocking research thread (see "What stays off
-  the critical path" below), not a dependency of this release.
+  — a parallel, non-blocking research thread, not a dependency of this release.
@@ -44,2 +53,4 @@ already recorded:
-> assesses them locally against my selected resume and preferences, and shows
-> actionable proposals in a readable dashboard.
+> shows pending/failed work independently of assessment, assesses locally
+> against my selected resume and preferences, and lets me browse the result and
+> explicitly track shortlist/application/interview outcomes in a readable local
+> surface.
@@ -49 +60,5 @@ true end-to-end, through the normally installed package, is not done —
-regardless of how many individual tasks are checked off.
+regardless of how many individual tasks are checked off. Assessment state and
+user tracking state are separate: assessment can be new, pending, running,
+succeeded, failed or skipped; tracking is an explicit user update among
+shortlisted, applied, interviewing, rejected or archived (plus untracked).
+Tailoring or finalizing a resume never implies application.
@@ -58 +73,2 @@ regardless of how many individual tasks are checked off.
-  connector's implementation if Days 2–3 run short (see Open items). Note
+  connector's implementation if the parallel-packet feasibility gate finds a
+  real conflict (see Open items). Note
@@ -73,2 +89,8 @@ regardless of how many individual tasks are checked off.
-- A dashboard showing new/pending/assessed jobs, source links, and existing
-  application history; usable with inference disabled.
+- An essential, simple local tracking UI for the operator and a few friends in
+  the first vertical slice: a basic job table with filters, immediate
+  new/pending/failed visibility, a detail view with source posting, selected
+  resume, evidence/gaps/actions, and explicit shortlist/applied/interviewing/
+  rejected/archived updates through existing validated operations. It must be
+  usable for browsing and status changes with inference disabled; it is not an
+  end-stage report and does not require React, FastAPI, a new framework,
+  hosting or multi-tenant expansion.
@@ -92,0 +115,2 @@ regardless of how many individual tasks are checked off.
+- A framework or hosting decision: reuse the existing local storage,
+  projections and validated operations with the simplest suitable interface.
@@ -95 +119,2 @@ regardless of how many individual tasks are checked off.
-  required for this release (see Day 4); broader comparison is deferred.
+  required for this release (see the installed integration and release
+  decision phase); broader comparison is deferred.
@@ -97,2 +122,5 @@ regardless of how many individual tasks are checked off.
-  schema redesign (S5), canonical-directory migration, a new frontend
-  framework, additional Gigs, or generalized autonomous orchestration.
+  schema redesign (S5), canonical-directory or other storage migration, a new
+  full Gig, additional Gigs, or generalized autonomous orchestration. The
+  one linear Scout graph in the Phase 3 proof (Amendment 01) is in scope. A
+  catalog, generic graph builder, branching or custom outcomes, and an
+  autonomous improvement workflow remain out of scope.
@@ -104,4 +132,6 @@ regardless of how many individual tasks are checked off.
-| S07 (execution modes) | Recorded; research/implementation not started | No — tracked in parallel, not a dependency (see below) |
-| S08 (cross-model eval methodology) | Research complete | **Partial — yes for a bounded Qwen-vs-Luna pilot (Day 4), required.** Full five-setup comparison explicitly deferred |
-| S09 (search/retrieval sourcing) | Research complete, incl. a small live trial | **Yes — already satisfied.** Directly shaped the acquisition design below |
-| S1 (accessibility/onboarding) | Not started | No — dashboard/navigation work here is release-scoped, not this spike's broader onboarding UX research |
+| S07 (execution modes) | Recorded; research/implementation not started | No — parallel, non-blocking research; do not hold the first slice for it |
+| S08 (cross-model eval methodology) | Research recorded with frozen-case, setup-parity and human-gold method | **Partial — pilot preparation and explicit quality disposition are required; research does not prove runtime/model quality.** Full five-setup comparison deferred |
+| S09 (search/retrieval sourcing) | Research recorded, including a small labeled query addendum | Informs acquisition; **does not prove indexing freshness, sustained coverage or durable storage rights** |
+| S10 (Ollama invocation/onboarding) | Research recorded; caller, installed and live boundaries remain open | Early audit input and local-route gate; adapter evidence alone is insufficient |
+| S11 (behavior-based tests) | **Phase 1 groundwork implemented and independently verified; complete migration/runner-performance preparation is now in progress:** 156 test files/1,133 test items and 25 installed verifiers inventoried; bounded acquisition migration and receipts recorded; [focused project-local lifecycle receipt](../evidence/S11-lifecycle-uv.json) is 13/13 passed, while the missing `questionary` result remains the historical reused-environment baseline. The new [runner/CI receipt](../evidence/S11-test-runner-and-ci-performance.md) records the single aggregate `make test` contract, historical CI bottlenecks and bounded selector checks; Luna A's full-suite timing/integration receipt remains pending. Terra task `task_6795b28e61ad` / dispatch `ctx_b6f405f97b53` confirmed the earlier migration/selector review and exact 13-case receipt. | **Foundation packet remains complete for the bounded Phase 1 slice; full migration timing, v0.1.7 verification, interface freeze, scheduled vertical proof and later gates remain required** |
+| S1 (accessibility/onboarding) | Not started | No — readable tracking UI is release-scoped; broader onboarding research remains separate |
@@ -110,3 +140,3 @@ regardless of how many individual tasks are checked off.
-| S4 (Dolt) | Not started | No — off the critical path; v0.1.7 already decided to keep SQLite |
-| S5 (schema redesign) | Not started | No — off the critical path |
-| S6 (local models/Ollama) | Pilot complete; full integration deferred | Partial — RUNTIME-01 already carries the narrow local-execution piece into v0.1.7 |
+| S4 (Dolt) | Not started | No — off the critical path; retain SQLite |
+| S5 (schema redesign) | Not started | No — off the critical path; no storage migration |
+| S6 (local models/Ollama) | Pilot complete; narrow runtime work is a v0.1.7 prerequisite, not claimed shipped here | No new S6 gate; reuse evidence and audit the actual shipped route |
@@ -114,2 +144,5 @@ regardless of how many individual tasks are checked off.
-**No spike blocks this roadmap.** The two spikes relevant to this release's
-design (S08, S09) are both done.
+S11 is the first release foundation, but it is deliberately bounded: its
+inventory and baseline establish the lanes and acceptance receipts needed for
+delivery, not a repo-wide rename. S07 runs in parallel and may inform
+coordination without blocking this roadmap. S08/S09/S10 research records are
+inputs with explicit evidence boundaries, not blanket release acceptance.
@@ -119 +152 @@ design (S08, S09) are both done.
-| When | Deliverable |
+| Phase | Deliverable and exit gate |
@@ -121,4 +154,29 @@ design (S08, S09) are both done.
-| Day 1 | Audit existing code; freeze the minimal shared record/interface contract; demonstrate one posting → saved job → local assessment → visible proposal, driven manually. Prove the scheduled-polling mechanism itself works (interval, missed-run handling, no silently-installed always-running agent — see the direction doc's scheduler caution), not just the acquire→assess→show chain triggered once. Start freshness observations (per S09 Section 9's named experiment: matched ATS-vs-search timing). **Required by end of Day 1, not optional:** the three Day 2–3 work packets are bounded and named (acquisition/scheduling, local assessment, dashboard/readable navigation), each has an assigned owner, agreed shared-interface contract, explicit file ownership to avoid overlap, and stated acceptance evidence; one person is named integration owner responsible for the daily integration in Days 2–3. **End of Day 1 is also the feasibility checkpoint** (Control 1, below) — both must close before Day 2 starts. |
-| Days 2–3 | Parallel implementation across the three work packets fixed on Day 1: acquisition/scheduling (ATS watchlist polling + one search backstop), local assessment (requirements extraction, evidence-backed edits/questions against the selected resume), dashboard/readable navigation (the navigation/readable-artifact slice of [V018-01](../tasks/V018-01-workpad-navigation-and-readable-artifacts.md) maps into this workstream; V018-01's separate storage-migration portion is deferred, not pulled into this release). Integration owner runs daily integration, not a Day-5 merge. |
-| Day 4 | Installed-package integration of the three workstreams (not source-tree-only). A small, bounded Qwen-vs-Luna assessment pilot — reusing S08's frozen-case/gold-adjudication methodology at pilot scale, **not** the full five-setup S08 comparison — to produce actual assessment-quality evidence for the model this release ships with, distinct from "a model was explicitly selected." Job postings may be real postings acquired during Days 1–3 (public data, subject to each source's own access permissions per S09); **candidate-side input (resume content, preferences) must be synthetic, fabricated for this pilot, never the operator's real private resume/preferences** — the pilot sends candidate data to a hosted Luna/Codex setup, and real private assessment inputs must not leave the local machine for that comparison. Both the Qwen and Luna/Codex setups are graded against the same frozen, non-private cases (real posting text + synthetic candidate data), per S08's setup-parity requirement. Restart/error/privacy acceptance checks (Control 3, below). Corrections from whatever Day 4 finds. |
-| Day 5 | Final artifact verification and publication; operator UAT follows, on the normally installed published package (per the existing v0.1.7 release-order convention). |
+| Groundwork — S11 first | **Phase 1 receipt (operator-authorized 2026-09-21):** inventory tests and installed verifiers by behavior and lane; capture the measured collection/setup/time baseline; define deterministic fixtures and separate unit, integration, CLI, installed and live/provider lanes; and perform one bounded public-acquisition migration. Coverage, negative/failure semantics and historical mapping are preserved. The historical missing `questionary` dependency in the reused environment is recorded, while the focused lifecycle now passes all 13 parameterized cases in the project-local locked environment. Terra independently confirmed the migration/selector review and exact 13-case receipt; broad collection, installed and live/provider work remain separately gated, and no mass rename or repo-wide cleanup was introduced. |
+| Audit and interface freeze | Audit the shipped v0.1.7 artifact/source and the relevant S10 caller/local invocation gaps. Freeze existing shared records and validated operations for acquisition/save/pending visibility, assessment/proposal, resume selection and explicit tracking updates, plus lineage/provenance and evaluation acceptance criteria. The freeze names exact source/schema ownership after the audit; it does not introduce a new schema or storage migration. Prepare S08's synthetic candidate inputs, frozen cases and human gold adjudication before any quality claim. |
+| Thin scheduled vertical proof | **Amended 2026-09-22 ([P2-FREEZE-04 Amendment 01](../phase-2/evidence/P2-FREEZE-04-amendment-01-scout-graph-proof.md)):** the proof runs as **one traversal of a real, linear Scout graph**, `acquire → assess → present` with `COMPLETE` edges. It has per-node status, evidence and available usage, and an interrupted run resumes without duplicate acquisition. Luna first traces the node-execution integration; the proof isn't sized before that trace. The integration owner exercises the actual scheduling path with an authorized public source: acquire, durably save and show a posting as new/pending immediately and independently of slow, stopped or failed assessment; then run local assessment and show a readable actionable proposal. Check privacy separation, restart/recovery, duplicate handling and model-off operation in this proof. A deterministic fixture can establish lane behavior, but cannot substitute for the installed scheduled proof. |
+| Parallel packets after freeze | Dispatch the three substantial packets below in parallel once the shared interfaces and ownership are frozen. Each packet groups implementation, behavior tests, docs, corrections and its acceptance receipts; it is not a short isolated agent task. Dependent integration work starts only after the producing packet's completion receipt. |
+| Installed integration and release decision | Integrate through the normally installed package, not source-tree-only. Re-run deterministic checks and explicit CLI/installed/live lanes as authorized; complete the S08 pilot with synthetic candidate inputs and human adjudication; verify privacy, restart/recovery, duplicates, model-off visibility, readable UI status updates and no silent hosted fallback. A model-quality failure receives an explicit disposition: fix/re-evaluate, defer assessment, or reassess release scope with the user. Publish/UAT only after these gates and the preserved v0.1.7 prerequisite are satisfied. |
+
+The planned Phase 2 audit/interface-freeze packet is indexed at
+[phase-2/tickets](../phase-2/tickets/README.md). Its three independent audit
+stories can proceed before the convergence freeze; it does not authorize the
+later implementation packets or change the v0.1.7 prerequisite.
+
+### Substantial implementation packets
+
+| Packet / owner | Scope and file ownership boundary | Frozen interfaces and acceptance evidence |
+| --- | --- | --- |
+| Acquisition and scheduling / acquisition owner | Source connectors and scheduler path around `src/gigai/scout_acquisition_*.py`, `scout_discovery*.py`, `scout_posting_inputs.py` and their behavior/CLI tests. Owns source snapshots, deduplication, restart/missed-run handling and immediate status publication; does not edit assessment or UI projection internals. | Existing acquisition/save/progress records and operation receipts; actual scheduled proof shows new/pending before assessment, visible failures, privacy-safe public query inputs, and no lost/duplicate postings. |
+| Assessment and evaluation / assessment owner | Requirements/proposal/resume-selection behavior around `src/gigai/scout_research*.py`, `scout_proposals*.py`, `scout_proposal_*.py`, `scout_tailor_selection.py`, and the S10 route audit of `src/gigai/adapters/ollama_local.py` plus `adapters/factory.py`; owns the corresponding behavior/CLI/installed tests. Owns S08 case pack, deterministic grader and local invocation acceptance; does not infer tracking state or own source acquisition. | Existing selected-resume, proposal/evidence and local-model adapter/caller interfaces; readable actionable proposal, synthetic frozen-case/human-gold result, explicit quality disposition and refusal of silent hosted fallback. |
+| UI, tracking and readable navigation / UI owner | Local table/filter/detail and readable projection/report surface around `src/gigai/scout_projection.py`, `scout_report*.py`, `application_events.py`, `application_cli.py` and the V018-01 navigation artifacts/tests. Owns presentation and explicit tracking mutations through validated operations; does not create a second source of truth or alter assessment meaning. | Existing projection and application-event operations; model-off browse/update works, assessment and tracking states remain distinct, source/resume/evidence/gaps/actions are linked, and resume finalization never emits an application event. |
+
+The interface-freeze ledger must name the exact existing record/schema owners
+before implementation begins (for example
+`scout-public-import-progress.schema.json`,
+`scout-proposal-discovery-job.schema.json`,
+`scout-proposal-revision.schema.json`,
+`scout-document-selection-v2.schema.json` and
+`application_events.py`/`application-event.schema.json`). These are audit
+targets, not permission to add a contract or migration. The ledger also records
+which public CLI/Run entry points call each operation so the UI cannot create a
+second write path.
@@ -126 +184,12 @@ design (S08, S09) are both done.
-September 26–27 (per the Target above) held for slip, not as planned scope.
+An integration owner owns the cross-packet receipts, daily/phase integration and
+installed-package proof, but does not absorb packet implementation or merge at
+the end. Root coordinates the freeze, dispatch, gates and reconciliation. The
+planned implementation route is Orca GPT `gpt-5.6-luna` (max) for packet work
+and Orca GPT `gpt-5.6-terra` (medium) for independent verification; deterministic
+checks and process receipts establish success, not model self-reporting.
+
+After each dispatch, record one rough ETA and yield to completion, a worker
+question/failure or a user prompt; do not hold an LLM turn open with polling.
+Reuse workers for same-role follow-ups, avoid agent churn and redundant reviews,
+and route independent findings back to the packet implementer. No arbitrary
+time quota decides completion; the evidence gate does.
@@ -130,33 +199,24 @@ September 26–27 (per the Target above) held for slip, not as planned scope.
-1. **End of Day 1 is the feasibility checkpoint.** If the existing
-   foundations cannot support the installed vertical slice (one real
-   posting through to one visible proposal, via the actual scheduling
-   mechanism), identify the exact obstruction immediately — not after three
-   days of separate component work that turns out to rest on a broken
-   foundation.
-2. **September 25 assumes v0.1.7 clears within roughly 24 hours of the
-   2026-09-21 planning date this roadmap was recorded on** — not 24 hours
-   of implementation start, which is circular since implementation start is
-   defined as "after v0.1.7 ships." When v0.1.7 actually ships, record that
-   date and the resulting implementation-start date in this file's change
-   log, then explicitly reassess whether September 25 is still reachable
-   from that real start date (roughly 4 working days out) before treating
-   it as the target. If v0.1.7's actual ship date is materially later than
-   2026-09-22, September 25 must be explicitly moved, not assumed to still
-   hold — a "Day 5" that silently drifts into the following week because
-   Day 1 started late is exactly the failure this control exists to catch.
-   This plan must not silently become a compressed version of itself either,
-   by cutting verification or by quietly dropping one of the three parallel
-   workstreams or the Day 4 pilot/integration checks to protect a date that
-   is no longer realistic.
-3. **Privacy and immediate-visibility are acceptance checks due by Day 4,
-   not scope bullets satisfied by design intent alone.** Two concrete tests
-   must pass before Day 5 verification: (a) a general-search query built
-   during a real acquisition run contains only values from the approved
-   public search-settings record — inspected directly, not inferred from
-   the presence of an allowlist in code — and the private preferences
-   record is confirmed unreachable from that code path; (b) with the local
-   model stopped or made artificially slow, a newly discovered posting
-   still appears in the dashboard in a visible "pending assessment" state
-   within the same acquisition cycle, not hidden until assessment
-   completes. Either check failing is a Day 4 finding to correct, not a
-   known gap to ship with.
+1. **Groundwork is the feasibility checkpoint.** After S11's inventory/baseline
+   and the v0.1.7/S10 audit, prove that the existing foundations can support the
+   installed vertical slice (one posting through the actual scheduler to a
+   visible pending job and then a readable proposal). Identify an exact
+   obstruction before expanding the three packets; do not spend a phase on
+   separate components that rest on a broken foundation.
+2. **The calendar follows evidence, not a fixed promise.** When v0.1.7 actually
+   ships, record that date and the implementation-start date in the change log.
+   Reassess the phase sequence against the measured S11 baseline, interface
+   freeze and feasibility proof. If the resulting calendar is not realistic,
+   the coordinator brings a date or scope decision to the user; it must not
+   compress verification, drop a packet, or silently treat a historical target
+   as current.
+3. **Privacy and immediate visibility are release gates, not design claims.**
+   Before the installed integration/release decision: (a) an authorized
+   general-search acquisition query contains only values from the approved
+   public search-settings record, with the private preferences record
+   structurally unavailable to that code path, and (b) with the local model
+   stopped or made artificially slow, a newly discovered posting appears in
+   the same acquisition cycle as visible new/pending work. Also verify that
+   tracking updates are explicit, resume finalization never creates an
+   application event, and private assessment data never silently falls back to
+   hosted inference. Any failure is corrected, explicitly deferred with user
+   scope review, or blocks release; it is not shipped as a known silent gap.
@@ -170,4 +230,5 @@ and generalized autonomous orchestration.
-S07 gets its first-priority *investigation* as separate v0.1.8 research, but
-its implementation is not a prerequisite for this release. Explicit
-local-model selection and no silent fallback are enough for this release's
-model-execution needs.
+S07 continues as separate parallel, non-blocking v0.1.8 research; its
+implementation is not a prerequisite for this release. Explicit local-model
+selection and no silent fallback are enough for this release's model-execution
+needs. No schema, memory, hooks, Dolt, storage migration or new full-Gig work
+is pulled onto this path.
@@ -185,4 +246,4 @@ model-execution needs.
-- If Day 1's feasibility checkpoint finds the backstop connector genuinely
-  cannot be delivered by Day 3 without endangering the other two workstreams,
-  that is a scope renegotiation to record explicitly (per Control 2), not a
-  silent drop of "optional" scope.
+- If the feasibility checkpoint finds the backstop connector genuinely cannot
+  be delivered without endangering the other two packets or the installed
+  proof, record a scope decision with the user; do not silently drop the
+  connector or compress the acceptance gates.
@@ -193 +254 @@ model-execution needs.
-- 2026-09-21: restored Day 4 (installed-package integration, bounded
+- 2026-09-21 (historical, superseded): restored Day 4 (installed-package integration, bounded
@@ -202 +263 @@ model-execution needs.
-- 2026-09-21: corrected S08's required/deferred split to consistently say
+- 2026-09-21 (historical, superseded): corrected S08's required/deferred split to consistently say
@@ -211,0 +273,33 @@ model-execution needs.
+- 2026-09-21 (current supersession): replaced the fixed September 25/five-day
+  promise with an evidence-gated phase/calendar reassessment tied to the actual
+  v0.1.7 ship date; made S11 the first foundation; added the essential local
+  tracking UI and separate assessment/tracking state; added the S10 audit,
+  shared-interface/evaluation freeze, substantial packet ownership, integration
+  owner, deterministic acceptance lanes and explicit model-quality disposition;
+  retained S07 as parallel non-blocking research and kept schema, memory,
+  hooks, Dolt, storage migration and new full-Gig expansion off the path.
+- 2026-09-21 (Phase 1 authorization and receipt): operator authorized the
+  bounded S11 groundwork packet before claiming any v0.1.7 shipment. The
+  inventory, measurements, explicit lanes, acquisition mapping and test move
+  are recorded; the later audit/interface freeze, scheduled vertical proof,
+  packet work and installed release decision remain planned.
+- 2026-09-22 (S11 runner preparation): the complete portable offline `make test`
+  entrypoint and CI source/behavior/wheel lane wiring were added; historical
+  CI timings and bounded direct-selector checks are recorded in the S11 runner
+  receipt. Full test migration and before/after timing remain in progress under
+  Luna A's test ownership; no remote rerun or release claim is made here.
+- 2026-09-22 (P2-FREEZE-04 Amendment 01, operator decision): the thin
+  vertical proof runs as a real, linear Scout graph (`acquire → assess →
+  present`, `COMPLETE` edges) rather than as a separate scheduled pipeline.
+  Graph execution is not deferred wholesale to v0.1.9. The proof has five
+  acceptance conditions:
+  1. one bounded source whose saved postings stay visible when assessment
+     fails;
+  2. assessment of the saved postings with selected local evidence;
+  3. presentation in the tracking UI;
+  4. one traversal that owns the run, with per-node status, evidence and
+     usage;
+  5. resume without duplicate acquisition.
+
+  A Luna integration trace comes first; there is no sizing before it. The
+  exact-tag G03 release blocker is unchanged.

```
