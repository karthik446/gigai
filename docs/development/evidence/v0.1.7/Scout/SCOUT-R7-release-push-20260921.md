# Scout R7 release verification push

Started September 21, 2026, approximately 14:35 UTC, after the operator requested
focus on getting the release out. This is execution state, not release approval
or publication evidence.

## Source freeze and previous handoff

The input/subprocess author ended with a durable completion report and failed
IPC delivery. On recovery the old dispatch was already failed/stale; abandonment
made no state or process change. The task result now records author completion
from the exact final transcript and report, not independent acceptance. Release
retained its external terminal. No product files were changed by the coordinator.

Source, tests, version metadata and verifier scripts are frozen for the two
verification lanes below. Read-only review can proceed concurrently with testing;
any later correction invalidates the matching artifact/source proof and must
be reconciled explicitly.

## Active lanes

| Lane | Task / dispatch | Owned output | Initial estimate |
|---|---|---|---|
| Final independent input and subprocess correction check | task_d998732de076 / ctx_9a76a185eb39 | SCOUT-R7-final-input-review-20260921.md | 20–35 minutes |
| Rebuilt exact wheel/sdist and release matrix | task_e4db5957b32f / ctx_7b3a133a6ca7 | SCOUT-R7-rebuilt-candidate-verification-20260921.md | 90–150 minutes |

Both are Luna-high workers with observed turn starts. They use the same
unrestricted local permission profile as the coordinator for disposable
loopback/IPC verification, not permission to read private data or alter global
configuration. No live model/provider calls, source edits, commits, tags, pushes,
uploads or publication are assigned to either lane.

The candidate worker publishes a local artifact-readiness handoff at
SCOUT-R7-rebuilt-candidate-artifact-20260921.md with exact wheel hash and isolated
environment identity before finishing the offline matrix. This permits a later
separately bounded real Qwen/Luna comparison against that exact artifact; it is
not approval of the release. Dependency installation and existing CI container
checks are allowed within disposable test environments; unavailable Docker is
reported rather than silently accepted or starting unrelated services.

## Completion gates

1. Final correction acceptance and no unaccounted source drift.
2. Rebuilt artifacts with content/privacy inventory and source provenance.
3. Dependency-complete exact-artifact smoke, installed verifier/scenario checks,
   and one durably captured full offline source suite.
4. Actual installed runtime comparison and remaining platform evidence, recorded
   separately from synthetic unit/integration evidence.
5. Publish through the existing release workflow with the required source commit,
   annotated tag and named publication environments. A dirty local wheel is not
   an exact-tag CI pass; do not bypass the workflow or silently publish unrelated
   changes.
6. Normal user install and personal UAT after authorized publication.

The existing model-free ten-minute fallback now observes both the artifact-ready
and final report files, plus task status. It is registered through 20:30 UTC;
no active coordinator polling or duplicate full suites are scheduled. Completion
notifications remain the primary signal. These estimates are worker handoff
windows, not a promised publication time.

## Artifact readiness and live comparison dispatch

Processed artifact_ready msg_c2b97306ead0 / delivery_e6a823477296 after reading
the complete readiness report. This is a frozen dirty-worktree snapshot at the
recorded HEAD, not a claim that HEAD alone contains all packaged changes.
New wheel SHA256:
`ed3b3336d8f4d0bd49f9175c1e216ccafcc7e20583ed94d1ccb550679b0a59d3`.
Installed import origin and dependencies are recorded in the readiness report;
the source/release-matrix worker is not settled by this status notification.

