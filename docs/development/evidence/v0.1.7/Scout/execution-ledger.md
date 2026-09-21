# Scout execution ledger

> Naming: Scout replaces JSL in documentation; `SCOUT-00`–`SCOUT-12` map
> one-to-one to the historical `JSL-00`–`JSL-12` development goals. Existing
> Orca task/dispatch IDs, Run IDs, test commands and artifact hashes are unchanged.
> New user-owned-software scope is [contract accepted](SCOUT-00-user-owned-gig-amendment.md).

**Date:** 2026-09-10  
**State:** SCOUT-00/01/02 accepted; bounded SCOUT-03/04/05 foundation and
approval-guard evidence accepted. SCOUT-06 packet/source review and public
research-Run integration active; independent SCOUT-07 authoring overlaps.
Release incomplete; operator code review/UAT remain required.  
**Roadmap:** [Scout](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md)  
**Baseline:** `fda4857`, branch `karthik446/gigai-v0.1.7`  
**Operator authorization:** "alright.. sounds good let get o it!! let's go"  
**Coordinator:** Astra  
**Orca Run:** `run_43c92f4fc427`

## Scope and authorization

Implement the approved SCOUT-00 through SCOUT-12 roadmap, using tracked agent
teams, independent review, proportionate tests, and durable checkpoints.
On 2026-09-09 the operator added
[RUNTIME-01](../../../v0.1.7/goals/RUNTIME-01-local-execution-and-backend-comparison.md)
as a required GigAI-level delivery after SCOUT-11 and before SCOUT-12: local
Ollama execution and Qwen/local-versus-Luna/Codex comparison using Gig-owned
cases. Detailed contract review and implementation remain pending. Existing
SCOUT-00 and G43.1 approvals are not retroactively extended.
Do not commit, merge, tag, or publish without an explicit request. Do not
change unrelated user work, disclose credentials, or use an agent to manufacture
GigAI operator confirmation. Existing provider dogfood and human UAT gates
remain required; no release completion is claimed from fixtures alone.

The operator was asked for a ceiling on separately billed provider calls.
While pending, no such call is authorized by this ledger. Development workers
use the requested existing Codex CLI sessions; their task scope prohibits
additional provider calls. Each initial task has one bounded attempt.

## Preserved starting work

- Existing modified G43.1 goal contract and terminal handoff.
- Existing untracked `.gigai/` contents.
- New roadmap and predecessor addendum authored in this conversation.

The old Orca Run `run_7266bebaaca0` contains historical G43 tasks. It is
untouched; stale task status is not current implementation evidence. A new
Run namespaces this approved effort.

## Goal status

