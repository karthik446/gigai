# SCOUT R4 integrated review

Date: 2026-09-11 (America/Denver)  
Review mode: read-only, synthetic/offline evidence only  
Verdict: **Not release-accepted.** The pinned worktree demonstrates a useful
R0-R4 service slice, but the authority boundaries and release evidence below
are not sufficient to claim an authenticated, user-facing full journey.

## Review snapshot and scope

The review was performed against HEAD
`fda48574f8642e66c0e7d53e7303ec04f04d7bb8` in the dirty worktree. The frozen
source snapshot hashes (Git blob hashes of the bytes read) were:

| Surface | Blob hash |
| --- | --- |
| `src/gigai/journal.py` | `9b7bcd0a2d454be58d2f4c0ff7e6dff728bc7fe1` |
| `src/gigai/workpad.py` | `1af1d9d502099c829629409d78cef676b97fe847` |
| `src/gigai/run.py` | `59ad649b51549c2b6314661cbdd2e822eb9d2fd8` |
| `src/gigai/scout_proposal_execution.py` | `2c941f4e97dc42344f48cb4a9e532836e6332b28` |
| `src/gigai/model_execution.py` | `2e94e5a5ced45062a6ddf58516ae8c62c81f341e` |

The report reader/document-record surfaces and the full-journey test were also
read at this snapshot
(`scout_document_records.py`
`31c737249d261d4dbc2801a8c4d98283cc50f755`;
`scout_report_readers.py`
`bc167bab25d5831f47934a187fdbb4be92872470`;
`test_scout_r4_journey.py`
`a0556897a4317d16bb70ab383f381003609c1cbd`). The author may still change
disjoint application, CLI, document, acquisition, or final-answer surfaces;
those are not accepted here and this report must be re-read against any later
snapshot.

This review read the release execution graph, R0 shared interfaces, local
runtime decision, R1/R2/R3 handoffs, R4 integration/full-journey handoffs, the
frozen implementation, production reader set, schemas, and focused tests. No
provider, live model, network, download, activation, configuration change, or
test suite was run by this review.

## Severity-ranked findings

### P1 — Committed schema-valid proposal records do not redeem their nested source authority

`read_proposal_revision` authenticates the record path/publication and then
calls `_validate_record`, but does not redeem
`opportunity.run_ref`, `receipt_ref`, `checkpoint_ref`, `posting_ref`,
`input_revisions.source_ref`, or their content digests against a pinned
`JournalSnapshot`
(`src/gigai/scout_proposal_records.py:313-324`). The report's
`_proposal_rows` does the same: strict shape validation plus
`read_committed_artifact` for the proposal record, then directly publishes
the nested refs as report authority
(`src/gigai/scout_report_readers.py:145-184`).

This is not a remote unauthenticated attack: it requires the caller to have
the in-process journal-writer authority. It is nevertheless an actual control
boundary if `JournalWriter.record` or an equivalent host entry is reachable:
the caller can commit a closed, schema-valid proposal record containing
arbitrary valid IDs/digests and model-declared assessment, and the default
reader/report treats it as an authenticated proposal. The immutable writer
proves publication and bytes, not the domain lineage. Minimal fix: make the
reader accept the already-pinned snapshot and redeem every nested source
reference through the R1 discovery/native readers, checking exact bytes,
scope, and the sealed source head; have `_proposal_rows` use that path. Until
then, raw records must be labeled unredeemed rather than trusted.

### P1 — v3 native source descriptors are not a complete binding to the selected transport input

`_validate_source_descriptors` requires each descriptor to refer to a selected
ID and to match its content digest, but only enforces that descriptor IDs are
a subset of `selected_ids`
(`src/gigai/model_execution.py:533-559`). It does not require
`set(descriptor_ids) == set(selected_ids)`. Consequently a v3 invocation can
send two selected source byte payloads while recording only one host descriptor;
the v3 schema also requires the array but does not express the one-to-one
relationship
(`src/gigai/schemas/model-invocation-v3.schema.json`,
`request.selected_references` and `request.selected_source_descriptors`).
The same validator accepts arbitrary non-empty `family` and `purpose`
strings until the later record-schema check, so routing validation is not
closed at the transport boundary.

