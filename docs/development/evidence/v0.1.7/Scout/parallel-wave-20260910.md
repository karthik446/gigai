# Scout parallel wave — 2026-09-10

All three sessions use Luna, medium effort, with `--approve-for-me` as previously
requested. The per-process `check_for_update_on_startup=false` avoids repeated
startup update prompts; installed software and global configuration are unchanged.
The [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
documents that option. Orca confirmed `ready/input_accepted` for every dispatch.

| Lane | Task / dispatch | Exclusive scope | Estimate |
| --- | --- | --- | --- |
| Independent discovery review | task_63166d3382df / ctx_8151a2bd8521 | New review report only; discovery/core held stable | 15–25 min |
| Research input loading | task_09b37dfbe109 / ctx_5653a1e8808b | scout_research_inputs.py, its tests, new evidence | 25–40 min |
| Application journal | task_2cccdca4aba7 / ctx_ba33d85e8a21 | New event service/CLI/schema/tests, bounded CLI/schema registration | 35–50 min |

No worker owns another lane's files. Research Plan-input union wiring, generic
record-application Run integration, tailoring integration and report UI remain
later steps. No full-suite duplication, provider execution, real private-state
mutation, commits, approval/activation or release is authorized by this wave.

Orca completion messages are primary. A ten-minute scheduled precheck watches
only these three tasks and report timestamps, notifying the coordinator about
changed handoffs/status. It never claims a report's existence means completion,
never stops workers and always skips the automation's model launch. Unchanged
running tasks are left alone. Automation `291cd268-95e6-4cc4-9478-089c75e9f2b9`
uses ten-minute cron ticks and disables itself once all three tasks are settled
or at 23:10 UTC (roughly three hours). The first scheduled tick is 20:10 UTC.
The CLI rejected the initial counted RRULE before creation; cron creation
succeeded. A direct precheck smoke run observed the three tasks and returned
the intended nonzero skip code. Scheduled notification delivery is not yet
verified. No active LLM wait loop.

### Scheduler correction

The manual Orca automation test bypassed the precheck (`precheckResult: null`)
and launched a default Codex session. Its prompt made it report the anomaly
and stop without changing files or workers. The automation was immediately
disabled; it is NOT the active fallback, and no claim is made about scheduled
cron behavior from this manual test.

The replacement is a session-scoped launchd job
`gui/501/com.gigai.scout-wave-20260910`, loaded from the repository's
`research/orchestration/scout-wave-20260910.plist` (not installed in global
LaunchAgents). It invokes the same small observation script every 600 seconds
with no model execution path. It sends clearly labeled scheduled status mail
on the coordinator's behalf, only for changed handoff/status observations.
It unloads itself on the next tick after all tasks settle or at 23:10 UTC.
The disabled Orca automation and original test history remain for diagnosis.

### 20:12 UTC — first fallback delivery handled

The launchd check successfully delivered a changed-report notification to this
Run. Discovery reviewer final transcript confirms completion despite IPC
failure; coordinator read the report and inspected the affected inventory
reuse branch. One real member-list validation gap was found; no broader
Run-path failure was reported. Exact terminal reused for Luna correction
`task_5d32829de605`; the watcher now tracks that replacement task instead of
the settled review. Application worker was still testing, so its report was
not treated as completion. Research lane remains undisturbed.

### 20:21 UTC — wave handoffs received, not accepted as a release gate

All three exact final transcripts were read after fallback notification; their
worker IPC completion calls failed. Coordinator explicitly recovered the task
statuses rather than fabricating worker messages. Research hydration reports
10 passing tests (latest transcript80.88s; report77.45s remains historical).
Inventory correction reports13 passing affected tests, but changed compiler
identities/timestamps and excluded a manifest from inventory beyond F1 scope.
Application delivery has only two pure tests; source inspection shows replay
hash instability from newly generated nested evidence timestamps, unredeemed
opportunity/document refs, and incorrect ordering/status isolation. Neither
of these latter two implementations is accepted.

New bounded Luna wave (watcher updated; original23:10 UTC expiry unchanged):

- `task_35e968728460`: correct inventory reuse against committed historical
  bytes without changing identities/time/inventory membership; same implementer.
- `task_a219ac4b5875`: application service corrections plus real disposable
  CLI/journal acceptance tests; same implementer.
- `task_5b118b36d89e`: fresh independent context reviews research hydration and
  tailoring candidate, reports only. No worker reviews its own implementation.

These are bounded correction/review tasks, not an assertion that whole Scout
or application tracking is working. No broad suite or real private mutation.

### 20:25 UTC — independent review received through native Orca completion

Read both new reports. Research hydration needs complete checkpoint/receipt
history and an existing-writer API to avoid nested writer-lock acquisition.
Tailoring needs exact included-claim-to-draft span binding and rejection of
dangerous raw HTML/alternate link forms. These candidates are not accepted.

Immediately reused the settled reviewer for bounded tailoring corrections
`task_d20af4828797`; this is implementation, not independent acceptance of
its own fix. Hydration corrections `task_a9370473c60c` are ready/queued for
the next available slot, not running. Inventory and application correction
workers remain undisturbed. Watcher includes the queued task so it does not
mistake three completed workers for an empty backlog; expiry stays23:10 UTC.

### 20:36 UTC — fallback handoffs inspected and workers reused

Exact final transcripts confirm inventory and application workers finished;
both attempted completion IPC once and failed. Coordinator recovered their
task states as completed implementation attempts, NOT accepted features.
No test suites were rerun by the coordinator.

Inventory correction restored historical ID/time behavior and complete member
validation. Worker reports six owned tests. Existing report's thirteen-test
result belongs to the preceding pass; current negative tests still call the
private validator with an altered in-memory payload rather than exercise the
public reuse entry against a committed corrupt inventory. That acceptance
coverage and independent review remain open.

Application correction reports three tests, including one disposable CLI
create/retry/status path. Source still sorts ISO date strings and mixes status
across opportunities; regex validation does not redeem opportunity authority.
The retry test does not explicitly advance the clock. Existing report was not
updated to reflect this pass. These are not end-to-end application acceptance.

Reused the inventory worker for queued hydration correction
`task_a9370473c60c` (`ctx_8da91ac9e880`). Reused the application worker for
`task_4782cff291ec`: narrower journal ordering, replay consistency, and concrete
adversarial CLI tests, with missing opportunity authority explicitly left open.
Tailoring correction continues undisturbed. Both replacement starts returned
ready/input_accepted; rough window20–35minutes. Watcher tracks the three active
tasks with the unchanged23:10UTC expiry. No additional worker or model launched.

### 20:37 UTC — tailoring completion arrived while handling delivery

Native `worker_done` for tailoring was delivered with the previous ACK.
Coordinator read SCOUT-08-tailoring-corrections.md: worker reports15 focused
tests and Ruff passing, explicit draft/source byte binding and conservative
HTML/link handling. This remains an unregistered pure candidate; independent
correction review and real Plan/Run integration remain open.

Reused that settled Luna for independent inventory verification
`task_6e73535979c0` (`ctx_c0313a728646`), limited to public-entry regression
tests and a review report, no production edits. It did not implement inventory.
Start returned ready/input_accepted; ETA15–25minutes. Watcher now tracks this
replacement alongside hydration and application corrections. Three active
workers; no polling loop or duplicate suite started by coordinator.

### Inventory verification completion handled

Read native completion, SCOUT-07-inventory-correction-verification.md and the
new public-entry tests. Independent reviewer reports12 tests passing in55.32s.
The five corruption fixtures journal one malformed inventory on initial
materialization, restore the row renderer, and assert public reuse refuses
without changing HEAD. Positive reuse preserves the historical member and
proposal bytes. Bounded inventory correction accepted; broader concurrency,
installed package and whole Scout remain outside this verdict.

Released the settled review dispatch (external terminal retained, no process
action). Started a fresh Luna context for independent tailoring correction
review `task_708652508600` / `ctx_761189aa1c5e`, terminal
`term_bf031aa6-7599-4970-8081-f807b68174b9`; ready/input_accepted confirmed.
This reviewer did not implement tailoring. ETA10–20minutes; hydration and
application workers not polled or interrupted. Watcher updated to the new
review task; existing expiry unchanged. No coordinator suite rerun.

### 20:41 UTC fallback handled — hydration and application final transcripts

Read both exact final transcripts and handoffs after changed-report notice.
Both workers finished and attempted completion IPC once unsuccessfully.
Recovered task state as completed implementation attempts, not acceptance.
Hydration reports Ruff passing but no completed pytest result. Application
reports four tests and Ruff passing; committed opportunity authority stays
explicitly open. No coordinator tests were started.

Hydration worker reused for `task_89d952b9e301` / `ctx_aab3a8880ac8`:
recover its exact existing test session before any new suite, then complete
owned verification and real complete-history regressions. A terminal yield is
not a test timeout; if genuinely hung, collect stack and fix owned code.
ETA10–20minutes.

Application dispatch released without process action. Fresh Luna context
started for independent bounded service review `task_75e745ee57e3`, terminal
`term_d9c18520-a39b-4e2e-a124-5dca486ee4f2`. It owns only its review report;
separate claimed service correctness from unresolved end-to-end authority.
ETA15–25minutes. Both starts confirmed ready/input_accepted.
Tailoring review remains undisturbed. Watcher maps updated, expiry unchanged.

### Tailoring independent residual findings handled

Native review completion received; read SCOUT-08-tailoring-corrections-review.md
and inspected the affected claim/link validator surface. Reviewer reports15
focused tests passing but reproduces changed claim statements accepted with
unchanged source/draft spans and declarations/processing instructions passing
the purported raw-HTML rejection. Correction not accepted yet.

Reused the settled reviewer for implementation task `task_8bf57f4a392c`:
bind included claim statement text deterministically to draft span text, keep
semantic source support explicitly agent-reported, and reject residual HTML
declaration/processing/comment forms. Requirement statements need not equal
resume prose; do not turn integrity checks into unsupported semantic claims.
Owned candidate/schema/tests only, with targeted regressions and clear schema
versus Python validation boundaries. This worker is now implementing, not
independently accepting its own correction. Ready/input_accepted confirmed;
ETA10–20minutes. Other workers remain undisturbed; watcher updated.

### Application independent review handled

Read SCOUT-09-application-service-review.md and inspected document redemption,
event validation and replay code. Reviewer reports four tests passing and finds
missing exact document-byte redemption, inconsistent evidence metadata accepted,
and a repeated correction target incorrectly labeled a cycle. Scope remains
the direct service subset, not whole SCOUT-09.

Coordinator qualification: changing a working reference source is NOT a valid
reproducer of committed-byte corruption. Historical committed references should
survive later working-copy edits. The missing recomputed byte check is visible
in source, but must be exercised with legitimately committed malformed/missing
snapshot authority, not by imposing new working-copy equality.

Reused settled reviewer for implementation `task_62a455fe7b7a`, restricted
to application service/tests/evidence. Correct exact pinned document redemption,
direct evidence/receipt identity and separate committed event agreement, and
actual ancestry checks while preserving contract-permitted correction branches.
No new branching policy or record family. Require public disposable positive
and no-publication negative tests; opportunity authority remains explicitly open.
Ready/input_accepted confirmed. ETA20–30minutes. Hydration and tailoring workers
remain undisturbed; watcher updated with unchanged expiry. No coordinator suite.

### All three correction workers finished — cross-review dispatched

On user prompt, checked each exact dispatch once and read all three latest
handoffs. Hydration final transcript reports exit0,13 tests in113.31s and Ruff
passing; recovered task completion after its failed IPC. Native completions
confirm tailoring26 tests in0.08s and application8 tests in13.66s, both Ruff
clean. These are worker verification results, not coordinator reruns.

Released tailoring dispatch (external terminal retained, no process action).
Reused two settled Luna contexts for independent bounded cross-review:

- `task_48f2d7a37a6d` / `ctx_3dc35fb24346`: hydration implementer
  reviews application correction; report SCOUT-09-application-corrections-review.md.
- `task_baedde819fe6` / `ctx_e78a85915fd7`: application implementer
  reviews hydration and tailoring; separate SCOUT-06-hydration-corrections-review.md
  and SCOUT-08-tailoring-final-review.md verdicts.

Neither reviews its own production changes. Both starts returned
ready/input_accepted. Source/test inspection plus at most three meaningful
targeted probes per worker; no repeat full focused suites or source edits.
ETA10–20minutes. Independent acceptance and product integration remain open.
Watcher now tracks these two tasks with unchanged expiry; no third worker.

### Hydration and tailoring correction closeout accepted

Read both independent reports delivered by native worker_done:
SCOUT-06-hydration-corrections-review.md and SCOUT-08-tailoring-final-review.md.
Both accept their bounded corrections through source/schema/test inspection
without rerunning the previously recorded13/26-test suites. This does not
accept Plan/Run callers, activation, or whole features.

Reused the settled Luna for `task_22c75be944d5`: explicit completed-research
selection in a subsequent v2 Plan/start with same-writer revalidation, strict
additive selected-input schemas, and real disposable public-entry fixtures.
Owns input/caller/schema integration only, not application/tailoring assets.
Research asset limitations, if any, must remain named rather than treated as
completed iteration. Ready/input_accepted confirmed; ETA25–40minutes.
Application correction cross-review continues undisturbed. Watcher updated,
expiry unchanged. Two active workers; no coordinator suite.

### Application reviewer exit recovered without accepting inaccurate evidence

Orca escalation reports `ctx_3dc35fb24346` failed with stop_unverified.
One read confirms terminal exited, transcript unavailable; no cause beyond
that is established. Saved SCOUT-09-application-corrections-review.md exists.
Coordinator read it and checked the cited tests/source. Its F3 claim of real
committed semantic-tamper proof is inaccurate: the test writes working copies
and refuses at the journal mirror boundary. Source guards exist but this
test does not establish them. Saved verdict not adopted wholesale.

Marked failed attempt honestly and released its dispatch without process
action. Created independent Luna task `task_76e97343eb1d` to add two
narrowly targeted, singly committed evidence/receipt
regressions and correct the evidence claim. Test/report ownership only, no
production edits or repeated full suite. Ready/input_accepted confirmed;
ETA10–15minutes. Research Plan integration continues undisturbed. Watcher
updated, expiry unchanged. No Orca restart, duplicate live review or app fix.
Initial reuse was refused with agent_unconfigured and created no dispatch;
the old terminal was no longer a recognized agent. A fresh Luna terminal
`term_01e6f749-fb1b-40da-9297-e2b2c2bbe31c` successfully accepted dispatch
`ctx_e7b6455b8670` (ready/input_accepted). The start confirmation above refers
to this successful fresh launch, not the refused reuse attempt.

### Application committed-evidence gap closed

Read native completion and SCOUT-09-committed-evidence-verification.md, then
inspected the new test construction and exact error assertions. Four new
parametrized cases passed and owned Ruff passed, per worker evidence; earlier
eight tests were not rerun. The new fixtures publish one hash-valid event and
receipt in a real journal transition and demonstrate semantic scope/actor,
operation-identity and missing-separate-event refusal with unchanged HEAD.
Working-copy drift test remains separately identified; review claim corrected.
No production defect or production edit was needed in this verification pass.

Bounded application document/evidence/correction fixes now accepted on corrected
independent evidence. Whole SCOUT-09 is NOT accepted: opportunity authority,
selected-user-request, generic Run integration and UI/projection remain open.
Released settled dispatch ctx_e7b6455b8670; research Plan integration is the
only remaining dispatched task in this wave. Watcher tracks it with unchanged
expiry. No coordinator suite rerun and no unrelated worker interruption.

### Completed-research Plan integration handoff received

Read native completion, SCOUT-06-research-input-integration.md and both new
integration tests. Worker reports2 new tests33.78s,26 affected role/replay
tests21.30s, Ruff and64-schema verification passing. Tests complete the original
research Run, then create/replay a second Plan and start its Run using selected
historical research. They do not execute the second Run checkpoint/submit.
The handoff's second-path statement therefore remains a pathway claim, not
executed completion proof. No coordinator suites rerun.

Released settled implementation dispatch and reused independent Luna application
verifier for task_eac41a5bc896 / ctx_d85351370e61, source/schema review plus a
real second-Run completion probe and missing no-publication assertions. Owns
only integration tests/report; no production fix-forward. Must identify any
historical-source asset consumption gap without faking broker acceptance.
Start confirmed ready/input_accepted; ETA15–25minutes. Watcher tracks this
review with unchanged expiry. Research iteration not yet accepted as complete.

### Second-Run verification found the renderer seam

Read native completion and SCOUT-06-research-input-integration-review.md.
Strict v2 input resolution and same-writer callsites accepted as a subset.
Actual second-Run producer rejects normalized scout_research in the inventoried
research renderer before checkpoint/submit. Reviewer records1 pass,1 xfail,
1 deselected in29.34s; xfail is explicit nonacceptance, not a completed Run.
Missing/cancelled negative paths now assert unchanged HEAD. No production edits
or prior suite reruns in the review.

Reused settled Luna for `task_39bef702f300`: implement versioned renderer and
fixed broker support for exact historical research bytes, preserve old resource
compatibility and sealed source identity, and replace the controlled xfail with
actual second-Run checkpoint/submit/replay proof. Scope domain resources and
narrow source/compiler binding; no application/tailoring/core union refactor.
Ready/input_accepted confirmed; ETA30–45minutes. Independent review follows.
Watcher updated; expiry unchanged. No coordinator suite or provider execution.

### Renderer resource-version decision resolved by coordinator

Worker task_39bef702f300 returned failed/no files changed: its authorized
ownership excluded recorder/resolver mapping changes needed for an additive
resource. Coordinator inspected current literal v2 mappings and resolved the
implementation choice; no user product decision or fresh approval is needed.

Preserve existing physical .071 / logical .073 v2 research resources and v2
bridge behavior. Add explicit literal .076 resources and v3 research-domain
bridge (domain version, not a new recording protocol), retaining closed support
for v2 and v3. New candidate source1.3/compiler4 binds the new resources;
historical sealed Gigs/Runs remain untouched and supported. No default
promotion, migration, activation or private-user mutation.

New task `task_47efc00729a7` explicitly owns the necessary narrow
external_recording/scout_research_inputs mappings, new resources/bridge,
source/compiler binding, strict schema enums/inventory and affected tests.
Requires actual legacy-v2 research -> new-v3 second-Run completion/replay with
exact historical bytes and bounded lineage, plus preserved old replay.
Ready/input_accepted confirmed. ETA30–45minutes; watcher updated, expiry unchanged.

### Versioned renderer completes current-to-current reuse; review dispatched

Read native completion and SCOUT-06-research-reuse-renderer-implementation.md.
New literal .076 domain-v3 bridge/resources and candidate1.3/compiler4 are
implemented. Worker reports29 tests77.29s plus one unsupported-validator test,
Ruff clean. Its64-schema verification was previously recorded, not a new run.
Real v3-to-v3 second Run now reaches checkpoint/submit/replay. True completed
v2-to-v3 transition remains explicitly unproven; no whole iteration acceptance.

Two independent work lanes started, both ready/input_accepted:

- Luna task_3cf0c4a5db95 / ctx_34c9529339d4: new test/report only for real
  historical v2 Run -> legitimate candidate successor -> completed v3 Run.
  Same implementer supplies missing evidence; not independent acceptance.
- Terra `task_bf1a29cb3e4a`: independent source-authority/version review and
  at most three targeted probes. Higher effort justified by broker/source
  authority changes and potential third-iteration refusal from one-hop ancestry
  limit. Owns report only, not Luna's new fixture.

ETA20–30minutes. Prior29/13-test suites not duplicated. Watcher tracks both;
expiry unchanged. No coordinator tests, providers, or user-state activation.

### Legacy fixture corrected at the caller boundary

Luna returned failed with a passing negative test: authoring scout-source.json
is not a successor Graph Set definition. Coordinator inspected the test and
existing supported successor examples; this is a fixture argument error, not
evidence that successor support is absent. Correct source is the compiled
first-graph-set-definition.json, converted to an amendment by removing the
initial-only fields as test_scout05_first_proposal already demonstrates.
test_scout02_graph_set_flow has actual repeated same-Gig propose/approve proof.

Reused settled worker for `task_5419f475ec9b` with these exact pointers,
allowing a coherent fixture-authored old v2-domain Graph Set followed by v3
successor rather than reconstructing an archived catalog release. Must still
publish real old research, preserve it, and complete reuse through new Run.
Tests/report only, no production edits; wrong-input refusal not acceptance.
Ready/input_accepted confirmed; ETA15–25minutes. Terra review undisturbed.
Watcher updated with unchanged expiry. No coordinator suite.

### Terra review: closed authority accepted, third-Run ceiling blocks lifecycle

Read native completion and SCOUT-06-research-reuse-v3-review.md. Reviewer accepts
closed package/source mappings, strict shapes, same-writer direct-history byte
hydration. Completed bridge probe plus source call chain proves rejection of
historical packet with its own research input; full public three-Run attempt
did not finish and is explicitly not claimed. This is an accidental two-Run
lifetime restriction, not a frozen product contract.

Released Terra's settled review dispatch. Fresh Luna high task `task_2e06ff6241ee`
implements bounded direct-historical-tuple integrity rather than recursive
ancestor reopening, preserving current input validation and trusted caller
boundaries. New public three-Run test plus direct tamper/foreign negatives
required, no resource/schema version churn or broad suites. Separate test
ownership from active legacy fixture. Ready/input_accepted confirmed;
ETA20–30minutes. Legacy fixture continues undisturbed. Watcher updated,
expiry unchanged. No coordinator test rerun or private/provider action.

### Legacy fixture partial completion received during ACK

Luna's corrected successor probe now stages a legitimate proposal (focused
test2.08s, Ruff clean), but still does not complete an old research Run and its
new-version reuse. Read revised report; no production blocker is established.
Released failed partial attempt. To avoid repeating the same incomplete Luna
fixture loop, reused Terra's settled context for `task_e70582ebb4f2`, test/report
only, to finish the actual legacy/new public Run fixture. This escalation is
bounded to the repeated fixture failure, not the default implementation model.
Ready/input_accepted confirmed; ETA20–30minutes. Luna third-Run correction
continues on disjoint owned files. Watcher maps updated, expiry unchanged.