| Goal | State | Owner / next evidence |
|---|---|---|
| SCOUT-00 | Contract complete, including workspace amendment | Original acceptance preserved; operator-supplied independent re-review on 2026-09-08 resolves all five workspace findings; schema/caller/migration/test evidence remains downstream implementation work |
| SCOUT-01 | Correction/re-review complete under operator delegation | Original findings corrected against unchanged baseline; fresh Claude/Luna review and Terra/Sol verification/adjudication leave no accepted or deferred findings; exact execution provenance and accounting limitations in [completion evidence](SCOUT-01-G44-correction-rereview.md) |
| SCOUT-02 | Accepted — amended A02 foundation | [Completion evidence](SCOUT-02-completion.md): Claude accepted, Luna independently verified source/package, coordinator 33-case acceptance run passed; explicit downstream limits retained |
| SCOUT-03 | C1/native, bounded C3 inputs, package privacy and tool-seam corrections accepted | [Native re-review accepted](SCOUT-03-native-corrections-rereview.md); [input integration review accepted](SCOUT-03-C3-input-review.md). Coordinator M1 CLI/schema checks: 7 tests and 109 subtests in 16.39s; 55 schemas verified. [Package correction](SCOUT-03-C3-package-correction.md) accepted after 20 tests in 3.18s. [Tool correction re-review](SCOUT-03-C3-tool-corrections-review.md) accepted with no scoped blockers; coordinator race/forgery/fresh-process regressions: 3 passed in 12.57s. Shipped command/full CRUD integration, other native input families and whole-goal acceptance remain open |
| SCOUT-04 | Bounded external lane accepted; integration/UAT open | [Final R1/R2 review accepted](SCOUT-04-R1-R2-final-review.md). Coordinator six-suite integration checkpoint: 70 tests and 109 subtests passed in 132.84s; refreshed 55-schema development inventory, external lint and compilation passed. Managed/external reader discrimination, native integration and fresh-session M1 UAT remain whole-goal gates. Optional O1/O2 are non-blocking, not another correction round |
| SCOUT-05 | Containment guard accepted within scope; installed workflow/default eligibility open | [Claude containment acceptance](SCOUT-05-reviewed-manifest-containment-review.md) closes F-A1 with no blocking findings; coordinator six new cases passed in 17.93s. Prior successor/adapter acceptance stands within scope. Historical non-generic legacy authority remains explicitly separate, not a broad security redesign. Whole SCOUT-05/default eligibility and installed workflow remain open |
| SCOUT-06 | Real recording corrections pass; re-review and historical-input implementation active | [Correction evidence](SCOUT-06-public-run-corrections.md): 35 combined tests pass in 140.51s, no xfails. Claude `task_cbb734e71162` / `ctx_b0e084b5af4e` reviews stable corrections; Luna `task_4420e7666950` / `ctx_efaf9e1a8f06` successfully reused for historical resolver after two unaccepted prompt-blocked launches. Exact consumer wiring and installed proof remain open; not whole-goal acceptance |
| SCOUT-07 | Pure packet/bridge implemented; independent review active, unregistered | [Candidate](SCOUT-07-discovery-packet-implementation.md) and [bridge/correction](SCOUT-07-discovery-bridge-implementation.md): 38 combined tests pass in 0.38s after root identity-collision correction. Claude reviews this separate scope alongside runtime corrections. Source/compiler, real recording and posting selection remain open; no provider/default promotion |
| SCOUT-08 | Pure tailoring implementation active; earlier checks retained | Luna `task_c465b07cbd4b` / `ctx_1ff2d688ad99` owns new `.075` tailoring packet/schema/tests only. Existing 14-test checks checkpoint remains separate. Requested-document generation, factuality review and persistence/finalization integration remain open |
| SCOUT-09 | Waiting dependencies | SCOUT-03/04/05 |
| SCOUT-10 | Waiting dependencies | SCOUT-06/08 |
| SCOUT-11 | Waiting dependencies | SCOUT-06 through SCOUT-10 |
| RUNTIME-01 | Authorized; queued after SCOUT-11 | GigAI local backend and execution-setup comparison; contract review, implementation, strong evaluation fixtures and installed proof pending |
| SCOUT-12 | Waiting dependencies | All required implementation and release evidence, including RUNTIME-01 |

## Current dispatches and file ownership

Current parallel-wave dispatches and exclusive ownership are in the
[parallel delivery plan](parallel-delivery-plan.md). The older dispatch table
and single-writer wave notes below are historical, not a constraint on the
user-authorized parallel wave. C1 storage function bodies remain frozen during
fresh review; Astra alone owns additive shared registrations and integration.

| Task | Requested model | Scope | State |
|---|---|---|---|
| `task_51f10a680b25` | Terra 5.6, high | SCOUT-01 implementation, its new contract/report, required runtime/schema/test changes | Worker completed, not accepted; terminal reused for `task_d9c3c0db23a8` |
| `task_f409259bcca7` | Luna 5.6, high | Read-only caller/compatibility audit; only writes `SCOUT-00-caller-audit.md` | Completed; `ctx_558dbea415f1` released with transcript archived |
| `task_3eb14e8cebfa` | Independent Terra 5.6, high | Read-only SCOUT-00 contract review; only writes `SCOUT-00-independent-review.md` | Completed with B1–B4/M1 findings; `ctx_22ca29f609e7` released with transcript archived |
| `task_4be92742839b` | Independent Terra 5.6, high | Re-review section 9 corrections; only writes `SCOUT-00-correction-review.md` | Completed with C1/C2; exact terminal reused for final check |
| `task_6a2b46f3948a` | Same independent Terra session | Focused C1/C2 check | Completed, accepted; `ctx_d38228b99435` released with transcript archived |
| `task_d9c3c0db23a8` | Same implementation Terra session | Journal no-clobber/recovery and invocation artifact corrections | Completed; `ctx_21ac08fdbf12` released with transcript archived |
| `task_d5dfec386e1d` | Independent Terra 5.6, high | Runtime review after corrected implementation | Completed with R1/R2; `ctx_d8acc699a90f` released and archived |
| `task_859246f568c2` | Luna 5.6, high | Isolated adversarial closeout regressions and correction verification | Completed and accepted; `ctx_d131672df8f2` released with transcript archived |

