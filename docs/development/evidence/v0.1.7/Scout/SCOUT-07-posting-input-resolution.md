# SCOUT-07 completed-discovery posting input resolution

Date: 2026-09-10. This bounded slice resolves one explicitly requested posting
snapshot from a genuinely completed public `find-jobs` Run. It is read-only
authority work: no caller integration, new schema, source inventory change,
Tailor Run, application transition, provider, private Gig, or activation is
claimed.

## Resolver contract

`src/gigai/scout_posting_inputs.py` accepts the closed selector:

```text
{
  "family": "scout_discovery",
  "run_id": "run_...",
  "receipt_id": "receipt_...",
  "output_kind": "discovery",
  "opportunity_id": "opportunity_<32 lowercase hex>",
  "snapshot_id": "snapshot_<32 lowercase hex>"
}
```

`resolve_discovery_posting_input(resolved, snapshot, selector)` reads only the
caller-owned pinned `JournalSnapshot`. The matching
`resolve_discovery_posting_input_from_journal` and
`hydrate_discovery_posting_input_snapshot` helpers accept an optional
caller-owned `JournalWriter`; when supplied, they reuse that lock and never
open a nested journal lock.

Before returning bytes, the resolver authenticates the same-Gig v2 Plan, Run,
checkpoint history, unique succeeded receipt, exact output tuple, committed
completion checks, selected profile bytes, and discovery domain sidecar. The
sealed output contract must bind exactly the fixed `.074` discovery schema and
validator IDs; committed source/schema refs must end in the inventoried
`tools/cap_00000000-0000-4000-8000-000000000074` members and their bytes must
match the installed package resources. The fixed discovery bridge then
revalidates origin, selected inputs, exact Markdown/sidecar bytes, and all
supporting members.

The result includes exact `posting_bytes` from the committed supporting member
referenced by the validated posting source capture, not discovery report
Markdown or caller-provided base64. It also returns the posting's locator,
status, opportunity/snapshot identity, capture ref, and bounded immutable refs
for the Plan, Run, checkpoint, receipt, discovery packet, domain binding, and
supporting members. Missing, foreign, incomplete, ambiguous, changed, or
tampered records refuse with typed `ScoutPostingInputError` causes and perform
no writes.

## Public fixture evidence

`tests/test_scout07_posting_inputs.py` builds a disposable initialized and
offline-approved Gig, imports a real profile record, seals and starts a
`find-jobs` v2 Plan, builds a valid `.074` discovery packet, checkpoints it,
and submits a succeeded receipt. The resolver then authenticates the committed
Run and returns the exact synthetic capture bytes
`b"Synthetic job capture."`, while the full journal snapshot remains
byte-identical.

Focused negatives cover changed snapshot selection, no-match opportunity
selection, malformed selector shapes before journal reads, and tampering with
the committed discovery domain bytes. Each asserts a typed refusal and
unchanged committed journal state; the selector does not resolve latest,
follow historical ancestry, or rely on working-tree mirrors.

## Verification record

Exact focused command and result:

```text
rtk .venv/bin/pytest -q tests/test_scout07_posting_inputs.py
7 passed in 42.57s

rtk ruff check src/gigai/scout_posting_inputs.py tests/test_scout07_posting_inputs.py
[]
```

No broad SCOUT suite, wheel/package build, network/provider operation, private
user data, activation, or commit was run for this slice. Existing `.074`
source/schema bytes and the candidate inventory remain unchanged.

## Minimal downstream wiring and remaining gates

The next caller should seal a user-visible selection record containing this
exact selector (including both opportunity and snapshot IDs), pass the same
writer-held snapshot to this resolver, and convert the returned posting bytes
and provenance into the existing explicit G45 `run_input` selection for
Tailor. It must retain the returned snapshot identity and refuse if the
resolver or writer snapshot changes; no implicit latest-posting lookup should
be added. Full discovery selection UX, shared external-recording registration,
Tailor caller integration, provider execution, and application effects remain
separate slices.

## Verification-gate update

The original 7-test record above is retained as historical implementation
evidence. The public-evidence gate now uses the one-call
`resolve_discovery_posting_input_from_journal` path for the happy case and
adds committed-journal coverage for a foreign second disposable Gig/Run, a
publicly cancelled incomplete Run, a test-only schema-valid second terminal
receipt publication, and a genuine completed `no_match` packet. The prior
in-memory domain mutation is explicitly labeled as a raw caller-pinned
snapshot boundary test, not committed-journal tamper evidence.
