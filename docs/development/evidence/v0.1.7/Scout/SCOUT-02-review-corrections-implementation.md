# SCOUT-02 — Review corrections implementation

**Date:** 2026-09-08  
**Owner:** Terra implementation  
**Status:** Implementation handoff for independent re-review and verification; not acceptance.

## Bounded corrections made

1. Restored strict nested validation to `run-plan-v2.schema.json` and
   `run-manifest-v2.schema.json`, carrying the existing v1 nested definitions
   forward while retaining v2-only top-level Graph Set fields. The v1 schema
   files were not changed.
2. Made `gigs` render valid v2 proposals and active pointers as their real
   title/status/version. G23 portability now recognizes a valid sealed v2
   pointer before returning the explicit `unsupported_schema_version`
   inspection-only diagnostic. Occurrence comparison dispatches both v1 and v2
   Run manifests and compares Graph Set/selected-graph identity explicitly, so
   v1/v2 or distinct selected graphs are deliberately incomparable.
3. Added the seven SCOUT-02 resources to the schema README and synchronized
   `SHA256SUMS`, the installed-schema verifier, all nine legacy 37-resource
   assertions, and the contract-spike resource/golden-instance fixture. The
   inventory is now 44 resources.
4. At the actual pre-Plan and pre-Run selection-record acceptance boundaries,
   `agent_explicit` now requires an exact `agent-invocations/<invocation>.json`
   reference, exact bytes/digest/size, a valid bounded agent envelope, matching
   agent identity (and supplied session), and a matching-agent journal handoff
   that committed those bytes. This remains selection provenance only; it does
   not create operator consent. The syntactic schema-only validator remains
   usable without a workpad root, while all authority acceptance callers pass
   the resolved workpad root.
5. `_provider_review_active` now accepts either sealed v1 or v2 Run manifests,
   preserving the existing G43.1 recovery behavior for an abandoned graph-
   selected provider review without invoking a provider.

## Regression coverage added

- `tests/test_scout02_graph_set_flow.py` now exercises unknown/missing/wrong
  enum and malformed nested v2 Plan/Run-manifest members, valid v2 listing,
  typed v2 portability refusal, v2 occurrence-comparison Run reading, and the
  v2 provider-review recovery seam.
- `tests/test_scout02_review_corrections.py` proves missing, unjournaled,
  tampered, and actor-mismatched agent invocation evidence is refused and only
  exact matching journaled bytes are accepted.

## Verification performed after corrections

| Check | Result |
| --- | --- |
| Final correction, v1 compatibility, reader, and contract-spike group | `70 passed, 87 subtests passed in 61.10s` |
| G43/G43.1/JSL focused seam group | `79 passed in 151.71s` |
| Compile, targeted Ruff, source schema verifier | pass; `verified 44 installed GigAI schemas` |
| Complete offline suite: `GIGAI_G30_UAT=0 .venv/bin/pytest -q` | `747 passed, 5 failed, 1 skipped, 94 subtests passed in 449.74s` |
| Permission-enabled rerun of the five failed cases | `18 passed in 6.48s` |

The five final full-suite failures were loopback HTTP bind environment limits,
not retained deterministic corrections: each returned `PermissionError` under
the restricted sandbox and passed in the permission-enabled rerun. (An earlier
run also encountered a transient multiprocessing mount-lock probe failure; its
isolated permission-enabled rerun passed and the final full run did not repeat
it.) The skip remains the deliberate live-provider gate (`GIGAI_G30_UAT=1`),
and no provider call was made.

## Isolated built-wheel evidence

A final `gigai-0.1.6-py3-none-any.whl` was built into the disposable directory
`/private/tmp/gigai-scout02-final-wheel.pwgBEr` and installed only into that
directory's fresh `venv`; the repository `.venv` was not replaced. With
`PYTHONPATH` unset, imports resolved from that venv's `site-packages`,
`tools/verify_installed_schemas.py` reported 44 verified schemas, and the
installed `gigai --help` plus `gigai graph-set --help` succeeded. The wheel
metadata is `0.1.6`, matching the current project metadata; it is recorded as
artifact provenance, not a v0.1.7 release claim.

## Boundary

No private Gig was written, no provider was invoked, and no commit, tag,
publication, old-schema rewrite, or v0.1.8 redesign was performed. Existing
dirty G43.1 work and coordinator-owned `tests/test_scout02_independent.py` were
preserved; this report is an implementation handoff, not a review verdict.