The two initial exact `worker-show` receipts report `ready` / `input_accepted`, with
requested and effective model/effort matching this table. Existing worktree
placement did not rerun setup. Live state is not completion evidence.
The independent reviewer launch receipt likewise confirms the exact requested
model/effort, `input_accepted`, and existing-worktree placement.

Astra owns the roadmap, this ledger, and SCOUT-00 contract amendments. Terra
is the sole writer for shared runtime/schema/CLI files in Wave A. Luna does
not modify runtime code. Neither worker modifies the G44 review subject,
approved requirements baseline, existing `.gigai/`, or unrelated work.

After Terra settled, Astra took ownership of the small R1 runtime correction
in `provider_review.py` and stale schema golden fixtures in
`research/contract_spike/tests/test_schemas.py`. Luna owns only the new
`tests/test_jsl_closeout_regressions.py` and its evidence report; no concurrent
runtime writer remains.

## Verification and evidence

Initial read-only checks confirmed the source baseline and dirty paths above.
Orca sandbox status reported `stale_bootstrap`; the same read-only check outside
the sandbox confirmed runtime `4f679819-0300-4605-b73e-c66fe6231ddc`, app
`1.4.193`, ready. No new runtime was started or old team reset.

Initial closeout fake-adapter suite passed 10 tests in 24.90 seconds in the
worker transcript. That snapshot is not accepted: Astra requested stronger
invocation/replay/path/race coverage and installed-schema inventory correction;
implementation is continuing. No live provider dogfood has run. Record the
final corrected test commands/results before accepting local SCOUT-01 support.

Astra drafted [SCOUT-00 amendments](SCOUT-00-contract-amendments.md) and sent them
for independent review. `git diff --check` passes at this checkpoint. The
original G44 requirements-baseline SHA-256 remains
`a293b003bfd1f56dd5e9ffb70159eb7d57e624c2021a180ace2c58a73045e59d`.
Neither check is runtime acceptance evidence.

The audit and first independent review were read in full. B1–B4 identified
missing external schema/API details, G45 compatibility, durable instance
migration/recovery, and application-request binding. Section 9 resolves the
draft gaps and adds M1's question/successor-Run acceptance; re-review pending.

First correction review accepted B1–B4/M1 substance and found C1 (direct CLI
origin cannot fit an agent-only schema) and C2 (request proof versus complete
event-operation hash). Both were corrected and the exact reviewer reused for
a focused final check. No runtime claims derive from this document review.

The final focused check accepted SCOUT-00 on 2026-09-07 with no remaining
implementation-blocking finding. Its explicit selection/invocation reuse rule
is incorporated into the contract and Plan input table. Runtime SCOUT-02 remains
blocked on SCOUT-01 live closeout; contract completion does not waive that gate.

Initial implementation completion reported 10 provider-review tests, 20
journal tests and 37 schema hashes. It did not yet address the latest
coordinator finding: no-clobber refusal must precede recovery-intent creation;
reviewer request/response artifacts also need validation through the real
model-execution fake-port seam. The same implementation terminal now owns
that focused correction; full-suite and independent runtime acceptance wait.

The second correction has now settled and is under independent runtime review.
Astra launched `rtk proxy env GIGAI_G30_UAT=0 .venv/bin/python -m pytest -q`;
full-suite result is pending. Source schema checksums and closed installed
resource verifier pass for 37 schemas.

