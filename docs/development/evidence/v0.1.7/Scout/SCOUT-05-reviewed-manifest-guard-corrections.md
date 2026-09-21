# SCOUT-05 reviewed-manifest guard corrections

Date: 2026-09-10. This bounded correction closes F-A from
`SCOUT-05-reviewed-manifest-guard-review.md`; it does not reopen successor
preparation, review CLI, schema, tool execution, or default-init work.

## Change

`reviewed_manifest_requires_successor` still treats the immutable journal as
the only authority.  For a selected manifest identity with no committed
artifact, it now performs a bounded regular-file and schema check, then
compares a stable tool-authority projection against each authenticated,
committed, passed generic review decision and its exact reviewed-manifest
artifact.  The projection includes Gig identity plus each capability's IDs,
goals, source constraints, declared effects, permissions, credentials/network
constraints, and exact tool binding/inventory; it deliberately excludes
manifest ID/version/timestamp/creator and review presentation fields.

An equivalent fresh-ID copy therefore returns the existing result of this
predicate -- *a successor is required* -- so `approve_offline` refuses it in
both existing writer-lock callsites before Commit A/tag or before recovery
Commit B.  A working `created_by = gigai/capability-review` claim with no
matching committed review is likewise an authority refusal; it never grants
approval.  An old working manifest that lacks generic-review provenance, such
as the retained `scout-c3-test` CRUD fixture, remains on its legacy path even
though it has `security_review.status = passed` and `availability_state =
available`.

The matching decision is not based on working decision or source bytes.  It
requires a manifest-keyed committed review decision, passed outcome, exact
project/Gig identity, exact reviewed-manifest artifact reference, a
single-publisher journal artifact for both files, schema validity, and a
decision source binding equal to the reviewed capability's inventory digest,
operations, effects, and permissions.  Changed or deleted working review
files cannot create or erase this authority.

## Focused evidence

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_reviewed_manifest_guard.py --tb=short` | `10 passed in 26.99s` |
| `.venv/bin/pytest -q tests/test_scout05_capability_prepare_cli.py::test_public_prepare_replay_then_separate_approve_and_fresh_wrapper_crud tests/test_scout05_tool_crud.py::test_fresh_wrapper_executes_approved_create_update_archive_with_cas_and_replay --tb=short` | `2 passed in 27.23s` |
| `uv run ruff check src/gigai/capability_successor.py src/gigai/lifecycle.py tests/test_scout05_reviewed_manifest_guard.py` | passed |

The new guard suite proves, with disposable candidate workpads:

- exact fresh-ID reviewed copies refuse in normal approval, without changing
  `HEAD`, **all** tag refs, or active-pointer bytes;
- the same refusal occurs during a post-tag missing-Commit-B recovery, even
  when reviewed source bytes were changed;
- changing the copied creator to an operator and changing timestamp, manifest
  version, compatibility text, and review rationale cannot hide the stable
  reviewed binding; removing the required creator fails schema validation
  before publication;
- deleted copied source, deleted copied manifest, and a copied-manifest
  symlink all refuse before publication;
- the real Click `gigai approve` surface refuses the fresh-ID reviewed copy;
- the pre-existing public prepare -> separate approve -> copied wrapper CRUD
  flow still succeeds; and the legacy `scout-c3-test` available/passed CRUD
  manifest still approves and supports create/update/archive/replay.

## Compatibility and limits

No public signature or schema changed.  The guard is called only inside the
existing journal writer lock in normal approval and recovery; its Git-backed
review authority reads are bounded to `manifests/capability-reviews/` and exact
`manifests/capabilities/capmanifest_<uuid>.json` artifacts, with no path
following from caller data.

The remaining explicit legacy trust assumption is narrow: a schema-valid,
never-generic-service-reviewed working manifest may still be selected by the
historic `--capability-manifest-id` route.  It must not claim the generic
review service and must not match an authenticated generic review's stable
binding; changing stable source/capability/effect facts produces a distinct
legacy manifest rather than a reviewed authority.  This correction does not
add a new consent mechanism, make arbitrary legacy manifests trusted proof of
review, or claim any provider, sandbox, installation, or overall SCOUT-05
release evidence.
