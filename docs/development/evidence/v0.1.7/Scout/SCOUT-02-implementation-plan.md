# SCOUT-02 — Multi-graph foundation implementation

**Date:** 2026-09-08  
**Status:** Accepted; [completion evidence](SCOUT-02-completion.md) records final checks and limits.  
**Authorization:** Operator: “okay let's continue with scout!”  
**Dependencies:** Accepted SCOUT-00 contracts/workspace amendment and
[SCOUT-01 correction/re-review](SCOUT-01-G44-correction-rereview.md).

## Outcome and boundaries

Deliver [G43.2](../../../v0.1.7/goals/G43.2-multi-graph-gig-versions-and-graph-selected-run-plans.md)
with the [accepted lifecycle amendments](SCOUT-00-contract-amendments.md).
One approved Gig version pins an immutable Graph Set. Each new Plan and Run
selects exactly one graph and seals exact inputs. Changes require a new proposed
and approved Gig version; old approvals and Run records remain byte-identical.

Semantic graph selectors and aliases are distinct from Goal Graph UUIDs.
Include agent-explicit selection provenance without making it operator consent.
Preserve legacy v1 readers and serializers; add strict versioned schemas and
central identity derivation. Validate the full chain before Run allocation.

No job research, provider calls, registry/default-init migration, user CRUD,
external recording workflow, frontend, or v0.1.8 storage cleanup is authorized
by this implementation slice. Tests use disposable fixtures, not the operator's
private G44 review Gig. Existing dirty runtime and documentation changes are
preserved. No commit, tag, publication, or bulk cleanup.

## Agent ownership

- Terra: single writer for implementation source, strict schemas, CLI/caller
  integration, packaged schema inventory, its focused tests, and
  `SCOUT-02-implementation.md`.
- Luna: independent caller/compatibility audit; writes only
  `SCOUT-02-caller-audit.md`. Reports concrete gaps and acceptance cases;
  does not edit shared source or tests.
- Astra: integration decisions, this plan/ledger, independent verification and
  later isolated adversarial tests. Does not edit Terra-owned source while
  that task is active.
- Claude: independent review after the implementation stabilizes; writes
  a readable review report. Review findings return to the implementation owner.

Use the existing orchestration Run with explicit task/dispatch records and
same-worktree fresh agent sessions. Runtime/source ownership is not inferred
from terminal activity. Record actual completion, tests, and review evidence.

## Acceptance

1. Two structural graphs (Career/Stock fixtures or equivalent) are independently
   selectable under one approved Gig through real local interfaces.
2. Graph aliases normalize before sealing; ambiguous/missing/invalid selections
   refuse without a Run allocation; only-member selection has explicit provenance.
3. Graph/descriptor/content changes create a new approved Gig version; old
   version selection and old Run history continue to resolve unchanged.
4. Plan, selection, approved Graph Set, selected Goal Graph, input, and manifest
   identities agree end to end. Unsafe, forged, foreign, missing, or altered
   evidence and widened shared policy refuse before execution.
5. Agent selection is not user approval or managed-provider Run consent.
   Another graph's outputs/prior Runs do not become inputs implicitly.
6. Legacy approval/Plan/Run/index/comparison/portability callers remain valid
   or return deliberate version-aware diagnostics. Virtual v1 Graph Sets are
   inspection-only, never new approval authority.
7. Focused integration/adversarial tests, existing regression suite, schema
   inventory/installed-package checks, and independent review support the final
   status. Structural fixtures are not claims of functioning domain workflows.

## Evidence log

Dispatches, implementation results, review findings/corrections, commands, and
verification results will be recorded as they occur. No completion is inferred
from this plan or the earlier provider-review success.

### Agent launches

Orca accepted both fresh worker launches on 2026-09-08 under orchestration
Run `run_43c92f4fc427`. Launch acceptance is not task completion.

| Work | Model | Task | Dispatch |
| --- | --- | --- | --- |
| Implementation | `gpt-5.6-terra` | `task_6c444688c749` | `ctx_074028772b74` |
| Independent caller audit | `gpt-5.6-luna` | `task_88a0503ff043` | `ctx_57de57d87bee` |

Claude's independent review remains pending a stable implementation.

### Operator-directed workflow correction

At 17:26 UTC the first implementation worker returned an explicitly partial
result (failed task outcome, not A02 completion), with the safe proposal/staging
writer and two-graph acceptance still missing. Its report was read and its
terminal released with transcript preserved.