An offline cached-dependency wheel/sdist build succeeded under
`/private/tmp/gigai-jsl-verify.AymQ2y/dist`. The wheel was installed with cached
dependencies into that directory's isolated `venv`; `python -I` installed
CLI and schema verifiers pass outside the source checkout, and the new
`provider-review closeout --help` is present. The user's existing installed
GigAI and project environment were not replaced. Metadata remains 0.1.6;
this is NOT a release artifact/claim for 0.1.7.

- Wheel SHA-256: `ed61a08d804c0361965024a187eeeb08d0fed112e3fe4e66e7dd8abe719227eb`.
- Sdist SHA-256: `1b563f0bffc4e982ef271c1a1d3432293923917243f9c988bac76c3f6805752e`.
- Ruff unavailable in `.venv`; offline `uvx ruff` also failed because no cached
  Ruff package exists. A subsequent PATH check found the existing pyenv Ruff
  0.9.2; scoped lint of changed runtime/tests/tool files passes. No network
  installation was needed or attempted.

The first full suite finished with `686 passed, 5 failed, 1 skipped, 6 errors,
7 subtests passed` in 347.56 seconds. Six setup errors came from a stale
34-schema golden inventory (missing the prior two G43.1 schemas and this new
receipt). Astra added the exact three schema fixtures and kept the closed
inventory assertion. The five failures were sandbox loopback HTTP permission
denials; all affected files passed with loopback access (`18 passed`). Focused
schema/provider tests then passed (`16 passed, 73 subtests passed`).

Independent runtime review R1 found same-evidence concurrent retry returned a
conflict rather than authenticated replay; Astra now catches the journal
collision, revalidates current evidence, and reads only the exact journaled
receipt. R2 requested direct missing/tampered/foreign/symlink/recovery cases;
Luna's independent regression task covers these plus real two-process replay.
The final full-suite rerun uses scoped loopback permission and disables live
UAT. It started before Luna's new test file was present, so that new file is
verified separately, not falsely counted in this suite run.

The final-runtime wheel was rebuilt offline into `dist-final` under the same
temporary verification directory. Installed CLI and all 37 schemas pass again;
real fake-port provider/journal tests against the isolated installed wheel are
running separately. Final-runtime artifact hashes:

- Wheel: `d5dbb45a8143f639aaaf3feea36598fe086f71f904951dd4cd2795b17afd3e8e`.
- Sdist: `08966ec4c4d2b2c2be3ddf77124a628784df12467c2846ec67d8d774a7b5a4cf`.

Full Ruff check of `src`, `tests`, `tools`, and the changed research schema
fixture passes using existing Ruff 0.9.2. All working changes remain uncommitted.

Worker sandbox RPC could not reach Orca despite a live runtime. The first
Terra completion send was not accepted; the same active dispatch was resumed
with scoped elevated RPC guidance, without a replacement writer. Audit/review
completion later arrived through valid `worker_done` and their exact terminals
were released. The worker audit issued `orca open` during its own recovery;
the receipt returned the same existing PID/runtime, not a new runtime.

## Continuation protocol

### Final local checkpoint — 2026-09-07

All current workers are settled and released; no writer remains active.
Final corrected full suite: **697 passed, 1 skipped, 80 subtests passed in
357.31s**, with scoped loopback access and live UAT disabled. Luna's new file
was collected separately: **15 passed in 60.43s** from source, then independently
**15 passed in 74.70s** against the final isolated installed wheel. The existing
installed-wheel provider/journal checks also passed: **31 passed in 48.38s**.
R1/R2 are resolved for local acceptance. Final full Ruff and diff whitespace
checks pass; source and unchanged public baseline hashes were rechecked.

[Coordinator verification](SCOUT-01-coordinator-verification.md) records exact
commands, final artifact hashes, evidence boundaries and direct user actions.
No live provider call or real private-Gig mutation was performed. The SCOUT-01
goal is not complete: the baseline approval, configured model target selection,
separately billed budget decision, directly confirmed real Run and applicable
correction/re-review or no-fix closeout remain outstanding. SCOUT-02 is inactive.

The earlier chronological checkpoints above are retained as history, not as
current pending worker status. All changes remain uncommitted.

