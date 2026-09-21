# SCOUT-05 generic local capability review service

**Status:** bounded implementation, 2026-09-10. This lane adds a generic,
offline review/consent service for the existing inventoried local
native-record tool boundary. It does not expose a CLI/API, activate a Gig,
approve a proposal, execute copied Python, install packages, call a provider,
or change existing capability/lifecycle/CLI code.

## Delivered

* `src/gigai/capability_review.py` exposes `review_local_tool` plus typed
  `CapabilityReviewError` and `CapabilityReviewResult`.
* `src/gigai/schemas/capability-review-decision.schema.json` is a strict
  immutable decision contract. The decision binds project/Gig, approved base
  version and proposal, parent manifest reference/digest, capability ID,
  inventory digest, admitted operations/effects/permissions, reviewer actor,
  positive rationale/evidence, separate direct operator confirmation, and an
  idempotency request digest.
* A passed review atomically journals a new
  `manifests/capability-reviews/<new-manifest-id>.json` and a **new** reviewed
  capability manifest. The decision filename is derived from the new manifest
  ID, so a later caller can authenticate the selected manifest without a
  directory scan; rejected reviews use a `capreview_<uuid>` decision filename.
  The pending parent is never overwritten and the active pointer is never changed. The new manifest preserves the exact source
  inventory and wrapper binding, sets deterministic compatibility and
  availability to `compatible`/`available`, and records `security_review:
  passed` only alongside the persisted reviewer decision and operator consent.
* A rejected review journals only the immutable rejected decision and returns
  `reviewed_manifest_ref: null`; it never fabricates a passed manifest.
* The service reads the active pointer/proposal only when their working bytes
  equal `HEAD`, requires an existing approved base version with no current
  capability pointer, and revalidates the editable tool source inventory
  under the journal writer lock before publication. Source inspection uses
  existing `scout_tools._inventory` and does not import or execute any source.
* Initial support is intentionally narrow: `kind: tool`, source kind
  `local_artifact`, native CRUD operations, exact `write_workpad` effect,
  `write_isolated` filesystem, no network, and no credentials. Other kinds,
  effects, permissions, source kinds, and operation sets receive typed
  refusals.

## Replay and refusal behavior

The operation key is bound to all semantic request inputs. An unchanged retry
returns the exact committed decision/result; reusing that key with changed
payload, current base version/proposal, foreign Gig, parent ref, source
inventory, or unsafe source path refuses with no new authority. The tests use
an in-lock source mutation hook to prove a source change between the initial
inspection and final publication check is refused. Review alone never writes
an active version, proposal, tag, project selection, receipt, Run, or provider
invocation.

The current capability-manifest semantic validator requires option decisions
to remain `pending`; therefore the reviewed manifest retains the selected
option as pending while the immutable review decision carries the explicit
selected effect and direct operator confirmation. This avoids weakening or
forking the existing validator. The future amendment/approval caller must
consume the decision and reviewed manifest as a successor, then use normal
direct operator approval to attach the reviewed manifest.

## Root registration observed

Root has already integrated the central registration in this worktree. The
observed production boundary is:

* `validators.SCHEMA_NAMES` includes `capability-review-decision.schema.json`
  and schema resources/hashes/goldens report 57 resources. The schema
  references `common.schema.json` definitions for `project_id`, `gig_id`,
  `gig_proposal_id`, `sha256`, and `artifact_ref`.
* `journal.TRANSITIONS` includes `capability_review_decided`; normal handoff
  validation/recovery remains the transition authority. The transition
  journals both decision and reviewed-manifest artifacts atomically.
* Add a generic CLI caller only in a later coordinated lane (for example a
   pure `capability inspect` and explicit `capability review` command). A
   caller must preserve the distinction between reviewer judgment and direct
   operator effect consent; an agent request cannot supply the latter.
* Build the reviewed manifest into a new amendment proposal and require the
   existing explicit approval path before active-pointer publication. Do not
   mutate the pending parent or invent approved flags.

The current focused tests use the real production schema/transition
registration; no registry or transition monkeypatching remains.

## Focused verification

| Command | Result |
| --- | --- |
| Historical `.venv/bin/pytest -q tests/test_scout05_capability_review.py` (before root registration) | **10 passed in 17.95s**, with temporary test-only schema/transition monkeypatches; retained as historical evidence. |
| `.venv/bin/pytest -q tests/test_scout05_capability_review.py` (real registration) | **13 passed in 24.45s** |
| `pytest -q tests/test_scout05_capability_review.py` | Not available: pyenv reported `pytest: command not found`; the same bounded lane passed with `.venv/bin/pytest`. |
| `ruff check src/gigai/capability_review.py tests/test_scout05_capability_review.py` | **All checks passed** |
| `.venv/bin/python -m py_compile src/gigai/capability_review.py tests/test_scout05_capability_review.py` | **Passed** |
| `uv run ruff check src/gigai/capability_review.py tests/test_scout05_capability_review.py` | Not available: shared uv cache `.git` returned `Operation not permitted`; direct `ruff check` passed. |

The historical ten-test run covered passed review/new manifest and no active mutation,
rejected review without a passed manifest, absent confirmation, mismatched
effects/permissions, missing pass evidence, exact replay, changed-payload
operation-key conflict, in-lock source mutation, and foreign-Gig refusal. All
fixtures are disposable synthetic workpads using the actual bundled Scout
source. The current thirteen-test real-registration run adds deterministic
pass-decision linkage to the new manifest ID plus replay refusal for missing,
divergent, or foreign reviewed-manifest authority, with no additional
publication. No real private state, providers, network, approvals, commits, or
full suite were used.

## Remaining gate

This is a reusable service implementation, not end-to-end successor
integration. A later owner must add a generic CLI/service caller plus
amendment/approval regressions proving
that the reviewed manifest is attached only to a new approved Gig version.
The same-account editable Python trust boundary remains unchanged and is not
a sandbox claim.
