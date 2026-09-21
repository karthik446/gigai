# SCOUT-03 C3 approved tool-to-record bridge

## Delivered boundary

`gigai.scout_tools.dispatch_native_record_tool` is the one supported entry for
the first Gig-owned Scout Python tool operation.  It does not import, execute,
or sandbox `tools/` source.  Instead, an adapter supplies a closed
`record_create` request; GigAI requires agent origin, the resolved Gig ID and
active V2 version, one approved capability ID and canonical entry path, the
exact `write_workpad` effect, and a caller inventory digest that must equal the
sealed authority.  Direct built-in native operations remain separate and have
no tool binding.

The authority is the committed active V2 pointer plus its committed capability
manifest, not installation state or working manifest bytes.  Approval now
publishes a newly selected manifest as an authenticated journal artifact in the
same acceptance transition as the pointer; a recovery publication does the
same.  The manifest's optional closed `tool_binding` pins a single canonical
entry, sorted complete inventory, inventory digest, operation and effect.  The
bridge reads all listed files under `tools/<capability_id>/`, rejects links,
executable members, altered tool or operation-schema bytes, unapproved extra
members, foreign Gig/version/capability, caller digest substitution, and any
broadened effect before dispatch.

The accepted request delegates to `create_native_record_from_tool`, which uses
the existing C1 native publisher and is its only record/journal mutation path.
The publisher replays an identical existing receipt before any new publication,
then invokes the same authority and source-closure validation while holding the
journal writer lock.  A new receipt carries a closed optional `tool_binding`
with the manifest reference, full inventory, active version, operation/effect,
and agent identity.  This proves which declared tool authority was accepted;
it is not proof that arbitrary Python instructions ran and does not grant a
same-account sandbox, SQL access, provider access, scheduler authority, or
user consent.

## Focused evidence

`tests/test_scout03_c3_tools.py` builds a disposable V2 Gig, inventories a
tiny non-executable Python entry and an operation schema, records the manifest,
and approves a fresh active version with that manifest.  It proves successful
typed dispatch through the native journal service, receipt binding, and
same-key replay; it also proves a direct built-in record remains unbound.

The negative parameterized cases reject a wrong active version, foreign Gig,
foreign capability, false caller inventory digest, broadened effects, and a
non-agent caller.  A further case changes the approved operation-schema bytes,
then adds an executable unapproved member; both refuse before a native receipt
can be published.

Commands run after the final implementation changes:

- `.venv/bin/pytest -q tests/test_scout03_c3_tools.py` — 8 passed in 26.58s.
- `.venv/bin/pytest -q tests/test_scout04_input_integration.py` — 4 passed in
  50.29s.
- `ruff check src/gigai/scout_tools.py src/gigai/native_records.py
  src/gigai/lifecycle.py tests/test_scout03_c3_tools.py` — passed.
- `python -m compileall -q src/gigai/scout_tools.py src/gigai/native_records.py
  src/gigai/lifecycle.py` — passed.

## Schema handoff and remaining gates

This slice changes existing registered schemas only; the coordinator owns the
central schema inventory, SHA256SUMS, verifier and golden fixture updates.
The final digests are:

- `capability-manifest.schema.json`:
  `b2227c05feee824c39e4cea12aa3eb10fad0b50ebe8c865dca354ddf80682493`.
- `scout-operation-receipt.schema.json`:
  `3c6414d096cce73187097865f7c281bda2c8afca069ea61c85e8c38c5dd49e75`.

Only `record_create` with an `agent_supplied` origin and exact
`write_workpad` effect is supported here; `record_update` and
`record_archive` deliberately refuse rather than infer their contracts.  The
remaining C3 work is tool/package initialization and shipping `gig.py`, any
operator-facing tool adapter, explicit user-consent UX, and any actual
execution/sandbox authorization.  No provider, scheduler, native storage
rewrite, package export, or full-suite/wheel verification was performed.