Minimal fix: require exact selected/descriptor ID equality, reject duplicate
or non-mapping values before calling `.get`, and enforce the closed family and
purpose enums in the invocation validator. More importantly, keep the
descriptor constructor/resolver host-private or require a redeemed source
identity rather than treating caller-supplied digest-only descriptors as
authentication. The R4 Tailor caller currently constructs these descriptors
from `_resolve_sources` and is bounded by that host path, but the public
`run_model_invocation` contract itself remains weaker.

### P1 — `allow_replaced_manifests` is an over-broad mutable exception

`read_committed_artifact` treats every `manifests/` path as replaceable when
`allow_replaced_manifests=True`
(`src/gigai/journal.py:1094-1104`). The snapshot path enables that flag for
every retained manifest
(`src/gigai/journal.py:1148-1162`), while filtering only top-level manifests
and most software manifests by a string-shaped capability path
(`src/gigai/journal.py:1153-1157`). This does authenticate the exact bytes at
the pinned HEAD and requires the selected publisher commit to contain one
handoff plus a matching `artifact_refs` digest; it does not authenticate that
the replacement is a legitimate manifest type, transition, actor, or owner. A
valid journal writer can therefore replace an arbitrary nested manifest and
have the generic reader select the newest publisher as current authority. The
broad exception also makes future manifest families mutable by accident.

Minimal fix: replace the boolean with explicit allowlisted predicates for the
few versioned mutable manifest paths, and validate the expected schema/domain,
transition, actor, and graph ownership at each caller. Keep graph-set and
active-pointer authority on their immutable approval/tag path; do not let a
generic snapshot flag silently widen it. The exact byte/digest check, pinned
HEAD, path traversal rejection, handoff exclusion, and extra/symlink scan are
good bounded controls, but they do not close this semantic transition gap.
The analogous run-details exception is intentionally scoped to
`runs/*/run-details.json`, but it likewise proves bytes/publication rather than
the legitimacy of every state transition; the run/goal readers must remain the
semantic gate for that mutable scheduler state.

### P1 — Final selection v1 remains a compatibility path with no underlying-document redemption

The current reader now recognizes and schema-validates selection v1 and v2 and
checks the opportunity tuple
(`src/gigai/scout_document_records.py:252-269`). However, v1 selection records
carry only document identities/digests and an opaque invocation; the report
reader validates the selection schema and emits each selected row without
reading/redeeming the named document revision or checking that the selection
was generated by a completed Tailor Run
(`src/gigai/scout_report_readers.py:197-208`). The v2 write path can bind a
completed Tailor result, but the legacy v1 path deliberately remains accepted.

This means an in-process writer can publish a schema-valid v1 selection over
arbitrary valid-looking document identities, and the projection can mark it
selected even when the documents are missing or unrelated. Minimal fix: make
the production reader redeem every selected document with the pinned snapshot
and require v2 `source_run` for the public Tailor selection path; retain v1
only for explicitly labeled legacy/imported data. Do not treat the current
full-journey v1 fixture as proof of Tailor provenance.

### P2 — R0 receipt-tamper obligations are implemented more broadly than they are tested

The proposal invocation reader checks exact request, response, record, actor,
source, and receipt-reference relationships
(`src/gigai/scout_proposal_execution.py`, reader path), but the named
regression in `tests/test_scout_proposal_run.py:519-626` only mutates the
committed receipt target/configured selector and proves that one target
mismatch is refused. The full-journey handoff explicitly says the R0
tamper/alias/extra-Goal/journal-conflict tests were not rerun
(`SCOUT-R4-full-journey.md:102-107`). This is an evidence gap rather than a
new claim that those checks are absent: add focused adversarial mutations for
each R0 obligation and record exact command/result before release review.

