# Scout — Parallel delivery plan

**Authority:** The user explicitly requested parallel implementation. This
supersedes the coordinator's earlier single-writer delivery sequence, not the
accepted product contracts or acceptance gates.

**Starting evidence:** The second C1 combined offline suite completed with
782 passed, 1 intentional live-model skip, 104 subtests passed, 7 fork warnings,
exit 0 in 504.40 seconds. Fresh C1 review remains necessary. C1 acceptance is
not a prerequisite for writing independent domain modules against its settled
interface; integration/release acceptance still requires it.

## Active wave and exclusive file ownership

| Lane | Deliverable | Exclusive writable files |
| --- | --- | --- |
| A — Terra | Native records and agent-facing CRUD: preferences, experience Q&A, source links, selected conversation, archive and task overrides | New `src/gigai/native_records.py`, `native_records_cli.py`, `schemas/native-record-content.schema.json`; new `tests/test_scout03_native_*.py`; `SCOUT-03-native-lane-implementation.md` |
| B — Terra | External recording service and CLI group: inspect requirements, plan, start, checkpoint, submit, cancel and inspect | New `src/gigai/external_recording.py`, `external_cli.py`, `schemas/external-recording-*.schema.json`; new `tests/test_scout04_*.py`; `SCOUT-04-external-lane-implementation.md` |
| C — Claude | Independent review of settled C1 storage correction | Only `SCOUT-03-C1-final-review.md`; source read-only, no competing full suite |
| Integration — Astra | Bundled Scout source/catalog, shared registration/CLI wiring, lane interfaces and integration evidence | Shared existing files and new bundled Scout assets; never edit a worker-owned module during its active task |

All workers stay in this worktree because the accepted foundation is dirty and
uncommitted. No broad checkout/copy, commit, reset or private-data mutation.
Fresh terminals provide independent agent contexts; file ownership prevents
concurrent edits to the same files. Shared C1 code remains frozen during its
review; additional transition/ID/schema registration is coordinator-owned and
identified separately from C1 logic.

## Frozen integration rules

- A uses existing registered Gig resolution, `JournalWriter`/snapshot authority
  and validated publication. It does not write SQL or fork the persistence
  implementation. Native scope is pinned with the native sidecar and normalized
  operation payload; do not silently change accepted outer revision fields.
  If a minimal core extension is necessary, request an exact change from Astra.
- A provides real command/library behavior with focused disposable-workpad
  tests, not just payload classes. Its independent Click group can be tested
  before the coordinator mounts it into the top-level CLI.
- B implements the accepted section 9 protocol, using the existing journal
  lock and approved graph authority. Source-family resolution is an explicit
  integration seam: exercise real G45 inputs now and consume native record
  resolution when A is integrated. No fake provider executor or inferred user
  consent. Do not modify managed Plan/Run code in this lane.
- B may expose its own independently testable Click group. Actual top-level
  registration, managed/external reader discrimination and installed-agent
  journey are coordinator integration gates, not waived requirements.
- Shared `canonical.py`, `journal.py`, `validators.py`, schema inventories,
  `cli.py`, packaging metadata, existing fixtures and persistence files have
  exactly one owner: Astra. Workers request registrations once their concrete
  files are ready; no monkeypatching production registries to claim integration.
- New schema names are limited to the already agreed native-content schema
  and five external protocol families. Any additional schema/interface need
  must have a concrete reason and coordinator decision, not a parallel redesign.
- Worker tests cover their own lane. One integrated full suite runs after the
  wave stabilizes. No competing full suites, live provider calls, or private
  UAT. Passing lane tests is not whole-goal/release acceptance.

## Next wave

As these lanes settle, use the same explicit ownership model for role
research/job discovery, tailoring/checks, and application tracker/report work.
Those domain features can proceed in parallel against the accepted output
contracts and common record interface. Initialization, installation, usability
and portability are integrated with them, not postponed until a final giant
merge. The first complete demonstration is supplied posting -> tailored
resume -> explicit application event -> tracker.

Orca dispatches and artifact handoffs are recorded below as they start.
Use completion/question messages; do not repeatedly inspect busy terminals.

## Dispatches started

- A: `task_caec791cac40` / `ctx_6bef8f5e7d01`, reusing the settled Terra
  terminal launched with `gpt-5.6-terra`, high effort, `--approve-for-me`.
- B: `task_8e951d7e26b9` / `ctx_f6d10e95d75f`, fresh terminal
  `term_b0c12c93-faa5-4912-9c14-fb6d1c399699`, same Terra launch flags.
- C: `task_2b6aec2cafca` / `ctx_6fde08ac2b0b`, fresh Claude terminal.

All three returned `ready/input_accepted`. The old broad C2 task remains
blocked/superseded by these bounded lanes; this is not C2 acceptance.
Astra registered canonical `CHECKPOINT`, `RECEIPT`, `TASK_CONTEXT` prefixes and
the seven accepted external recording transition names. No C1 storage function
was changed by that additive registration. New schema registrations follow
when worker-owned schema files are ready.

## First handoffs and rolling wave

- C1 Claude review [accepted](SCOUT-03-C1-final-review.md), source-only. Astra
  read the report and combines it with the completed 782-test regression run
  and the earlier independently reproduced acceptance defects. The bounded C1
  correction is accepted; this is not whole SCOUT-03 acceptance. Claude's
  malformed-database observation must **not** be read as permission to delete
  `state.sqlite`: unreadable G22 trace may still need recovery. No automatic
  removal or force-rebuild was implemented.
- Native Terra delivered [six focused tests](SCOUT-03-native-lane-implementation.md).
  Astra's native/source combined check passed 20 tests before the later CLI
  mount-regression case was added. Fresh Claude native review is active:
  `task_446007d47d0e` / `ctx_77aa66ae9cef`. Source-only reviewer owns only
  `SCOUT-03-native-lane-review.md`; no native source changes while it reviews.
- External Terra's first handoff is not accepted. It requested a new
  `external-recording/` top-level root, contrary to the frozen layout. Same
  terminal reused for `task_5afa062cfce4` / `ctx_3b33db72fcea`: canonical
  `run-plans/`, `runs/`, and namespaced `records/external/` storage; substantive
  lifecycle acceptance tests and readable contract requirements. Same file
  ownership as Lane B. No workpad allowlist widening.
- Luna checks lane started: `task_34dcccf7ccea` / `ctx_6104956f2770`, terminal
  `term_40f1b8d4-7ddc-4cdf-ab45-59516b1e116e`, model `gpt-5.6-luna`, high
  effort, `--approve-for-me`, `ready/input_accepted`. Owns only new
  `src/gigai/scout_checks.py`, `tests/test_scout08_checks.py`, and
  `SCOUT-08-checks-lane-implementation.md`. Pure deterministic advisory checks,
  no semantic truth claims, score, provider calls, schema family or persistence.
- Astra authored the five canonical Scout instruction assets and inert catalog
  candidate, source loader, simple local HTML/CSS, package-data inclusion and
  source/package tests. `scout_catalog_candidate()` does not add an unfinished
  default to the live catalog. Domain behavior and initialization remain open.
- Astra registered the six new schemas, central IDs/transitions and CLI groups.
  `gigai external` is mounted. Native CRUD is additively mounted as
  `gigai record native` so it cannot replace existing G45 `record create/read`.
  Shared valid-fixture inventory integration remains pending the external lane's
  concrete fixtures. The installed-resource verifier currently reports 55
  schema files; this check uses the development installation, not a release wheel.

### Checks handoff disposition

Luna delivered the pure checker with nine passing tests. Astra read the module
and evidence, then independently ran checks plus source tests: 24 passed in
0.16 seconds. Four extra disposable in-memory probes reproduced missing
acceptance behavior: prose satisfies a required heading; a 100,000-character
invalid kind is echoed into a 101,159-byte report; the declared output-digest
scope cannot reconstruct its digest; and changed length requirements can
produce identical reports with no requirements identity. The lane is not
accepted. Same Luna terminal is assigned correction task `task_18028e05bbef`
with the same three-file ownership; other workers remain undisturbed.

### External correctness correction dispatched

The frozen-root correction passed five tests in Astra's rerun (17.13 seconds),
but disposable-workpad probes demonstrated false success for an unrelated
output with an empty sidecar/no checks, broken checkpoint/submit retry, and
failed explicit selection reuse. Actual persisted invocation bytes fail their
schema while malformed empty nested Plan objects pass. Ruff reports 35 errors.
Under the user's explicit instruction, Terra is assigned
`task_20c80017807a` in the same external terminal, with the same bounded file
ownership. It must correct strict protocol publication/reads, completion
validation, all-operation idempotency, authenticated selection reuse and lint,
with both valid completion and adversarial regressions. No layout widening or
extra schema family. Central inventory refresh follows final schema bytes.

