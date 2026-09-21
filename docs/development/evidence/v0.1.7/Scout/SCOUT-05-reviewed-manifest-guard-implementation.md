# SCOUT-05 reviewed-manifest legacy-selection guard

**Status:** bounded approval/recovery authority correction, 2026-09-10. This
closes only the carried-forward path where a manifest produced by the generic
committed capability-review service could be newly attached through an ordinary
proposal; it does not redesign schemas, approve a real Gig, or add a public
surface.

## Implemented rule

`reviewed_manifest_requires_successor` resolves a selected manifest and its
manifest-keyed review decision only with `read_committed_artifact`. It validates
the immutable manifest identity/schema, the immutable decision schema,
project/Gig identity, passed outcome, and exact reviewed-manifest reference.
When that provenance exists, `approve_offline` requires the existing
authenticated successor context rather than falling through to the ordinary
`--capability-manifest-id` branch.

The guard runs inside the lifecycle writer preflight before Commit A/tag and
again inside recovery before any missing Commit B publication, including the
already-published-pointer consistency path. A missing, conflicted, malformed,
or mismatched immutable review artifact is a typed authority refusal. Working
decision bytes are deliberately not an input: editing or deleting them cannot
hide the committed review provenance. An operator-authored legacy manifest with
no committed generic-review provenance remains on its existing path, as do
no-manifest approvals and unchanged inherited pointer references.

## Focused evidence

`tests/test_scout05_reviewed_manifest_guard.py` uses real disposable Scout
candidate materialization, the public review service, and an ordinary Graph Set
amendment with no successor sidecar. It proves that a reviewed manifest is
refused before publication even when the working review decision is edited and
tool bytes are tampered; HEAD, tag, and active pointer remain unchanged. It
also crashes a valid ordinary no-manifest approval after Commit A/tag, then
refuses an attempt to newly attach the reviewed manifest during recovery before
Commit B; a subsequent no-manifest recovery remains valid.

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_reviewed_manifest_guard.py --tb=short` | `2 passed in 5.81s` |
| `.venv/bin/pytest -q tests/test_scout05_capability_successor.py tests/test_scout05_capability_prepare_cli.py tests/test_scout05_tool_crud.py --tb=short` | `20 passed in 97.87s` |
| `uv run ruff check src/gigai/lifecycle.py src/gigai/capability_successor.py tests/test_scout05_reviewed_manifest_guard.py` | passed |
| `.venv/bin/python -m py_compile src/gigai/lifecycle.py src/gigai/capability_successor.py tests/test_scout05_reviewed_manifest_guard.py` | passed |

The affected regression set preserves the public prepare-to-separate-approve
flow and shipped wrapper proof through successor tests, and the legitimate
operator-authored legacy CRUD manifest flow through the CRUD tests. The tool
source remains independently revalidated by the successor path under its
writer lock; the new ordinary-proposal guard refuses the reviewed binding at
approval rather than relying on runtime rejection.

## Remaining gates

This is not a general legacy-manifest redesign: unreviewed operator manifests
retain existing behavior, and no schema family was changed. Public review UI,
effect-consent presentation, revision-option F7, default eligibility, package
or release validation, providers, and real-user workpad proof remain separate
SCOUT-05 work.