### Real legacy fixture exposes missing successor artifact metadata

Read Terra completion and revised legacy verification report. Real v2 research
now completes, v3 successor proposal/approval succeeds, old selection/start and
old submit replay succeed. New v3 checkpoint refuses because successor Graph
Set publication lacks artifact_refs for staged nested Goal Graphs. Test records
1 controlled xfail in19.17s, Ruff passing; full transition not accepted.

Coordinator inspected lifecycle.py: first publication includes exact artifact_refs;
successor record_transition omits them. Reused Terra with its exact fixture
context for `task_414cd8f8d775`, a narrow new-publication metadata fix plus
authenticated-reader regression and completion of the same legacy test. No old
journal backfill, pointer fabrication or weakened reader. Ready/input_accepted
confirmed; ETA10–20minutes. Luna third-Run correction remains undisturbed.
Watcher updated, expiry unchanged. No coordinator suite or provider action.

### Successor provenance correction independently accepted by coordinator

Read native completion and SCOUT-06-successor-publication-correction.md.
Independently inspected the lifecycle.py successor change: exact artifact_refs
over the already published artifact tuple, matching first-publication behavior,
without changing locks, approval, historical entries, or reader rules.
Inspected reader/digest/size assertions and preserved shared-proposal
multi-publisher refusal in the real legacy fixture. Bounded fix accepted.