The operator then directed the coordinator to let the background implementation
agent finish its work and tests before verifying. The remaining implementation
and verification commands are assigned together to follow-up Task
`task_7a0c7f937d13` (Terra). The coordinator will wait for completion or a concrete
question, without inspecting in-flight edits or running competing tests.
Independent verification and Claude review follow the completed handoff.
The follow-up launch was accepted as Dispatch `ctx_7d65aec2788b`.

### Completed implementation handoff; review/verification wave

The follow-up worker wrote its final implementation report and terminal answer,
reporting 29 focused passes. Its completion and escalation RPCs failed; the
coordinator subsequently confirmed the final handoff. This is not a received
`worker_done` or acceptance of all A02 obligations. The recurring tooling issue
is deferred as [ORCA-01](../../../followups/ORCA-01-worker-completion-delivery.md).

Coordinator recovery: task-update initially refused while the Dispatch was
active. A stop attempt returned `dispatch_inactive`; subsequent read-only
worker-show reported the Dispatch/worker failed and the exact process exited.
Release returned `retained / identity_unproven` with no further process action.
No forced cleanup or app restart followed. Preserve the on-disk handoff as the
implementation evidence; do not turn these transport states into a code verdict.

At 17:49 UTC the following fresh background workers were launched. Source is
frozen while they work; each writes only its own report.

| Work | Agent | Task | Dispatch | Report |
| --- | --- | --- | --- | --- |
| Independent review | Claude | `task_a3b7cf8a59cd` | `ctx_c91ba5087448` | `SCOUT-02-independent-review.md` |
| Full suite and isolated built-wheel verification | Luna | `task_145650c846c9` | `ctx_2c5fd127fcc8` | `SCOUT-02-verification.md` |

SCOUT-03 remains dependency-waiting until this review/verification is resolved.

### Independent review returned; corrections authorized

Claude delivered its [review](SCOUT-02-independent-review.md) with a
changes-requested verdict: two A02 blockers and three smaller findings. Its
worker was released with transcript captured and the completion acknowledged.
Luna remains at an interactive build-permission prompt; no final verification
verdict has been received. Source remains frozen for that attempt.