Live comparison task_b121835491b9 / ctx_71f043137ef6 /
term_570dfa76-fe79-49a7-8bf4-6ed222605957 is assigned to Luna-high. It owns
SCOUT-R7-live-comparison-20260921.md and disposable synthetic evidence only.
Use this exact wheel through installed supported operations, with authenticated
Gig-owned graph/pack and independent real Qwen/Ollama versus Luna/Codex attempts.
Maximum one comparison of the three shipped synthetic cases across two setups,
bounded timeouts and no automatic retries or alternative backend fallback.
No private data, API billing, model downloads, global settings changes, source
edits or publication. Existing Codex subscription authentication is used only by
its CLI; credential bytes must not be printed/copied. Missing local runtime or
unsupported installed entry is an explicit blocker, not permission to fake a
result or bypass authority.

Expected handoff30–60minutes. Preserve errors, quality failures, observed model
identity and outputs; verify fresh-process reload. A poor model score is not
itself a product failure, while missing validated local execution remains a
separate requirement. The final review and release matrix continue concurrently.
The ten-minute fallback now also tracks this comparison handoff.

## Final correction review accepted

Processed genuine worker_done msg_df9f24332c91 / delivery_5b710bde0ace and
read SCOUT-R7-final-input-review-20260921.md completely. The reviewer accepts
R7-CR-01 and the batch-shell corrections with no new defect in scope. Public
Click probes cover code, structured data, mixed/opaque inputs, unchanged sealed
references, symlink refusal and distinct text-only boundaries. The AST check
finds28subprocess calls and zero shell-keyword violations; Ruff and compile
checks pass. Exact source hashes are recorded in the review for reconciliation
with the rebuilt artifact. No new product-wide review is requested.

Task/dispatch settled automatically; release requested and delivery acknowledged.
Release-matrix and live-comparison workers remain separate active lanes. This
acceptance is for the corrected source slice, not publication or whole R7.

## Live comparison found a release blocker

Processed worker_done msg_72b9c39b38ea / delivery_13a17573f778 and read the
entire SCOUT-R7-live-comparison-20260921.md. One comparison made six real
provider executions, no retries; configured/observed identities were retained.
All six outputs graded invalid_output. Fresh installed show/status failed with
`comparison pack digest is not authenticated`: raw journaled source digest and
canonical parsed-pack digest are distinct but the reader compares them directly.
Coordinator inspected runtime_comparison.py and confirmed that comparison path.
No local validated-goal or comparison durability success is claimed.

The output-shape failures are not yet evidence of model weakness: inspect the
actual transmitted output contract and criterion identities before attributing
them to either model. The worker also reported setup endpoint parsing lacks
ollama_local despite config/factory support; preparation via schema-valid temp
config is not full public-setup acceptance.

The live task settled automatically; release retained external_terminal, delivery
acknowledged. Release worker was told this candidate is blocked regardless of
its ongoing matrix outcome and to finish the same matrix without source edits.

Luna-high task_deaf16c02aea / ctx_16c3e5bb43f4 /
term_24435b96-5d39-4bde-9a32-5f27d0d67a75 now prepares minimal corrections in
an isolated scratch copy ONLY, with report
SCOUT-R7-live-corrections-preparation-20260921.md. Main src/tests/tools remain
frozen. Scope: authenticated raw versus canonical digest regression and reader
fix; exact prompt/output-contract investigation and gold-safe shape instructions;
confirm and minimally address local setup parser gap if real. No new model calls,
no mutation of the live comparison history and no broad suite. Read-only replay
of the existing synthetic result with the scratch reader is permitted. Prepared
patches require separate application after the current matrix ends.

Initial estimate45–90minutes; fallback tracks the report. Publication remains
blocked. This is a concrete newly discovered integration defect, not another
unrequested feature or redesign.

## Isolated correction preparation handed off

Processed genuine worker_done msg_c4dc43c45e0f / delivery_087b9ad23153
for task_deaf16c02aea / ctx_16c3e5bb43f4. Read the preparation report and
PATCH-README.md. The bounded preparation task is complete, not the production
correction or release acceptance. Worker release returned retained with reason
external_terminal and processAction none.