Worker-recorded verification: true legacy-v2 -> v3 research checkpoint/submit
fixture1 pass27.43s; existing successor regression1 pass4.80s; first-proposal
regression1 pass2.32s; Ruff clean. No xfail remains in that fixture. Coordinator
did not rerun these suites. This closes the legacy transition evidence gap,
not whole Scout or ordinary third-Run acceptance.

Released settled Terra dispatch ctx_84bea2831ba3. Luna third-Run correction is
the remaining active task; watcher tracks it with unchanged expiry. No extra
review worker launched for the independently inspected small publisher fix.

### Third-Run correction finished; independent boundary review started

On user prompt checked exact dispatch once; final transcript confirms completion
despite failed IPC. Read SCOUT-06-iterative-research-correction.md: four focused
tests117.68s and Ruff pass, real three-Run checkpoint/submit/replay, earlier bytes
preserved, spy accounting shows direct selected second-Run paths and no first
ancestor artifacts. Tampered-working-copy case honestly identifies mirror drift,
not committed semantic corruption. Coordinator did not rerun suite.

Recovered task completion from exact final transcript and released dispatch.
Reused Terra for `task_a60bb777250b`, independent narrow review of Luna's changed
historical-integrity/current-input trust boundary. Terra's prior lifecycle.py
metadata change is outside this review; it does not review its own code.
Source/test inspection and at most two relevant short probes; no repeated
two-minute suite. Ready/input_accepted confirmed; ETA10–15minutes.
Watcher updated; existing expiry unchanged. Whole Scout remains unaccepted.

### Iterative research correction accepted; initial Tailor Run dispatched

Read native completion and SCOUT-06-iterative-research-correction-review.md.
Independent Terra accepts direct historical integrity versus current-input
validation, retained nested refs as inert data, and no recursive bridge ancestry.
Four-test117.68s/Ruff evidence not rerun. Nonblocking caveats: hydration spy
covers explicit bridge-reader calls, not every generic journal snapshot read;
foreign-shaped missingID case is not a separately created foreign-Gig fixture.
These limits remain explicit. Research repeated-Run and legacytransition
integration proofs accepted within offline fixture scope, not live release.

Released settled Terra review. Fresh Luna high task `task_04adcd6e6e4d`
implements initial real tailor-application Plan/start/checkpoint/submit/replay
using existing exact G45 posting/evidence/request inputs, .075 pure packet and
closed fixed bridge. Narrow template/compiler/domain/schema wiring permitted,
no application/private-storage/UI/research authority refactor. Requires real
resume-only/both outputs, regeneratedchecks, source-integrity and no-publication
negative fixtures; no finalized/applied state invention.
Ready/input_accepted confirmed; ETA35–50minutes. Watcher updated;23:10UTC
expiry unchanged. No duplicate coordinator tests or model/provider execution.

### Initial tailoring integration finished; precise test and authority review

Fallback changed-report notice triggered one exact transcript read. Luna final
confirms completion despite IPC failure; handoff read. Candidate1.4/compiler5
registers .075 through fixed bridge, sealed G45 request/posting/evidence inputs,
and a framed logical tailoring bundle. Worker final reports62 focused and58
affected tests, Ruff and64 schemas; report lacks exact prior command provenance.
Coordinator did not rerun tests. Resume-only/both Runs reach submit, but replay
test's permissive OR assertion is insufficient and changed-draft test name
overstates its actual initial-output coverage. Acceptance remains open.

Recovered implementation task from exact final transcript. Two tasks launched:
- Luna task_2956fcec871f / ctx_dec7ded48f7d: tests/report only, exact replay,
  no-publication adversarial public cases and honest prior command provenance.
- Terra task_1f2457252fdc / ctx_573c4f7e7737: independent stable production
  source/request/bundle authority review, report only. Does not treat Luna's
  evolving tests as settled proof or repeat large suites.

Both ready/input_accepted. ETA15–25minutes. Watcher updated;23:10UTC expiry
unchanged. No production edits assigned during independent review.

### Tailoring review findings queued behind current test owner

Read native Terra completion and SCOUT-08-run-integration-review.md. Fixed
source/byte authority and normal recorder paths accepted as a subset. Actual
public probe starts Tailor without request, and later resolver accepts a
request-shaped g45_reference; missing Plan-time exact typed request role is
blocking. Parser also accepts noncanonical +9 length token. Bundle recovery
is storage proof only, not standalone user-ready documents.

One dependency-status read confirms Luna still editing its public regression
tests. Released Terra review and queued `task_dc4854fd7add` dependent on
task_2956fcec871f. NOT launched: avoids source changes racing that verification.
Fix scope is Plan-sealed exact distinct G45 request identity plus canonical
bounded ASCII framing, with public no-publication and parser regressions.
Watcher tracks active test lane and queued correction, expiry unchanged.
Will dispatch Luna after actual test handoff. No coordinator suites or polling.

### Tailoring tests finished; request/framing correction dispatched

Fallback notice triggered one exact dispatch read. Final transcript confirms
tests-only task completion despite failed IPC. Updated handoff reports8 tests
in99.37s and Ruff passing; exact replay now asserts created=False, payload,
HEAD and artifact-map equality. Changed-draft successor and actual malformed
producer/domain refusals now have public-path evidence. Prior62/58-test counts
remain explicitly prior provenance, not current reruns. Source blockers from
Terra review remain unresolved by this test-only pass.

Recovered completed dependency task; queued task_dc4854fd7add is now dispatched
to the same Luna for Plan-time typed request binding and canonical bundle
framing. Ready/input_accepted confirmed; ETA20–30minutes. No test-owner race.
Watcher now tracks that correction only. Session-scoped fallback expiry extended
from23:10UTC to00:10UTC September11 to cover this correction and its bounded
review; cadence remains600seconds, no model polling or global agent install.
No coordinator suite rerun or provider/private-user action.

### Request/framing implementation finished; bounded re-review dispatched

Fallback changed-report notice handled with one exact worker read. Final
transcript confirms completion despite failed IPC. Read updated correction
evidence:23 integration/framing tests136.26s,26 pure packet tests0.08s,Ruff and
64-schema verification passing. Recovered completion and released dispatch.
No coordinator rerun, no acceptance inferred from implementation alone.

Reused original independent Terra reviewer for `task_feb1d96e2b90`, focused on
Plan-sealed typed request identity and canonical framing, schema/old-reader
behavior and actual test guards. Source/test inspection plus at most two short
probes; previously reported suites not repeated. Ready/input_accepted confirmed;
ETA10–15minutes. Watcher maps updated;00:10UTC September11 expiry unchanged.
Only that review is active in this wave. Whole UI/finalization/Scout remains open.

### Nested request gap isolated; Luna correction dispatched

Read native Terra completion and SCOUT-08-request-framing-review.md. F2 canonical
framing accepted. Plan-sealed request identity and redemption accepted as a
subset, but F1 remains open: nested evidence mappings are not strictly typed
before Plan/Run allocation. Review used two disposable probes, not suite reruns.
Historical v2 tailoring Plans lacking the descriptor now fail strict parsing;
this is an explicit unsupported boundary, not historical continuation proof.

Released the completed Terra dispatch. Reused Luna for task_c71e70f8a3ba /
ctx_a8a3e50f430e, ready/input_accepted confirmed. Scope: bounded nested reference
shape validation and public atomic Plan-refusal regressions; preserve accepted
F2, sealed descriptor and inventoried .075 bytes. Draft-byte membership and
semantic checks remain at checkpoint, not Plan time. ETA 15–25 minutes.

Confirmed launchd fallback remains loaded at 600-second cadence, unsettled;
updated its watched task, retaining September 11 00:10 UTC expiry. No coordinator
suite reruns, provider calls, activation or private user data changes.

### Nested correction delivered; typed enum edge correction dispatched

Scheduled report-change notification handled with one exact worker read. Final
transcript confirms completion and failed worker IPC; recovered task completion
without fabricating worker_done. Read source, new tests and evidence: worker
reports 11 new tests in 43.52s and combined 24 tests in 175.41s, Ruff clean.
Coordinator did not rerun those suites. Public negatives assert the exact typed
refusal and unchanged journal HEAD/artifact map for all five nested families.

Three coordinator no-write canonical-request probes found remaining TypeError
leaks: requirement assessment=[], claim status={}, and draft document_kind=[].
These enum membership checks need explicit string guards. Not accepted yet.
Reused Luna for task_6b4704e83422 / ctx_c1e6e530514e; ready/input_accepted.
Scope is those typed refusals, analogous request-only guards and cheap boundary
coverage, plus bounded public refusal/valid-start checks; no combined suite rerun.
ETA 5–10 minutes. Existing fallback now watches this follow-up, expiry unchanged.

### Tailoring request correction accepted; discovered-posting resolution next

Scheduled notification handled with one exact worker read; final confirms the
enum correction completed despite worker IPC failure. Recovered task completion.
Coordinator inspected the JSON-type matrix, bounded-reference guards, and public
no-publication assertions, then independently repeated all three original
no-write enum probes: each now raises ScoutTailoringError/tailoring_input_invalid.
Accepted this bounded F1 correction; prior F2 acceptance stands. No suite rerun.
Worker evidence records the initial test-only invalid expectation (unsupported
is a valid claim status), its correction, and 24 bounded checks in 14.54s plus
Ruff. The full 35-case file was not rerun after that test-only correction.
This does not close all SCOUT-08 or remove the documented historical Plan limit.

