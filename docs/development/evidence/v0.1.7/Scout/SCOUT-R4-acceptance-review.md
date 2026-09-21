# SCOUT R4 acceptance re-review

Date: 2026-09-11  
Review mode: independent, read-only acceptance review of the frozen dirty worktree  
Reviewed HEAD: `fda48574f8642e66c0e7d53e7303ec04f04d7bb8`  
Verdict: **Conditional bounded R4 acceptance; R4 is not complete.**

The bounded R0-R4 source and lifecycle corrections are materially present, and the
public synthetic journey now exercises real returned IDs through answer, proposal,
Tailor, final-select, application, replay, correction, and reporting. Two acceptance
gates remain: durable/public exposure of bounded acquisition progress is absent, and
the reported fresh capability-manifest bootstrap refusal is not reconciled with the
intended one-publisher bootstrap control. The second issue may be a fixture/republication
problem rather than a source logic defect, but it prevents me from calling the frozen
evidence release-clean.

## Review snapshot

The worktree was already dirty with unrelated historical and current G43/Scout edits.
I changed only this report and did not attribute the complete pre-existing journal or
run diff to R4. The following SHA-256 values identify the major surfaces reviewed;
they are content hashes of the files in this dirty-worktree snapshot, not commits:

| Surface | SHA-256 |
| --- | --- |
| `src/gigai/journal.py` | `2d8142dbefc9bc30688212e4c3744fca6f1fb440` |
| `src/gigai/run.py` | `65a0717ac551703f658031ed5ccc9c924ad99d40` |
| `src/gigai/model_execution.py` | `773b20835d561614f05fa654a585bb95710e6c01` |
| `src/gigai/scout_proposal_execution.py` | `770b20ee40304ee98481eb433dbd456e4bb4831c` |
| `src/gigai/scout_proposal_records.py` | `d496e95e9724e2784cdbaf37cefe699290a13c36` |
| `src/gigai/scout_document_records.py` | `31c737249d261d4dbc2801a8c4d98283cc50f755` |
| `src/gigai/scout_report_readers.py` | `ca36fdf9c4facea8699ef54c5ea2baa1a2f82d1e` |
| `src/gigai/application_events.py` | `c54534392733edbed502efa25b8b4c6486d36943` |
| `src/gigai/scout_discovery_job.py` | `c4480aa383471ec34e2ffde8a0e22ff199f9c759` |
| `src/gigai/scout_answer_cli.py` | `a74d09bdaf1a5e89ce6682466d2e28f0e20da779` |
| `src/gigai/scout_documents_cli.py` | `705fcec13714544e0e175474a2098024830d6501` |
| `src/gigai/data/scout/gig.py` | `4a132acc6bac28d487f4f2cb423ee59186f990c1` |
| `tests/test_scout_r4_journey.py` | `8be1cd9fcae052720b179e2304773ceaa3bb0595` |
| `tests/test_scout_r4_review_corrections.py` | `c797d110e582e38689e76d3b23355b2b7f3b9b7f` |
| `tests/test_scout_proposal_run.py` | `9c9fd8ac52d61595a42163b9f73226d72e5b18eb` |

The author reports 43 combined focused tests in 430.41 seconds, seven committed
tamper variants in 145.79 seconds, lint, and a 73-contract schema inventory. I did
not repeat the seven-minute suite or tamper suite. The inventory is not proof of an
isolated wheel, and all reported journey evidence is synthetic/offline; it is not
live-provider, hosted-model, scheduler, installation, privacy, or semantic/hiring
truth evidence.

## (a) Accepted bounded R0-R4 corrections

### Proposal lineage and source redemption — accepted

`scout_proposal_records.py:440-470` acquires one snapshot when no snapshot is
supplied and passes it through the proposal reader. `_redeem_pinned_record` at
`scout_proposal_records.py:195-311` resolves exact committed bytes and digests for
the discovery posting, native input/sidecar or external input, run/receipt/checkpoint,
invocation/result, assessment, and proposal record. It checks posting identity,
question membership, result/assessment/invocation digests, scope, and sealed-head
ancestry against that same snapshot. `scout_report_readers.py:150-191` uses that
reader with its snapshot rather than publishing nested unredeemed references.

This closes the previous mixed-head/source substitution finding for the demonstrated
public reader. It is an accepted bounded control, not a claim that arbitrary host
compromise is prevented.

### Model invocation v3 descriptors — accepted

`model_execution.py:540-570` rejects non-mappings, missing or duplicate selected
IDs, unknown IDs/families/purposes, closed-shape violations, malformed digests,
and any mismatch between selected IDs and descriptor IDs before adapter resolution;
the caller supplies one descriptor per selected source (`model_execution.py:154-162`).
The correction tests monkeypatch the adapter and cover the refusal shapes, so these
are meaningful pre-transport negative checks rather than adapter-only mocks.