Luna's checks correction handoff is received: 14 tests pass in the coordinator's
rerun (0.01 seconds), evidence read and worker released through Orca (retained
because the terminal is externally owned). Independent acceptance and workflow
integration are still separate. Native review also returned changes requested;
its findings remain open and its terminal was retained by Orca due to user
takeover, so it must not be reused without a new ownership decision.

### SCOUT-04 seven-test handoff: strict schema finding remains open

`ctx_8282941ce4a3` delivered seven focused tests; Astra reran them successfully
(27.57 seconds), confirmed Ruff clean, and refreshed the five supplied schema
hashes in shared inventories. Development resource verification passes 55
schemas. However the original malformed Plan probe still passes: empty
`invocation`, an empty `inputs` item, and empty `limits` are accepted. Thus this
handoff is not accepted. Same Terra terminal now owns `task_197677a48aaa`, a
narrow strict-nested-contract correction across the existing five schema
families, actual emission/read validation, and parameterized negative tests.
The worker evidence document also remains stale at five tests and must be
updated. No additional schema family or workpad root is authorized.

### Strict correction verified; independent review active

`ctx_39f9e687d2c6` delivered the strict correction. Astra reran all 13 focused
tests (25.35 seconds) and separately reproduced rejection of the original
empty nested Plan mutation. The five schema hashes were independently checked
and refreshed; the development resource verifier passes all 55 schemas.
Shared count assertions and the complete valid-fixture inventory now include
the six native/external families. The central schema suite passes 6 tests and
109 subtests (0.29 seconds), using operation-appropriate positive fixtures,
not a count-only bump. No full-suite or release-wheel claim is made.

Fresh Claude source-only review runs as `task_fd39b00bd305` /
`ctx_00aa2cf4f5ad` in `term_a8ebcb49-4bfe-4d43-902b-4115728eda11`.
Only writable file is `SCOUT-04-independent-review.md`. It reviews completion
semantics, replay, authority/selection, strict protocol and lifecycle boundaries;
native-source integration remains a separate unimplemented seam. SCOUT-04 is
still not accepted pending this review and broader integration evidence.

### Fresh SCOUT-04 review: corrections dispatched

Claude completed [independent review](SCOUT-04-independent-review.md) with
changes requested. Astra read the complete report. F1/F2 require completion
to enforce the sealed approved output/check contracts, not self-declared kinds
and arbitrary artifact references. F3 requires deterministic repeat selection
construction and cause-specific journal errors. F4 requires strict resolved
G45 reference/run-input and Scout-over-G45 revision variants at sealing,
checkpoint and start. Native `jsl_blob` integration remains separately deferred;
it must not be silently accepted without validation. B1 continuation and safe
successor metadata need explicit regressions as described in G1/G2.

Terra correction task `task_11348c2cc182` / `ctx_9e22e982ef58` started in
the existing external terminal `term_b0c12c93-faa5-4912-9c14-fb6d1c399699`;
Orca confirmed `ready` / `input_accepted`. Ownership remains the external
module/adapter, five existing schemas, focused tests and implementation note.
No additional schema family or shared-file ownership is granted. Unsupported
approved contract shapes must fail deliberately; genuinely unspecified semantics
must be escalated rather than guessed. Worker owns focused tests; coordinator
refreshes central inventories and verifies after handoff, followed by independent
review. The completed Claude dispatch is released.

The report lists CLI registration and central inventory as outside its review
scope. These are already integrated with bounded coordinator evidence above;
managed/external reader discrimination, broader integration, fresh-session M1
UAT and whole-goal acceptance are not thereby established.

### 2026-09-09: coordinator verification and parallel corrections/review

External correction `task_11348c2cc182` settled with a worker-reported fifteen
tests. Astra read its handoff, released the dispatch (retained external
terminal, no process action), and acknowledged the delivery. The preceding
check-shape question was already closed by the inactive dispatch; its proposed
shape is not retroactively approved by the completion report.

Astra ran the stable external/source/checks/schema suites together:

```text
.venv/bin/python -m pytest -q tests/test_scout04_external_recording.py tests/test_scout05_source_bundle.py tests/test_scout08_checks.py research/contract_spike/tests/test_schemas.py
50 passed, 109 subtests passed in 54.42s
```

The invocation and Plan schema digests were independently calculated and
updated in both central hash inventories. Development schema verification
passes 55 families; external module/adapter/test Ruff passes. This is not a
full-suite or isolated release-wheel result.

Despite these passes, a disposable in-process probe of the existing completion
test substitutes the checkpointed check sidecar as the submitted output
sidecar; its check Markdown also explicitly reports failure. The service still
returns `succeeded`, with receipt output sidecar equal to the check reference.
This proves the invalid output/check pairing is not refused. Whether a supplied
check passed cannot currently be evaluated from its three-field sidecar alone;
do not claim the recorder actually executed or verified the external check.
No real private Gig, provider, or repository source was changed by the probe.

Fresh Claude review is active as `task_9b1bdf6f1ffa` /
`ctx_2635dd35c573`, terminal `term_94914337-a9d4-4420-99cb-675b7ccad5e9`.
Only writable report: `SCOUT-04-corrections-rereview.md`. The reviewer received
the coordinator probe. External implementation files remain stable during
review, and native `jsl_blob` integration stays a separate seam.

In parallel, native Terra task `task_8660111d677d` / `ctx_766a1e984d8a`
is active in `term_a91a17c6-90e2-4684-8e96-c593a349c52f`. Ownership is native
module/adapter, its one schema, native focused tests and implementation note.
Disposition: fix explicit/generated override argument validation, require exact
employer evidence, require nonempty authenticated conflict evidence, and bind
media metadata. User-reported eligibility needs jurisdiction/context, not an
invented external-proof requirement. Document archive copying and the existing
imported-reference branch without creating a new content family. C3 default
selection and update affordances remain separately open. Both new starts were
confirmed `ready` / `input_accepted`; the user-taken-over reviewer was untouched.

### 2026-09-09: R1 confirmed, R2 disposition, native correction delivered

Both completion reports were read in full and delivery acknowledged. External
Claude confirms the coordinator's output-sidecar swap as R1; earlier F2/F3/F4/
G1/G3 are resolved and G2 is operationally satisfied. Its settled dispatch was
released with archived transcript. Native Terra delivered nine focused tests
and unchanged schema bytes; that settled dispatch was released/retained as an
external terminal. Native acceptance awaits coordinator checks and re-review.

**R2 coordinator implementation disposition:** a declared evidence label alone
cannot satisfy B1's completion gate. The existing nested check-sidecar shape
must include required `result: pass|fail`. Checkpoints retain either result;
submit refuses a selected failing check and requires passing declarations for
every approved evidence kind. An explicitly selected corrected pass can follow
an older immutable failure; mixed supplied pass/fail must not silently discard
the failure. Missing/unknown results are invalid. This records the external
actor's declaration, not actual execution or independent verification:
`execution=unobserved` and `actor_report=declared` remain mandatory. No Markdown
status guessing, provider evaluation, new schema family, baseline rewrite or
automatic evidence migration is authorized. Detailed domain rule/version/
findings/severity reports remain SCOUT-08 obligations; this minimal envelope
gate does not claim to fulfill that whole domain report contract.

External correction `task_536573d677a4` / `ctx_5be4db681659` is active in
the existing external Terra terminal. Ownership is only the external module,
existing invocation schema, focused test file and implementation note. R1 must
revalidate submitted bytes and exact checkpoint output tuples, not merely
their independent existence. Fresh Claude native review is active as
`task_35685f1719f3` / `ctx_5838f1c9ae49` in
`term_f01d4b25-fdf8-4012-8341-3daac6f365e4`; only writable report is
`SCOUT-03-native-corrections-rereview.md`. Both starts returned
`ready` / `input_accepted`. Coordinator native/C1/source tests run separately;
native lint already passes. No source changes overlap these workers.

Coordinator result collected:

```text
.venv/bin/python -m pytest -q tests/test_scout03_native_records.py tests/test_scout03_c1_acceptance.py tests/test_scout05_source_bundle.py
35 passed in 64.74s
```

This focused native/C1/source result is not a full-suite or release acceptance.

### 2026-09-09: bounded native and external acceptance

Astra read both final reports in full:
[native corrections](SCOUT-03-native-corrections-rereview.md) and
[external R1/R2](SCOUT-04-R1-R2-final-review.md). Both recommend bounded
acceptance, which the coordinator accepts at those stated scopes. Native
N1/N2 and external O1/O2 remain recorded non-blocking observations; they do
not trigger another implementation/review round. C3 and whole-goal integration
are not implied by these lane verdicts.

