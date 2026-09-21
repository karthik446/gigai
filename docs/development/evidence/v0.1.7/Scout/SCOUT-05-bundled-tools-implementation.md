# SCOUT-05 bundled Scout CRUD source and prepared capability boundary

**Status:** bounded implementation evidence, 2026-09-10. This prepares
bundled, private per-Gig CRUD source and strict provenance; it does not approve
execution, complete capability review, promote Scout to a default, or claim an
external agent's Python execution is sandboxed.

## Delivered boundary

The Scout candidate bundle now contains:

- `tools/cap_00000000-0000-4000-8000-000000000071/record_tool.py`, a typed
  create/update/archive operation constructor that delegates to
  `gigai.scout_tool_adapter` and has no journal, SQLite, review, or authority
  writes.
- Its strict `operation.schema.json` sibling.
- The actual copied root `gig.py` plus the exact selected tool subtree in the
  immutable software inventory.

`scout_materialization` compiles the candidate's real graph goal IDs, creates
a schema-valid pending capability manifest bound to those IDs, and journals
both the sealed source copy and the derived
`manifests/capabilities/capmanifest_00000000-0000-4000-8000-000000000072.json`.
The manifest has `availability_state: missing`, pending security review, and a
pending effect-selection option; materialization invokes no copied code and
does not attach the manifest to an active version.

The new inventory member is exactly literal `gig.py`, not a broad root path.
The selected executable entry remains below its exact `tools/cap_<uuid>/`
subtree. A binding that inventories `gig.py` must carry the identical
`wrapper_ref`; normal tools-only historical bindings remain valid. The wrapper
submits its own digest/size/type, the authority revalidates it along with the
closed tool subtree at dispatch and again under the native-record writer lock,
and a receipt records the same bound inventory. This is provenance for the
approved mutation entry path, not proof of every instruction an external actor
executed.

## Recovery and current-authority rules

`repair_prepared_scout_capability_manifest` restores only a missing derived
unapproved manifest from the exact committed `software_inventory` reference.
It requires the pinned current compiler/source digest, rejects bad refs,
symlinks, malformed or divergent committed bytes, and never compiles current
package source, rewrites custom tool files, allocates IDs, restages a proposal,
or changes active selection. The narrow `default_init` caller skips repair for
an upstream update or any Gig with committed current authority; after Graph Set
approval it reports `capability_review_required` instead of falsely telling
the user to approve the already approved Graph Set again.

## Verification

Executed with disposable synthetic workpads only:

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_bundled_tools.py` | `6 passed in 10.64s` |
| `.venv/bin/pytest -q tests/test_scout05_first_version_tools.py` | `5 passed in 25.34s` |
| `.venv/bin/pytest -q tests/test_scout05_materialization.py` | `7 passed in 10.82s` |
| `uv run ruff check` over the changed bundled-source, authority, init, and focused-test files | passed |
| `.venv/bin/python -m py_compile` over changed Python files | passed |

The bundled tests prove source copy equality, real compiled goal references,
literal-root/foreign/traversal schema refusals, pre-approval refusal, Graph
approval without capability review refusing mutation and issuing no receipt,
root-wrapper source mismatch detection, custom tool preservation, missing
derived-manifest recovery, unchanged active authority after approval, and an
upstream candidate change reported without adopting or repairing current source.

## Explicit remaining caller and release gates

There is no existing public capability-review/effect-consent transition that
can safely transform this candidate's pending manifest into a reviewed,
`available` one. This lane deliberately does not set `security_review: passed`
or an option decision to approved in production or tests, so a full copied
`gig.py` CRUD/replay success path with the bundled source remains blocked on
that dedicated review/consent caller. Installed-package proof, domain workflows,
actual reviewed capability approval, source/proposal adoption, and release
eligibility remain open; no provider/network call, real private workpad,
actual user approval, repository commit, or release promotion occurred.

## Coordinator schema handoff

Root must update SHA256SUMS, schema registration/verifier expectations, and
golden fixtures; no central inventory was edited here.

```text
2d36b8e0552c810f1ec17e50d4edbc68be39cc1572c0f535073bf7f59731b5a7  capability-manifest.schema.json
a387d8d258dda7f86612e52b4698990062de8ae5ee846f4d3f154a91427e1bcd  scout-operation-receipt.schema.json
```
