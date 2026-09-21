# SCOUT-06 coordinator integration checkpoint

Date: 2026-09-10. Work in progress, not whole-goal or release acceptance.

## Implemented by coordinator

- Registered external invocation/checkpoint/receipt v2 resources, then added
  external Plan v2 for a typed inline `role_request`. Old schema IDs and bytes
  stay unchanged. There are currently 62 central schema resources.
- Added exact role-text validation and opt-in v2 resolution/revalidation in
  `scout_inputs.py`. Old callers refuse the new family by default. A Plan
  request is not a fabricated native record, operator confirmation or posting.
- Added strict schema goldens with nonempty domain evidence and a succeeded
  receipt, nested malformed-data cases, and a mixed research/check checkpoint
  regression. Integration initially caught a missing `raw_input_ref` schema
  definition; the Plan v2 resource now contains the explicit strict union.
- Candidate definition 1.1 / compiler 2 describes one composite `research`
  output. Its closed domain binding pins the exact journaled software schema
  and validator source, with schema ID and fixed validator ID separately.
- Candidate source now inventories 16 assets. Research is mapped to a separate
  `.073` tool directory in the Gig, while the packaged candidate resources
  remain in `.071`. This deliberately does not expand the closed CRUD
  capability's `.071` working-directory inventory or grant a new capability.

The source/contract is still a candidate. No live default promotion, user
Gig mutation, approval, activation, provider call, tag or publication occurred.

## Verification so far

- `tests/test_scout06_role_request_input.py`: **21 passed in 0.08s**.
  Pure input/schema parity only; no Plan/Run publication is claimed.
- Schema documents/goldens/nested-domain checkpoint: **3 tests and 129
  subtests passed in 0.35s** after resolving the missing schema reference.
  This predates the newly added mixed research/check regression, which is
  assigned to the generic channel owner for implementation.
- Installed-schema verification script: **62 resources verified in the
  development environment**. This is not a newly built isolated-wheel test.
- Scoped coordinator Python Ruff checks passed.
- Earlier pure packet correction checkpoint: **48 passed in 0.13s**, before
  the now-active candidate v2 input/bridge revision; not claimed for new code.

## Active owners and remaining gate

Luna `task_c9c4bfffa3b8` / `ctx_6fb587ea8145` owns external persistence and
v2 invocation/checkpoint/receipt schemas. Terra `task_87508b66dd64` /
`ctx_0c9a820457f2` owns candidate research v2, the fixed packaged bridge and
focused tests. Root owns registrations, Plan v2, role-input helpers and
source/compiler integration. No duplicate broad suite runs while they edit.

Next: finish mixed checkpoint support, wire Plan v2/role requests through the
real external caller, prove atomic checkpoint and reread-on-submit using the
actual fixed research validator, then independent review and installed caller
verification. Exact historical research-input reuse remains required. The
candidate is not ready for user UAT or release yet.

## Settled component checkpoint — 17:58 UTC

Both implementation workers above settled and were released. Coordinator
combined packet/bridge/role-input/source-contract/legacy source-bundle tests:
**95 passed in 0.59s**. The generic transport plus complete schema contract
suite: **24 passed, 203 subtests passed in 0.67s**, now including the mixed
research/check checkpoint and exactly-one-role Plan v2 regressions. This is
component integration, not a positive journaled research Run yet.

The domain bridge owns exact semantic packet validation. The generic recorder
owns committed-source authentication and direct comparison to fixed packaged
source/schema bytes. A proposed redundant bridge binding helper was not
delivered and is unnecessary if the generic layer performs that check; no
acceptance depends on a missing helper. The packet handoff's inventory-pending
wording describes its original isolated scope: coordinator source inventory
now includes the candidate's mapped `.073` files, without default promotion.

Luna `task_2a6b8217ec90` / `ctx_a4ab3daf0646` now owns public Plan2/CLI and
actual journaled research-Run integration. Explicit CLI protocol selection
preserves v1 compatibility; any necessary Run v2 resource must be centrally
registered before testing. Claude `task_82a8d3db1c32` / `ctx_49ada2374ea9`
independently reviews the settled packet/role/source slice, without duplicate
suite runs or reviewing the active generic caller edits.