External Terra's first completion notification failed IPC. The exact worker
transcript and on-disk handoff established that implementation had finished.
A coordinator manual task settlement was refused because the dispatch was
still active; no forced stop, app restart, or invented delivery followed.
The worker was asked to resend only its completion notification. The retry
arrived as `msg_45b7ea853361`, settled `ctx_5be4db681659`, and was acknowledged
after release (`retained/external_terminal`). Both completed review terminals
were retained by Orca due to user takeover and must not be reused or closed
by the coordinator.

The invocation schema digest was independently calculated as
`0608170d6b707198f903e4097f6a14cba61a92581a8f119b0e074da685df7316`
and refreshed in both central inventories. Development verification reports
55 installed schemas. External lint, compileall and diff checks passed.
The already-completed integration checkpoint was:

```text
.venv/bin/python -m pytest -q tests/test_scout04_external_recording.py tests/test_scout03_native_records.py tests/test_scout03_c1_acceptance.py tests/test_scout05_source_bundle.py tests/test_scout08_checks.py research/contract_spike/tests/test_schemas.py
70 passed, 109 subtests passed in 132.84s
```

No rerun is required merely to acknowledge the final review. Future correction
loops should select the changed regression/module checks; combined integration
checks run at integration checkpoints, and the full suite at release gates.
This result is neither a pure unit-suite timing nor installed-wheel proof.
Managed/external reader discrimination, native-input integration, fresh-session
M1 UAT, owner-aware initialization and the Scout domain workflows remain open.

### User-directed parallel implementation: test timing and Scout integration

The user explicitly corrected the timing assignment: one worker must implement
test-timing improvements, while another implements the next Scout pieces.
Luna task `task_7254c1f477ee` / `ctx_cd5fe37fd639` therefore no longer has
an audit-only scope. Its superseding coordinator message `msg_97be8d75c75d`
and accepted terminal prompt direct actual fast-unit/integration lane
separation and safe measured timing improvements, preserving full offline
coverage. Ownership: pytest configuration only in `pyproject.toml`, test
targets/helpers/classification and non-Scout fixtures where justified, new
timing tests and `test-timing-implementation.md`. No runtime source, Scout
integration tests, central schema fixtures, or package metadata changes.

Terra task `task_7e5455c6cc3e` owns the next runtime slice: exact native
profile/experience/preference inputs in external Plans/Runs, immutable history
through a fresh-process question/answer/successor story, and explicit managed/
external reader discrimination. Ownership is `scout_inputs.py` if needed,
external module/adapter and existing schemas, narrow reader guards if required,
new C3/input-integration tests and its evidence note. It cannot edit Luna's
configuration/test tooling or central schema inventories. C3 tool binding,
package guard, bundled initialization and domain workflows remain separate
subsequent work. Both lanes use focused checks, not repeated full suites.

#### Native input task-context disposition

Terra asked `msg_55bb7065d4bf` whether the external input envelope can bind
native task context. Approved as a closed pre-release refinement, not a grant:
native `scout_record` selections require explicit scope
`{mode: saved_default, task_context_id: null}` or
`{mode: run_override, task_context_id: <canonical task_context UUIDv4>}`.
Match those fields against the committed native scope; the committed scope
also carries `base`, which must be retained and authenticated, not discarded
or mistaken for an extra caller field. Seal the full resolved native scope
and exact base, include them in identity, and revalidate before start.

Preserve the accepted unscoped Scout-over-G45 and direct G45 input variants.
Missing native scope cannot be inferred. Explicit saved defaults may accompany
overrides from one selected task context, but mixed override contexts in one
Plan must refuse. Per-input equality alone is not sufficient task isolation.
Task context is caller-selected isolation metadata, not authentication,
approval or provider-disclosure authority. Required regressions cover absent,
wrong, malformed and mixed contexts, valid default/override selection, replay,
and old explicit revisions remaining pinned after updates. No new schema
family or automatic migration/approval is authorized.

### Native-input handoff and next independent package boundary

Terra delivered [native input integration](SCOUT-03-C3-input-integration.md).
Astra read the report, released the completed dispatch and acknowledged
delivery. Supported native families are explicitly profile/preferences and
experience Q&A; other native families remain refused, not implicitly supported.
The two changed schema hashes were independently calculated and refreshed in
both central inventories. Development resource verification passes 55 schemas.

Coordinator verification selected only the fresh-process M1 CLI story plus
central schema checks:

```text
.venv/bin/python -m pytest -q tests/test_scout03_c3_inputs.py research/contract_spike/tests/test_schemas.py
7 passed, 109 subtests passed in 16.39s
```

Fresh Claude integration review is active as `task_bf15320a6441` /
`ctx_e7776d9c91cd` in `term_5dd45510-8bf9-4ed9-a92b-db785b9ea043`.
Only writable report: `SCOUT-03-C3-input-review.md`. It is explicitly told
not to repeat the delivered integration suite, using source review and only
narrow suspected-defect probes. Input integration is not accepted yet.

The next disjoint Terra task is `task_b5cb607f8665`, the C3 clean-package
privacy guard. Ownership: `package.py`, optional `package_privacy.py`, one
new package-privacy test file and `SCOUT-03-C3-package-guard.md`. It must reject
recognizable private records/projections/imports in otherwise valid inventory
packages while preserving inert Scout templates and source; no private transfer
or arbitrary prose detection claim. Native-input/runtime files remain stable
for review. Tool binding and bundled initialization are still subsequent work.

Luna's timing implementation handoff is received and read: it reports 236
fast-unit tests in 3.18s, with full discovery preserved and no total-suite
speedup claimed. Its classification is heuristic and not independently
accepted. The settled Luna dispatch was released/retained as an external
terminal. No full-suite rerun was performed for these handoffs.

### Native-input integration accepted at delivered scope

Claude delivered [bounded acceptance](SCOUT-03-C3-input-review.md).
Astra read the complete report and accepts the profile/preferences and
experience-Q&A input integration, alongside the already-recorded fresh-process
M1 CLI/schema verification. Exact committed revisions/scopes/base references,
single-override-context selection, historical continuation, G45 compatibility,
and deliberate managed-family refusal are covered at this scope. Other native
input families and full C3 tool/default-selection behavior are not implied.
The two source-only optional observations are recorded, not a new correction
round. The reviewer was released with archived transcript before delivery ACK.
Terra's package-guard message was only a heartbeat; that worker remains active
and untouched. No tests were rerun to process this acceptance.

The subsequent ACK returned a new package-guard completion delivery. Astra
read its full report, released the settled Terra dispatch (retained external
terminal), and acknowledged it. Coordinator package-only verification:

```text
.venv/bin/python -m pytest -q tests/test_scout03_package_privacy.py tests/test_g41_package_boundary.py::test_package_export_is_validated_idempotent_and_authority_free tests/test_g41_package_boundary.py::test_package_export_refuses_symlinked_parent
15 passed in 1.96s
```

Fresh Claude task `task_f1db3d20046d` reviews the package guard and staged
copier; sole writable report is `SCOUT-03-C3-package-review.md`. It is told
not to repeat the test suite, only investigate specific suspected defects
with narrow disposable probes. Package acceptance remains pending. The
discussion of behavioral eval packs did not authorize their implementation
and has not changed the Scout baseline.

### Package F1 correction and local tool service — 2026-09-09

The [package review](SCOUT-03-C3-package-review.md) is delivered: bounded
acceptance with F1, missing private `manifests/` detection. Package acceptance
remains pending correction. The reviewer was released (retained due to user
takeover); no further reviewer work was started.

Two independent implementation dispatches have accepted input:

- Luna: `task_0a6dcda5d603` / `ctx_8a45deab8642`, terminal
  `term_75c2f9c8-51b5-40cd-b37c-03d524dbb1db`. Owns the private-manifest
  guard, package privacy regressions and correction evidence only. The old
  Luna terminal was unavailable; a fresh Luna/high session was launched with
  `--approve-for-me` and attached to the task.
- Terra: `task_d856622567d0` / `ctx_6c369e36985b`, reused terminal
  `term_b0c12c93-faa5-4912-9c14-fb6d1c399699`. Owns the next C3 Gig-owned
  tool/service seam: approved source and effects bound to mutation receipts,
  rechecked at publication, with a real fixture entry through validated
  persistence. This is implementation toward shipping local `gig.py`, not
  SCOUT-05 default initialization itself. Package files remain Luna's scope;
  central schema inventories/registration remain the coordinator's scope.

Both briefs require focused tests, not repeated full suites or wheel builds.
Dispatch acceptance is not implementation or verification evidence. Await
worker handoffs before coordinator regression verification.

