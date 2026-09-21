# SCOUT-05: from bound instance to prepared software

Date: 2026-09-09. Coordinator implementation breakdown of the accepted
SCOUT-00 contract, not a new contract approval or completed implementation.

## Current evidence and dependency

The coordinator's combined init/corrections/CRUD/scaffold/source-bundle/C3
checkpoint passed 59 tests in 119.44s. Scoped lint, compilation and all 55
schema resources passed. Separate disposable probes reproduced two remaining
init defects: cache loss after a completed batch creates a duplicate Gig;
changing a bundled template refuses re-init instead of reporting an update.
Two selected legacy migration tests also fail because they still require v2
and implicit migration. These are being corrected before init integration.

The current Scout bundle contains the five authoring graph sources and copied
`gig.py`, but init does not yet materialize those files or a real proposal.
It truthfully returns `binding_only_unready`.

## Concrete runtime gap

`lifecycle.propose_graph_set_offline` currently requires an existing approved
proposal and active version; it creates an amendment, not a first proposal.
`approve_offline` resolves the project active workpad rather than accepting
an explicit Gig selector. Existing fixtures create and approve a predecessor
before proposing a Graph Set. That fixture sequence must not become hidden
init behavior: initialization grants no approval and changes no active Gig.

## Integration goals, in dependency order

1. **Fresh multi-graph proposal service.** Extract/reuse validated Graph Set
   staging for a reserved new Gig, with a real `kind=create` pending proposal,
   null predecessor/base-version references and validated creation/Gig document
   artifacts. Preserve existing amendment callers. Resolve the specific Gig,
   journal proposal publication once, and recover its exact proposal ID on
   retry. No provider, synthetic predecessor approval or active selection.
   Make explicit-Gig inspection/approval possible while preserving existing
   caller compatibility and direct approval requirements. Audit schema-family
   versus numeric Gig-version assumptions, including first-version tool
   receipts; do not weaken unrelated constraints globally.

2. **Inert source materialization and binding.** Use the frozen v2 workpad map:
   editable root `gig.py`, `goalgraphs/`, UI and navigation copies; journaled
   source inventories/snapshots under `manifests/software/`; existing canonical
   private document/reference stores. Validate complete source membership,
   relative paths, digests, size limits, link rejection and collision behavior
   before publication. Copying never imports or executes Python. A separately
   approved tool manifest must bind the actual supporting executable source;
   merely including a wrapper in package files is not execution authority.

3. **Connect recoverable init to that service.** Extend the pinned batch stages
   to copied source and actual proposal references. Register strict binding
   data and central schema/hash fixtures. Return per-instance Gig/proposal IDs,
   approval state and usable next actions. Recover each interruption window
   without overwriting customizations, losing historical provenance or changing
   active selection. Preserve explicit tracked-package adoption checks.

4. **Promote only usable defaults.** Replace the implicit catalog-wide default
   assumption with an explicit versioned eligible inventory. Existing catalog
   authoring entries are not automatically executable Graph Sets. Compile real
   Scout descriptors and referenced contracts, with domain behavior delivered
   by SCOUT-06 through SCOUT-10; structural stubs alone cannot establish release
   eligibility. Keep candidate preparation and final promotion distinguishable.

## Bounded acceptance and ownership

Use synthetic disposable fixtures first: fresh multi-graph proposal with no
prior approval, exact source copy without execution, no-active/different-active
Gig cases, explicit approval of the named proposal, repeat initialization,
source customization, upstream update availability, deleted-cache repair,
collision/redirect refusal and interruption at each publication stage.
Later installed-package proof must exercise the real public CLI and copied
script, not manually construct the missing proposal state in a test helper.

Dispatch the first-proposal service after current init edits settle, with
separate ownership for lifecycle/approval callers and init/source publication.
Coordinator owns central schema integration and the combined checkpoint;
independent review follows stable handoffs. No source/proposal implementation
or release promotion is claimed by this plan.
