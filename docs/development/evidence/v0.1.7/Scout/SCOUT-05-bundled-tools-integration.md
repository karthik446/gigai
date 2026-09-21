# SCOUT-05 bundled-tool integration checkpoint

Date: 2026-09-10. Coordinator verification of the delivered inert bundle,
not whole Scout acceptance or execution permission.

## Source, review and schema checks

[Claude's independent review](SCOUT-05-bundled-tools-review.md) accepts the
inert-preparation scope. Coordinator read the complete report, released the
reviewer with captured transcript, and acknowledged completion.

- Combined bundled/materialization/source-bundle/init-recovery/first-version
  tests plus historical wrapper CRUD regression: **38 passed in 76.44s**.
- After addressing review F1 with a full tool-bearing capability-manifest
  golden and nested negative cases: **10 tests, 179 subtests passed in 0.33s**.
- Scoped Ruff and schema resource verification passed (56 resources).
- No full-suite rerun, user approval, provider call or default promotion.

F1 is addressed in coordinator-owned central fixtures. F2 (init messaging
after a reviewed capability exists) remains assigned to the forthcoming
review/consent caller integration. F3 precision: explicit Graph approval may
reference a still-pending capability manifest; runtime source/security/effect
gates still refuse its execution. F4 is intentional: restoring inert manifest
bytes does not overwrite customized tool source or approve the changed bytes.

## Reproduced and corrected wheel omission

An offline wheel build included `record_tool.py` but omitted its required
`operation.schema.json`. Added only `scout/tools/*/*.json` to the existing
`gigai.data` package-data list in `pyproject.toml`, preserving unrelated edits.
A static package-data regression covering all non-Python, non-generated Scout
assets passed in 0.12s. The rebuilt wheel includes the tool schema.

Build and temporary install:

```sh
uv build --wheel --offline --out-dir /tmp/scout-bundle-wheel-Fy03SK/fixed
uv pip install --offline --no-deps --target /tmp/scout-bundle-wheel-Fy03SK/installed /tmp/scout-bundle-wheel-Fy03SK/fixed/gigai-0.1.6-py3-none-any.whl
```

Version metadata remains **0.1.6**; this is an unreleased worktree checkpoint,
not a v0.1.7 release artifact. Build backend came from the existing offline
cache. No dependency download occurred.
Wheel SHA-256: `c3b32dc026caac28692016c0f5927afc4f7709ccf537a3dd6fae948e5dade7c3`.

## Installed-code isolation and bounded proof

Executed `.venv/bin/python -I -S` with only the temporary installed target
and the existing `.venv/lib/python3.13/site-packages` added to the standard
library path. Site startup and editable `.pth` hooks were disabled; the
repository source directory was not on the module path. Asserted `gigai` was
loaded from the temporary installed directory.

Results:

- Installed source inventory loads all 14 members, including the 838-byte
  tool schema; installed CLI `--help` succeeds.
- `tools/verify_installed_schemas.py` verifies all 56 installed schema bytes.
- A disposable installed-library setup/candidate init, using the real
  `scout_candidate_inventory()`, copies shipped tool/schema assets and creates
  a pending capability manifest and pending first proposal.
- Candidate result is `approval_required`, security review remains `pending`,
  and project active selection remains unset. No copied tool was executed.

Dependencies were deliberately reused from the existing environment, without
processing its editable hooks. This is installed GigAI code/resource proof,
not a fresh dependency-resolution test or complete public-CLI Scout onboarding.
Candidate inventory remains an explicit internal path; general default
eligibility and reviewed bundled CRUD remain unproven.

## Next service

[Luna's caller audit](SCOUT-05-capability-consent-caller-audit.md) confirms the
missing generic reviewer-decision/operator-effect-consent path. New task
`task_f1488274c56f` / `ctx_ba22a943bea4` implements that bounded service in new
files; it must emit immutable decision/manifest artifacts without approving a
Gig or activating tools. Existing option-decision fields retain their current
pending semantics; reviewer judgment and effect selection belong to the new
explicit decision. Public CLI/successor approval integration follows separately.

### Decision registry integration checkpoint

While Luna owns the service and its focused tests, coordinator registered
`capability-review-decision.schema.json` and the `capability_review_decided`
journal transition. Updated the resource checksum inventories and added a
full decision golden plus nested strictness/operator/effect negative cases.
The central schema lane passes **11 tests and 184 subtests in 0.34s**;
the current editable resource inventory verifies **57 schemas**. Scoped Ruff
and `git diff --check` pass. This is registration evidence only, not service
acceptance; the earlier isolated wheel still contains the 56-resource
checkpoint and does not include the in-progress review service.

### Service handoff and bounded follow-up

Luna settled the initial service dispatch with a reported **10 passing tests
in 17.95s**, Ruff and compilation. Coordinator inspected the handoff and code:
the test fixture still monkeypatches registration, and replay returns the
decision's reviewed manifest reference without authenticating that result's
bytes. The handoff also predates coordinator's completed central registration.

Follow-up `task_6710b2cc954d` reuses Luna for three bounded integration changes:
test actual production registration, authenticate replay result references,
and key passed decision artifacts by the new manifest ID for direct caller
lookup. Rejected decisions retain their independent decision ID. The worker
must refresh evidence and focused tests before independent review. Initial
service completion is not acceptance or whole SCOUT-05 completion.

### Corrected service checkpoint; independent review active

Luna completed the follow-up as `ctx_e512cb118d39`: **13 tests in 24.45s**
against production registration, with replay-result authentication and
manifest-keyed decision paths. Coordinator read the updated handoff, released
the settled dispatch (external terminal retained without process action),
and ran the combined service/bundled-tool/schema lane:

```sh
.venv/bin/pytest -q tests/test_scout05_capability_review.py tests/test_scout05_bundled_tools.py research/contract_spike/tests/test_schemas.py --tb=short
```

Result: **30 passed, 184 subtests passed in 36.83s**. Current editable schema
resource verification (57), scoped Ruff, and `git diff --check` pass.
No full-suite or wheel rerun. Claude independent read-only review is active
as `task_e29f320cea1c` / `ctx_e0f0d3fb17fb`; its verdict remains pending.
Public CLI, successor approval, and actual reviewed bundled CRUD remain next.

### Independent review disposition

[Claude's report](SCOUT-05-capability-review-service-review.md) accepts the
bounded service with findings, not unconditional downstream acceptance.
Coordinator read the full report and released the reviewer with transcript
captured. F1 permits multiple passed successors for one pending parent when
the operation key changes; F2 contains two non-discriminating no-publication
assertions. Neither activates a tool, but both require correction before the
next caller relies on this service. F3 improves identity/race evidence;
F4/F6 address diagnostic and optional-reference compatibility precision.

Luna follow-up `task_08da6a195c99` owns these bounded service/test corrections
and a new findings implementation report. F5 needs evidence precision only.
Rejected-then-explicitly-passed review remains allowed and will be tested;
multiple passed successors for the same parent must refuse without breaking
exact same-key replay. CLI/successor approval remains separately open.

### Parallel CLI adapter and final replay correction

Luna reports **22 passing tests in 40.88s** in the
[findings handoff](SCOUT-05-capability-review-findings-implementation.md).
Coordinator source inspection identified two remaining F1 edge cases: a
sorted passed decision can prevent replay of an earlier rejected decision,
and the duplicate-parent check also refuses a different parent rather than
limiting the reservation to the exact parent. The requested committed-pointer
race test is also still missing (the existing test edits only working bytes).

Two disjoint workers are now confirmed `ready/input_accepted`:

- Luna `task_4d0e4458ba36` / `ctx_ee2789168f00`: service/test-only correction
  for those cases, preserving exact replay and authenticated source authority.
- Terra `task_a999319fee50` / `ctx_fdc4f156f4af`: thin generic
  `gigai capability review` CLI adapter with separate explicit operator
  confirmation, closed reviewer input, typed diagnostics, and focused tests.
  It does not implement successor approval or change the service.

No coordinator suite rerun while either owned surface is being edited.
Next combined verification follows settled handoffs; lifecycle approval
guards, successor preparation, and truthful post-approval init status remain
subsequent work, not implied by the new review command.

Luna settled `ctx_ee2789168f00` with **24 focused tests passing in 48.53s**,
Ruff and compilation. Coordinator inspected the two-pass operation-key and
exact-parent scan plus locations of the new replay/distinct-parent/committed
race regressions. The public service signature is unchanged. Released the
settled dispatch (external terminal retained), acknowledged the handoff, and
notified Terra. The CLI worker remains active; combined coordinator tests
wait for its settled handoff. The 24-test result is worker evidence, not a
fresh coordinator rerun or end-to-end approval proof.

### Public review CLI handoff and combined checkpoint

Terra completed the [review CLI adapter](SCOUT-05-capability-cli-implementation.md)
with 11 focused tests in 19.62s. Coordinator read the handoff and adapter,
released/acknowledged its dispatch, and ran the combined service, CLI,
bundled-tool and central-schema tests: **52 passed, 184 subtests passed in
78.67s**. Current schema inventory verifies 57 resources. Broader Ruff over
`cli.py` reports nine pre-existing private-record lint findings; the new
surfaces have no reported findings. This is not a clean whole-CLI lint claim.

An inert coordinator probe reproduced a missing-reviewer-ID `KeyError` when
an optional actor field is present. Adapter inspection also found permissive
duplicate-member JSON parsing. Terra follow-up `task_b45245777455` owns the
minimal actor fix, strict JSON parser reuse, typed excessive-nesting refusal,
and focused no-publication regressions. No schema or lifecycle changes are
authorized in this follow-up. The combined checkpoint predates these fixes.
Successor preparation and approval guarding remain open.

### Successor registration and first real integration failures

Both parallel workers handed off. Coordinator read both reports, released
both settled dispatches (external terminals retained), and acknowledged the
delivery. Luna's registration question was closed by dispatch settlement;
the attempted structured reply returned `dispatch_inactive`. Coordinator had
not integrated the schema while the worker waited: the reported seven
registration failures were not successful successor verification.

Registered `capability-successor-binding.schema.json` in the production
registry, hash inventories, full central golden, and nested negative cases.
Hash: `392422209e81fc86bb6259afda18ffa0d2f99c94df496469d4310c4afe4a2bf8`.
The current resource verifier confirms **58 schemas**. Central schema-only
tests pass **12 tests / 189 subtests in 0.37s**; scoped registry/golden Ruff
passes. No wheel rebuild was performed.

First combined successor/research/schema run after registration:
**7 failed, 23 passed, 186 subtests in 16.57s**. The seven successor cases now
fail at a real publication error: the writer treats the intentionally mutable
`manifests/gig-proposal.json` as immutable. It also mislabels that conflict as
an unregistered transition. The 12 candidate research tests pass in this run.

Luna correction `task_a150a97a821a` / `ctx_5d33ed5c446a` is active, covering
guarded mutable-proposal publication, accurate diagnostics, missing-sidecar
new-reviewed-manifest selection refusal, and recovery validation under lock.
No immutable journal guarantees may be relaxed. Claude independently reviews
the pure research candidate as `task_8c63d1b7ec23` / `ctx_8c3bd20e1a8e`.
Both starts confirmed `ready/input_accepted`. Neither successor execution nor
research persistence/default eligibility is accepted yet.

### Corrected successor checkpoint and public adapter lane

Luna completed the [successor corrections](SCOUT-05-capability-successor-implementation.md)
with eight focused tests passing. The disposable fixture now exercises actual
bundled source through review, pending successor preparation, separate approval,
and fresh-process `gig.py` create/update/archive/read/list/context operations.
This is fixture evidence, not activation of a user's Gig.

Coordinator combined verification after settlement: **38 tests and 189 subtests
passed in 101.98s**, covering `test_scout05_capability_successor.py`,
`test_scout05_tool_crud.py`, `test_scout05_capability_cli.py`, and the central
schema suite. This supersedes the earlier seven-failure checkpoint for those
paths; it is not a full-suite or rebuilt-wheel result.

Claude independent review is active as `task_e4223eafe151` /
`ctx_a591ccb7c782`, restricted to successor authority, approval, and recovery.
Luna public `capability prepare-successor` adapter work is active as
`task_15380ee396ee` / `ctx_6c8c44e9df81`. Startup confirmed
`ready/input_accepted`; the coordinator cleared its edit gate after the combined
suite settled. The adapter must prepare only, leaving approval as an explicit
separate command. Neither independent acceptance nor the complete installed
workflow/default eligibility is claimed yet.

Separately, coordinator read the [research-packet correction handoff](SCOUT-06-research-packet-corrections.md)
and reran its focused candidate tests: **35 passed in 0.11s**. Terra's correction
dispatch is settled and released (external terminal retained). These candidate
files remain outside the explicit bundled source inventory and persisted Run
output path; this is not whole SCOUT-06 acceptance.

### Independent successor review: corrections required

Coordinator read the completed [Claude review](SCOUT-05-capability-successor-review.md),
released the reviewer with transcript capture, and acknowledged its completion.
The review accepts bounded preparation/replay/publication properties but finds
two approval blockers: missing prior-pointer recovery reads an unbound local,
and successor enforcement can be bypassed by changing the mutable proposal's
creator field while deleting the working sidecar. Runtime still refuses changed
source; this is an approval-authority failure, not proof of arbitrary code execution.

Terra correction task `task_eba337257646` owns narrow core fixes and committed
regression cases. Luna's separate adapter lane may continue without editing core;
its tests during core changes are provisional. The prior passing checkpoint
does not establish coverage for these newly reproduced paths. Combined tests
and independent correction acceptance remain required after both handoffs.

### Public prepare adapter handoff

Luna completed the [public prepare adapter](SCOUT-05-capability-prepare-cli-implementation.md).
Coordinator read the handoff and released/acknowledged the settled dispatch
(external terminal retained). Worker evidence: **5 focused cases passed in
15.95s**, then **20 review/prepare CLI cases passed in 41.91s**, plus scoped
Ruff and compilation. The fixture exercises public review, public preparation,
the returned separate approval command, and a fresh-process wrapper create.

No coordinator test rerun was started while Terra's core correction dispatch
`ctx_d063fa6a92eb` remains active. These adapter results do not resolve the two
independently reproduced approval blockers. Combined verification and fresh
review must cover the settled core and adapter together.

### Both handoffs settled: combined verification and re-review

Terra completed the [F1/F2 corrections](SCOUT-05-capability-successor-corrections.md),
reporting **12 successor tests passed in 47.81s**, scoped lint and compilation.
Coordinator read the handoff and inspected committed-sidecar detection and the
absent-pointer recovery branch, then released/acknowledged the dispatch.

Coordinator started one combined suite covering successor, existing CRUD,
review CLI, prepare CLI, and central schemas on settled source. Result is
pending. Scoped Ruff across core, adapter, and their tests passes; the resource
verifier confirms 58 schemas. No new wheel or full-suite result is claimed.

Claude re-review `task_fd483c7b5d76` / `ctx_5f75474dec54` confirmed
`ready/input_accepted`. It covers the two fixes and the public prepare adapter,
with no suite reruns. Source remains frozen for that review. The prior review's
minor stale-base diagnostic observation was deliberately left unchanged by the
correction lane; acceptance of the blocker fixes is still pending.

The settled coordinator combined suite completed successfully: **47 tests and
189 subtests passed in 131.07s**. This covers successor, existing CRUD, review
CLI, prepare CLI, and central schemas together. Claude was notified of the
result to avoid duplicate suite execution. Independent re-review remains in
progress; this checkpoint is not full-suite, wheel, or release acceptance.

### Re-review accepted bounded changes; residual approval guard remains

Coordinator read the [independent acceptance review](SCOUT-05-successor-and-prepare-acceptance-review.md),
released the reviewer with transcript capture, and acknowledged the handoff.
F1 first-approval recovery and F2 mutable-successor detection are accepted as
corrected; the public prepare adapter is also accepted within its bounded scope.

The review still reproduces a reviewed manifest being selected through an
ordinary proposal with no committed successor sidecar, including recovery.
Regardless of its pre-existing classification, this remains a Scout acceptance
blocker. Terra task `task_f07f9bf83460` owns a minimal committed-provenance guard
on that legacy path, preserving legitimate never-reviewed legacy fixtures and
safe unchanged inheritance. No acceptance of the whole approval boundary is
claimed until that route is closed and independently verified.

Evidence clarifications: preparation journals a pending proposal; it is not
literally read-only despite wording in the review verdict. The 47-test checkpoint
is a focused combined suite, not a full-suite pass. Also, workpad `tools/` is
ignored: Git working-tree restoration does not restore tampered tool bytes.
Runtime inventory validation is the surviving source-integrity control.

### Legacy guard handoff and focused coordinator checkpoint

Terra completed the [legacy-selection guard](SCOUT-05-reviewed-manifest-guard-implementation.md).
Coordinator read the handoff, helper, and new regressions, then released and
acknowledged the settled dispatch. Worker evidence: **2 new tests in 5.81s**
and **20 affected successor/prepare/CRUD tests in 97.87s** passed.

Coordinator ran the new guard tests plus central schemas: **14 tests and
189 subtests passed in 6.56s**; scoped Ruff passes. The 20-test affected suite
was not duplicated. The new regressions directly call the real review and
lifecycle services; they are not themselves public CLI invocation tests.

Claude task `task_1a4f5933e0ac` is assigned independent review of this narrow
guard and its two lifecycle callsites, with no suite reruns. Until that review
settles, the legacy-selection blocker is implemented but not accepted closed.

### Guard review: fresh-identity copy still bypasses

Coordinator read the [guard review](SCOUT-05-reviewed-manifest-guard-review.md),
released the reviewer and acknowledged completion. The review reproduces a
generic-reviewed manifest copied under a fresh ID taking the legacy branch on
approval and recovery. The original-ID guard holds, but this blocker remains.
Terra task `task_693442501637` owns the targeted correction and renamed-copy
regressions, including a public CLI case and creator-metadata variations.

Coordinator verified a factual error in the suggested review fix: the actual
legacy CRUD fixture imports `_manifest` from `test_scout03_c3_tools.py`, whose
capability has `security_review.status: passed` and availability `available`.
Blanket rejection of every passed manifest would therefore break the promised
legacy compatibility. The correction must use generic-review provenance and
binding/content relationships rather than weakening those fixtures or trusting
mutable review claims. This does not claim to redesign arbitrary legacy
operator-authored authority. No coordinator tests run while the worker edits.

### Renamed-copy correction handoff

Terra completed the [renamed-copy correction](SCOUT-05-reviewed-manifest-guard-corrections.md).
Coordinator read the report and implementation, released the settled dispatch,
and acknowledged completion. Worker evidence: **10 guard tests passed in
26.99s** and **2 targeted public-successor/legacy-CRUD tests in 27.23s**.
The comparison now uses authenticated committed review bindings, excluding
manifest metadata, while explicitly retaining the historic never-service-reviewed
legacy trust assumption.

Coordinator guard/schema verification is running; scoped Ruff passes. Claude
task `task_e43c3d7900b0` is assigned focused correction review with no suite
duplication. It must assess the copied-source equivalence boundary and report
any necessary contract decision rather than imply all legacy authority has
been redesigned. Neither guard acceptance nor release readiness is claimed.

Coordinator correction checkpoint completed: **22 tests and 189 subtests
passed in 28.96s** (guard plus central schemas). Claude dispatch
`ctx_ee4af5ab63c4` confirmed `ready/input_accepted` and was notified of the
result. Independent acceptance remains pending.

### Per-capability containment checkpoint

The [correction review](SCOUT-05-reviewed-manifest-guard-corrections-review.md)
found whole-list matching still admitted an unchanged reviewed capability when
a second capability was appended or prepended. Terra completed the
[containment correction](SCOUT-05-reviewed-manifest-containment-implementation.md):
only capabilities individually named by authenticated passed decisions populate
the comparison set, and any matching selected capability triggers refusal.

Coordinator read the handoff and changed matching/authentication code, released
the worker and acknowledged completion. The six new normal/recovery/CLI
containment cases pass: **6 passed, 10 deselected in 17.93s**. This is focused
verification, not a repeat of all prior tests. Claude `task_bbad2d8c5b72` /
`ctx_d640eefca260` confirmed `ready/input_accepted` for bounded source/probe
review with no suite reruns. Guard source stays frozen during review.

In parallel Luna `task_1eadff1cf7ad` / `ctx_18dd1e042997` is mapping research
persistence integration. The user has explicitly reserved code review and UAT
at the release-candidate handoff; no release publication is authorized here.

Claude's [final bounded containment review](SCOUT-05-reviewed-manifest-containment-review.md)
**accepts F-A1** with no blocking findings. Coordinator read the report,
released the reviewer with transcript capture and acknowledged completion.
The six new cases were independently run by the coordinator; the reviewer
used source/test inspection and two in-memory probes, with no suite reruns.
This closes the copied-capability containment finding within the explicitly
retained legacy trust boundary, not all legacy-manifest authorization or the
whole installed Scout workflow. Non-blocking diagnostic/style observations do
not start another correction loop.