The operator also requested [v0.1.8 local-model Spike 6](../../../v0.1.8/spikes/README.md#spike-6--local-models-through-ollama-and-agent-harnesses).
It records Ollama versus external-harness integration, per-goal evaluations,
hardware/runtime behavior and explicit privacy/fallback rules. Research and
model installation are deferred until after Scout; no v0.1.7 scope expansion.

### Package F1 correction accepted

Luna completed `task_0a6dcda5d603`. Astra read the
[correction handoff](SCOUT-03-C3-package-correction.md), inspected the root
guard and seven manifest regressions, and reran only the package privacy file:

```text
.venv/bin/python -m pytest -q tests/test_scout03_package_privacy.py
20 passed in 3.18s
```

The correction closes Claude's F1 at the bounded canonical-path scope:
correctly inventoried private manifests now fail actual inspect/export/install
before destination publication; inert Scout source remains covered. This is
coordinator correction acceptance following Claude's review, not a fresh Claude
re-review or whole-C3 acceptance. The worker was released (retained external
terminal) before completion delivery ACK. Terra's tool task remains assigned;
no terminal polling or duplicate full-suite verification was performed.

### Tool bridge delivered; independent review active

Terra completed `task_d856622567d0`. Astra read the
[handoff](SCOUT-03-C3-tool-implementation.md) and bridge source, released the
settled worker (retained external terminal), then acknowledged delivery.
The supported entry accepts an agent-supplied typed `record_create` operation;
it does not execute the inventoried Python entry. The real-tool fixture and
approved source/operation authority obligations therefore remain review gates,
not implicitly completed by the worker's completion notification.

The two supplied schema hashes were independently recalculated and refreshed
in `SHA256SUMS` and `tools/verify_installed_schemas.py`. Resource verification
passes 55 schemas in the current development environment; no wheel was built.

Fresh Claude review: `task_d60805cf7fcf` / `ctx_4e5220d9b466`, terminal
`term_db735b6d-7327-4ed0-b8c2-e047b63992d2`. Sole writable file:
`SCOUT-03-C3-tool-review.md`. The reviewer is instructed to inspect contract
coverage, authority, strict schemas and publication-time checks without
rerunning suites; only narrow suspected-defect probes are allowed. Coordinator
owns the new-tool and central-schema regression run. No whole-C3 acceptance,
provider execution or default-tool shipping is claimed.

Coordinator focused verification completed:

```text
.venv/bin/python -m pytest -q tests/test_scout03_c3_tools.py research/contract_spike/tests/test_schemas.py
14 passed, 109 subtests passed in 31.39s
```

Passing these cases does not close the independent review's contract and
adversarial-coverage obligations. No full suite or existing native-input suite
was rerun by the coordinator for this handoff.

### Tool review received; bounded corrections dispatched

Astra read the full [Claude review](SCOUT-03-C3-tool-review.md). Verdict:
partial service seam, not the real Gig-owned fixture-entry acceptance and not
whole C3. F1 requires a committed mid-flight mutation regression; F2 requires
the actual tool-owned entry; F3/F4 concern approval consistency and source
identity; F5 requires precise schema-compatibility disclosure. The reviewer
was released (retained due to user takeover) before delivery ACK. Luna's
co-delivered Qwen evaluation heartbeat was acknowledged without interruption.

Evidence discrepancy: the review's Method section records an 8-test rerun,
while its completion message and out-of-scope section say no suite reruns.
Do not repeat the latter as established provenance; the coordinator's separate
14-test/109-subtest run above is the unambiguous recorded verification.

Terra correction task `task_89ba4c9ada2a` addresses F1/F3/F4 and implements
a minimal explicitly invoked Gig-owned fixture entry for F2. Template shipping
and general initialization remain SCOUT-05. It also investigates a coordinator
source-level concern: the public native tool publisher accepts both the
claimed binding and caller-provided revalidator, and currently compares their
outputs rather than itself deriving committed authority. A forged callback
must be tested before the review's no-bypass claim can be accepted. This is
not a claim of an OS sandbox or a confirmed exploit reproduction yet.

Ownership is limited to tool/native/capability integration, dedicated tool
regressions, a minimal adapter/fixture and `SCOUT-03-C3-tool-corrections.md`.
Central schema inventories remain coordinator-owned. Only focused regressions
and lint are requested; no full suite, unrelated native-input rerun, provider
execution, private-data access or broad schema redesign.

### End-of-Scout v0.1.7 addition — RUNTIME-01

On 2026-09-09 the operator explicitly added
[RUNTIME-01](../../../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md)
to the release: GigAI-owned local Ollama execution and a comparison runner for
Qwen/local harness versus Luna/Codex. Scout owns the first cases and domain
grading expectations, not the backend. The user explicitly wants to compare
complete execution setups rather than isolated models.

Delivery sequence is SCOUT-11, then RUNTIME-01, then final SCOUT-12 installed
release proof. Detailed contract review precedes runtime activation; no worker
is launched or redirected for this future dependency now. The initial local
pilot is supporting evidence only; known weak grading must be addressed before
the installed comparison is accepted. Separate attempts preserve exact inputs,
outputs, failures and honest quality/time results without implicit private-data
forwarding or application-state changes. Broader v0.1.8 Spike 6 research remains
deferred; its earlier all-integration-deferred wording is superseded for this
narrow delivery only.

### Tool corrections delivered; focused re-review active

Terra completed `task_89ba4c9ada2a`; Astra read
[the correction handoff](SCOUT-03-C3-tool-corrections.md), released the settled
worker (retained external terminal), and acknowledged delivery. The handoff
reports 19 focused tests passing in 39.89s, publisher-owned authentication
instead of caller callbacks, approval consistency/identity checks, a committed
mid-flight mutation regression and a real fresh-process fixture entry. No new
schema bytes were introduced.

Coordinator independently inspected the three new integration regressions and
ran only those cases:

```text
.venv/bin/python -m pytest -q tests/test_scout03_c3_tools.py -k 'change_after_dispatch or fabricated_binding or fresh_process'
3 passed, 16 deselected in 12.57s
```

Development resource verification still passes 55 schemas; `git diff --check`
passes. No full suite or wheel build was repeated. Fresh Claude re-review
`task_29ee3ba9ce98` / `ctx_0802d48a4ae7` is active in
`term_b22b678d-0e30-4ae0-8c25-a7504907dbfc`, with sole writable report
`SCOUT-03-C3-tool-corrections-review.md`. It is explicitly prohibited from
rerunning pytest; review is focused on F1–F5, publisher authority and the new
entry loader. Correction acceptance and SCOUT-05 distribution remain open.

### Tool correction accepted; SCOUT-05 implementation wave

Astra read the complete [independent re-review](SCOUT-03-C3-tool-corrections-review.md):
accepted for the reviewed library/real-fixture seam, with no remaining scoped
blockers. Combined with the already recorded coordinator regressions, this
closes the bounded correction, not whole C3. Source-only review does not prove
an OS sandbox or stronger executed-byte provenance than the loader provides;
the review explicitly notes the pre-load/read race limitation. No new tests
were run to process this verdict. The reviewer was released with archived
transcript and its completion delivery acknowledged.

SCOUT-05 now has two disjoint implementation lanes:

- Terra `task_0fdd4e360ea2`: general username-aware init, owner/default-instance
  registry integration, journaled bindings, pinned recoverable batch, real
  multi-default/interruption fixtures, and unchanged unrelated package/user
  state. Runtime init/registry/package/callers belong to this worker; settled
  tool/native service and Scout source assets are not its edit scope.
- Luna `task_aadad30ca81a`: copied `data/scout/gig.py` and supporting tools,
  separate scaffold tests/evidence, actual fresh-process read/list/context
  and approved create path where supported. No runtime/init/schema edits;
  no bypass through operator APIs to fake agent-authorized update/archive.

Coordinator integrates source inventory and central schema resources after
handoffs. Current `scout_source_files()` is the stable shared input until then.
Full CRUD, root-wrapper authority, graph/proposal wiring and release-eligible
default promotion remain explicit SCOUT-05 integration gates. A partial
create/read scaffold is not the final product. Unfinished graphs cannot be
advertised as release-ready; no actual user initialization, provider execution
or default activation is performed by development dispatch.

### Copied scaffold delivered; owning-Gig correction required

Luna delivered [the scaffold handoff](SCOUT-05-tool-scaffold-implementation.md),
reporting five fresh-process tests in 19.67s and scoped lint passing. Astra read
the report, wrapper source and fixture setup. No coordinator suite rerun was
performed. The worker was released (retained external terminal) before delivery
ACK; source-inventory integration is held until the correction below settles.

Coordinator finding: all wrapper operations omit `gig_id`, so the shared
resolver uses project active selection instead of the copied script's owning
Gig. Two independent-copy tests use separate projects/homes and do not exercise
this cross-Gig case. Copy A could read/write B when B is active. Init also does
not select a Gig, so reliance on active selection would break normal first use.

Luna correction task `task_19d47f8418ec` binds the wrapper's location to an
existing registered/validated workpad and passes that exact Gig identity to
services. It must reject mismatched target/home or redirected/foreign source
without switching active selection. New fresh-process tests require two Gigs
in the same project and include absent/different active selection. Scope stays
in the wrapper, scaffold tests and correction evidence; Terra retains init and
registry ownership. Full CRUD and pinned root-wrapper source integration remain
explicit follow-on gates, not accomplished by this correction.

### Wrapper identity correction delivered

Luna completed `task_19d47f8418ec` / `ctx_ffe8363355eb`; Astra read
[the handoff](SCOUT-05-tool-scaffold-correction.md) and owning-workpad resolver.
The wrapper now selects its own exact registered path/Gig, validates the
project target and passes the explicit Gig ID to services instead of using
active selection. Worker evidence reports eight scaffold tests in 32.48s,
including two Gigs in the same project with B active or no active selection.
This is reported verification, not yet coordinator integration acceptance.

The settled worker was released (retained external terminal) before delivery
ACK. Terra was notified; its frozen source-inventory input remains unchanged
until coordinated integration. Independent scaffold review and coordinator
verification are held for that integration checkpoint, avoiding tests against
in-flight shared init/registry changes. Full CRUD and approved root-wrapper
source binding remain open.

Provenance clarification: the handoff calls the earlier workpad syntax issue
coordinator-owned. The file was actually owned by Terra's active init task;
Astra changed no source, confirmed the current file compiled, and notified both
workers. No source repair or full-suite run by Astra is implied.

### Init delivery checkpoint and full-CRUD wave

Terra completed init task `task_0fdd4e360ea2` / `ctx_5b5b161ca334`.
Astra read [the implementation handoff](SCOUT-05-init-implementation.md) and
the batch publication implementation. The worker was released before delivery
ACK and subsequently reused for the independent CRUD implementation below.
The current init returns prepared bindings; actual source/proposal preparation
and the remaining owner/inventory/recovery contract obligations require review.
Worker completion does not establish SCOUT-05 acceptance.

Astra added the copied `gig.py` to `scout_source_files()` and added exact-byte
source and inspected-package assertions. The coordinator checkpoint
`tests/test_scout05_init.py tests/test_scout05_source_bundle.py` passed:
**23 tests in 7.70s**. This is source-checkout fixture verification, not an
installed-wheel test, wrapper CRUD acceptance, or real user initialization.
Scout remains an unpromoted catalog candidate.

Parallel dispatches, both confirmed `ready` / `input_accepted`:

- Claude `task_d73b104039cd` / `ctx_b1328c89c876`: independent init review;
  frozen init/registry/workpad/journal/CLI files, no suite reruns, only a new
  review artifact. Check actual owner pins, cached/journaled identity,
  interrupted batches, migration, path safety, strict data contracts and
  prepared source/proposals against the accepted amendment.
- Terra `task_c0b1e8adcb94` / `ctx_05c4578b3b66`: implement approved tool
  update/archive and copied-wrapper calls with publisher-owned revalidation,
  exact owning Gig, CAS and replay protection. Tool/native/capability files and
  dedicated CRUD tests belong to this lane; init files are excluded.

Coordinator retains source inventory and central schema hash/resource integration.
The CRUD handoff requires fresh independent review and a focused combined
checkpoint after edits settle. No private records, actual init, providers,
activation, full-suite rerun, commit or release was performed by this checkpoint.

### Init changes requested; CRUD delivered for review

Astra read both [the init review](SCOUT-05-init-review.md) and
[the CRUD implementation handoff](SCOUT-05-tool-crud-implementation.md).
Both workers reported completion; this does not mean implementation acceptance.
Release was requested before delivery ACK. Orca retained the init reviewer due
to user takeover (no process action); the CRUD worker was retained as an external
terminal and then reused for the correction task below.

- Init: two blockers (fresh v1 layout, lost typed CLI diagnostics), plus owner
  retry integrity, snapshot-conflict handling, Unicode validation and explicit
  migration authority require correction. Lower-severity suggestions require
  source confirmation: in particular, resetting an in-memory intent does not
  itself remove the durable file. Preserve atomic publication and legitimate
  Unicode names rather than mechanically applying every suggested fix.
- CRUD: worker reports create/update/archive through approved entries, CAS,
  exact replay, non-destructive tombstones and owning-Gig isolation, with
  focused verification. This evidence has not yet received independent
  acceptance; no coordinator CRUD suite rerun is claimed.
- Coordinator recomputed the receipt schema digest, updated `SHA256SUMS` and
  the verifier inventory, then verified all **55 schema resources** in the
  current Python environment. This is not isolated-wheel release proof.

New parallel tasks, confirmed `ready` / `input_accepted`:

- Terra `task_ecacaf402346` / `ctx_c9260a9e9ee3`: init corrections and focused
  regression evidence. Owns init/registry/workpad/target/journal/init CLI;
  settled tool and wrapper files are excluded.
- Fresh Claude `task_281b129dc440` / `ctx_30d2679825ad`: independent read-only
  CRUD review, no suite reruns. Owns only the review report; moving init files
  are excluded.

Actual source copying, prepared proposals, explicit default eligibility,
registered binding schema and approved root-tool source integration remain
SCOUT-05 release obligations. Describing them as deferred from this review
slice does not defer them beyond v0.1.7 or mark SCOUT-05 complete.

### CRUD independent review accepted, scoped

Astra read [the full CRUD review](SCOUT-05-tool-crud-review.md): scoped accept,
no blocking CRUD finding. The basis is source inspection plus recorded worker
verification, not a fresh successful reviewer run. The reviewer attempted one
pytest probe despite the no-suite-rerun instruction; collection failed on an
in-progress `target_binding.py` edit in Terra's excluded init lane. No repair
or additional test run is made against that active edit. Terra was notified;
the combined coordinator checkpoint waits for its settled handoff.

Two nonblocking observations are retained: the eventual tracker must distinguish
the tombstone action's agent origin from the original record's provenance;
publication authority revalidation currently uses inert record placeholders,
which must not acquire record-scoped semantics without using the actual target.

Accuracy correction to the review's open-gates paragraph: `gig.py` **is** already
in `scout_source_files()` and its package candidate byte inventory, as recorded
by the earlier 23-test coordinator checkpoint. Actual per-instance source copy,
approved root execution-source binding and installed proof remain open. The
review wrote its report; its final "No files modified" wording means no runtime,
test or fixture modifications, not absence of that evidence file.

Claude `ctx_30d2679825ad` was released before delivery ACK; Orca retained it
for user takeover and performed no process action. Terra remains the sole
implementation owner of the current init-correction lane. Whole SCOUT-05 is
not accepted by this bounded CRUD verdict.

### Coordinator verification and identity-recovery correction wave

Terra's first init correction task completed and was released (retained external
terminal) before delivery ACK. Coordinator verification, requested by the user:
59 tests passed in 119.44s across init, init corrections, CRUD, wrapper scaffold,
source bundle and C3 tools; scoped lint/compilation and 55 schema resources passed.
No full suite or isolated wheel was run.