The operator authorized correcting the findings and separately requested
[v0.1.8 schema simplification research](../../../v0.1.8/spikes/README.md#spike-5--schema-simplification-and-redesign).
The [correction brief](SCOUT-02-review-corrections.md) covers all five findings.
Orca task `task_6ad58e659829` is pending on the existing verifier's handoff;
Terra is the intended implementation owner, followed by Claude re-review and
Luna independent verification. No correction dispatch or runtime change is
claimed by this planning entry.

### Verification handoff received; correction worker launched

Luna delivered `worker_done` with its [verification report](SCOUT-02-verification.md):
74 focused passes, genuine built-wheel checks passed, full suite 729 passed /
15 failed / six errors / one skipped. Five socket failures passed in the
permission-enabled 18-test rerun; inventory/fixture and CLI expectation failures
remain. These findings are now explicitly included in the correction brief.

The completion was acknowledged after reading the report. Release retained the
terminal with `user_takeover`, so no close or forced cleanup was attempted.
Terra launched in a fresh same-worktree session: task `task_6ad58e659829`,
dispatch `ctx_f79e69f40791`, model `gpt-5.6-terra`, high effort. Orca confirmed
`input_accepted`. The coordinator does not edit or test its in-flight source;
Claude re-review and independent verification follow its completed handoff.

Prepared dependent tasks (not launched while Terra is editing): Claude
`task_0a9065fb9a86` writes `SCOUT-02-corrections-rereview.md`; Luna
`task_ee29c7705dee` writes `SCOUT-02-corrections-verification.md`. Both depend
on the correction task. The coordinator must read Terra's completed evidence
before launching them; pending DAG entries do not schedule workers themselves.

Terra's interim verification reports 91 focused tests and 87 schema subtests
passing, plus 79 G43/JSL seam tests passing. Its full offline run returned
746 passed, six failed, one skipped, and 94 subtests passed in 445.29 seconds.
The worker attributes the remaining failures to five loopback-bind restrictions
and one multiprocessing mount-lock probe; that classification is not yet
independently confirmed. It is paused on a user permission prompt to rerun the
affected group with loopback/interprocess access. No final correction handoff,
post-correction wheel result, or re-review verdict has been received yet.

### Final correction handoff and independent checks

Terra delivered `worker_done` at 20:55 UTC. The coordinator read
[its completed report](SCOUT-02-review-corrections-implementation.md): all five
bounded corrections, 70 final focused passes / 87 schema subtests, 79 G43/JSL
seam passes, compile/Ruff/schema checks, and a final full run of 747 passed /
five loopback failures / one skip / 94 subtests. The five failed cases passed
in an 18-test permission-enabled rerun. A freshly rebuilt isolated wheel also
passed checks from `site-packages`; its metadata remains 0.1.6, not a release.

Completion was acknowledged. Release retained Terra's terminal as
`user_takeover` with no process action. The operator's updated permission mode
was not changed. The correction source is now frozen for independent checks:

| Work | Task | Dispatch | Report |
| --- | --- | --- | --- |
| Claude re-review | `task_0a9065fb9a86` | `ctx_5059c2d9347d` | `SCOUT-02-corrections-rereview.md` |
| Luna verification | `task_ee29c7705dee` | `ctx_4c67b1df3595` | `SCOUT-02-corrections-verification.md` |

Both launches returned `input_accepted`. Luna was launched with the installed
Codex CLI's `--approve-for-me` flag and `gpt-5.6-luna` / high, then attached to
its supervised task with `worker-start --terminal`. This implements the
operator's request for automatic approval review on new Codex sessions, not
blanket sandbox bypass. Claude uses its configured defaults. No product-level
Run consent, baseline approval, or provider execution is created by either check.

Claude's re-review returned **accepted for A02/the five corrections** at
21:13 UTC. The coordinator read the report, released the worker with transcript
captured, and acknowledged completion. The remaining non-blocking notes and
G44 producer-side residuals are tracked in the
[review disposition](SCOUT-02-corrections-review-disposition.md); no approved
G44 input was changed. Luna's full-suite/wheel verification and the remaining
acceptance evidence accounting are still pending.

Luna completed the [baseline caller audit](SCOUT-02-caller-audit.md) at
17:13 UTC. The coordinator read the report and sent its integration hazards to
Terra. The settled audit worker was released with its transcript captured.
This is a baseline audit, not implementation acceptance. Its external protocol
operation-key/receipt cases remain SCOUT-04 obligations, not added SCOUT-02 scope.

### Independent verification cases

The coordinator's compatibility check includes sealing a Plan under version N,
approving changed version N+1, and then resolving/executing the Plan against N.
Current mutable `manifests/goal-graph.json` and current active capability refs
must not substitute for the approved version's pinned evidence. This is a
required test, not a claim that the implementation already handles it.

Capture before implementation, for byte-preservation checks of existing strict
schema resources (SHA-256):

| Existing schema | Digest |
| --- | --- |
| `gig-proposal.schema.json` | `515f16368059c7d8d4bf88cb47d8fc0df63afc50a51e13c8c75601c013f134b3` |
| `active-gig-version.schema.json` | `634af1729f4dbfe1f8564fd9d31b6c5cb8c56a2e1c7b80393e59ae877f76f5e1` |
| `run-plan.schema.json` | `0c3ba1cc9c6095e0468dc3b7476878d1ee55bf579b0ddc8fd46b1f7d82d3b1cb` |
| `run-manifest.schema.json` | `a14126ac4943e71980371eb215fbc191434cfb0fb2f2761259a0faabb36af24f` |
| `run-details.schema.json` | `c2388d917e08cfcc0860ecd3a20b389be4f434aadde6b21ffa18ee4d6457111f` |
| `handoff-frontmatter.schema.json` | `126e608ed9e9bfdcf2fb7fad1514005f8ca886fdfaf30522d93234a02d3a8247` |

Pre-implementation checks on 2026-09-08:

- Existing `tests/test_canonical.py` and `tests/test_canonical_ownership.py`:
  **59 passed** (0.29 seconds).
- Coordinator-owned `tests/test_scout02_independent.py`: **6 passed**
  (0.01 seconds); this initially guards the six captured schema byte digests.
- `ruff check tests/test_scout02_independent.py`: passed.

These are baseline/compatibility checks, not multi-graph acceptance evidence.

### In-progress independent regressions

The coordinator added policy/provenance tests in
`tests/test_scout02_independent.py` while Terra retained source ownership.
The initial 14-case run returned **6 failed, 8 passed**: agent actor schema,
two finite-cost ceiling cases, noncanonical persisted alias, and two invalid
deterministic-selection actor cases. Each finding was sent to Terra with its
reproduction. A subsequent isolated real-workspace legacy-Plan test also
failed because new nullable graph fields had entered the v1 identity
projection. These results describe intermediate code, not a final review.

Corrections and final reruns remain pending; do not use earlier passing
baseline tests or fresh-Plan self-consistency as historical-identity proof.

At 17:26 UTC, the coordinator reran the expanded independent file after
Terra's corrections: **15 passed in 1.57 seconds**. This resolves the
reproduced first-pass validation/legacy-identity cases. It does not complete
two-graph end-to-end, version-change, installed-package, or final review proof.
