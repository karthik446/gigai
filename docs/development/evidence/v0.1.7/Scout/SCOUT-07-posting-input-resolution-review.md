# SCOUT-07 completed-discovery posting resolver review

Date: 2026-09-10. Independent, read-only review of the completed-discovery
posting resolver, its focused tests, the implementation note, SCOUT-00 B2,
and the SCOUT-07 discovery boundary. No source or test was changed, no private
Gig/provider/activation was used, and the reported focused test and Ruff runs
were not repeated.

## Verdict

**Accepted as a bounded read-only library resolver, with one caller-boundary
gate.** `resolve_discovery_posting_input_from_journal()` (or explicit
`hydrate_discovery_posting_input_snapshot()` under the caller's already-held
writer) is the authenticated public path. A downstream Tailor/selection caller
must not treat the exported raw `resolve_discovery_posting_input(resolved,
snapshot, selector)` primitive as a journal opener: that primitive intentionally
trusts its caller-owned pinned `JournalSnapshot`, and a fabricated in-memory
snapshot is not independently authenticated merely because its internal hashes
agree. This is not a defect in the claimed isolated library slice, but it is an
acceptance gate for the first caller.

No blocking resolver correctness defect was found in the reviewed boundary.
The code makes no latest-Run search, ancestor traversal, editable-mirror read,
or write; it resolves only a supplied Run/receipt/opportunity/snapshot tuple.

## Authority and byte provenance

The selector is closed and scalar-typed before journal access
(`scout_posting_inputs.py:341-350`): only `scout_discovery`/`discovery`, valid
Run and receipt entity IDs, and exact lower-case `opportunity_`/`snapshot_`
identities proceed. Malformed mappings and scalar replacements therefore reach
the typed `posting_input_invalid` refusal rather than a Python shape error.
The wrapper also maps unexpected failures to the content-free typed refusal
(`494-503`).

The resolved history is strongly linked. The selected receipt must be a v2
record at its exact Run path, succeeded, and the sole terminal receipt
(`351-368`); the Run and its Plan must have the resolved project/Gig, mutually
identical Plan ref and version, and matching `plan`/`start`/`submit`
invocations (`369-388`). Every checkpoint under that Run is schema-decoded,
path/sequence/parent checked, and the sole receipt output must occur in exactly
one such checkpoint (`243-266`, `389-404`). This rejects foreign, incomplete,
or multiple terminal/receipt-output histories rather than selecting a plausible
one.

The packet itself remains content authority. The output tuple supplies exact
digest/size/media refs for Markdown, sidecars, and bounded supporting members;
all must live below the selected Run/checkpoint (`269-320`). Receipt checks
must be committed checkpoint checks that cover the sealed completion contract
and pass against the exact output digest (`408-445`). The resolver has exactly
one authenticated profile blob (`323-338`) and revalidates the whole discovery
domain with those selected-input bytes (`446-461`). The returned posting bytes
come only from the supporting member named by the selected posting's
`capture_ref` (`462-490`), never sidecar base64 or report Markdown. This meets
the B2 principle that a later wrapper is not a second mutable posting-content
store.

The domain binding is not sidecar-selected: it requires the fixed discovery
schema ID and validator ID, paths ending in the inventoried `.074` members,
and byte equality with the installed literal package resources (`185-218`). It
then imports only the fixed bridge symbol. Thus arbitrary schema URLs, source
paths, and validator bytes do not acquire authority through historical output.

## Snapshot and lock boundary

The raw resolver is explicitly a pure reader over `JournalSnapshot`; `_ref`
authenticates members only against that supplied object's bytes (`58-79`). That
is sound for an already-authenticated caller-held snapshot, but it is a trust
boundary rather than journal authentication by itself. Its export is therefore
safe only under the documented convention; no untrusted caller input should be
allowed to supply `JournalSnapshot` directly.

The hydration/public path supplies the missing journal proof. It obtains one
writer (or validates the supplied writer belongs to the same resolved workpad),
takes a pinned bootstrap head, enumerates only the selected Run, referenced
Plan, exact output/check/input refs, and directly required checkpoint paths,
then calls `read_committed_artifact(..., head=bootstrap.head)` for every
hydrated member (`530-626`). It neither opens a nested writer lock nor follows
ancestors or a latest pointer. `resolve_discovery_posting_input_from_journal`
always hydrates before resolving (`634-639`). The smallest downstream rule is
therefore: keep that writer/head through selection sealing and pass only this
hydrated result (or use the one-call public resolver).

## Focused-test evidence and its limits

The reported evidence is **7 passed in 42.57s** for
`tests/test_scout07_posting_inputs.py` plus Ruff; it is worker-reported and was
not rerun for this review. The principal fixture does perform a real disposable
same-Gig v2 `plan_v2` → `start_v2` → `checkpoint_v2` → `submit_v2` lifecycle
before hydration (`tests/test_scout07_posting_inputs.py:27-110`) and proves the
returned supporting bytes/provenance plus unchanged journal snapshot
(`113-130`). That is meaningful public-record coverage, though the test calls
the raw resolver after hydration rather than the one-call public helper.

Two stated negatives are narrower than their labels:

- The "tampered committed domain bytes" case changes a copied in-memory
  `JournalSnapshot` (`169-179`); it proves the raw resolver rejects a changed
  hydrated member, **not** a forged or changed committed journal artifact.
- The "no match" cases request absent opportunity/snapshot IDs from a successful
  discovery packet (`133-145`); they prove selector absence, **not** a genuine
  completed `no_match` discovery Run with no selectable posting.

These are evidence limits, not source defects. The implementation itself has
typed refusal branches for foreign scope (`372-382`), incomplete/non-succeeded
receipt (`351-356`), and multiple terminal receipts (`357-366`), but the seven
tests do not construct those committed-journal cases. The selector-shape test
does cover three malformed mappings only (`148-166`), so it should not be
presented as exhaustive scalar/nested shape coverage.

## Smallest follow-up gate

Before wiring Tailor, add focused public-hydration cases for a foreign Run/Gig,
an incomplete or non-succeeded receipt, and two terminal receipts, asserting
the exact typed refusal and unchanged head. Add one actual `no_match` packet
fixture if that outcome will be selectable, and call the one-step public
resolver in the happy-path test. None requires a new record family, recursive
lookup, duplicated research authority system, or a broader architecture change.

This review does not accept CLI/UI selection, Tailor integration, application
effects, provider execution, or final selection sealing; those remain separate
callers that must retain G45's exact content authority and this resolver's
pinned provenance.