Two independent disposable probes found remaining runtime defects:

- After a successful init, deleting only the template-instance cache causes
  repeated init to create another Gig instead of recovering the existing
  committed binding (two workpads observed).
- Updating the same bundled template's version/source causes
  `template_instance_conflict`; existing bindings are incorrectly compared to
  current upstream bytes rather than their original committed provenance.

Two targeted legacy migration tests failed in 0.30s: one requires current
schema 2, the other assumes read-only open implicitly migrates. Fix their
contract expectations while retaining real backup/recovery assertions.

New parallel dispatches, both `ready` / `input_accepted`:

- Terra `task_14782dc13b87` / `ctx_bad8df141822`: init identity/cache recovery
  and update availability, dedicated regression tests; no registry/tool edits.
- Luna `task_4a29ee828f00` / `ctx_6b87d3ac5693`: explicit v3 migration test
  alignment and retained failure/crash/concurrency proof; no production edits.

The next implementation breakdown is
[source and first-proposal integration](SCOUT-05-source-proposal-integration-plan.md).
It records the actual first-proposal gap: the current Graph Set stage caller
requires an approved predecessor. Initialization must not manufacture that
approval to reuse a fixture workflow. Source/proposal implementation remains
queued; these correction tasks are not whole-SCOUT-05 acceptance.

### Migration regression handoff and narrow coordinator correction