The eight reported passing tests exercise a dependency-free semantic helper,
not imports of the proposed production overlays. The historical comparison
check likewise demonstrates dual-digest semantics, not successful public
show/status through the patched service. Carry this limitation into the next
task: production-path regression tests and installed CLI roundtrips are still
required before accepting or publishing these fixes.

Main release source remains frozen while the existing matrix completes. Next:
review and apply the narrow overlays after that handoff, update the relevant
resource inventories and new pack contract without rewriting historical data,
then rebuild and verify the corrected installed paths. No additional live model
calls, duplicate full suite, or publication was started by this notification.

## Matrix completed; bounded production correction wave

Processed genuine worker_done msg_d9c59c1a5072 / delivery_68de22eb0491
and read SCOUT-R7-rebuilt-candidate-verification-20260921.md fully.
The verification task completed, but the candidate is not accepted:
1564 passed, 1 failed, 1 skipped (3208.71s); 22/22 installed verifiers,
35/35 installed scenarios, synthetic Scout smoke and sdist smoke passed.
No drift was reported in source/tests/tools/build metadata during the matrix.
Docker was unavailable; other Python/platform and exact-tag gates remain open.
Release returned external_terminal retention with no process action; delivery
acknowledged. The source freeze is now lifted for these explicit owners only.

Two independent Luna-high tasks dispatched, with no duplicate full suite or
new provider/model calls authorized:

- task_6fe23824f71e / ctx_b90ea74ca4c8 /
  term_69594567-5194-426d-a97a-94f87bc4cc2c: review and apply the prepared
  comparison authentication, public output-contract and Ollama setup corrections.
  Owns runtime_comparison.py, setup-only cli.py changes, evaluation pack
  schema/data, directly related inventories and focused tests. Requires actual
  production-path tests and isolated installed verification, not the scratch
  semantic helper. Report SCOUT-R7-live-corrections-implementation-20260921.md.
  Estimated handoff45–75minutes.
- task_1361b214fe2f / ctx_de46de072e52 /
  term_931f2ec9-288e-4058-af6b-aaf2344b4be6: diagnose and correct the sole
  capability-review refusal test failure. Owns capability_review.py and
  test_scout05_capability_review.py with bounded regression evidence. Distinguish
  genuine refused-operation writes from fixture/background Git activity before
  changing assertions; no silent exclusions or weakened authority guarantees.
  Report SCOUT-R7-capability-refusal-correction-20260921.md. Estimated
  handoff30–60minutes.

Cross-owner edits require coordination. Both preserve historical comparison
workpads, unrelated dirty files, private data and v0.1.8 work. No commit, tag,
push or publication is authorized by these tasks. The existing model-free
ten-minute fallback tracks both; completion messages remain the primary signal.
After completion, independently review the bounded changes and reconcile the
combined candidate before further release acceptance.

## Capability-refusal test correction accepted, bounded scope

Processed worker_done msg_23501df80b28 / delivery_d6ed04752f46 for
task_1361b214fe2f / ctx_de46de072e52. Read the handoff, changed test helper
and regression, production preflight, and original matrix traceback.
Production capability_review.py is unchanged. Refusal precedes the writer;
the revised snapshot excludes Git internals while retaining non-Git paths,
symlink targets and exact file bytes. The new regression traps any writer
entry and compares committed authority records before and after refusal.

Evidence clarification: pytest's positional list message "Left contains one
more item: ui/template.html" is not a set difference and does not establish
creation of that file. An earlier insertion shifts the final list element.
The first actual mismatch names .git/objects/pack/tmp_pack_OwK63L; the revised
test does not exclude ui/template.html. Do not carry the worker report's
unsupported interpretation of UI-file appearance into release claims.

Worker reports 28 focused tests and three targeted repetitions passed.
Independent coordinator run of the exact failing parameter plus new preflight
regression: 2 passed in 6.82s, using .venv/bin/pytest. Bounded correction accepted;
the original full matrix remains 1564 passed/1 failed/1 skipped, not retroactively
green. Release returned external_terminal retention with no process action;
delivery acknowledged. Comparison/Ollama production integration remains separate.

