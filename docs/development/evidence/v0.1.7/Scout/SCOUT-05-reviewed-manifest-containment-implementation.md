# SCOUT-05 reviewed-manifest containment correction

Date: 2026-09-10. This is the bounded F-A1 correction requested by
`SCOUT-05-reviewed-manifest-guard-corrections-review.md`; it changes only
stable matching inside `capability_successor.py` and its focused guard tests.

## Change

The former fresh-ID guard compared one ordered projection of an entire
capability list.  It now projects capabilities independently and refuses when
**any** selected capability is contained in the authenticated committed
generic-review set for the same Gig and schema version.  Appending a decoy,
prepending it (which moves the reviewed capability to a non-zero index), or
reordering the selected list therefore cannot hide an otherwise identical
reviewed tool binding.

The committed set is intentionally not "every capability in a reviewed
manifest."  For each manifest-keyed passed review decision, the helper first
authenticates its journal decision and reviewed-manifest references, validates
the exact decision source binding, then projects only the capability named by
that decision.  Its projection preserves capability ID, goals, source
constraints, declared effects, permissions, credential/network constraints and
the complete tool binding/inventory; it excludes mutable presentation/review
prose.  Working copies only cause successor-required refusal; they never grant
authority.

The existing legacy `scout-c3-test` fixture remains valid: it has
`security_review.status = passed` and `availability_state = available`, but no
authenticated generic-review decision, so no blanket `passed` heuristic or
new creator allowlist was introduced.

## Focused verification

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_reviewed_manifest_guard.py -k containment --tb=short` | `2 passed, 14 deselected in 6.16s` |
| `.venv/bin/pytest -q tests/test_scout05_reviewed_manifest_guard.py -k "public_cli_refuses_contained or contained_reviewed_capability_is_refused" --tb=short` | `4 passed, 12 deselected in 12.36s` |
| `.venv/bin/pytest -q tests/test_scout05_reviewed_manifest_guard.py -k "reviewed_manifest_cannot_bind or renamed_reviewed_manifest_refuses_before_normal or public_cli_refuses_renamed or renamed_reviewed_manifest_unsafe" --tb=short` | `8 passed, 8 deselected in 22.57s` |
| `.venv/bin/pytest -q tests/test_scout05_reviewed_manifest_guard.py -k "reviewed_manifest_is_refused_during or renamed_reviewed_manifest_is_refused" --tb=short` | `2 passed, 14 deselected in 6.65s` |
| `.venv/bin/pytest -q tests/test_scout05_capability_prepare_cli.py::test_public_prepare_replay_then_separate_approve_and_fresh_wrapper_crud tests/test_scout05_tool_crud.py::test_fresh_wrapper_executes_approved_create_update_archive_with_cas_and_replay --tb=short` | `2 passed in 29.39s` |
| `uv run ruff check src/gigai/capability_successor.py tests/test_scout05_reviewed_manifest_guard.py` | passed |
| `.venv/bin/python -m py_compile src/gigai/capability_successor.py` and scoped `git diff --check` | passed |

The new public normal, recovery and Click cases exercise both appended and
prepended decoys, deliberately change copied creator metadata, and assert an
unchanged `HEAD`, all tag refs, and active-pointer bytes for every refusal.
The normal-path case also changes reviewed source bytes, proving the approval
gate refuses before runtime source checks would be relied on.  The valid
prepare-successor -> separate approve -> copied wrapper flow and the accepted
legacy available/passed CRUD flow remain separately green.

## Limits

No public signature, lifecycle callsite, schema, provider, activation, or
consent mechanism changed.  The pre-existing ordinary legacy route still
trusts a schema-valid manifest only when it neither claims generic review nor
contains a capability whose stable authority projection matches an
authenticated generic reviewed capability; this correction does not claim that
legacy history is generic review proof.  Broader consent UX, release/default
eligibility, and complete SCOUT-05 evidence remain outside this lane.