## Independent packet/source review — 18:06 UTC

[Claude's review](SCOUT-06-packet-source-review.md) found **no blocking
correctness defect** in the settled packet, bridge, role-input helpers or
source/compiler mapping. The reviewer used source reading and pure probes,
not duplicated suites. Its exact terminal was released and transcript archived.

F1/F2 are recorded non-blocking domain-schema parity gaps: the packet schema
alone permits missing/multiple role requests and blank role text, while the
unconditional renderer validation rejects them. F3 notes that role context is
bound in the sidecar but not printed in Markdown. None changes the accepted
runtime binding behavior. Keep these bytes stable during public Run
integration; any later polish requires fresh resource digests and regression
evidence. The external Plan v2 schema separately enforces role cardinality and
nonblank request text already.

This is acceptance of the reviewed component scope only. Actual public
recording, historical research reuse and installed proof remain open.

## Real public Run integration — 18:24 UTC

The prior public-caller worker finished with partial evidence, but its Orca
question and completion RPCs failed while the coordinator could still reach
the app. The coordinator read the final terminal output and handoff, fenced
dispatch `ctx_a4ab3daf0646`, and marked `task_2a6b8217ec90` failed with the
partial-result explanation. No worker completion was manufactured, and no
process or app was restarted.

Coordinator corrections after that worker settled:

- Registered the strict external Run v2 schema and its complete inventory/golden
  fixture (63 core schema resources now).
- Added strict structural admission for the candidate v2 output contract in
  Graph Set lifecycle validation. This does not approve effects or execute
  inventoried Python; recording still authenticates exact committed resources.
- Read the selected graph through its exact committed artifact reference.
  A broad manifests snapshot was tried and rejected because it encounters
  legitimate multi-publisher artifacts. That broad read was removed; journal
  conflict checks were not weakened.
- Corrected the integration fixture's completion kind to the sealed
  `research-role-completion` contract.

The real candidate initialization/approval, role-only Plan, Run start, mixed
research/check checkpoint, terminal submit and exact submit replay now pass:
**5 tests in 15.26s**. No domain validator or authority was stubbed. A separate
contract/role-input/source/transport selection passed **61 tests and 205
subtests in 0.72s**. Scoped Ruff passed. The current development installation
verifier reports **63 schemas**; this is not a new isolated-wheel proof.

Luna task `task_722bebf61b6d` owns additional real failure-path tests, not core
source. Root retains core ownership. Independent generic-caller review,
historical research reuse and installed workflow verification remain open.

## Public recording corrections — 18:46 UTC

[Independent runtime review](SCOUT-06-public-run-review.md) found a blocking
cross-protocol replay defect. [Coordinator corrections](SCOUT-06-public-run-corrections.md)
preserve existing digests while checking replay response versions, restore
two small executable bounds, and add final source/schema binding checks before
checkpoint/submit publication. The combined real-flow/adversarial/protocol/
transport suite passes **35 tests in 140.51s**, with no expected failures.
This supersedes the worker's 16-pass/one-xfail hardening checkpoint, not the
remaining whole-goal gates.

Claude `task_cbb734e71162` / `ctx_b0e084b5af4e` independently reviews those
corrections and the separate unregistered discovery candidate. Luna
`task_4420e7666950` / `ctx_efaf9e1a8f06` owns the new historical-input resolver.
Another existing Luna session `task_c465b07cbd4b` / `ctx_1ff2d688ad99` reached
ready/input_accepted for a disjoint pure tailoring candidate under `.075`.
No runtime source is shared between these implementation workers.

Separately, root removed the nine previously recorded `cli.py` lint errors:
one unused import and eight semicolon-separated error/return statements.
No command semantics were changed. Scoped Ruff/compile and CLI help pass;
source and runtime gates still need the final installed/full-release checks.
