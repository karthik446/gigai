# Scout: remaining execution graph to v0.1.7

Date: 2026-09-11 (America/Denver).
Status: execution plan, not implementation or release acceptance.

Operator release-order update: complete implementation and automated installed
verification, publish with explicit authorization, then install normally for
personal UAT and patch fixes. Human UAT is not a pre-publication gate.

This replaces the remaining delivery sequence, not the accepted product scope
or historical approvals. Read with the [roadmap](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md),
[local/private proposal decision](SCOUT-local-runtime-decision.md), and
[UAT checklist](SCOUT-12-user-uat-checklist.md).

## Honest estimate

Planning range, not a measured forecast or a promise:

- First integrated synthetic workflow suitable for early user feedback: **2–3
  focused working days** (roughly 12–24 elapsed working hours with parallel workers).
- Full-scope candidate ready for the user's code review and release UAT:
  **4–6 focused working days** (roughly 28–44 elapsed working hours).
- Publication follows automated candidate verification and explicit authorization.
  User turnaround and fixes discovered during post-publication personal UAT are
  not included above.

Assumptions: two implementation lanes plus a third independent test/evaluation
lane when useful; Luna available without long quota interruptions; installed
local runtime available for synthetic evaluation; existing foundations reused;
one integrated review and consolidated correction pass per delivery wave.
Budget approximately 35–61 worker-hours before contingency; parallelism does
not divide all of these by three because shared contracts and integration are
serial. The full-candidate range includes integration/review contingency, not
another schema redesign. Re-estimate after the first integrated workflow, using
actual elapsed time and remaining failures. A major new authority defect or
scope change invalidates this range; report it instead of rolling the ETA forward.

## Inspected starting point

Current source and evidence show reusable pieces, not a complete product:

- Default initialization, copied Scout source, local record CRUD and research/
  discovery/tailoring contracts exist. `data/scout/gig.py` exposes record commands;
  it is not yet the full daily proposal/tailoring/report entry.
- Local Ollama execution and proposal construction exist. `run.py` has a real
  proposal Run entry; the main CLI has no corresponding proposal caller.
- The [latest proposal review](SCOUT-proposal-run-final-review.md) accepts the
  operation/Goal-shape and terminal recovery source corrections, but requests
  receipt byte/reference/actor/target authentication and missing negative tests.
- Discovered posting resolution exists separately from the older Tailor request
  contract. Saved proposal-to-Tailor input integration still needs delivery.
- Application event commands exist. They are not proof of opportunity/document
  linkage, dashboard integration or the complete application workflow.
- A copied UI template/CSS and interview graph instructions exist. They are not
  evidence of a populated tracker or resumable interview workflow.
- `pyproject.toml` still declares 0.1.6. The UAT checklist is explicitly unexecuted.
  A standalone Qwen extraction experiment is not the installed comparison goal.

No tests were rerun to prepare this plan. Existing worker evidence remains
bounded to its stated checks; absence of a completion report is not by itself
proof that every relevant implementation is absent.

## Dependency graph

```text
R0 receipt repair + shared interface freeze
 ├── R1 acquisition / private proposals / answers ──┐
 ├── R2 explicit Tailor / documents ────────────────┼── R4 integrated user journey
 └── R3 tracker / application linkage ─────────────┘          │
                                                            ├── R5 interview / transfer
R0 ── R6 core comparison pack and runner ─────────────────────┤
                                                            └── R7 exact-wheel release proof
                                                                      │
                                                              authorized publication
                                                                      │
                                                              user install + UAT
```

R1–R3 author independently against R0 fixtures; their acceptance requires the
combined R4 path. R6 can start whenever a slot is free after R0; it does not wait
for interview or UI. R5 fixture design may start early, but its real acceptance
uses the integrated saved records. These are development goals, not a claim
that a new GigAI executable Plan has already been sealed or confirmed.