### Journal publication and capability manifests — bounded control accepted,
bootstrap evidence still open

`journal.py:1077-1147` keeps immutable records at exactly one publisher, verifies the
handoff's exact `artifact_refs` bytes/digest/size, and separates the two explicit
replacement options: run details at `journal.py:1100-1105`, and only the versioned
capability-manifest path matching `_MUTABLE_CAPABILITY_MANIFEST` at
`journal.py:1104-1106`. For a replacement manifest it requires the
`capability_review_decided` transition and validates the capability schema, manifest
ID/path stem, and Gig owner (`journal.py:1129-1146`). Graph sets, active pointers,
proposal manifests, and arbitrary nested manifests do not receive replacement
semantics. This is materially narrower than a general allow-replaced-manifests
escape hatch.

`journal.py:1133-1137` intentionally permits the one-publisher initial materialization
path while requiring the review transition for multiple publishers. That bounded
policy is acceptable in principle; the concrete unresolved evidence is recorded
below.

### Final selection and report readers — accepted bounded control

`scout_report_readers.py:194-254` validates document descriptor/content references
against the pinned snapshot and requires each selected tuple to match a committed
document revision and the selection opportunity/snapshot. A v2 selection invokes
`_tailor_result_redeemed` (`scout_report_readers.py:257-294`), which requires an
actual complete Tailor result, matching run/goal/invocation/output digest, local
scope, a complete Goal, exact result evidence, and selected documents present in
the result. V1 remains readable only as legacy, with `selected=False` and
`legacy_selection=True`; it cannot silently become authoritative.

`scout_report_readers.py:296-410` reads committed external Run rows and local Scout
Run details/result rows, checks local scope and result identity/status, and requires
the exact result artifact to be Goal evidence. The correction is sufficient for the
bounded report reader. The source does not separately require an overall
`run-details.status == "succeeded"`, but the complete result plus complete Goal and
exact Goal evidence are the effective gate in this path; I found no concrete public
CLI exploit from that distinction.

### Run lifecycle and terminal recovery — accepted

`run.py:330-525` publishes a successful proposal record only when the proposal result
is complete; an invalid assessment retains its invocation/result evidence while
avoiding a false successful proposal publication. The failure path writes one owning
Goal failure and one owning Run terminal result. At `run.py:408-496` and
`run.py:1321-1374`, an already-terminal owning Goal is distinguished from a competing
terminal Run; the owning Run is finished once, while cancellation, publication
conflict, duplicate-run-event, and recovery races retain their distinct outcomes.
The reported post-fix focused evidence covers the invalid path and terminal
assertions; the seven committed tamper variants cover target/request/response/record/
actor/source/ref refusal without relying on only target tampering.

### Public action, privacy, and package boundaries — accepted within scope

The answer CLI persists a real native `experience_qa` record and returns real IDs;
the final-select CLI (`scout_documents_cli.py:46-105`) requires real document IDs,
actual completed Tailor run/goal/invocation/output digest, and confirmation before
writing the v2 selection. `application_events.py:196-294` validates the closed
`scout_document` reference shape, exact content/opportunity/snapshot lineage, Tailor
result identity, and explicit application effects. Current journey source uses the
returned IDs and actual Tailor result, then exercises saved/applied/replay/rejected
actions and the production default reader set.

These public commands route through committed readers/writers and do not treat raw
caller dictionaries as authenticated completed Tailor, document, proposal, or
application evidence. This conclusion is source-backed and supported by the current
synthetic journey; it excludes arbitrary host compromise as instructed. Targeted
inspection of `package.py`, `catalog.py`, and `package_privacy.py` found no Scout
catalog/private-export path broadening: package export still calls the private
provenance refusal (`package.py:216-220,250-286`), and Scout private roots and typed
schemas remain recognized by `package_privacy.py:16-77`.

No hosted fallback, live provider/model, network, private-data route, or activation
was introduced or claimed. Exact local configuration and sealed-input requirements
remain part of the existing invocation/runtime boundaries.

## (b) Genuine remaining R4 user-product acceptance requirements

### P1 — bounded acquisition progress is transient and has no public persistence

Evidence: release graph R1/R4 requires each considered posting, duplicate/failure/
exclusion reason, source snapshot, and a bounded invocation that exits at its
deadline with durable progress. The current implementation only has a transient
`DiscoveryImportProgress` DTO (`scout_discovery_job.py:52-87`).
`run_bounded_public_import` (`scout_discovery_job.py:97-152`) validates and classifies
caller-supplied rows, tracks `next_index` in its return value, and deliberately does
not allocate a journal entry or write a job state. The public command registrations
expose answer, documents, proposal/Tailor, report, and application operations, but
no acquisition/import/status command. A caller can therefore lose deadline progress
and cannot replay or inspect the considered/duplicate/failure/exclusion ledger via
the accepted public product surface.