## Production comparison corrections delivered; independent review active

Processed genuine worker_done msg_656bb1fca90d / delivery_4e82bbbb0a3a
for task_6fe23824f71e / ctx_b90ea74ca4c8. Read the complete implementation
report. Author reports 12 production-path tests, schema inventory (82), Ruff,
compile, and fresh installed show/status against an unchanged-content disposable
copy of the old comparison passed. No new provider calls were made. Corrected
wheel at /private/tmp/gigai-r7-live-corrections-0F5F5u/dist/ has SHA256
fbca831bc46c6992ee813d9c37ce90802a83073440777897a2c7b84ffc8e2fe9.
This is not the same wheel as the earlier full release matrix; preserve that
distinction. Author's stale reference to the parallel capability-review failure
is superseded by the preceding bounded acceptance, not a new failure.

Release returned external_terminal retention without process action; delivery
acknowledged. Independent Luna review task_53252f05053d /
ctx_fbc8b0e5680d reuses term_931f2ec9-288e-4058-af6b-aaf2344b4be6, which did
not author the comparison changes. Read-only review of the bounded delta against
the old wheel (not incomplete git HEAD), with optional disposable offline probes;
owns only SCOUT-R7-live-corrections-review-20260921.md and unique review artifacts.
Expected handoff20–40minutes. No repeated full suite or new inference authorized.
The model-free fallback tracks the review.

New approved pack/graph coherence and public setup roundtrip remain explicit
review checks. Whole release, cross-platform/tag CI, any corrected live run and
publication remain unproven. Personal UAT remains after authorized publication.

## Review requested two bounded pack-coherence corrections

Processed worker_done msg_703694c97414 / delivery_842906d12f6c for
task_53252f05053d / ctx_fbc8b0e5680d, reading the complete review. Verdict:
changes requested for fresh comparisons. Prior dual-digest/historical read and
local/remote setup corrections are bounded-accepted. P1: pack-level output
version can disagree with per-case prompt contract, with approved graph binding
not demonstrated. P2: public criterion IDs can differ from deterministic
expected.criteria keys. Neither malformed pack should reach model execution.

Coordinator inspected the existing approved Graph Set -> evaluation_contract
-> exact pack_ref bytes/digest chain. This already provides an immutable pack
binding; the correction must use/prove that equivalent authority rather than
automatically adding redundant manifests. Explicit contradictory graph output
contracts and internal pack versions must still be rejected. Historical
no-contract packs and saved outputs remain unchanged.

Luna task_7b2a343dfc55 / ctx_4f6733137719 reuses implementation terminal
term_69594567-5194-426d-a97a-94f87bc4cc2c. Owns runtime comparison and narrow
pack/schema/inventory tests as needed; report
SCOUT-R7-contract-coherence-correction-20260921.md. Requires public-path
approved-fixture negatives with zero invocation/publication, valid fresh
roundtrip, installed proof and preserved historical compatibility. No new model
calls/full suite, private workpad mutation, v0.1.8 changes or publication.
Expected handoff20–40minutes; scheduled fallback tracks the task.
Reviewer release returned external_terminal with no process action; delivery
acknowledged. Final release acceptance remains pending.

## Coherence correction delivered; final same-candidate verification wave

Processed genuine worker_done msg_00d7427af71a / delivery_a3b05694031c for
task_7b2a343dfc55 / ctx_4f6733137719. Read the full report, independently
inspected the production delta against the preceding wheel, and reviewed actual
production-path negative and valid-roundtrip tests. No further blocking issue
identified in this narrow source review; author reports 17 focused tests,
Ruff/compile, schema verification and installed historical show/status passed.
This does not establish fresh installed/live comparison acceptance.