1. Read this ledger and the current contracts/reports; inspect Git status.
2. Bind/inspect the existing Orca Run and exact dispatches before launching
   replacements. Do not duplicate a live writer based on a timeout.
3. Process worker questions/completion with the orchestration mailbox; inspect
   actual diffs and evidence, then dispatch independent review or fixes.
4. After each accepted worker completion, reuse its exact terminal for a new
   task or release it through the tracked worker lifecycle.
5. Update goal status and next action here. Preserve pending budget/operator
   questions; elapsed time is not approval.
6. Start only ready dependency work. If live closeout is awaiting the operator,
   continue independent contracts/fixtures but do not claim dependent runtime
   activation or change the dependency silently.

## Next checkpoint

Complete SCOUT-02's bounded implementation and independent verification for the
multi-graph/version/Run foundation. SCOUT-01's correction/re-review branch is complete under the
operator's explicit delegation on 2026-09-08: G44 corrected, baseline unchanged,
fresh Plan `run_plan_3322ecf0-b016-4730-bdc2-95e953c4f434`, Run
`run_23375ef2-59f8-4139-848f-37a70da3f4c7`, all required participants completed,
three new findings independently checked and rejected, none accepted/deferred.
This is not a zero-finding/no-fix receipt or an assertion that the operator
personally edited files or typed commands. See
[correction and re-review evidence](SCOUT-01-G44-correction-rereview.md).
SCOUT-02 was subsequently activated by the operator's explicit request to
continue Scout; its implementation/evidence plan is linked in the goal table.

### Historical checkpoint before the operator-directed correction

SCOUT-00 contract review is complete. The supplied baseline approval is
validated. Claude readiness now passes after the login-context fix below.
The standard G43.1 Plan is sealed and inspected:
`run_plan_c1e9326b-6358-40dd-8477-c7a477cd67ca`.
The operator subsequently requested coordinator execution of that Plan.
The first real Run returned misleading top-level success while its provider
review was blocked; two document findings were verified and accepted. Response
framing and Run-status recovery are now corrected and independently reviewed.
The fresh Plan `run_plan_cde78ed0-f2c8-47af-8a1c-33ef8c361680` produced Run
`run_9b92eb24-f8ef-4a69-8b7f-9d3edab9c921`: all four CLI participants completed,
including Claude as reviewer, and Run status truthfully matches provider
completion. One finding was verified and accepted on the retry: the unchanged
G44 subject still contradicts the approved baseline on one-off request-text
persistence. Earlier findings were not edited or silently resolved. Next is
an explicit document-findings resolution decision, then a fresh review; no
no-fix closeout or dependent Scout runtime activation is justified yet.
Final recovery verification: 734 tests passed, 1 opt-in live test skipped,
80 subtests passed; focused independent review accepted the recovery fixes.
The real four-participant retry is separate live evidence, not a fixture claim.
See [live review recovery](SCOUT-01-live-review-recovery.md) for exact identities,
authorization provenance, findings, and worker ownership. API targets remain
excluded. Do not duplicate the approved Gig or bypass the live dependency.

### Latest operator context and documentation-only update

The operator supplied a successful direct approval receipt:
`requirements_baseline_approval_feffb475-1357-4a26-a986-a5456a2b212d`, with
receipt `content_sha256` of
`sha256:6c73970872c79f0921fdaaf1c25093b8a9f06cf0df0a85cef8bb5c680c0552a9`.
That field hashes the approval receipt, not the baseline file. The public
baseline remains `a293b003bfd1f56dd5e9ffb70159eb7d57e624c2021a180ace2c58a73045e59d`.
This documentation pass records the supplied result; it did not inspect or
mutate the private receipt or seal a new Plan.

Supplied model discovery lists `claude-default`, `codex-default`, `codex-luna`,
`codex-sol`, and `codex-terra` as configured. Configured is not tested readiness.
No readiness probe or provider invocation was performed in this rename pass.
The separately billed provider budget is still unresolved.

The operator's latest convention is to invoke GigAI commands directly, without
RTK, both for agents and user instructions. Earlier exact test/build command
evidence is retained as history, not rewritten to claim different executions.

