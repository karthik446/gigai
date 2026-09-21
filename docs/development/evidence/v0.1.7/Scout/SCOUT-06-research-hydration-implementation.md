# SCOUT-06 pinned historical research hydration

Date: 2026-09-10. This is the Luna-owned caller-ready hydration seam. It
does not register research in the later input union, create a Plan, or claim
whole research reuse, activation, provider execution, or live dogfood.

## Delivered

`src/gigai/scout_research_inputs.py` now exposes:

```python
hydrate_research_input_snapshot(
    resolved: ResolvedWorkpad, raw: object
) -> JournalSnapshot

resolve_research_input_from_journal(
    resolved: ResolvedWorkpad, raw: object
) -> dict[str, object]
```

The combined helper obtains a writer-locked snapshot at one committed HEAD,
uses a bounded Run-family bootstrap only to discover the sealed Plan, receipt,
and checkpoint references, then re-reads only the exact referenced graph,
output/check contracts, output/checkpoint/supporting artifacts, and fixed
domain schema/source with `read_committed_artifact(head=snapshot.head)`. The
returned `JournalSnapshot` is filtered to authenticated exact paths before the
existing closed selector resolver runs; it never follows the active Gig
pointer, selects latest, snapshots broad manifests, reads mutable mirrors as
authority, re-imports native/G45 content, or publishes anything.

The resolver's existing identity and byte checks remain authoritative,
including project/Gig/Run/Plan/receipt/checkpoint identity, exact graph and
contract binding, validator identity/source bytes, historical completed Runs,
cross-checkpoint output/check support, and normalized envelopes without
embedded `selected_inputs`. Committed multi-publisher refusal remains in the
journal's `read_committed_artifact` path and was not weakened.

## Fixture and verification

The disposable research fixtures now obtain their snapshots through the public
hydration helper rather than manually injecting graph, contract, schema, or
source refs. Coverage retains historical completion with a newer Run present,
cross-checkpoint checks, missing/foreign selectors, forged bytes, cancellation,
unknown validators, and exact revalidation; the new combined helper has a
direct positive regression.

| Command | Result |
| --- | --- |
| `rtk .venv/bin/pytest -q tests/test_scout06_research_inputs.py` | 10 passed in 77.45s |

No full suite, wheel/package proof, provider, private root, activation, CLI,
external recording, schema, discovery, tailoring, native-record, or commit
work was performed.

## Later integration steps

1. At the caller's input-union boundary, accept the raw closed selector and
   call `resolve_research_input_from_journal(resolved, raw)`; do not construct
   or preload a `JournalSnapshot` in the caller.
2. Use the returned normalized envelope as the historical research member of
   the input union. Preserve its `run_plan_ref`, `run_ref`, `receipt_ref`,
   `checkpoint_ref`, graph ref, output refs, and domain binding as provenance;
   do not copy `selected_inputs` into the envelope.
3. When sealing or replaying a later Plan, invoke
   `revalidate_research_input(resolved, locked_snapshot, sealed_member)` with
   the caller's own writer-locked snapshot and require exact equality before
   publication. Keep later input-union/Plan publication and any consumer
   projection in their owning modules.