Candidate frozen for the following two read/verification lanes:
/private/tmp/gigai-r7-contract-coherence/dist/gigai-0.1.7-py3-none-any.whl,
SHA256 0371662823de1b963455f50a967d226c95cfe2fadbefe03557630b3f844ca5b7.
Matching sdist SHA256
11fb92ba696cc1ffc67808930857dee6c0c7f5f91f1972e5cc5f867ec638a9a5.
Main product/test/tool/schema/build metadata must not change until both settle.

- Fresh installed synthetic comparison: task_444f0f25de6d /
  ctx_2a27e80337b6 reuses term_69594567-5194-426d-a97a-94f87bc4cc2c.
  Report SCOUT-R7-fresh-live-comparison-20260921.md. New disposable public
  setup/approved Graph Set and coherent pack, existing local Qwen and subscription
  Luna, at most one readiness probe each and six case calls without retries.
  Preserve raw failures and old history; no invented quality pass or winner.
  Supported-path gap is a blocker, not permission for private journal injection.
  Estimate30–60minutes.
- Final candidate verification: task_6c9e4a94c415 / ctx_ed553784b6d0 /
  term_9231d753-9be1-4800-903c-aae970d90e8e, Luna-high. Report
  SCOUT-R7-final-candidate-verification-20260921.md. Reconcile candidate with
  source, one final locked Python3.11 offline full suite with durable logs,
  required installed verifiers/scenarios/smokes on exact artifact, drift and
  packaging checks. Docker only if already available; no daemon launch.
  Estimate60–90minutes. No test retry loop or source correction in this lane.

Original verification-terminal reuse was refused with agent_unconfigured;
the task was confirmed still ready before starting it in the new terminal.
No duplicate verification attempt was launched. Both new dispatches report
observed turn start. Completion delivery was acknowledged after reassignment;
no reclaimable worker remains. Existing model-free fallback tracks both tasks.
No commit, tag, push, publication, real private input or v0.1.8 work authorized
by this wave. Exact-tag/cross-platform release workflow remains a later gate.

## Fresh comparison blocked at public binding; isolated correction dispatched

Processed worker_done msg_cb7d30da5bde / delivery_00bf1d571275 for
task_444f0f25de6d / ctx_2a27e80337b6 and read its complete report. Installed
setup, disposable public lifecycle and backend readiness succeeded. No comparison
calls occurred: the public proposal operation allocates the ID only after the
evaluation contract must supply a sealed output-contract path containing it.
The report also records v2 pending-proposal check/reject using the v1 schema.
Task completion means the bounded investigation ended, not release acceptance.

Reused Luna terminal term_69594567-5194-426d-a97a-94f87bc4cc2c for
task_392c62f73413 / ctx_61dfb14507a3, observed turn start. Scope: reproduce
and correct public binding in an isolated copy of current public candidate
files; public CLI regression and installed offline roundtrip, negative authority
tests, and bounded v2 check/reject correction if confirmed. No main product
edits, provider calls, full suite, private workpads or publication. Deliver
SCOUT-R7-public-binding-correction-20260921.md plus a transferable patch and
file digests. Estimated 30–45 minutes. Integration must wait for the frozen
verification lane to settle; any correction changes the final candidate.

Final candidate verifier task_6c9e4a94c415 remains running (heartbeat only).
Source freeze remains intact. The scheduled model-free fallback now tracks the
isolated correction. Duplicate fallback notification was informational only.

## Scratch correction delivered; regression closure required

Processed msg_091928e197d2 / delivery_26c2a85944d2 for task_392c62f73413 /
ctx_61dfb14507a3. Read full report and inspected both production file diffs
against frozen main bytes. Source-local output identity is authenticated before
rewriting the staged evaluation reference, and pending proposal validation now
dispatches v2 separately. Installed offline comparison and fresh show/status
were demonstrated. Main source remains unchanged.

Not merge-ready: the complete runtime comparison test file reports 12 fixture
failures; no durable new pytest regression was delivered, and first-proposal
rejection plus several required negative cases remain unproven. Completion of
the scratch task is not acceptance of the correction.

