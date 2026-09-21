# SCOUT-06 iterative research correction — independent final review

Date: 2026-09-10

## Decision

**Accepted for the bounded third-Run correction.** This review is confined to
the new direct-history bridge boundary in `scout_research_v3.py`, the handoff
in `external_recording.py`, and its focused three-Run regression. It neither
reopens the previously accepted closed external-dispatch/resource boundary nor
claims provider, activation, wheel, full-suite, or live-research acceptance.

## Authority and traversal review

The current Run remains on the normal sealed-input path. Before checkpoint
domain validation, `_progress` revalidates every Plan input under its held
writer; `scout_inputs.revalidate_external_input` re-resolves a
`scout_research` selector through `resolve_research_input_from_journal` and
requires the regenerated normalized selector to equal the sealed Plan value.
That resolver authenticates the direct Run, Plan, receipt, checkpoint,
markdown, output sidecar, domain sidecar, supporting artifacts, check
contract, and fixed schema/source references at its writer-pinned committed
HEAD. Missing or changed values therefore cannot be replaced by a sidecar or
by a caller-selected historical identity.

The corrected v3 bridge has a separate, narrower responsibility.
`_historical_research_inputs` passes actual committed markdown, sidecar,
domain, and supporting bytes only after the direct selector was revalidated.
It carries `domain_schema_id` from the resolved selector's fixed
`domain_binding`, rather than from the historical domain sidecar. The v3
bridge requires that trusted identity to agree with both the resolved binding
and the committed domain wrapper, then dispatches only between the literal v2
and v3 validators. It does not import a source named by the sidecar.

`_validate_historical_packet_integrity` derives origin exclusively from the
trusted direct selector. A historical packet's `selected_inputs` are supplied
to its fixed validator only as sealed data to check that packet's own render
and canonical input tuple; the historical call receives no
`historical_inputs` argument and does not resolve those nested references.
This removes the previous two-Run ceiling without creating an ancestry walk,
user flag, or semantic-success bypass. The direct selected output, origin,
domain, markdown, supporting evidence, and recognized fixed resources still
receive normal byte and identity checks.

## Focused regression review

`tests/test_scout06_iterative_research_reuse.py` creates three real public
v2-protocol recording lifecycles using the v3 research domain: first completes,
second selects first and completes, and third selects second and completes.
It asserts third success, exact second selected output, unchanged first/second
artifact bytes, and exact third submit replay. This is meaningful public-path
coverage, not a direct bridge-acceptance monkeypatch.

The missing and foreign-selector cases assert the exact public
`external_record_not_found` code and preserve HEAD. The test's “foreign” value
is an absent foreign-shaped Run ID rather than a separately created foreign
Gig; current-workpad resolution makes a foreign ID absent at this boundary, so
the typed refusal is appropriate, but the name should not be read as
cross-workpad fixture evidence. The tampered case changes only an uncommitted
working-copy output and correctly demonstrates mirror/committed-authority
drift refusal with unchanged HEAD; it is not represented here as proof that a
forged committed semantic packet would be accepted or rejected.

One accounting caveat is non-blocking: the spy wraps the direct
`scout_research_inputs.read_committed_artifact` calls and proves the bridge's
explicit hydration reads second-Run paths rather than first-Run paths. It is
not a complete process-wide read audit, because the existing generic journal
snapshot machinery calls its own reader while building normal checkpoint
state. Source inspection establishes that the corrected bridge itself does
not recursively validate or hand off first-Run packet data. A future
hardening-only test may instrument the journal snapshot reader as well and
state the distinction explicitly; no production change is indicated by this
review.

## Verification provenance

I did not rerun the reported focused suite, the 29-test lane, the legacy
fixture, or any full suite. The implementation evidence records:

* `rtk ruff check src/gigai/scout_research_v3.py src/gigai/external_recording.py src/gigai/scout_research_inputs.py tests/test_scout06_iterative_research_reuse.py` — passed.
* `rtk .venv/bin/pytest -q tests/test_scout06_iterative_research_reuse.py` — **4 passed in 117.68s**.

This acceptance rests on the inspected source and test evidence above plus
that reported focused result. No provider/network call, private-user-data
access, wheel build, activation, commit, or production/test modification was
performed during this review.