Reused Luna for task_e9d291832ba7 / ctx_1e2fa5776d6f, ready/input_accepted:
SCOUT-07 exact discovered posting resolution. New module/test/evidence only;
prove same-Gig completed discovery provenance and exact supporting posting
bytes under the caller-owned committed snapshot before wiring the input caller.
Actual public completed-discovery fixture required; no user data or providers.
Selecting a posting must not start Tailor or record an application. Downstream
Plan integration and durable opportunity application linkage remain open.
ETA 20–30 minutes. Fallback watches the new task; expiry extended to September 11
00:40 UTC to cover implementation and bounded review, cadence unchanged.

### Posting resolver delivered; independent authority review dispatched

Scheduled notification handled with one exact worker read. Final transcript
confirms delivery despite failed completion IPC; recovered task and released
dispatch without stopping a process or fabricating worker_done. Read evidence:
7 tests in 42.57s and Ruff reported; no coordinator suite rerun. New resolver
is 645 lines, new test file 185 lines. Caller/Tailor wiring is not implemented.

Coordinator test inspection identified evidence boundaries for review: the
tamper negative modifies an in-memory JournalSnapshot, not a committed journal;
the no-match negative requests missing IDs, not a genuine no-match Run.
These do not by themselves establish a production defect, but stronger
committed/no-match provenance must not be claimed from those cases.

Terra task_86bef67fe306 / ctx_e2aecd73e09d ready/input_accepted for independent
source/test authority review. Report-only, maximum two cheap probes, no broad
suite rerun. Assess exact completion/source/posting authority and meaningful
remaining gaps before downstream integration. ETA 10–15 minutes. Watcher
updated to that review; September 11 00:40 UTC expiry unchanged.

### Posting resolver review accepted; public-path evidence gate dispatched

Read Terra native completion and full SCOUT-07-posting-input-resolution-review.md.
No blocking correctness defect found in the bounded read-only resolver. Raw
snapshot resolution is caller-trusted; the first integration must hydrate from
committed journal under the caller-held writer or use the one-call public helper.
Review did not rerun reported tests. Foreign/incomplete/multiple-terminal receipt
branches and a genuine no-match Run remain public-path evidence obligations.

Released completed Terra dispatch. Luna task_908aff4a2098 / ctx_779723f34ef3
ready/input_accepted for focused tests and honest evidence labels only. Require
actual foreign Gig, incomplete lifecycle, deliberate committed double-terminal
fixture and true no-match packet; exact refusal and unchanged journal assertions.
No production refactor or Tailor wiring in this task; genuine source defects
must be reported, not hidden by weakened fixtures. ETA 15–25 minutes.
Watcher updated; expiry extended to September 11 01:00 UTC to cover this gate
and handoff, same 600-second cadence. No coordinator suite rerun.

### Public posting evidence accepted; Tailor caller integration dispatched

Read exact final transcript and verification note after scheduled notification;
worker finished despite failed IPC. Recovered completion without worker_done
fabrication. Coordinator inspected public happy path, real foreign Gig/Run,
public cancellation and adversarial second-terminal fixture with authenticated
unique publication refs. Fixture packet explicitly uses no_match; absent-ID
selection is refused without claiming all captured but nonmatching jobs are
unselectable. Accepted bounded evidence gate. Worker reports 11 tests88.85s and
Ruff; no coordinator rerun. Copied-snapshot tamper now labelled honestly.

Luna task_e9734830561e / ctx_a27e1fe925d4 ready/input_accepted for actual public
discovery-to-Tailor input integration. Direction: explicit completed-output
selector, following research input reuse; direct committed discovery capture
provenance, not a false operator_paste import. G45 remains canonical for actual
imports. Must preserve strict scalar request binding, same writer/head hydration,
historical output bytes and all existing input families. Fixed .074/.075 inventory
must not be altered; if existing renderer cannot represent the distinct input,
report the versioning requirement instead of relabelling or weakening authority.

Required evidence is real discovery completion through Tailor Plan/start/
checkpoint/submit/replay, plus narrow no-publication and compatibility checks.
No UI/application-state claims. ETA30–45minutes; scheduled fallback updated and
extended to September11 01:30UTC, unchanged 600-second cadence.

### User-directed local proposals and privacy spike

Prior integration worker stopped at fixed .075 input admission after a public
Plan-sealing probe. Its task was recovered as failed, not accepted; partial
scout_inputs/external_recording edits remain unaccepted. Promised integration
report was absent and worker removed its incomplete test. No claim of discovery
through Tailor completion is made. That work is now paused for the user-directed
product boundary: discovery ends in stored job proposals, never automatic resumes.

User explicitly requested Luna spike on local aggregation, evolving preferences/
resumes, daily fit proposals and enforcing personal data never leaving the machine.
task_40c807abf124 / ctx_8cc6593153a0 ready/input_accepted on existing Luna terminal.
Output SCOUT-local-proposals-privacy-spike.md; design/evidence only, no production
changes or actual user private data. Scope includes enforceable public-research /
local-private-analysis separation, local detector limits, hosted CLI isolation,
SQLite/journal ownership, bounded scheduled execution and concrete acceptance
tests/implementation slices. This spike does not itself approve a new contract.

ETA20–30minutes; no parallel implementation while boundary is unsettled. Previous
launchd watcher expired and was absent when checked. Retargeted session watcher
for this spike and expiry September11 05:40UTC; restarting its existing 600-second
fallback, no model polling or background suite duplication.

### Spike delivered; user-authorized correction pass dispatched

Read full spike and exact worker final; recovered delivery after IPC failure,
released old dispatch and stopped its fallback. No implementation contract
accepted. Coordinator identified four design gaps: private fit/rejection
reasoning mixed with public acquisition; insufficient enforceable v0.1.7 privacy;
local private UI conflated with export; simple UI and useful model-assisted
proposal scope weakened/deferred. User explicitly requested Luna improve it.

task_5d0f9bdaae73 / ctx_54b5ea306a00 ready/input_accepted on Luna. Docs-only
revision of the same spike, explicit response to four findings, concrete primary
execution topology and testable boundaries. No production/schema/roadmap edits,
actual PII, model installation, activation or provider task execution. Any
new runtime installation/authority requirement must remain a documented decision.
ETA20–30minutes. Restart existing 600-second fallback for this task with
September11 06:00UTC expiry; no continuous coordinator polling.

### User-authorized local runtime demonstration

Revised proposal/privacy spike delivered and reviewed; product separation improved,
but runtime isolation remains a proposal, not a demonstrated boundary. Prior
Luna dispatch recovered and released after IPC failure; fallback stopped.
User now explicitly requests Luna demonstrate the boundary before more work.

task_2fc61864a597 / ctx_a1eacb4638fa ready/input_accepted on Luna. Own synthetic
research/scout-local-boundary-demo/ and SCOUT-local-boundary-demonstration.md only.
Demonstrate useful bounded inference on installed Ollama separately from actual
file/network denial over inference server and descendants. Client-only isolation
or flags alone cannot be accepted as server isolation. Test denied operations
with allowed positive controls; label native GPU/runtime feasibility and any
experimental platform dependence honestly. No actual PII, installs, global
configuration/firewall changes, service disruption, model downloads or production
edits. Missing installation/admin authority must be reported, not assumed.
ETA20–30minutes. Restart existing 600-second fallback for this demo with
September11 16:20UTC expiry; no continuous coordinator monitoring.

### Demo partial; selective local communication follow-up

Read report, exact final and scripts after scheduled notification. Recovered
completion despite IPC failure. Two local qwen calls on task-owned unsandboxed
Ollama completed around32s each but exhausted220 tokens; no complete proposal.
Generic sandbox primitive denied sentinel reads/direct+child loopback. Actual
Ollama test only applied deny network* and failed at bind; actual server/runner
privacy remains unproven. Coordinator does not accept that this excludes
selective local IPC or establishes signed-helper/container installation required.

Luna task_b100784e27b3 / ctx_c4b6b51af765 ready/input_accepted for one bounded
selective-local-communication feasibility follow-up. Require actual inference
under server/runner policy, positive/negative controls for distinct endpoints,
explicit filesystem limitations, no blanket loopback privacy claim, sufficient
bounded proposal budget, and client proxy/redirect/model allowlist checks.
Synthetic-only; same research/report ownership, no installation/global changes
or production implementation. ETA15–25minutes. Watcher retargeted; existing
September11 16:20UTC expiry and 600-second cadence unchanged.

### User-authorized fixed-endpoint experiment

Selective-policy follow-up delivered and inspected: API11499 can bind, but
actual Ollama runner chose63662 and was denied. Generic direct/child endpoint
controls passed; actual enforced inference and full filesystem isolation remain
unproven. Worker recovered/released after IPC failure; prior fallback stopped.
User explicitly requested Luna continue figuring out controlled runner endpoint.

task_a0d2985e8427 / ctx_107fce8bf446 ready/input_accepted. Scope: inspect installed
runtime/help for fixed runner control, then if needed test bundled runner directly
with fixed endpoint and existing model bytes. Direct runner is a distinct harness,
not Ollama API. Require actual bounded generation under inherited policy, negative
controls, explicit filesystem/portability limits and no unrestricted localhost.
Research/report only; no installs, global changes, private data or production edits.
ETA20–30minutes. Restart 600-second fallback with September11 17:05UTC expiry;
baseline existing report timestamp under new task to avoid stale startup notice.

### Operator accepts trusted local runtime; parallel implementation resumes

Direct bundled-runner spike demonstrated inference through Unix IPC, not an
acceptable proposal or full privacy boundary. Source review found unrestricted
Unix-socket exceptions and allow-default filesystem; no absolute privacy claim.
Worker settled/released and watcher stopped. Operator now explicitly accepts
correct local identification and asks to move on finishing Scout instead of
more isolation work. SCOUT-local-runtime-decision.md records this new boundary;
spike and roadmap point to it, without rewriting historical approvals.

Two Luna implementation tasks ready/input_accepted with disjoint ownership:

- task_5ec587742dc6 / ctx_9f7c7292ccda: core Ollama local transport in new
  adapters/ollama_local.py and focused offline tests. Existing configuration,
  factory and invocation lifecycle integration remain a subsequent reviewed gate.
- task_849338a75730 / ctx_531b21e84198: new Scout private proposal builder/
  validator and synthetic tests; immutable input identity, useful reasons/gaps/
  questions, no auto-Tailor/application. Journal/projection/UI wiring remains next.

No sandbox/container installation required. Local identity/proxy/redirect/cloud
refusal and no-hosted-fallback remain required; test data synthetic only. These
standalone slices are not full release acceptance. ETA25–35minutes. Restart
600-second session fallback for both tasks with September11 17:30UTC expiry;
no continuous coordinator waiting or duplicate suites.

### Local adapter and proposals delivered; focused independent reviews

User completion prompt triggered one exact read per worker. Both final transcripts
confirm implementation delivered despite failed IPC; recovered completion without
fabricated worker_done. Read both reports: adapter31tests0.07s, proposals12tests
approximately0.06–0.07s, Ruff clean, all worker-reported offline evidence. No
coordinator suite rerun. Neither configuration/runtime integration nor durable
proposal journal/SQLite/UI is delivered by these slices.