Renamed the current lifecycle roadmap and evidence directory to Scout and
updated inbound documentation links. The new
[workspace amendment](SCOUT-00-user-owned-gig-amendment.md) records local
per-Gig tools, SQLite, clean HTML/core CSS, `docs/`, linked goal graphs and
handoffs, private clone ownership, and pending decisions. Existing G-goal IDs,
Orca receipts, runtime/test code and public review inputs are unchanged.

Documentation validation: 104 local link targets and 27 section anchors across
15 documents resolve; no stale links to the old roadmap/evidence paths remain.
Historical line labels in reviews are retained, with renamed-document links
updated to corresponding sections. `git diff --check` passes. The runtime/
tests/tools/research diff fingerprint and selected untracked schema/test hashes
match their pre-edit values, as do the G44 subject/baseline hashes. No runtime
tests were rerun for this documentation-only change.

### 2026-09-08 — review corrections and all-default initialization

The operator supplied a changes-requested review: SQLite authority, canonical
storage mapping, editable-tool authority, initialization/clone identity, and
generated/custom UI ownership. The updated
[amendment](SCOUT-00-user-owned-gig-amendment.md) freezes revised decisions;
the [review response](SCOUT-00-workspace-review-response.md) maps each finding.
These are proposed corrections pending independent re-review, not accepted
runtime changes or completed SCOUT-00 scope.

The operator explicitly chose general `gigai init` with a supplied username
to create private instances of all bundled default Gigs. This replaces both
the older named-init plan and the reviewer's suggested `init scout` minimum.
There is no clone command or personal suffix requirement for v0.1.7. Full
Gig identity, project/template/default uniqueness, idempotent batch recovery,
private owner metadata, and preservation of customizations remain required.

Use the existing per-Gig `state.sqlite`, not a second `gig.sqlite`. Scout CRUD
appends journaled revisions/tombstones through validated local persistence;
Scout SQL tables are rebuildable. Existing G22 trace data in the same file is
preserved. The v2 path/ignore map separates private authority from editable
working copies and generated report bundles. Gig-owned domain tools remain
customizable while supported mutations validate version/digest/effects.

No runtime code, tests, public G43.1 review inputs, or private Gig state were
changed. No provider call or newly approved Run was performed.

Documentation checks passed: 118 local link targets, 32 section anchors and
diff whitespace. Runtime/test/tool diff and protected input/schema/test hashes
are unchanged. The independent re-review verdict is still outstanding.

### 2026-09-08 — independent workspace re-review accepted

The operator supplied an independent acceptance verdict for the SCOUT-00
workspace-amendment contract scope: all five findings resolved, no remaining
implementation-blocking contract issue. Recorded the verdict in the
[review response](SCOUT-00-workspace-review-response.md) and synchronized
current roadmap/amendment status. Earlier changes-requested and pending-review
entries above remain chronological history.

Strict schemas, caller changes, migrations and regressions remain implementation
evidence obligations. No implementation goal is completed by this verdict.
The reviewer reports no file changes or runtime tests and explicitly excludes
unrelated G43.1/runtime changes. The coordinator's acceptance-recording pass
changes documentation only and does not invoke providers, alter private
state, or bypass the separate SCOUT-01 live closeout prerequisite.

### 2026-09-08 — actual Plan preparation attempt

The operator requested preparation of the real review Plan. The direct command
selected `claude-default` and `codex-luna` as reviewers, `codex-terra` as verifier,
and `codex-sol` as adjudicator, using standard profile and the supplied
requirements-baseline approval. No alternative provider/target was substituted.

The first attempt was refused by filesystem sandbox permissions while updating
the existing private workpad's disposable index. Scoped elevated preparation
then validated the approval and its journal provenance, but refused sealing
with `target_not_usable` for `claude-default`. No new Run Plan or Run was created.
Preparation may refresh disposable index state; no approval was manufactured.

A read-only readiness check finds a current configuration-bound usable record
for each selected Codex target. Claude is configured but lacks a usable record.
These are existing readiness records, not fresh live-provider evidence. The
implementation's explicit `models --probe` sends a model prompt and can consume
quota/cost; no probe was run while the separate spending/permission decision
is unresolved. The next bounded request is one Claude CLI readiness check,
not a baseline reapproval or a request to launch the review.