Reproduction from source: call `run_bounded_public_import` with a bounded synthetic
row list and a clock that reaches the deadline; the returned `to_json()` contains
`next_index`, but a fresh process/workpad has no committed artifact or CLI operation
from which to recover that value. This is a public product gap, not an arbitrary
host-compromise concern.

Smallest exact completion task: add one explicitly invoked public acquisition/import
operation accepting already-acquired public rows and a deadline, persist one
authenticated discovery progress artifact (including batch/replay identity,
processed index, every considered/duplicate/failure/exclusion reason, and source
snapshot), and expose a status/retry/next-index read through the normal committed
journal path. Add one offline synthetic test proving deadline interruption followed
by replay resumes deterministically and that the persisted rows remain public-only.
Do not add a crawler, installed scheduler, background agent, private matching, or
network provider; those are outside this bounded R4 completion task and the accepted
local decision.

### P1 evidence gate — fresh capability bootstrap/republication conflict is not
reconciled

The user-actions completion handoff records a fresh isolated journey failure before
any Scout action: an existing `capmanifest_...0072.json` with historical
`scout_source_materialized` publication was refused because the frozen journal had
multiple candidates and the current replacement rule accepts only
`capability_review_decided`. The same handoff records that the immediately preceding
27-test run passed. The source policy (`journal.py:1129-1146`) says exactly one
publisher is bootstrap-compatible and only multiple-publisher replacement needs the
review transition, so this may be fixture contamination or an incorrectly
re-published bootstrap artifact rather than a code defect.

This cannot be closed by broadening `allow_replaced_manifests`, accepting arbitrary
transitions, or treating a moving historical fixture as authority. The smallest
fix is to make the isolated accepted fixture/materialization path produce one
publisher for the initial manifest, or to route a legitimate replacement through
the explicit review transition; then capture one focused proof for both bootstrap
and reviewed replacement, including refusal of an unrelated manifest and an
unapproved transition. Until that reconciliation is recorded, release acceptance
must remain conditional even if the source logic is retained unchanged.

### P2 — journey assertions do not fully assert report-row correctness

The current journey calls the production `default_reader_set` and confirms populated
proposal/document/application sections, but does not assert the exact selected
document IDs, opportunity/snapshot pair, and local Run/evidence rows returned by the
report. The reader source has these checks (`scout_report_readers.py:221-250,
296-410`), so this is an evidence-strength gap rather than a demonstrated source
forgery. Add focused assertions to the existing synthetic journey that the selected
rows equal the real final-select IDs and opportunity, and that the local Run and
result evidence rows contain the actual committed IDs/digests. This is subordinate
to the P1 acquisition persistence gate.

### Public-action evidence limits

The completion handoff's full journey uses real returned IDs and actual Tailor v2
provenance; it no longer uses `goal_synthetic` or `inv_synthetic` for final selection.
That is adequate bounded synthetic evidence for the public action path. It does not
prove a live browser/CLI installation, a hosted or local model, external acquisition,
semantic/hiring truth, or a production scheduler. The package catalog/private-export
boundary was inspected and no silent Scout broadening was found; this review does
not reopen unrelated package/G43 work.

## (c) Planned R5/R6/R7 work not due in this review

Do not relabel the following as new R4 defects:

- R5 interview-flow, portability, and runtime comparison work.
- R6 core comparison and broader product evaluation.
- R7 exact-wheel/isolation proof, complete release matrix, installation/UAT, and
  final provider/runtime evidence.

The 73-schema inventory is useful checkout evidence only; it is not R7 wheel proof.
Likewise, no crawler, installed scheduler, richer future UI, or live runtime is
required to close this bounded R4 review beyond the explicitly missing public
acquisition-progress persistence/CLI seam above.

## Consolidated disposition

Accepted controls are the one-snapshot proposal redemption, strict v3 descriptor
validation before transport, narrow reviewed manifest replacement, legacy-only v1
selection, Tailor-v2-bound final selection, committed local Run/evidence rows,
seven real committed tamper refusals, corrected owning Run terminal lifecycle, and
real-ID public action path with private-only routing. Remaining release blockers are
P1 durable public acquisition progress and the unresolved fresh bootstrap evidence;
P2 exact report-row assertions should be added to make the user-product claim
auditable. Therefore this is a conditional bounded R4 result, not full R4 acceptance,
with no architecture rewrite or universal review of unrelated G43.