Independent review wave ready/input_accepted:
- Terra task_3209305e2d70 / ctx_f5a62d85cac5: adapter endpoint/model/digest,
  proxy/redirect/output bounds and real API compatibility. Old Terra terminal
  was agent_unconfigured; launched fresh term_687087e1-bbc8-40f1-bd11-56d8de129ab8.
- Luna task_d5dfd85a3d2f / ctx_068546dea519: proposal DTO/actual source identity,
  exact lineage, malformed errors, evidence claims and practical generation
  contract. Reuses adapter author, independent of proposal implementation.

Released proposal implementation dispatch. Both reviews report-only, maximum
two cheap probes each, no broad suite or OS-isolation reopening. ETA10–15minutes.
Watcher retargeted to reviews; September11 17:30UTC expiry unchanged.

### Adapter review findings assigned; proposal review remains independent

Read Terra native completion and full adapter review. Documented bare Ollama
digest cannot match literal prefixed configured digest; positive mocks masked
real API incompatibility. Also missing assistant-role/client response bounds and
port-zero refusal. Two offline reviewer probes, no suite or model rerun. Adapter
not accepted yet; trusted-runtime decision unchanged, no OS-isolation reopening.

Released Terra review. Luna task_267271386aea / ctx_6c229d9ace08 ready/input_accepted
on idle proposal-implementation terminal; owns adapter fixes/tests/evidence only.
Original adapter author continues independent proposal review on disjoint files.
Correction scope: canonical digest normalization, official-shaped mocks, strict
assistant/count checks, pre-materialization streaming byte bound, port validation.
No config/factory/production caller expansion. ETA10–20minutes. Watcher updated
for correction plus existing proposal review;17:30UTC expiry unchanged.

### Adapter corrections inspected; runtime wiring and proposal corrections