### 2026-09-08 — authorized Claude CLI probe requires login

The operator authorized proceeding with the CLI subscription targets. Ran
`.venv/bin/gigai models --probe claude-default --json` directly, without RTK,
with scoped permission for the existing private workspace. The command exited
1: Claude Code was detected, but the probe returned
`authentication_required: Not logged in · Please run /login`. Discovery
operation: `discovery_a7ff0d96-3f28-405f-90aa-1d1b1a5e8a1d`, captured at
`2026-09-08T15:39:46.439515Z`.

This was an attempted live CLI readiness check, not a successful model response
or review Run. No API target was invoked, no new Plan or Run was created, and
no baseline approval was changed. The adapter does not establish the account's
billing mode, so no zero-cost claim is made. Authentication is the current
blocker; the earlier missing probe-permission checkpoint is superseded.

### 2026-09-08 — login-context regression fixed; actual Plan sealed

After the operator completed login, a repeat Claude probe still returned
`authentication_required` (discovery `discovery_d15eba4f-a769-46ac-8bb4-cdc908c923be`).
Read-only `claude auth status --json` checks in temporary working directories
isolated an adapter bug: inherited environment reports a logged-in Claude Pro
account, whereas GigAI's environment allowlist reports no login. Adding only
`USER` restores login detection; `LOGNAME`, `SHELL`, or
`__CF_USER_TEXT_ENCODING` individually do not. No credential files or token
values were read for this diagnosis.

Updated only the Claude adapter to preserve `USER` alongside its existing
optional OAuth-token forwarding. The shared environment allowlist is unchanged.
Two fake-process regression cases cover present/absent `USER`, exclusion of an
unrelated synthetic secret and API key, and no invented OAuth token.

Verification:

- CLI adapter tests: 11 passed in 2.51s.
- Discovery/setup tests first produced 16 passes and one sandbox loopback-bind
  failure. The combined adapter/discovery/setup rerun with scoped loopback
  permission passed all 28 tests in 5.08s; no live models are used by these tests.
- Ruff passed for the two changed Python files using the existing PATH binary.
  The initial `.venv/bin/ruff` attempt found no executable; no tool was installed.
- The real `.venv/bin/gigai models --probe claude-default --json` then exited 0
  with `readiness: usable`, discovery
  `discovery_be3ad128-e001-4186-a75e-e105455343db`, captured at
  `2026-09-08T15:47:36.095173Z`. This is live readiness evidence, not review evidence.
- Full-suite and installed-wheel tests were not rerun for this narrow fix.

Created and read back the sealed Plan through `run-plan create` and
`run-plan show`; both returned `ok: true` with no diagnostics:

| Field | Sealed value |
| --- | --- |
| Plan | `run_plan_c1e9326b-6358-40dd-8477-c7a477cd67ca` |
| Plan SHA-256 | `1910ed74d0a97ba97d64d9bc60dbcecfbf170a08d08f73ae23bda60be133fec6` |
| Gig | Existing `gig_84de15da-f79d-4dd6-b39d-f02f29cc345c`, v1 |
| Reviewers | `claude-default`, `codex-luna` |
| Verifier / adjudicator | `codex-terra` / `codex-sol` |
| Inputs | Original G44 subject and existing approved requirements baseline; hashes unchanged |
| Profile | `standard`; `usage_unreported_policy: block_before_call` |
| Limits | 12 model calls, 30,000 tokens, 600,000 ms, zero tool calls, one parallel goal; USD 3.00 accounting ceiling |

The accounting ceiling is not a charge estimate or proof of subscription billing.
No API adapter was invoked. Only Plan preparation is complete; no direct Run
consent, review result, or G43.1 closeout has been recorded by this pass.

Operator commands from this worktree:

```sh
.venv/bin/gigai run-plan show run_plan_c1e9326b-6358-40dd-8477-c7a477cd67ca --json
.venv/bin/gigai run --plan run_plan_c1e9326b-6358-40dd-8477-c7a477cd67ca --execute-review --confirm --wait --json
```