Same Luna terminal now owns task_5ee4c2d207eb / ctx_a884ad1ba5a8 (observed
turn start). It must correct source-local fixtures, add public-path regression
tests and missing negative cases, prove fresh v2 rejection and v1 compatibility,
pass complete focused files, rebuild/recheck installed offline proof and deliver
an actual transferable diff including tests. All work remains scratch-only;
main report SCOUT-R7-public-binding-regressions-20260921.md. Estimate20–35
minutes. No provider calls, full matrix or main integration. Scheduled fallback
tracks this task. Frozen verification continues independently.

## Regression handoff reviewed; explicit rejection closure assigned

Processed msg_bd4cefc2fc03 / delivery_41576d804f12 for task_5ee4c2d207eb /
ctx_a884ad1ba5a8. Read complete report and 305-line transferable diff. Worker
reports 25 runtime tests and 41 combined focused tests passing, plus rebuilt
installed offline comparison. Fixture construction now uses public input import
and local refs rather than predicted proposal IDs and manual pack journal writes.

Remaining public-path blocker confirmed in source: reject resolves only the
active Gig and has no explicit Gig selector, preventing rejection of a fresh
default instance's first proposal. Coordinator also identified an untested
optional canonical digest inconsistency: rewritten evaluation ref retains
canonical_sha256 but staged descriptor drops it; runtime exact-ref comparison
would disagree. A valid optional-ref roundtrip and false-digest refusal are
required before accepting this representation.

Luna reuses same terminal for task_f18b2d64bc7a / ctx_e9ceed8b0ac1, observed
turn start. Scratch-only optional --gig rejection with strict proposal identity,
legacy behavior and no activation; close optional-reference coherence with real
digest authentication and public-path tests. Deliver updated transferable patch,
focused lifecycle tests and installed offline proof in
SCOUT-R7-public-binding-final-closure-20260921.md. Estimate15–30minutes.
No main product integration/provider calls/full suite; frozen verifier remains
independent. Scheduled fallback tracks new task; prior completion is not release
acceptance.

## Frozen candidate offline matrix passed; Debian invocation corrected separately

Processed msg_4253ecce7d93 / delivery_9744bd2eddb4 for task_6c9e4a94c415 /
ctx_ed553784b6d0. Read complete report and independently checked raw full-suite
exit status, command provenance and final summary: 1574 passed, 1 skipped,
269 subtests passed, exit0, Python3.11.14, 3226.82seconds. Report records all22
installed verifiers, all35 installed scenarios, synthetic Scout acquisition/report
and sdist setup/doctor smoke passing. Protected source scope had no drift.
These results belong to wheel0371662823de1b963455f50a967d226c95cfe2fadbefe03557630b3f844ca5b7,
not the separate scratch binding correction or a future rebuilt candidate.

Docker was available; raw stderr confirms its build never started because the
temporary image tag included uppercase characters. Same Luna verification
terminal now owns task_c1c1503916e8 / ctx_657a3c8ef2d5, observed start, for one
correctly named Debian workflow attempt with private-safe disposable context,
exact baseline identity and durable logs. No macOS matrix/verifier reruns,
provider calls, daemon startup or source edits. Report
SCOUT-R7-debian-verification-20260921.md; estimate15–30minutes cache-dependent.
Scheduled fallback tracks this task. Main product freeze remains in place;
publication, corrected-candidate live comparison and exact-tag CI are unproven.

## Personal-UAT release scope; final scratch patch enters integration

User explicitly directed release for personal use: finish in-flight fixes,
targeted integrated verification and installed Scout smoke, then required
publication workflow. No further broad review/full-suite cycle or live Qwen/Luna
quality gate; comparison remains experimental for post-release evaluation.
Known privacy/data-loss/core-function failures remain material.