Scheduled notice handled with one exact read per dispatch. Both finished despite
IPC failure; recovered deliveries without fabricated worker_done. Read reports.
Adapter44tests0.10s and Ruff reported. Coordinator inspected canonical bare/
prefixed digest normalization, assistant-role/count guards, streamed byte bound
and associated tests; accepts this bounded correction, not whole runtime. No
suite rerun. Constructor now accepts canonicalizable bare digest too (implementation
report's prefixed-only constructor wording is narrower than current source).

Proposal review changes requested: invented universal ref_/revision_ pairs don't
map to actual source authorities; model repeats host lineage, empty complete
proposals pass, renderer leaves active Markdown/omits sections, malformed DTOs
leak incidental errors. Local routing and registered invocation role are caller
gates, not proof from prompt text. Both workers immediately reused, disjoint:

- task_8902dc1289cf / ctx_425325ca8726: core config/factory/invocation integration,
  explicit local permission distinct from hosted network, digest policy, resource
  lifecycle, synthetic durable invocation fixtures; no live model/private data.
- task_d8bb81d06f50 / ctx_86898a96f696: correct proposal DTO to real source
  families/refs, host-owned lineage, useful sections, typed refusals and safe
  renderer. Pure source authentication remains caller responsibility; no hidden
  journal/config changes or fabricated durable IDs.

Both ready/input_accepted; ETA30–45minutes runtime,25–35minutes proposals.
Watcher updated and expiry extended to September11 18:00UTC; no broad suites,
continuous coordinator waits, OS isolation work, activation or real PII.

### Proposal correction handoff and bounded coordinator findings (17:13 UTC)

Received genuine worker_done for task_d8bb81d06f50 / ctx_86898a96f696;
read its exact final transcript and SCOUT-proposals-corrections.md. Worker
reports 24 synthetic tests in 0.10s and clean Ruff/formatting. Source inspection
confirms compact source handles, host-attached lineage and inert full-section
Markdown. No claim of authenticated caller or end-to-end acceptance.

Coordinator reproduced a remaining malformed-output crash: setting
proposed_resume_focus.state to [] in the synthetic proposal raises TypeError
from _section set membership. Also requested removal of mandatory nonempty
blockers/unknowns: absence of known concerns must not force invented content.
Meaningful fit assessment and explicit focus/question states remain required.

Same Luna terminal immediately reused for task_2657f36d80d6 /
ctx_0da208d615d3, ready/input_accepted, bounded to proposal module/tests and
SCOUT-proposals-validation-followup.md. ETA 10–15 minutes. Runtime integration
task remains separate and was not interrupted. Fallback watches the new task;
no broad test rerun, live model call, or private data access occurred.

### Proposal validation follow-up accepted (17:16 UTC)

Read genuine worker_done and final transcript for task_2657f36d80d6 /
ctx_0da208d615d3. Both bounded coordinator findings resolved; independently
ran the proposal suite: 35 passed in 0.11s. No model or full-suite run.
The worker disclosed that it amended the earlier corrections evidence instead
of writing its promised report. Coordinator created the missing
SCOUT-proposals-validation-followup.md with explicit authorship and verification
provenance. This accepts only the pure validator follow-up, not end-to-end Scout.
Worker release requested; core runtime integration remains the active dependency.

### Runtime completion recovered; independent review dispatched (17:17 UTC)

Scheduled fallback reported runtime handoff present with task still dispatched.
Read exact ctx_425325ca8726 final: implementation complete, worker_done IPC failed
with Orca unavailable. Read SCOUT-local-runtime-integration.md; 48 focused tests
reported, schema inventory and existing AST-test failures explicitly retained.
Fenced stale dispatch through worker-abandon (no process action), completed task
with recovery provenance and independent acceptance still pending. Release retained
terminal with identity_unproven; no process stopped.

Independent Luna (proposal author, not runtime author) assigned
task_0f9cad11140b / ctx_399aa1c3cadf, ready/input_accepted. Review owns only
SCOUT-local-runtime-integration-review.md; concentrates on local/hosted permission
separation, source privacy, identity, transport lifecycle, strict schema history
and inventory provenance. Synthetic focused probes only. ETA 15–25 minutes.
Watcher now follows this review; expiry remains 18:00 UTC. Proposal integration
and live model acceptance are not claimed by the implementation handoff.

### Runtime changes requested; local Qwen extraction enters rotation

Read worker_done and exact final for task_0f9cad11140b / ctx_399aa1c3cadf,
and SCOUT-local-runtime-integration-review.md. Independent Luna found reachable
binding cleanup leak at request construction, missing durable local endpoint/model
digest pinning, and in-place invocation-schema change plus inventory conflicts.
Six focused synthetic tests passed; installed-schema verification failed. This
is changes requested, not accepted runtime integration.

Two disjoint Luna tasks are now ready/input_accepted:

- task_641375350f78 / ctx_aeab1c20c20e: runtime author corrects cleanup,
  durable identity and explicit versioned schema compatibility; reconcile inventory
  with provenance, preserve unrelated dirty schema content, remove fictional CLI
  example. Focused synthetic verification only; ETA 25–40 minutes.
- task_8157a32d879e / ctx_250d89e3df9f: reviewer reused for a small research-only
  Qwen/Ollama extraction test. Two synthetic postings, exact salary/sponsorship/
  location values plus source quotes, deterministic expected results. Existing
  numeric-loopback daemon only; exact installed model identity, no pulls/server
  starts/cloud fallback/private data. At most two inference calls with bounded
  output and five-minute run deadline. No imports of concurrently changing
  production runtime. ETA 10–15 minutes. Evidence SCOUT-qwen-extraction-demo.md.

All implementation remains Luna; Qwen outputs are evaluated data, not authority
to edit or apply. Watcher follows these tasks through 18:15 UTC. Native doctor
integration discussed with user remains unimplemented; no extra diagnostic
scope was silently added to this runtime-correction lane.

### Qwen worker environment limitation; coordinator run started

Read task_8157a32d879e / ctx_250d89e3df9f worker_done, exact final and
SCOUT-qwen-extraction-demo.md. Harness prepared; worker loopback connection
failed with Operation not permitted before inference. No Qwen quality result
from that attempt. Release retained the external terminal without process action.

Coordinator inspected the complete harness and synthetic fixtures. A five-second,
proxy-free GET of the same 127.0.0.1:11434/api/version succeeded (0.34.0), so
the worker failure does not demonstrate daemon unavailability. Started the
existing bounded two-call harness from the coordinator environment, exec session
87085; results pending under research/scout-qwen-extraction/runs/. No daemon
start, configuration change, hosted fallback or real private data. This is a
research run, not production GigAI invocation acceptance. Runtime corrections
continue independently.

### Runtime correction recovery and first real Qwen extraction results

Scheduled fallback led to one exact runtime worker read: task_641375350f78 /
ctx_aeab1c20c20e finished; IPC completion failed. Read corrections report,
fenced stale dispatch without process action, settled task with explicit recovery
and pending review. Worker reports 50 focused tests, eight integration tests and
65 schema checks. Not independent acceptance yet.

Coordinator background session 87085 finished: two Qwen/Ollama responses complete
in 36.002s total. Original status invalid; inspectable run 20260911T173135Z shows
correct salary/sponsorship facts and source quotes, but hidden location enums and
overly exact expected quote grading made the test unfair. See coordinator addendum
in SCOUT-qwen-extraction-demo.md. Historical results remain unchanged.

Both Luna terminals reused, ready/input_accepted:

- task_7f5f33cda886 / ctx_f1906cc3fbda: previous independent runtime reviewer
  verifies bounded corrections and actual versioned reader behavior; report
  SCOUT-local-runtime-corrections-review.md, ETA 10–20 minutes.
- task_02ad8c6399c3 / ctx_045aefb9228b: runtime author now owns research-only
  extraction evaluator correction, explicit enum contract, honest supporting-quote
  grading and preserved failure evidence; report
  SCOUT-qwen-extraction-evaluation-correction.md, ETA 10–15 minutes. Offline only;
  no further model calls in that worker. No production overlap with review.

Watcher follows these tasks; no Terra implementation, hosted calls, private data
or repeated coordinator waiting.

### Coordinator adjudication and saved-input proposal caller (17:47 UTC)

Both worker finals read; both completion IPCs failed. Recovered task_7f5f33cda886
and task_02ad8c6399c3 via stale-dispatch fencing (no process action) and explicit
task results. Runtime re-review has contradictory/stale schema claims: coordinator
fresh verifier passes 65 schemas, historical model-invocation.schema.json has no
git diff. Combined local-integration and research-evaluator tests: 13 passed in
4.24s. Do not accept the review's repeated old checksum failures as current facts.
Self-contained configured identity/digest journaled by host is the implemented
bounded contract; separately journaled readiness authority is not silently made
a new release requirement. Actual Scout proposal caller remains real next work.

Qwen evaluator source and tests inspected; offline correction accepted for its
two-fixture research scope. A fresh two-call local run started from coordinator
as exec session 30703; no new model/network calls assigned to workers.

Two Luna tasks ready/input_accepted:

- task_f4d4b20512b0 / ctx_729464c86777: correct stale review document from live
  evidence, no production edits; ETA 5–10 minutes.
- task_bab599850aaf / ctx_1b5519cf64fe: actual authenticated saved discovery and
  private selected inputs through local reviewer invocation, durable host-bound
  proposal result, synthetic real-journal reader evidence. No automatic Tailor,
  applications, UI/server/scheduler or real private data; ETA 35–50 minutes.

Watcher now follows these tasks through 18:45 UTC. Review-document repair is
independent of caller implementation; no duplicate broad runtime review launched.

### Both handoffs recovered; proposal caller under independent review

On user completion prompt, read both exact finals and reports. Worker completion
IPC failed for both; fenced ctx_729464c86777 and ctx_1b5519cf64fe without process
action and settled tasks with explicit recovery evidence.

Corrected runtime review agrees with coordinator's fresh schema/byte checks;
bounded runtime corrections accepted. New proposal caller reports 39 tests in
53.63s and clean Ruff. Coordinator inspected source: real authenticated discovery
and private sources, local reviewer invocation and journaled result are wired,
but caller still requires real G45 reference anchor, only validates Run/goal ID
shape before goal terminal event, and has no replay deduplication. These are
limits to assess, not whole-feature acceptance. No coordinator suite rerun.

Author release retained identity_unproven terminal, no process action. Prior
reviewer reused for task_02a32846d49b / ctx_20bc9aff78af, ready/input_accepted;
owns SCOUT-proposal-execution-review.md only. Review prioritizes actual Run/goal
authority, recorded source coverage and source-purpose mapping, real reader
integration and durable artifact contract. Synthetic bounded probes only;
ETA 15–25 minutes. Watcher follows this task through existing 18:45 UTC expiry.

### Proposal caller review changes requested; bounded corrections dispatched

Scheduled fallback delivered review handoff hint. Read exact ctx_20bc9aff78af
final and SCOUT-proposal-execution-review.md; completion IPC failed. Recovered
task_02a32846d49b via stale-dispatch fence and explicit completed-review result.
Release retained reviewer terminal with identity_unproven; no process action.

Concrete findings: caller admits unallocated or terminal Run/goal IDs then writes
goal terminal events; nested model invocation completes Goal before domain
validation, producing duplicate/contradictory events; source purpose inferred
from storage family mislabels resume/experience and answers. Prior root source
inspection is consistent with these findings. Review supplies disposable repros;
no full test suite repeated by coordinator.

Luna author assigned task_4cb869f9c06b / ctx_9cc0bfbf5c48, ready/input_accepted.
Own bounded caller/runtime integration corrections: actual committed Run/graph/
goal/effect eligibility before execution and publication, single owning Goal
terminal event after domain validation, explicit host-owned private source
purpose. Preserve accepted transport and existing invocation caller compatibility.
If existing allocation/effect contract cannot supply authority, refuse rather
than manufacture it. G45 anchor and later UI/replay/revision work stay disclosed.
Evidence SCOUT-proposal-execution-corrections.md; ETA 30–45 minutes. Watcher
updated through 19:15 UTC, no hosted/local model call or private data in worker.

### Proposal correction handoff: remaining terminal-state consistency

Read task_4cb869f9c06b final and SCOUT-proposal-execution-corrections.md;
worker reports six proposal tests, seven G18 tests and clean Ruff. IPC completion
failed; stale dispatch fenced and task recovered without process action.
Source inspection confirms active Run/graph membership checks, explicit source
purpose, and invocation-only evidence held for one domain result publication.
The fixture still publishes test-only active state after deterministic execution;
this is not a scheduler-owned live proposal entry.

Coordinator found a remaining state-consistency defect: eligibility reads running
Run Details, but _publish_result writes no updated Run Details and does not check
prior Goal terminal history. Repeated calls can therefore remain eligible after
the first result. Same Luna reused for task_7f20152a5bd0, ready/input_accepted;
bounded atomic state/event correction and sequential repeat regressions with real
reader evidence. Capture concurrent terminalization/evidence-loss limitation
honestly, no duplicated Goal event or invented whole-Run completion. Evidence
SCOUT-proposal-terminal-consistency.md; ETA 15–25 minutes. Watcher updated.

### Sequential terminal consistency delivered; real proposal Run entry next

Read ctx_ae65f8bc8d81 final and terminal-consistency report. Worker completion
IPC failed; fenced stale dispatch without process action and recovered
task_7f20152a5bd0. Eight focused tests reported passing (125.83s) and clean Ruff.
Coordinator inspected the atomic Run Details/result publication and committed
terminal-history guard; no repeat of the two-minute suite. Accept only bounded
sequential terminal consistency and publication duplicate guard, not scheduler
integration or durable in-flight cancellation behavior.

Real proposal Run allocation remains absent: fixtures manually reactivate
deterministic Runs. Same Luna now task_b90861e91840, ready/input_accepted,
implementing a sealed explicit proposal execution entry through existing Run
authority/consent/allocation/Goal start and terminal lifecycle. Must not select by
role/name or fabricate active state. Preserve alreadyperformed invocation evidence
when a concurrent terminalizer wins without second Goal completion. Also replace
terminal-history read-failure-as-no-history behavior with fail-closed reads.
Evidence SCOUT-proposal-run-integration.md; ETA 45–60 minutes, watcher through
20:00 UTC. No live model/private data/UI/daily scheduler/activation/commits.

### Proposal Run entry delivered; coordinator authority findings

Read exact final and SCOUT-proposal-run-integration.md on fallback notification.
Worker reports two real-entry tests, eight caller tests, six G13 tests and Ruff;
IPC completion failed. Recovered task_b90861e91840 with explicit changes-requested
disposition, fenced stale dispatch without process action.

Coordinator source inspection found: local assessment repurposes the approved
tailor-application graph; proposal branch strips graph_authority and selected
descriptor before Run manifest creation; post-seal execution reloads configuration
and reuses mutable request selectors; exception path may mark Goal failed after
another terminalizer already won, while invocation evidence race remains open.
Do not accept successful fixture execution as resolution of these authority gaps.

Same Luna reused for task_44d42d56c571, ready/input_accepted: distinct versioned
proposal operation preserving historical Tailor, full selectedGraph/v2Manifest
provenance, immutable sealed request/target execution, and single-terminal race
handling with invocation evidence preservation or explicit unresolved receipt
contract. No user activation, live models or broad redesign. Evidence
SCOUT-proposal-run-authority-corrections.md; ETA 40–60 minutes. Watcher through
20:30 UTC; no coordinator repeated test suite.

### Distinct proposal entry correction delivered; independent review

Read final ctx_2bed02cfbea4 and SCOUT-proposal-run-authority-corrections.md.
Recovered task_44d42d56c571 after failed completion IPC; fenced stale dispatch
without process action, release retained identity_unproven terminal. Worker
reports three entry tests, 19 combined source-bundle/entry tests and clean Ruff.
Coordinator inspected separate proposal-assessment registration, v2 graph metadata
and execution from sealed selectors/config snapshot; full acceptance pending.

Independent Luna task_28bba33f12c3 now ready/input_accepted, owns only
SCOUT-proposal-run-authority-review.md. Focus exact approved operation/package
inventory, v2 identity/readers, immutable request/target, real lifecycle/race
evidence and backward compatibility. No duplicate broad runtime review or model
calls. Post-invocation cancellation evidence remains explicitly open, not silently
closed by refusal of a duplicate Goal event. ETA 15–25 minutes; watcher updated.

### Independent entry review findings assigned together

Read ctx_38b1c0ae1a55 final and SCOUT-proposal-run-authority-review.md.
Completion IPC failed; recovered completed-review task_28bba33f12c3 with changes
requested and released retained identity_unproven terminal without process action.
Accepted controls: separately inventoried operation, approved Graph Set/v2
selection manifest, sealed immutable request/config, explicit consent, local
source and model-output boundary. Findings: F1 alias-only operation substitution;
F2 extra/nonentry Goals admitted; F3 post-Goal terminalization failure strands
Run; F4 completed invocation evidence lost on pre-publication cancellation race.

Luna author assigned task_bf7529b35428, ready/input_accepted. Own four focused
corrections and corresponding synthetic regressions, preserving historical
contracts. F4 requires a real minimal durable attempt/evidence path, or a concrete
blocking authority decision instead of another generic deferral. No long-held
writer lock through inference or invented second Goal/Run outcome. Evidence
SCOUT-proposal-run-final-corrections.md; ETA 35–55 minutes. Watcher through
20:45 UTC; no coordinator duplicate test suite or model run.

### Final correction handoff recovered; receipt acceptance review

On user completion prompt at September 12 01:28 UTC (September 11 local), read
ctx_55ad668affd7 exact final and current receipt source. Recovered completed
task_bf7529b35428 after failed IPC; stale dispatch fenced, author release retained
identity_unproven terminal without process action. Worker reports six Run tests
plus two-case focused checks and clean Ruff. No coordinator full-suite rerun.

New nonterminal proposal_invocation_recorded receipt commits invocation artifacts
before domain result. Source inspection shows reader validates invocation shape
and IDs, but receipt refs and pinned snapshot authentication need focused checking
before acceptance. Independent Luna task_bea96caea0b0 / ctx_36b4d1e27f25 now
ready/input_accepted, owns SCOUT-proposal-run-final-review.md only. Prior F1-F4
correction scope; minimal adversarial receipt and terminal-recovery checks, no
new architecture scope. ETA 15–20 minutes. Expired fallback renewed through
September 12 02:15 UTC for this worker; no continuous coordinator wait.

### R0 launched from release execution graph

Processed scheduled fallback delivery_1b513986dd8c and read the reviewer's exact
completed transcript. IPC failed; recovered task_bea96caea0b0 with changes
requested and released its dispatch (retained identity_unproven, no process
action). Review confirms the remaining receipt byte/reference/actor/target
binding defect and missing narrow regressions; no broad runtime rerun.

Luna author now owns task_de1d3ab8de36 / ctx_6f58b106bcff, ready/input_accepted:
R0 receipt repair plus concrete shared interfaces for the three product lanes
in SCOUT-release-execution-graph.md. Evidence SCOUT-R0-implementation.md and
SCOUT-R0-shared-interfaces.md. Milestone estimate 60–120 minutes, not a release
ETA. R1–R3 source work waits for these interfaces. Existing ten-minute fallback
retargeted through September 12 04:15 UTC. No active coordinator polling.

### R0 handoff received; R1, R2, R3 launched in parallel

September 12 02:01 UTC: read exact R0 final transcript and both handoff documents.
Worker reports 10 focused Run tests passing in 152.25s, tamper smoke passing and
clean Ruff. Coordinator inspected the pinned receipt-reader changes; no repeat
suite or independent acceptance claim. Recovered task_de1d3ab8de36 after failed
worker IPC, fencing only its stale dispatch; immediately reused the author.

| Lane | Task / dispatch | Luna terminal | Evidence |
|---|---|---|---|
| R1 jobs, proposals, answers | task_77958116146f / ctx_a157956d7c1f | term_96aeb8af-6826-4719-8ee6-aba6a0057225 | SCOUT-R1-implementation.md |
| R2 requested Tailor, documents | task_80f6c1edd25e / ctx_c880cfc5f7c1 | term_09740ecd-2a8a-4340-bd2d-ffa4276152b8 | SCOUT-R2-implementation.md |
| R3 tracker, applications | task_e9170c226d71 / ctx_f235548ce673 | term_57981590-6bb8-437d-aab2-a446476348b1 | SCOUT-R3-implementation.md |

All three returned ready/input_accepted. R3 is a fresh Luna-high session with
approve-for-me; R1/R2 reuse existing Luna sessions. Same dirty worktree, explicit
nonoverlapping ownership: R1 owns native/private record extensions; R2 owns
Tailor/input/external-recording changes; R3 owns new report/projection modules
and application files. Shared CLI/Run/journal/schema-registry/source-inventory
changes are concrete integration patches for one R4 owner, not simultaneous
edits. Worker tests remain distinct from the combined R4 acceptance journey.

Planning estimates remain R1 6–10, R2 5–8, R3 5–9 worker-hours, not promises or
reasons to stop after a helper. Use completion events and the existing bounded
ten-minute fallback, now through September 12 12:30 UTC; no active waiting.
The user's updated release order is automated installed verification, publish,
then personal UAT and patch fixes. Human UAT is no longer a pre-publish gate;
implementation and automated acceptance remain required.

### First-pass lanes recovered; actual service completion and integration launched

Processed delivery_8c698d03ad3d: all three exact worker transcripts confirm
completion after failed IPC. Read all three handoffs. R1 delivered immutable
proposal records and answer associations but not native-only/public entry;
R2 delivered pure DTO/document helpers, not authenticated journaled Tailor;
R3 delivered a report renderer/projection with injected readers and application
read-time validation, not the real default reader/mutation path. Worker tests
are bounded evidence, not completed R1–R3 product acceptance. Recovered each
first-pass task and immediately reused its terminal; no process action.

| Continuation | Task / dispatch | Exact owner / evidence |
|---|---|---|
| R4 integration plus remaining R1 | task_1c683bd56a37 / ctx_b2b0b63a9f13 | existing R1 Luna; SCOUT-R4-integration.md |
| Actual R2 Tailor services | task_666b1b58d3cf / ctx_0b9473584887 | existing R2 Luna; SCOUT-R2-service-contract.md then SCOUT-R2-completion.md |
| Actual R3 readers and linkage | task_0789ed15dbbb / ctx_7ba566c2c50b | existing R3 Luna; SCOUT-R3-completion.md |

All returned ready/input_accepted. R4 now explicitly owns shared files,
registry/source inventory and R1/native/private code. R2 owns document/Tailor
hydration/execution/persistence modules; R3 owns production report readers and
application mutation validation. R2 publishes its concrete reader/service
contract early for peers. Shared changes funnel through R4, not concurrent
patch application. No isolated review cycle was started. Acceptance target is
the actual synthetic public-entry journey with committed records, then one
combined independent review; reports must not substitute helper tests for it.
Planning window for this continuation is 2–4 focused hours for integration,
with R2/R3 1–3 each in parallel, not a release promise. Existing ten-minute
fallback retargeted; no active coordinator waiting or duplicate suite run.

### R2/R3 continuation handoffs delivered to R4

September 12 02:39 UTC: processed delivery_fa2666c44396 and read both exact
completed worker transcripts plus SCOUT-R2-completion.md and
SCOUT-R3-completion.md. Recovered task_666b1b58d3cf and task_0789ed15dbbb
after failed IPC, then released both (retained identity_unproven, no process
action). R2 reports 20 focused tests and real synthetic document persistence;
R3 reports production readers and application mutation linkage, but its wider
checks had 12 bootstrap failures for missing model-invocation-v3.schema.json.
That is pending integration verification, not proven pre-existing failure.

Sent msg_a2998f4289a6 to active R4 dispatch ctx_b2b0b63a9f13 with both handoffs,
the schema/test caveat, and exact document-reader compatibility checks. R4 now
owns integration changes to the released R2/R3 modules as needed; their authors
are no longer editing. Shared worker remains undisturbed. R4 must finish schema
registration, rerun affected focused tests, and prove actual populated report
and complete public-entry workflow before combined independent review.
Fallback now tracks R4 only; no duplicate tests or ongoing polling here.

### R4 partial delivery returned for the missing downstream journey

Processed delivery_1cffef7d66eb and exact completed transcript. R4 reports two
passing synthetic tests and one skipped downstream acceptance test, plus Ruff
and 72-schema verification. Its peer-owned downstream deferral is stale: both
peers finished earlier. Source confirms the skipped test still raises that
their APIs are unavailable. Do not accept whole R4 or start final review.

Recovered task_1c683bd56a37 as partial delivery, then immediately reused Luna
for task_81e3751e3721 / ctx_d6f92e3e9392, ready/input_accepted. Fresh injected
task explicitly grants sole integration ownership across released peer files
and names existing document/final-selection/report/application APIs. Required
evidence SCOUT-R4-full-journey.md: actual non-skipped downstream journey,
saved answer/reassessment, final selection, populated report, explicit
application/correction/retry and fresh-session history. Must rerun R3's affected
checks after schema stabilization and report remaining product gaps honestly.
Prior queued inbox guidance was not proven consumed; this direct dispatch
removes reliance on worker IPC for the ownership update. Ten-minute fallback
retargeted; no coordinator suite repetition or active wait.

### Bounded full service journey delivered; product actions plus integrated review

Processed delivery_b15d362508bf and read exact completed transcript and
SCOUT-R4-full-journey.md. Worker reports 22 focused passing tests and 72-schema
verification. The formerly skipped downstream service test now executes, but
records saved/rejected events with empty document refs, lacks public answer/
final-selection actions, and supplies invented goal_synthetic/inv_synthetic
provenance to final selection. Coordinator inspected those test lines. Do not
call this complete user-facing R4 or release acceptance.

Recovered task_81e3751e3721 after failed IPC and immediately reused author for
task_1e80a6cb8249 / ctx_77933a5bc486: close application-to-Scout-document
references, actual explicit applied event, public final selection/answer
commands, bounded public-import deadline/progress and real CLI-backed journey.
Report SCOUT-R4-user-actions-completion.md. Author must remove fake invocation
provenance rather than treat shape-valid metadata as authority.

Independent fresh Luna-high reviewer task_9342b156c2e0 owns only
SCOUT-R4-integrated-review.md: consolidated R0–R4 authority/privacy/lifecycle
review, particularly generic artifact references, mutable manifest/Run reader
exceptions and native invocation v3. Journal/workpad/Run/proposal-execution/
invocation-v3 files frozen while reviewer examines them; author may finish
disjoint application/document/CLI/acquisition actions. Final changing surfaces
require integration-diff coverage later. No duplicated broad suite or model
calls. Fallback tracks both; no coordinator active wait.

### Integrated review received; grouped core fixes parallel to user actions

Processed delivery_e9aa7f18bf59, exact reviewer final and
SCOUT-R4-integrated-review.md. Recovered task_9342b156c2e0 after failed IPC,
released ctx_33b8de665631 (retained identity_unproven; no process action).
Review requests changes: nested proposal-source redemption, exact v3 selected
source descriptor coverage/pretransport types, overbroad mutable-manifest
exception, and unredeemed legacy final selection; also local Run/evidence
report coverage and R0 tamper evidence. Source-only review, not independent
exploit/test proof. Prior 22-test aggregate remains worker-reported unless an
exact reproducible command/scope is recorded; schema inventory is not a wheel.

Luna core correction task_3225bb3fb93a / ctx_c5f0dc91b762 ready/input_accepted,
reusing idle R3 terminal. Owns journal/model descriptor/proposal record/report
reader surfaces plus focused correction tests. Existing user-actions author
keeps exclusive application/document/CLI/discovery/journey files. Sent scope
update to active user-actions dispatch; core author must request any necessary
document helper rather than collide. Evidence SCOUT-R4-review-corrections.md;
confirm minimal defects and preserve legitimate paths rather than add broad
policy machinery. One grouped correction; no new independent review until
both surfaces are stable. Fallback tracks the two implementation tasks.

### Public actions delivered; shared manifest compatibility regression remains

Processed delivery_6029110a92c5, exact user-actions final and
SCOUT-R4-user-actions-completion.md. Recovered task_1e80a6cb8249 after failed
IPC and released ctx_77933a5bc486 (retained identity_unproven; no process action).
The report now supplies an exact grouped 27-test passing command and actual
answer/final-selection CLI plus explicit applied event with Scout document
references and real Tailor provenance. That earlier snapshot is bounded
evidence, not current green integration: a later journey rerun fails native
fixture setup because the concurrent narrowed journal reader rejects the
legitimate scout_source_materialized capability-manifest publication.

Sent the exact transition mismatch and reproduction to core-fix dispatch
ctx_c5f0dc91b762; require legitimate publisher compatibility without reopening
the arbitrary-manifest replacement exception, then one stable combined rerun.
User-actions files are no longer being edited. Core worker remains undisturbed;
fallback tracks it alone. No coordinator test duplication or acceptance claim.
Bounded acquisition currently classifies supplied rows/deadline progress only;
durable import progress remains an explicit product gap, not a shipped crawler
or scheduler and not silently accepted as full discovery implementation.

### Core corrections handed off; exact failed-assessment Run recovery assigned

Processed delivery_1f9440e586f0 and exact core worker final. Its report still
records invalid-result failure and stale peer-owned integration requests;
successful full journey passed in 158.90s, but that does not close the failure
path. Recovered task_3225bb3fb93a as delivered/not accepted after failed IPC.

Coordinator source inspection of run.py identified likely cause: unconditional
record_proposal_revision after an already-failed assessment raises, then the
exception path mistakes its own terminal Goal for a competing terminal Run
and leaves that Run running. Fresh directly injected task_986afa7f39ba /
ctx_4917088a5ac4 now ready/input_accepted on the same Luna terminal. Explicit
sole source ownership, including run.py; no peer or frozen-file gate remains.
Fix the exact failure without duplicate Goal/Run events, then one combined
seven-file regression suite, lint and schema inventory. Require accurate R0
tamper coverage claims. Evidence SCOUT-R4-terminal-recovery-completion.md;
independent acceptance remains after the stable combined result. Fallback
retargeted, no root test rerun or active waiting.

### Stable combined evidence delivered; independent R4 acceptance review started

Processed delivery_3fdd9cef0a85 and exact final transcript. Recovered
task_986afa7f39ba after failed IPC and released ctx_4917088a5ac4 (retained
identity_unproven, no process action). Full handoff records 43 combined tests
passing in 430.41s, seven committed tamper cases in 145.79s, lint and 73-schema
inventory passing. This is worker evidence, not independent acceptance or
isolated wheel verification. Invalid-assessment Run lifecycle repair is
delivered and no implementation worker remains active.

Previous independent Luna reviewer now task_33e8593f74d8 /
ctx_0cb721bb44e8, ready/input_accepted, owns SCOUT-R4-acceptance-review.md.
All implementation surfaces frozen while reviewing prior P1/P2 fixes and
current user-action/core integration together. Review separates bounded source
acceptance, actual remaining R4 product gaps (including durable bounded import
progress), and later R5/R6/R7 scope. Source/evidence first; only necessary
focused probes, not another seven-minute suite by default. No root duplicated
tests, providers or active waiting. Fallback tracks reviewer only.

### Bounded corrections independently accepted; final R4 gates parallelized

Processed delivery_a22281fb5bac and exact reviewer final/report. Recovered
task_33e8593f74d8 after failed IPC, released ctx_0cb721bb44e8 (retained
identity_unproven). SCOUT-R4-acceptance-review.md accepts bounded proposal
redemption, v3 descriptors, narrowed mutable manifests, v2 final selection,
local Run/evidence readers, terminal lifecycle and real-ID public actions.
Whole R4 remains incomplete: durable public acquisition progress/CLI and
bootstrap/replacement evidence, plus exact report-row assertions.

Two Luna tasks started, both ready/input_accepted:

- task_e6633035d221 / ctx_78c26e775ab8 on former user-actions author: implement
  durable public import/deadline/resume/status with actual CLI, immutable input
  snapshots, replay and exact journal progress. Own acquisition/CLI/unique
  schemas and registration; no core journal-reader changes. Evidence
  SCOUT-R4-acquisition-completion.md.
- task_162cfe81916e / ctx_3d7819119bc3 on former core author: tests/evidence only
  for legitimate one-publisher bootstrap/reviewed replacement/refusals and exact
  selected document/opportunity/Run/evidence rows in real journey. Reconcile
  the historical concurrent-edit failure against later stable passing evidence;
  do not assume it is still a source defect. Evidence
  SCOUT-R4-bootstrap-report-verification.md. Also verify the review table's
  forty-hex Git blob hashes, incorrectly labeled SHA-256, in the evidence note.

Disjoint source/test ownership, no universal core re-review, no duplicated
seven-minute suite, no crawler/scheduler or model calls. Fallback tracks both.

### Bootstrap and exact report-row evidence delivered

Processed delivery_f2faaca83b45 and exact completed transcript plus
SCOUT-R4-bootstrap-report-verification.md. Recovered task_162cfe81916e
after failed IPC; released ctx_3d7819119bc3 (retained identity_unproven,
no process action). Worker reports eight bootstrap/replacement tests passing
in 13.52s and strengthened real journey in 157.02s, plus lint/diff checks.
Coordinator inspected test cases/assertion coverage without rerunning tests.

Initial materialization and normal capability review use actual services;
same-path allowed/refused replacements are explicitly direct writer predicate
tests, not forged service approval. Exact document/opportunity/proposal/Run/
result-evidence rows now asserted. Historical bootstrap failure is reconciled
with the later one-publisher correction and current fresh passing evidence.
The review hash-table algorithm mislabel is clarified with actual Git blob
and SHA-256 values, without altering the independent verdict.

Durable public acquisition remains the outstanding R4 implementation task;
task_e6633035d221 stays assigned and undisturbed. Fallback now tracks it alone.
No independent wheel/provider acceptance or whole R4 completion implied.

### Fresh acquisition worker replaces non-delivery

September 12 21:39 UTC: user explicitly requested starting a replacement.
Prior task_e6633035d221 ended with an old user-actions summary; assigned
acquisition report/modules/CLI were absent on source inspection. Marked the
task failed after fencing stale dispatch, then released it. Do not count the
repeated prior report as acquisition implementation.

Fresh Luna-high session term_a1ff8e38-b448-4853-86ea-d9bdc6879232 launched with
approve-for-me for task_88b1241177de. Concrete required deliverables are actual
journal-backed public import/resume/status CLI and copied wrapper, immutable
batch input/progress, deadline/replay/fresh-process evidence and strict public
row schema. No crawler/scheduler or private matching. Own exact acquisition
files/tests and minimal registration changes; preserve accepted core controls.
Report SCOUT-R4-acquisition-completion.md must describe this new work, not old
user-action functionality. Expected 1–2 focused hours, not release ETA.
Expired fallback renewed through September 13 01:00 UTC; no active polling.

### Durable acquisition delivered; narrow independent review started

September 12 21:54 UTC: processed genuine worker_done msg_03c5d7a2e90d in
delivery_374e165db00f. Task task_88b1241177de / ctx_043334b4bcfc settled
automatically; no manual recovery. Exact final and acquisition handoff read,
CLI/copied wrapper registrations and new source/test files inspected. Worker
reports seven focused tests (three new acquisition cases plus four existing
discovery helpers), clean Ruff and 75-schema inventory. Acquisition input and
progress schemas, journal publication, resume/status commands now actually
exist. Released author dispatch; retained external_terminal, no process action.

The report's remaining bootstrap note is stale; bootstrap/exact report evidence
was already delivered. Only narrow independent acquisition acceptance is next,
not a repeat of accepted core or the seven-minute suite. Initial attempt to
reuse prior reviewer terminal found it operator-closed and created no dispatch;
left it untouched. Fresh Luna-high reviewer term_40acfe87-55d8-4037-9c05-bf9a464d7e0c
now task_c2ca72725f29 / ctx_c620af9b0bff, ready/input_accepted. Owns
SCOUT-R4-acquisition-review.md; validates public durable progress, replay/CAS,
scope, path/byte authority and real commands. No implementation edits or broad
release claims. Fallback now tracks reviewer only.

### Acquisition review: one parent-path defect assigned

Processed genuine worker_done msg_32e9ed9dccc0 / delivery_f8ef32c7a42a.
Review task_c2ca72725f29 settled automatically; released ctx_c620af9b0bff
(retained external_terminal). Read SCOUT-R4-acquisition-review.md: regular-path
durable acquisition controls accepted, but disposable probes accept symlinked
parent directories for persisted status and CLI/copied-wrapper rows input.
One concrete P1, not a bootstrap/core reopening.

Acquisition author reused for task_b6dbf5cdd6f0 / ctx_fdcd11240377,
ready/input_accepted. Narrow shared parent-path guard/reuse and actual negative
tests for each relevant ancestor/final component, unchanged journal on refusal,
regular deadline/resume/replay compatibility. Evidence
SCOUT-R4-acquisition-path-correction.md. No global journal/schema redesign,
no broad suite rerun, no root polling. Fallback tracks correction worker.

### R4 bounded closure; R5 and R6 launched

Processed genuine worker_done msg_57192bb4b9a7 / delivery_4382ec54e6fc.
Path-fix task_b6dbf5cdd6f0 settled automatically; released ctx_fdcd11240377
(retained external_terminal). Read report and inspected guard/helper/call sites
and tests. Coordinator independently ran four targeted normal/copied-wrapper
input-path tests, all passing in 2.87s. SCOUT-R4-closure.md records bounded R4
acceptance, relying on prior independent core/acquisition review plus actual
bootstrap/report/path correction evidence, not whole release acceptance.

Two fresh Luna-high approve-for-me workers now ready/input_accepted:

- R5 task_229441d9e2a1 / ctx_629cbef7af8e / terminal
  term_d7f535c5-f02d-4efd-8e9b-4eed8562b7a5: actual interview preparation and
  feedback continuation, definition/private-transfer separation, customization
  and second-home synthetic evidence. SCOUT-R5-contract.md and
  SCOUT-R5-implementation.md. Owns Run/compiler/copied wrapper integration.
- R6 task_96759ccad66c / ctx_2f7dbdc43c9e / terminal
  term_db36a46c-7bd5-49fa-b13a-286227dbc330: core backend comparison runner and
  Gig-owned synthetic evaluation pack with durable attempts/readable results.
  SCOUT-R6-contract.md and SCOUT-R6-implementation.md. Owns main CLI/registry/
  inventory integration, including exact R5 registration requests.

Explicit shared-file ownership and stable peer dispatch addresses sent to both;
no source collisions or deferred helper-only acceptance. Synthetic execution
tests only this implementation wave; actual Qwen/Codex and exact-wheel proof
remain later. Independent combined review follows both deliveries. Planning
budgets R5 4–7 and R6 4–8 worker-hours, not deadlines or release promises.
Fallback retargeted through September 13 08:30 UTC; no active coordinator wait.

### R5/R6 author handoffs recovered; consolidated review dispatched

2026-09-12 23:12 UTC. Read both exact Orca worker transcripts and both contract/
implementation reports after the user's completion signal. Both authors ended
their turns; both worker_done RPCs failed with runtime_unavailable. Coordinator
task completion initially refused active dispatches, so the stale dispatches
were abandoned without any process action and the authoring tasks explicitly
recovered as completed attempts, NOT accepted R5/R6 milestones. Release receipts
retained the terminals with identity_unproven; no process was stopped.

R5 delivered standalone interview persistence/transfer tools but reports main
CLI/schema registration and full journal fixture verification unfinished.
Source inspection confirms missing main interview/transfer CLI registration and
no interview integration in run.py. R6 reports 10 focused/regression tests and
78-schema verification; these remain worker evidence, not combined acceptance.
Its later schema repair may remove R5's earlier transient setup failure.

Fresh Luna-high independent reviewer task_b6c2edbef176 / ctx_37ccdc0f483c /
term_d45918d4-75ff-4fc4-9385-a4492aa987a4 started ready/input_accepted. Owns only
SCOUT-R5-R6-review.md plus disposable synthetic probes. Review original frozen
R5/R6 scope, run the two focused suites once, and consolidate source authority,
real graph/Run integration, transfer safety/second-home usability, grader and
comparison lifecycle findings. No source fixes, private data, provider calls,
whole-suite reruns, activation or publication authorized for this review.

Expected review window 45–75 minutes, not a release estimate. Ten-minute cheap
fallback retargeted; coordinator yields for completion/user signal. Consolidated
Luna corrections follow findings; R7 remains after accepted integration.

### September 20 restart: three Luna correction lanes

User explicitly authorized parallel remaining corrections after confirming the
September 12 findings. No intervening completion was inferred. Original R5/R6
review remains changes-requested; author handoffs are not milestone acceptance.
See SCOUT-R5-R6-correction-wave-20260920.md for scope/ownership/acceptance.

- Interview: task_5e515011757a / ctx_982aa6e8181a /
  term_90045554-3e62-4d84-b717-1a157a2afeea.
- Transfer: task_480b839c058e / ctx_0d20ab6e8b82 /
  term_188d0033-b7a1-42f6-9259-37264939e7ed.
- Comparison and central registration: task_486ca03d2754 / ctx_b4b430652738 /
  term_aa804676-c738-4a32-962d-dc6040bb3019.

Fresh sessions launched with gpt-5.6-luna, high effort, approve-for-me. Each
dispatch reports ready/input_accepted; provider turn-start observation is
unsupported, not falsely claimed as proof of implementation progress. Stable
peer addresses sent. Separate Run/wrapper, source/materialization and CLI/schema
owners prevent concurrent shared edits. Synthetic tests only; private data,
live providers, actual activation and publishing excluded. Historical committed
bytes must not be rewritten or deleted as a shortcut for transfer safety.

Expected first handoffs roughly 1–3 hours, not a release ETA. Existing ten-minute
model-free fallback retargeted through September 21 14:30 UTC. Coordinator yields
for completion/user signal; combined review then R7 follow accepted corrections.