### P2 — Production report `runs`/`evidence` readers omit the local Scout Run tree

The default report reader enumerates only `runs/*/external-run.json`
(`src/gigai/scout_report_readers.py:243-261`). R4 proposal and Tailor Runs
persist under `runs/<run_id>/run-details.json` with model-invocation and
Tailor-result evidence, so the default `runs` and derived `evidence`
sections cannot represent those local Scout Runs even when proposal/document
rows are present. The full journey asserts populated proposal/doc/application
sections, not that local Run/evidence rows are complete
(`tests/test_scout_r4_journey.py:376-385`). Minimal fix: add a strict local
Scout Run reader for the authenticated run-details/result/terminal set, or
state that the report intentionally excludes local Runs and remove any
whole-journey implication.

## Bounded controls that held in this review

- `read_committed_artifact` pins a Git head, requires the artifact in the
  publisher commit, requires one handoff, and checks exact pinned bytes, size,
  and digest. Immutable paths still reject multiple publishers unless a
  caller opts into a mutable exception.
- `JournalSnapshot` rejects invalid prefixes, path traversal, symlinked
  working evidence, extra files, and worktree bytes that differ from the
  pinned tree. Excluding `handoffs/` from the snapshot is appropriate because
  the handoff is authentication metadata, not a source artifact.
- `launch_run` validates the selected approved graph, effect/capability
  shape, explicit operator consent, local-only Ollama target, configured
  digest, posting/private selector families, and sealed source/target inputs
  before the real proposal/Tailor execution path
  (`src/gigai/run.py`). The local adapter's loopback/identity checks are a
  trusted-runtime control, not OS-level no-egress proof.
- Proposal and Tailor execution hold the journal writer boundary while
  resolving sources and publishing result/terminal artifacts; pinned-head
  reads and recovery paths are materially safer than latest/worktree reads. No
  fail-open scan or mixed-head acceptance was established in the reviewed
  normal path.
- Application event validation requires operator confirmation, binds request
  and payload digests, and rejects unsupported document-reference shapes. The
  Scout-document reference implementation now validates exact document bytes
  and Tailor result provenance when that reference kind is used; the full
  journey did not exercise it.

## Full-journey and test-claim accounting

`SCOUT-R4-full-journey.md:72-89` records exactly one focused command and one
pass for `test_r4_full_tailor_report_application_journey`; the test file has
three R4 tests, two of which are direct proposal/Tailor behavior tests and one
is the full journey. The older integration handoff records `2 passed, 1
skipped` plus Ruff and a schema-inventory command
(`SCOUT-R4-integration.md:67-99`). The later wave note says “22 focused
passing tests,” but does not identify one reproducible command or exact test
scope (`parallel-wave-20260910.md:1301-1309`). It must therefore be treated as
an aggregate historical claim, not independent integrated acceptance. The
72-schema command verifies the checkout's installed resource set through
`tools/verify_installed_schemas.py`; it is not proof of an isolated built-
wheel/install boundary.

The known gap is explicitly bounded and already assigned to the author, not a
new reviewer discovery: the full journey uses `goal_synthetic`/`inv_synthetic`
for final-selection provenance and records saved/rejected application events
with empty `document_refs`
(`tests/test_scout_r4_journey.py:328-371`;
`SCOUT-R4-full-journey.md:40-45`). It also lacks public answer/final-selection
actions. This is honest fixture documentation, but it means the current
journey does not prove real Tailor provenance, application-to-Scout-document
linkage, or public CLI authority. The deterministic transports establish only
bounded control-flow and exact fixture bytes; they establish no semantic,
hiring, or employment truth claim.

## Release recommendation

Hold release acceptance until the P1 authority findings are either fixed or
explicitly narrowed behind private host-only APIs, then rerun focused
adversarial evidence for the R0 obligations and a real public-entry journey
with v2 Tailor provenance and closed Scout document application references.
Re-review the final author-owned application/CLI/document/acquisition diff
against a fresh HEAD; this report does not accept those concurrent changes.