Luna completed `task_4a29ee828f00` / `ctx_6b87d3ac5693`; its
[handoff](SCOUT-05-migration-regressions.md) reports 37 passing migration
tests in 0.89s and scoped lint/compilation. Astra read the full diff and
released the worker (retained external terminal) before delivery ACK.

The new legacy-v2 reader helper had no test caller despite the report's coverage
claim. Astra added a real predecessor-open/current-refusal test, including
unchanged bytes on refusal. The concurrent second-opener test was also restored
to `allow_migration=False`: after the migrating writer releases its lock, the
reader must open the complete v3 registry without needing migration authority.
Both changed cases passed in **0.24s**. The full migration suite was not rerun
by the coordinator; final combined acceptance waits for Terra's init handoff.

Terra `task_14782dc13b87` remains the active init identity/update implementation
lane. No other worker was started, and no production file changed in this
migration handoff processing.

### Identity corrections verified; first-proposal implementation next

Terra completed `task_14782dc13b87` / `ctx_bad8df141822`; Astra read
[the handoff](SCOUT-05-init-identity-corrections.md), four new regressions,
historical binding/cache comparison, durable-intent validation and publication
flow. Discovery now resolves committed bindings through project workpad
locators; cache loss no longer determines a fresh identity. Incoming upstream
bytes are compared for update availability, not substituted for historical
binding provenance. The worker was released before delivery ACK.

Coordinator combined verification after all edits settled:
`test_scout05_init.py`, `test_scout05_init_corrections.py`,
`test_scout05_init_recovery.py`, and `test_registry_v2_migration.py`:
**56 passed in 23.40s**. Scoped lint passed. This closes the two reproduced
identity/update defects and the bounded migration-regression integration;
it does not imply full release, installed-wheel or complete bootstrap acceptance.

Next Terra task `task_0ffd805551a4`: implement the first Graph Set proposal
service and explicit-Gig approval/CLI targeting, retaining existing amendment
callers. Scope is lifecycle and proposal/approval CLI plus dedicated tests;
init/storage/tool/source files remain stable. No approved predecessor may be
manufactured, and no active selection may be changed to make fixtures pass.
Actual editable software copying and init-to-proposal wiring follow this
service. The worker must distinguish first Gig version from schema-family
version and report downstream assumptions rather than fake an approval history.

### First Graph Set proposal delivered for independent review

Terra completed `task_0ffd805551a4` / `ctx_c0af641e447f`. Astra read
[the implementation handoff](SCOUT-05-first-proposal-implementation.md);
it reports five focused new/amendment cases passing in 10.26s (the notification
rounded/reported 10.24s). This is worker evidence, not a coordinator rerun.
The worker was released (retained external terminal) before delivery ACK.

Delivered callers are `propose_first_graph_set_offline`,
`graph-set propose --first --gig`, and optional explicit Gig selection for
approval. A fresh Graph Set proposal uses the v2 schema family but correctly
becomes numeric Gig version 1 when separately approved. The existing receipt
minimum of 2 is therefore a known integration gate, not permission to invent
an approved predecessor or skip directly to version 2.

Fresh Claude task `task_fe0e7790ecc2` reviews frozen lifecycle/CLI changes,
source identity, bounded path handling, lock-held publication, exact replay,
explicit target/approval and amendment compatibility. No pytest reruns; report
source hashes and distinguish recorded evidence from any necessary disposable
probe. Actual source materialization, batch wiring and Scout graph bodies
remain subsequent work; no whole-SCOUT-05 acceptance is recorded.

### First-proposal review disposition and source-integration wave

Astra read [the independent first-proposal review](SCOUT-05-first-proposal-review.md).
Verdict: approve staging, bounded, with five low/nit findings; no reviewer
pytest run or probe. The worker was released before ACK; Orca captured its
transcript and closed the exact completed reviewer terminal.

Coordinator qualification: the definition path is resolved before checking
`is_symlink()`, so the review's blanket source-root safety claim is not
accepted as proof that a symlinked original definition is rejected. Luna must
exercise and correct this original-path boundary rather than merely restating
the guard. F1/F2 negative tests, F3 typed canonicalization failure and F4 pure
revalidation are included in the follow-up; F5 remains a sequential-compatibility
observation. No artificial version increment is an acceptable remedy for the
known first-version tool-receipt integration gate.

Parallel tasks:

- Terra `task_80cdedc3c043`: real source materialization/compiler and init
  wiring to pending proposals, using accepted v2 paths and pinned recovery.
  Owns default init, dedicated template modules, Scout source assets and init
  response fields; strict new binding/schema needs are handed to coordinator
  for central registration/hash integration. No lifecycle edits or promotion
  of unfinished Scout graphs to release-eligible defaults.
- Luna `task_208578a3ad82`: first-proposal robustness and negative tests.
  Owns only lifecycle source-validation/revalidation helpers and first-proposal
  tests; public service API stays stable for Terra. No init/materialization,
  registry/workpad/journal or tool/schema edits.

No coordinator tests were rerun during this handoff. Final review and combined
verification follow settled implementation artifacts, not edits in progress.

### First-proposal robustness delivered

Luna completed `task_208578a3ad82` / `ctx_d4127c5705cf`; Astra read
[the handoff](SCOUT-05-first-proposal-robustness.md) and the original-path and
locked source-revalidation implementation. The worker reports 13 first-proposal
tests passing in 18.00s, scoped lint and compilation. No public API change.
The worker was released (retained external terminal) before delivery ACK.

The implementation checks the original definition path before resolution,
rechecks original path identity under the journal writer lock, handles exact
known macOS system aliases, and uses a pure member verifier during revalidation.
New negative cases cover path/link rejection, replay working-copy/receipt
tamper and canonicalization errors. This corrects the previous review's
overstatement about the pre-existing symlink guard; no new coordinator test
result or independent re-review is claimed yet.

Terra was notified of the settled helper/API and remains active on source
materialization and init wiring. Combined verification waits for that handoff.

### Source/init delivery and central schema integration

Terra completed `task_80cdedc3c043` / `ctx_79b914dbb4ed`; coordinator read
[the implementation handoff](SCOUT-05-source-materialization-implementation.md),
released the dispatch (external terminal retained), and acknowledged completion.
The candidate path now copies editable source, journals inert software snapshots,
and stages a real pending first Graph Set without approval or active selection.
This is not default-inventory promotion or whole SCOUT-05 acceptance.

Coordinator registered `template-instance-binding:2` in the central registry,
56-resource inventory, digest verifier and golden/negative fixtures. Integration
found and corrected the invalid common-schema package-ID reference by reusing
`gig-package:1#/properties/package_id`, and fixed the username control-character
constraint (C0/C1 including trailing newline, preserving legitimate joiners and
private-use characters). Final schema SHA-256 is
`23b99113a000faabbce21c2b97e8734ea8baec2abbc887f5d6e1f1321d12ed40`;
this supersedes the original worker handoff hash and registration TODO.

Central checks: 7 tests plus 134 subtests passed in 0.30s; 56 schema resources
verified in the existing source environment; scoped Ruff passed. This is not
isolated installed-wheel proof. Worker notification and handoff report slightly
different focused timings (12.85s versus 12.87s); those remain worker-reported.
Coordinator combined focused verification follows the now-settled source edits.
Independent integration review is assigned to `task_832e04fbdd6d`; no duplicate
suite reruns requested of the reviewer.

Combined coordinator checkpoint completed: **89 passed in 51.68s**, covering
`test_scout05_materialization.py`, `test_scout05_first_proposal.py`,
`test_scout05_source_bundle.py`, `test_scout05_init.py`,
`test_scout05_init_corrections.py`, `test_scout05_init_recovery.py`, and
`test_registry_v2_migration.py`. Scoped implementation/test Ruff and
`git diff --check` also passed; no full-suite or installed-wheel claim.
Claude review dispatch `ctx_628eff6ff33d` reached `ready/input_accepted` in
terminal `term_e63e71b4-1c62-49c5-b992-24fd4254f929`; review remains active.

### Candidate review accepted; next implementation wave

Claude completed [the integration review](SCOUT-05-source-proposal-integration-review.md):
the bounded candidate source-to-pending-proposal slice is accepted, not whole
SCOUT-05. R1 is a low-severity missing runtime schema-validation call; R2/R3
are documentation and committed multi-publisher coverage gaps. Coordinator read
the report, released the reviewer with captured transcript, and acknowledged
completion. No suites were rerun for that review.

After the user requested continuation, two implementation dispatches reached
`ready/input_accepted` (2026-09-10 UTC):

