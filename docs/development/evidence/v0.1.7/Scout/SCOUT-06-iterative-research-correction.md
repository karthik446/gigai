# SCOUT-06 iterative research-reuse correction

Date: 2026-09-10

## Scope

This correction removes the accidental two-Run ceiling from the ordinary v3
research lifecycle. It keeps the frozen sealed-input contract, direct Run
selection, fixed installed validator/source binding, and exact committed-byte
authentication unchanged. The selected historical Run is validated as one
complete direct packet at the current writer-pinned boundary; its own sealed
research references remain data and are not recursively reopened.

The before-fix reproducer from
`SCOUT-06-research-reuse-v3-review.md` selected a completed second Run from a
third Run and reached checkpoint validation with:

```
research_domain_input_mismatch: historical research input ancestry is too deep
```

That refusal came from the v3 bridge's explicit ancestry check, before the
third checkpoint could publish. The corrected bridge now uses the already
resolved direct selector's trusted Run/Plan/graph provenance to rebuild and
authenticate the selected packet. Current-run input validation remains in the
normal broker/input resolver path; historical packet integrity validation does
not authenticate the selected packet's own ancestors.

## Implementation

* `scout_research_v3.py` separates direct historical packet integrity from
  current-run input validation and dispatches historical v2/v3 packets through
  the closed fixed validator choices. It no longer rejects a packet merely
  because its sealed selected inputs include `scout_research`.
* `external_recording.py` passes the fixed domain identity from the resolved
  sealed selector into the historical bridge. This prevents historical
  validator selection from widening trust to a sidecar-declared identity.
  Existing same-writer hydration still redeems only the selected Run family,
  including its complete receipts, checkpoints, output/check/contract/source,
  and supporting bytes.
* `tests/test_scout06_iterative_research_reuse.py` adds public offline fixture
  coverage for three actual v3 Runs: first complete, second selects first and
  completes, third selects second and completes. It checks exact submit replay,
  unchanged first/second bytes, direct-history hydration accounting, and
  missing, tampered, and foreign direct-history refusal without HEAD changes.

No `.071`/`.076` schema or renderer bytes, materialization, Graph Set, source
inventory, existing integration/legacy fixture tests, provider, network,
private user data, full suite, wheel, activation, or commit was changed or
used.

## Verification

Commands:

* `rtk ruff check src/gigai/scout_research_v3.py src/gigai/external_recording.py src/gigai/scout_research_inputs.py tests/test_scout06_iterative_research_reuse.py`
* `rtk .venv/bin/pytest -q tests/test_scout06_iterative_research_reuse.py`

Results:

* Ruff passed.
* **4 passed in 117.68s**.
* The hydration spy observed direct second-Run paths during third-Run
  processing and no first-Run artifact paths.
* The three refusal cases left the authoritative workpad HEAD unchanged;
  tampered working-copy bytes were refused as uncommitted authority
  divergence before publication.

This is fixture-only public offline lifecycle evidence. It does not establish
provider execution, live research, activation, deployment, wheel proof, or
whole-Scout acceptance.