| Goal | Deliverable and completion evidence | Estimate |
|---|---|---|
| R0 — Finish shared boundary | Repair the existing receipt reader using one pinned journal snapshot, exact artifact bytes/refs and invocation/actor/target binding. Add the review's concrete negatives. Freeze a small shared interface sheet for opportunity, proposal revision, answer, document selection and projection DTOs using existing storage. No general schema redesign. | 1–2 worker-hours |
| R1 — Find and assess | Supported agent/local-tool entry persists each considered posting, duplicate/failure/exclusion reason and source snapshot. A bounded invocation with a deadline exits with durable progress. Selected private preferences/experience produce revisioned local proposals with fit, salary/sponsorship uncertainty, focus and questions. Save answers and reassess without rewriting old inputs. Integrate existing local execution; do not introduce an agent scheduler or crawler. | 6–10 |
| R2 — Requested Tailor | Version the actual input contract for supplied posting, discovered posting and selected proposal plus answers. Support resume only, cover letter only and both; materialize supported document files, inspect checks, iterate and explicitly select final revisions. No automatic Tailor or application transition. Private input stays on the local route; hosted acquisition/review receives only explicitly public or synthetic material. | 5–8 |
| R3 — Tracker and applications | Populate clean local HTML from the rebuildable SQLite view, with job/proposal/question/document/source/Run links. Link explicit application events to real opportunity and selected document identities. Preserve external applications without inventing a Tailor Run. Local CRUD and report generation, no HTTP CRUD API. UI edits survive regeneration. | 5–9 |
| R4 — First integrated journey | One supported entry from a fresh included Scout instance through preferences, discovery, proposals, answer, explicit Tailor, final selection and explicit application. Prove reload, changed preferences, duplicate discovery, interrupted work and failed local assessment. Run one independent integrated review; fix findings together. Deliver an installable early-feedback artifact, explicitly not the full release candidate. | 4–7 |
| R5 — Interview and portability | Reuse saved research/posting/experience for role-only and opportunity interview preparation; save feedback and resume in a new session. Prove customization/update behavior, portable definition and separate explicit private transfer to a second home without old absolute links or credentials. Reuse existing transfer code where valid. | 4–7 |
| R6 — Core setup comparison | Ship the existing runtime through supported installed entry points, a Gig-owned synthetic evaluation pack and core comparison operation. Freeze grader and cases, check known good/bad outputs, run Qwen/Ollama and Luna/Codex as independent durable attempts, retain failures and readable quality/time/setup identity. No private data forwarded for comparison. | 4–8 |
| R7 — Candidate and release proof | Build a versioned candidate, test the exact wheel in a dependency-complete isolated environment, run the full required matrix and upgrade/schema checks, update help/docs/changelog. One final independent risk-focused review and consolidated fixes. Supply commands, artifact digest, evidence and readable UAT handoff. | 6–10 |

## Interface freeze and file ownership

Before parallel source edits, R0 publishes concrete field shapes, existing
authority helpers to call, allowed transitions and fixture examples. It does
not demand a fresh framework. In particular settle proposal revision identity,
answer association, Tailor selectors and projection inputs together so workers
cannot invent incompatible wrappers or fabricate G45 references.

- R1 owns proposal/discovery domain modules and their tests.
- R2 owns versioned Tailor/domain document modules and their tests.
- R3 owns tracker/projection modules, UI source and application integration tests.
- R6 owns core comparison/evaluation modules and synthetic cases.
- One designated Luna integration owner handles shared `cli.py`, `run.py`,
  journal, schema registration, source inventory and `gig.py` changes. Other
  lanes return bounded patches/requests; no concurrent edits to shared files.
- Existing unrelated G43/runtime changes remain preserved and separately
  accounted for at release. No reset, broad cleanup or automatic commits.

The latest local-proposal decision governs the new workflow. Earlier roadmap
text excluding unattended discovery does not authorize a newly installed
scheduler: ship a bounded, explicitly invoked job that an external local
scheduler can call; do not install a recurring job or background agent. Document
this boundary explicitly rather than claiming background scheduling shipped.

## Review and orchestration policy

All implementation uses **Luna**. Use Luna for routine independent checks;
reserve Astra/stronger review for combined privacy/authority boundaries or an
unresolved defect, not every function. Use local Qwen for small synthetic
extraction/evaluation work with deterministic validation, not final authority.
Do not send private records to a hosted implementation/review model.

Workers run focused regression tests and lint for their changes and save exact
commands/results. The coordinator reads completion evidence and checks the
integration diff; it does not repeat every worker suite. R4 owns the first
combined workflow suite; R7 owns the final complete release matrix. A failed
integration test produces one grouped correction task, not a new review chain
for each assertion. Changed authority interfaces still require independent
review before being treated as accepted.

Use Orca completion notifications. If IPC fails, the existing low-cost ten-minute
fallback may check status once and recover the exact terminal handoff. No
continuous polling, active waiting, or repeated broad test runs while workers
are busy. State the delivery milestone being worked toward, then yield.

Maintain completion evidence against R0–R7. Report **first workflow usable**,
**full candidate ready for UAT**, and **approved to publish** as different states.
None is implied merely by all workers being idle.