- Luna `task_0fee1d70954b` / `ctx_693eecebab74`: R1–R3 corrections. Owns
  `default_init.py`, schema README, materialization and first-proposal tests,
  and `SCOUT-05-integration-review-corrections.md`. No lifecycle/materialization
  source or schema-byte changes without coordination.
- Terra `task_e4356f783f21` / `ctx_2e6aeaa3229a`: first-version tool compatibility.
  Owns operation-receipt schema, narrowly necessary tool/native service edits,
  new `test_scout05_first_version_tools.py` and
  `SCOUT-05-first-version-tools-implementation.md`. Prove copied-wrapper CRUD
  using real candidate init and separate explicit approval at numeric version 1;
  preserve schema-family/capability/effect checks. A fixture inventoried tool is
  permitted but must not be reported as shipping the real executable bundle.

Coordinator owns central hashes/fixtures after Terra's schema handoff. Workers
run focused tests; coordinator verification follows stable handoffs. These lanes
do not approve user Gigs, promote Scout to default eligibility, or run providers.

### R1–R3 corrections delivered

Luna completed `task_0fee1d70954b` / `ctx_693eecebab74`. Coordinator read
[the correction handoff](SCOUT-05-integration-review-corrections.md) and the
registered-schema validation helper and write/read call sites. The worker
reports 21 materialization/first-proposal tests passing in 31.95s, scoped Ruff
and compilation. Tests add malformed-binding refusal and real disposable
committed multi-publisher conflicts; no schema bytes changed.

The settled worker was released (external terminal retained) before completion
ACK. Terra was notified and continues first-version tool integration. No
coordinator suite rerun occurred during Terra's edits; correction verification
and combined acceptance remain pending that handoff.

### First-version receipts integrated; shipped executable assets next

Terra completed `task_e4356f783f21` / `ctx_2e6aeaa3229a`. Coordinator read
[the handoff](SCOUT-05-first-version-tools-implementation.md) and all five new
tests. Production change is the receipt tool-binding numeric-version floor
from 2 to 1, not a change to v2 active-schema/capability/effect authority.
Real candidate init, separate explicit first approval and copied-wrapper CRUD
are exercised, but executable entry source/manifest are still synthetic fixtures.
Released the worker (external terminal retained) and acknowledged completion.

Coordinator verified receipt SHA-256
`2c45e5d2954abaf625728cb59013f969acdfe31511d0c29b49c80b9026126872`, refreshed
SHA256SUMS/verifier, and added central schema regressions for first/later
versions, invalid numeric/type values and strict required binding fields.
Combined settled-source checkpoint: **34 passed, 153 subtests passed in 56.96s**
across materialization, first-proposal, first-version-tool and central schema
tests. Scoped Ruff, source-environment 56-schema verification and diff checks
passed. This independently exercises Luna's R1–R3 fixes together with Terra's
receipt fix; it is neither a full-suite nor isolated-wheel result.

Next task `task_98e9c05ae89f` prepares real shipped CRUD assets and an unapproved
capability manifest using actual compiled goal IDs. Terra owns bundled assets,
template/materialization preparation and new bounded tests. Wider CLI/lifecycle/
capability/schema changes require coordination. Init must stay inert and cannot
invent security approval; root-wrapper source binding must be resolved honestly,
not treated as approved merely because the wrapper was copied. No eligibility
promotion or live user approval is authorized by this task.

Dispatch `ctx_d338c0c95fd7` reached `ready/input_accepted` in Terra's existing
terminal `term_b0c12c93-faa5-4912-9c14-fb6d1c399699` after the combined tests
finished. This is the active implementation worker; no reviewer is running.

### Bundled-tools attempt blocked; root-wrapper scope resolved

`task_98e9c05ae89f` / `ctx_d338c0c95fd7` settled **failed**, with no implementation
changes. Terra asked `msg_54159b332b2e` whether the inventory could include the
executed root `gig.py`; existing schemas admitted only capability-subtree paths.
The coordinator did not process that question before the worker escalated and
settled. This was an unanswered ownership decision, not a completed bundle or
a runtime transport failure. Reply after settlement returned `dispatch_inactive`;
the decision is supplied in a fresh task, not by reviving stale lifecycle IDs.

Coordinator inspected the accepted actual-source contract and current callers.
Authorized the narrow extension: literal root `gig.py` as an explicit inventory
member, with the executable tool entry still under its selected capability
subtree. No arbitrary root/foreign paths; preserve canonical closed inventories,
exact bytes, source safety and dispatch plus writer-lock revalidation. Historical
tools-only bindings retain their existing scope without gaining root-wrapper
approval. This is supported-mutation provenance, not a Python sandbox.

Fresh task `task_edc6827b09cd` carries that decision and expanded schema/tool
ownership, while retaining the original inert-bundle objective and all separate
review/approval boundaries. Coordinator still owns central hashes and fixtures.

Fresh dispatch `ctx_3557ac364379` reached `ready/input_accepted` in the same Terra
terminal. This immediate reuse transfers ownership from the settled attempt;
no duplicate worker or process restart was needed. Old failed completion and
its closed question were processed together before delivery acknowledgement.

### Existing-binding derived-manifest recovery scope

Answered Terra question `msg_a032f19e3a0b` while dispatch
`ctx_3557ac364379` remains active. Authorized a narrow `default_init.py`
recovery caller and focused tests: authenticate existing Scout v2 binding,
preserve exact pinned source/package/proposal identity, and restore missing
derived unapproved capability material from committed software snapshots.
Do not adopt an available upstream update, overwrite conflicting/reviewed/
approved material, or restage an approved proposal. Historical binding metadata
alone is not current approval authority. If the full materializer cannot uphold
these constraints, use a narrow derived-manifest repair helper. Tests must cover
missing-file recovery, customization, upstream change and rerun after approval.

### Inert bundled-tool handoff and parallel integration review

Terra `task_edc6827b09cd` / `ctx_3557ac364379` completed
[bundled-tool preparation](SCOUT-05-bundled-tools-implementation.md), with real
CRUD source assets, exact root-wrapper inventory, pending capability preparation
and narrow recovery. Worker reports bundled tests 6/6, first-version tests 5/5,
materialization tests 7/7, scoped lint/compile. It explicitly does **not** claim
successful bundled CRUD after real capability review: that public caller remains
to be established. Coordinator read the handoff, released the external worker
and acknowledged completion; no implicit approval or default promotion occurred.

Coordinator verified and registered schema hashes:

- capability manifest: `2d36b8e0552c810f1ec17e50d4edbc68be39cc1572c0f535073bf7f59731b5a7`;
- operation receipt: `a387d8d258dda7f86612e52b4698990062de8ae5ee846f4d3f154a91427e1bcd`.

Central tests now cover literal root reference presence, strict nested shape,
root/path refusals and historical tools-only shape compatibility. **9 tests,
173 subtests passed in 0.33s**; 56 source-environment schemas verify and scoped
Ruff passes. No isolated installed-wheel proof. A focused combined integration
checkpoint is running on stable production source.

Both next dispatches reached `ready/input_accepted` on 2026-09-10:

- Claude `task_01769ab72731` / `ctx_ae24895d9876`: read-only bundled-tool review,
  sole report `SCOUT-05-bundled-tools-review.md`; no suite reruns.
- Luna `task_acf2751fb8a9` / `ctx_ddfcbd224a23`: read-only generic capability
  review/consent caller audit and bounded implementation packet, sole report
  `SCOUT-05-capability-consent-caller-audit.md`. Verify actual existing callers,
  not just the handoff's assertion of a missing path; no production edits yet.

Coordinator combined checkpoint completed: **38 tests passed in 76.44s**,
covering bundled tools, materialization, source bundle, init recovery,
first-version tools, and the historical copied-wrapper create/update/archive
CAS/replay regression. This is a focused integration result, not a full-suite
or wheel claim. Claude received the result; review and Luna's caller audit
remain active, with no production implementation edits during this checkpoint.

### Bundled preparation accepted; consent service implementation active

Claude completed [the bundled review](SCOUT-05-bundled-tools-review.md), accepting
inert preparation with F1/F2 low and F3/F4 informational. Coordinator read it,
released the reviewer, and acknowledged completion. F1 full-manifest golden
coverage is added (central 10 tests/179 subtests pass); F2 truthful post-review
init status is retained for the consent-caller integration. No repeated review
or full-suite cycle was launched for these non-blocking findings.

Luna completed [the caller audit](SCOUT-05-capability-consent-caller-audit.md).
Coordinator read it, released the external audit worker, and assigned fresh
task `task_f1488274c56f` / `ctx_ba22a943bea4` to the same Luna terminal; dispatch
reached `ready/input_accepted`. Ownership: new `capability_review.py`, a narrow
review-decision schema if needed, new focused tests and service handoff. Root
owns shared transition/identity/schema registration. No existing capability,
lifecycle, CLI or bundled-source edits without coordination. The service records
supplied reviewer evidence and separately explicit operator effect consent;
it cannot activate a Gig or grant execution simply by inspecting source.

