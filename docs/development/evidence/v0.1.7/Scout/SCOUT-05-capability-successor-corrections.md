# SCOUT-05 capability-successor correction evidence

**Status:** bounded F1/F2 correction, 2026-09-10. This updates only the
reviewed-capability successor authority and the shared lifecycle recovery hook;
it does not add a public prepare/approval caller, alter schemas, execute tools,
or claim SCOUT-05 completion.

## Findings corrected

### F1 — absent first-version pointer is a recoverable state

`_recover_approved_publication` now treats an absent
`manifests/active-gig-version.json` as the expected post-Commit-A/tag state for
version one. It validates a present pointer only when one exists, refuses an
absent pointer for later versions, and publishes exactly one missing Commit B.
The focused parameterized regression crashes after the approval tag for both
legacy and v2 first approvals, then retries twice. Both paths produce version
one, retain the original sealed tag, and have exactly one `gig accepted`
publication commit.

### F2 — successor authority is journal-authenticated, not working-tree hinted

`successor_approval_context` now first asks `read_committed_artifact` whether
the exact proposal-id sidecar was immutably published for this project and Gig.
Only a proven sidecar proceeds to `_committed_file`, which requires current
working bytes to match HEAD, followed by the existing pinned snapshot,
decision/manifest/source authentication, pending-copy comparison, and
under-lock source revalidation. A missing immutable sidecar returns `None` for
ordinary legacy/no-capability approvals; a committed sidecar deleted or changed
only in the working tree produces the typed successor-authority refusal.

The mutable `created_by` field is no longer used to decide whether successor
authority is required. The regression edits it to an operator value, deletes
the working sidecar, and tampers the tool source before both ordinary approval
and post-tag recovery; both refuse before publication with unchanged HEAD,
pointer, and tags. Restoring bytes alone changes no journal state; leaving the
source tampered still refuses under the writer lock, while a later explicit
retry after full restoration is the separate legitimate approval/recovery.

## Focused verification

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_capability_successor.py --tb=short` | `12 passed in 47.81s` |
| `uv run ruff check src/gigai/capability_successor.py src/gigai/lifecycle.py tests/test_scout05_capability_successor.py` | passed |
| `.venv/bin/python -m py_compile src/gigai/capability_successor.py src/gigai/lifecycle.py tests/test_scout05_capability_successor.py` | passed |

The suite retains exact preparation replay, explicit-manifest approval,
omitted-manifest approval/recovery, normal source revalidation, current/pending
proposal refusal, immutable sidecar swap refusal, and legitimate no-capability
first approvals. F3's stale-base diagnostic code was intentionally not changed:
the correction avoids altering public-facing diagnostics while the CLI adapter
lane is separately owned.

## Remaining gates

The public prepare CLI and normal explicit approval UX remain separate work,
as do capability-review UI/effect-consent presentation, default eligibility,
provider execution, package/release evidence, and real-user workpad proof.
This focused disposable-fixture result is not a sandbox claim or release
approval.
