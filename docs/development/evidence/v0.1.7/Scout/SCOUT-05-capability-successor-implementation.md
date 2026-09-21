# SCOUT-05 reviewed-capability successor and approval integration

## Scope and ownership

This bounded lane adds `src/gigai/capability_successor.py`, its strict
`src/gigai/schemas/capability-successor-binding.schema.json` sidecar contract,
the lifecycle approval preflight/recovery hook, focused successor tests, and
this evidence.  It does not edit the public CLI, the review service, default
initialization, bundled source, capability schemas, or existing review tests.

The successor service is generic over the inventoried local native-record
tool boundary.  It accepts only an already committed approved Graph Set base
and an independently committed passed review decision; it inspects the
reviewed manifest, parent manifest, exact source inventory, wrapper bytes,
operations, effects, permissions, reviewer evidence, and separate operator
consent.  It never executes source, activates a version, changes project
selection, or republishes the reviewed manifest.

## Authority shape

Preparation writes an ordinary strict v2 amendment proposal and one immutable
`manifests/capability-successors/<proposal-id>.json` sidecar.  The sidecar
pins project/Gig, operation key and input digest, old active pointer and
proposal refs, immutable pending proposal ref, review decision and reviewed
manifest refs, parent manifest ref, and the complete closed source binding.
The pending proposal copy, mutable current proposal, and sidecar are journaled
under the existing writer lock.  A second pending successor refuses; an exact
operation-key replay authenticates all committed refs and returns the prior
result without publication.

Approval invokes the service's under-lock preflight through the existing
`approve_offline` two-commit path.  It authenticates the sidecar, current base,
review decision, reviewed manifest, source bytes, and pending proposal before
Commit A; Commit B references the already committed reviewed manifest and does
not publish that manifest again.  Recovery after the approval tag repeats the
same checks and publishes only the missing pointer transition.

## Focused verification

The successor test module uses the actual disposable bundled Scout candidate
fixture, real review service output, fresh wrapper subprocesses, and the
production validator/journal transition registrations.  It checks the base
v1 pointer/history, no active selection mutation, exact replay, pending
conflict, missing/foreign/mismatched authority, source revalidation, actual
wrapper create, duplicate reviewed-manifest publication refusal, and
post-tag approval recovery.

| Command | Result |
| --- | --- |
| `rtk proxy .venv/bin/pytest -q tests/test_scout05_capability_successor.py --tb=short` | **8 passed in 33.98s** against the production schema registration and journal transition. |
| `.venv/bin/pytest -q tests/test_scout05_tool_crud.py --tb=short` | **3 passed in 35.48s**, preserving existing approved bundled CRUD behavior after the lifecycle hook changes. |
| `ruff check src/gigai/capability_successor.py tests/test_scout05_capability_successor.py src/gigai/lifecycle.py src/gigai/journal.py` | Passed. |
| `.venv/bin/python -m py_compile src/gigai/capability_successor.py tests/test_scout05_capability_successor.py` | Passed. |

Root registered `capability-successor-binding.schema.json` in
`validators.SCHEMA_NAMES`, schema resource/hash/golden inventories (58
resources), and retained the `capability_successor_prepared` journal
transition.  The final run used those production registrations without any
test monkeypatch.

## Remaining gates

This is library/service integration, not public CLI integration or activation
proof.  The next lane must wire a public prepare caller and normal explicit
approval UX, while retaining separate review and operator consent.  Full CRUD,
default eligibility, provider execution, OS-account sandboxing, real private
state, and release/package evidence remain outside this bounded lane.