Coordinator also reproduced and corrected a wheel package-data omission and
verified installed assets/candidate preparation without editable source hooks.
Exact results and dependency-isolation limits are in
[the integration checkpoint](SCOUT-05-bundled-tools-integration.md). This adds
bounded wheel proof, not full public-CLI onboarding or reviewed bundled CRUD.

### Reviewed successor and role-research output wave

The review service and public review CLI have settled after bounded fixes;
see [coordinator integration](SCOUT-05-bundled-tools-integration.md) for
combined and later focused evidence, including the nine existing `cli.py`
lint findings. No new full-suite run is planned while workers edit.

- Luna `task_8942da73ba36` / `ctx_507a10f05526`: new capability-successor
  service/sidecar/tests and bounded lifecycle/journal approval/recovery
  integration. Must prove actual shipped `gig.py` CRUD after separate approval,
  without republishing already committed reviewed manifests. Root registers
  the sidecar schema/hashes/goldens and `capability_successor_prepared`.
- Terra `task_9b8f0674772c` / `ctx_edc0c37e6fed`: NEW candidate research.py
  and research.schema.json under the Gig-owned source tool directory, with
  pure role-research packet validation/rendering tests. Existing explicit
  source inventory, bundle and core runtime stay untouched until the
  successor lane settles. Publication and Run handoff wiring are still needed.

Both exact terminals reached `ready/input_accepted`. The two scopes share no
writable file. Role research can advance without treating incomplete SCOUT-05
or these new candidate files as release-ready. RUNTIME-01 remains required
after Scout implementation and before SCOUT-12 release proof.

### Current wave — containment correction and research persistence

User direction on 2026-09-10: continue implementation toward a reviewable
release candidate; the user will perform code review and UAT before release.
This authorizes continued implementation/verification, not publication or
fabricated operator acceptance. SCOUT-12 remains open until that human gate.

| Owner | Current task / dispatch | Exclusive write scope |
| --- | --- | --- |
| Terra | `task_7c7938e4bd6f` / `ctx_a91022f209c4` | Capability matching helpers, guard regressions, new containment evidence. Replace whole-list equality with authenticated per-capability containment; preserve legacy trust limits. |
| Luna | `task_1eadff1cf7ad` / `ctx_18dd1e042997` | New `SCOUT-06-persistence-integration-plan.md` only. Resolve exact domain-packet/external-envelope integration design; runtime/source read-only. |
| Astra | Coordination, contract reconciliation, shared registration and evidence | No competing source edits in either lane; implementation tasks follow the concrete persistence plan. |

Both workers confirmed `ready/input_accepted`. No coordinator suite runs while
Terra edits. Reviewers must not duplicate the implementer's/coordinator's
test suites; use bounded independent source review/probes. The most recent
22-test/189-subtest checkpoint predates the new containment correction.

The remaining domain work will be parallelized by file ownership after the
research persistence interface is resolved: research/discovery, tailoring,
application events, and report consumers. No unfinished default is promoted
merely to unblock a downstream fixture. RUNTIME-01 stays required before the
installed release-candidate handoff, and tag/publish/merge remain separately
authorized actions.

### Wave rollforward — 2026-09-10 17:40 UTC

The containment correction/review settled and was accepted within its frozen
legacy boundary; its workers were released. Pure research input corrections
also settled: coordinator verification passed 48 tests in 0.13s.

- Luna `task_c9c4bfffa3b8` / `ctx_6fb587ea8145` implements the generic
  external domain-evidence channel: `external_recording.py`, optional new
  `external_domain_artifacts.py`, three new external-recording v2 schemas,
  focused tests and evidence. Existing v1 resources remain unchanged.
- Terra `task_edab0a264d88` / `ctx_967964151ab8` owns only the new
  `SCOUT-06-input-mapping-design.md`: exact sealed role-request provenance,
  without inventing native role records or relabelling job postings.
- Astra owns central schema registration/hashes/goldens, the new fixed
  `scout_research.py` bridge, and later source/compiler integration. Shared
  interface decisions go through explicit Orca question/reply messages.

Both current workers reached `ready/input_accepted`. Three v2 resources are
registered (61 total); schema shape corrections and goldens remain in progress,
so this is not an installed-schema or runtime acceptance claim.

### Wave rollforward — 2026-09-10 18:00 UTC

The preceding generic-channel and candidate-bridge tasks settled and were
released. Coordinator combined checks passed 95 tests in 0.59s, then 24
transport/schema tests plus 203 subtests in 0.67s. Source inventory is now
candidate definition 1.1/compiler 2; it is not a live default.

| Owner | Current task / dispatch | Exclusive scope |
| --- | --- | --- |
| Luna | `task_2a6b8217ec90` / `ctx_a4ab3daf0646` | `external_recording.py`, `external_cli.py`, new real research-Run flow tests, necessary v2 external resources and handoff. Public Plan2/CLI and actual atomic checkpoint/submit using the fixed research validator. |
| Claude Opus | `task_82a8d3db1c32` / `ctx_49ada2374ea9` | Independent settled packet/bridge/role-input/source/compiler review; only new review Markdown. Active generic caller edits are outside this verdict. No duplicated suites. |
| Terra | `task_b85524dca3b8` | Independent NEW discovery.py/schema under `.074`, pure discovery fixtures and evidence. No existing source, runtime, registry or inventory edits. SCOUT-07 acceptance still waits for integration. |
| Astra | Shared schema registration/goldens and integration evidence | Root packet/source files stay frozen during Claude review. Further runtime edits are coordinated with Luna. |

Research source's `.073` Gig copy remains separate from the `.071` CRUD tool
inventory. Its original packaged resource location is still `.071`; exact
byte binding connects them. No new executable capability is approved by that
mapping. Actual journaled research and historical reuse remain required before
SCOUT-06 closure, and all user review/UAT/release limits remain unchanged.

### Wave rollforward — 2026-09-10 18:32 UTC

The packet/source review accepted its bounded scope. The public-caller worker
finished partially but could not deliver its lifecycle RPC; the coordinator
recovered its handoff, fenced that dispatch without stopping the process, and
recorded the task as failed/partial. Coordinator wiring corrections now pass
the real five-test research Run flow. See the [integration evidence](SCOUT-06-coordinator-integration.md).

| Owner | Current task / dispatch | Exclusive scope |
| --- | --- | --- |
| Luna | `task_722bebf61b6d` / `ctx_659ea43a40af` | Real research flow fixture and new adversarial tests plus handoff; no core source edits during review. Fresh Luna launch reached ready/input_accepted. |
| Claude Opus | `task_a2863549c5a3` / `ctx_a8062999b815` | Stable external runtime, CLI, graph-contract admission and core v2 schema review; read-only except its review report. Ready/input_accepted. |
| Terra | `task_4420e7666950`, pending | Planned disjoint historical research resolver and tests. Reuse attempt `ctx_7855786ae4a8` failed before task input because the terminal is on a model-switch reminder. No implementation started; terminal remains untouched. |
| Astra | Core ownership and separate discovery bridge | Core is frozen for Claude. New `scout_discovery.py` and its tests are disjoint; 19 pure bridge tests pass, no generic registration or source promotion. |

Terra's discovery correction `task_1b9ddbcc18e4` / `ctx_67e0790fcc6d`
completed and was released before acknowledgement. Its 17 tests also passed
at the coordinator. These are unreviewed candidate components, not SCOUT-07
completion. A reported source-change/publication race is under concrete
test and independent review; do not infer acceptance from the positive flow.

### Worker recovery — 18:36 UTC

The fresh Luna attempt for historical reuse also stopped before input, on a
Codex update prompt (`ctx_c737fd01e580`). Release retained the exact residual
terminal as `no_owned_resource`; no terminal was forcibly closed and no tool
update performed. After the original Luna completed hardening, its existing
session was reused successfully: `task_4420e7666950` /
`ctx_efaf9e1a8f06` reached ready/input_accepted on
`term_1520b140-d718-47fd-9a6a-59f813f10edd`. That Luna session now owns the
historical resolver; the original Terra label in the task specification is
superseded by this explicit model/ownership record. No additional launch retry
or interruption of a running worker was needed.

Hardening task `task_722bebf61b6d` is settled, **not accepted as a clean gate**:
its worker reports 16 passing tests plus one strict expected failure. Root
owns its now-stable test files and will adjudicate the race alongside Claude's
final runtime review. Luna owns only the new research-input module/tests and
its own report. Root's new protocol-replay regression file is disjoint.