Processed msg_1a5b2df9feef / delivery_d6a062319707 for task_f18b2d64bc7a /
ctx_e9ceed8b0ac1. Read full closure report and494-line patch. Narrow source
review accepts explicit rejection selection and optional canonical digest
authentication/preservation for integration. Author reports28 runtime tests,
16 graph/first-proposal tests,1 legacy rejection test and installed offline
comparison/rejection checks passing, not whole-suite or live-quality evidence.

Luna task_aab269db2e95 / ctx_a2e1aa118383 reuses implementation terminal,
observed start. Conditional main ownership limited to lifecycle.py, cli.py and
tests/test_runtime_comparison.py once Debian worker confirms immutable copied
context (not main bind reads). Apply reviewed patch after input digest match;
targeted integrated tests, new wheel/sdist and isolated installed Scout smoke.
Report SCOUT-R7-release-integration-20260921.md; estimate15–25minutes.
No commit/tag/push/upload/provider call in this task. Debian worker received
coordination message msg_88c01307e0f5. Fallback tracks integration task.

## Debian report incomplete: bounded first-failure diagnosis only

Processed msg_e5f99b8c195d / delivery_78e45bc81b44 for task_c1c1503916e8 /
ctx_657a3c8ef2d5. Full report read: baseline image built and isolated preflight
passed; pytest showed at least10 failure markers by64%, then worker terminated
the group before traceback/summary/status receipt. The task's succeeded label
does not mean test success. No named failure or root cause was established.

Same Luna terminal reused for task_36db593d7ef3 / ctx_9b822d919a27, observed
start. Existing immutable image only, first-failure pytest -x traceback, hard
10-minute bound, no full-suite retry or code edits. Durable exit/timeout logs,
classify actual failure in SCOUT-R7-debian-first-failure-20260921.md. No
provider calls or model wait loops. Integration continues independently; this
does not reopen broad review or establish a new platform scope. Fallback tracks
diagnosis. Personal-UAT release direction remains in force.

## Integration accepted for personal-use release preparation

Read full SCOUT-R7-release-integration-20260921.md on worker_done
msg_3fc0fb71e880 / delivery_edd84c0ca87a, task_aab269db2e95 /
ctx_a2e1aa118383. Exact reviewed patch integrated in three authorized files,
postimages match scratch;45 combined targeted tests and installed Scout
acquisition/report plus offline comparison/rejection checks passed. New wheel
da75dde1f9672e6e096ed4fed3f78da8b1915c6ce6b104174d46c9c2354b527e,
sdist d89629aaa301432b65a003ffe91184887462d68ff26493e682393bacd512e0e3
in /private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/dist.
Prior full matrix is baseline evidence, not a rerun of this changed candidate.

Same Luna terminal assigned task_44eb1062f8f2 for5–10minute publication prep:
read-only exact git include/exclude manifest and remote release prerequisites,
keeping unrelated v0.1.8/private work out; report
SCOUT-R7-publication-preparation-20260921.md. No new implementation/tests,
stage/commit/tag/push/upload in this preparation task. Required existing release
workflow and pending Debian diagnosis remain explicit; live comparison quality
does not block personal UAT publication under revised user scope.

## G28 fixture correction accepted

Processed msg_69de068d6790 / delivery_cfac757904a6 for task_682e0a101e94 /
ctx_40b9de9a0558. Read report and exact eight-line test-only diff: explicit
synthetic endpoint/model target removes incidental reliance on installed host
CLIs without changing resolution assertions or runtime behavior. Author reports
3 current-environment tests passing and the exact case passing in isolated
Debian, exit0. No other Debian failure is thereby cleared. Worker released;
publication-prep worker already notified of test inclusion.

Report artifact hashes037166.../11fb92... identify the OLD Docker baseline,
not the current integrated release artifacts. Current wheel remains da75dde1...
and sdist d89629aa... as recorded above. Test-only correction changes no product
bytes; no extra build or full test run requested.
