# SCOUT-05 init implementation

## Implemented slice

`gigai init --username <name>` now resolves a trimmed 1--64 codepoint username
before package or instance writes.  A noninteractive missing username produces
`username_required`; an existing saved owner is reused when omitted, and a
different explicit username produces `workspace_owner_conflict`.

The additive registry v3 migration retains v1/v2 rows and backs up v2 before
adding `workspace_owners` and `template_instances`.  Init uses a private,
atomic batch intent containing the pinned owner, ordered default inventory
digest, and reserved Gig IDs.  It provisions one non-active workpad per
release-eligible catalog entry, journals an exact
`template_instance_bound` binding, and then records a cache row.  A restart
after the journal write reconstructs only the missing cache row from the
committed artifact; it does not mint a replacement Gig ID or alter existing
history.  A later inventory adds only missing defaults.

The current three built-in catalog entries are the release-eligible default
inventory.  Scout remains `prepared_unready`: this slice neither imports Scout
private data nor declares incomplete Scout graphs release eligible.  It does
not execute a tool, probe a provider, grant approval, import evidence, or set
an active Gig.

## Closed preflight extension

The existing package/target route now recognizes only `.gigai/local` and
`.gigai/locks` after a valid project binding, matching project registry record,
and saved owner exist.  Both must be real directories, not symlinks; tracked
private state and every unknown sibling remain refused.  Workpad reconciliation
is narrowly enabled only for this caller's already-journaled binding roots and
still rejects arbitrary top-level state.

## Evidence

Executed 2026-09-09:

* `.venv/bin/pytest -q tests/test_scout05_init.py` — 8 passed in 7.01s.
  Coverage includes two synthetic defaults, repeat/new-inventory preservation,
  post-journal interruption recovery, CLI username gating, v2 registry
  migration, active-selection absence, owner conflict, and private-root,
  unknown-sibling, tracked-private, symlinked-private-root, and
  non-directory-private-root refusal.
* `uv run ruff check src/gigai/default_init.py src/gigai/registry.py src/gigai/target_binding.py src/gigai/workpad.py tests/test_scout05_init.py`
  — passed.
* `.venv/bin/python -m py_compile src/gigai/default_init.py src/gigai/registry.py src/gigai/target_binding.py src/gigai/workpad.py src/gigai/cli.py`
  — passed.

No new JSON schema resource was added, so there is no central schema inventory
or SHA256SUMS change for this slice.  The journal binding is strict at both
write and committed-read time (exact field set and pinned values); central
registration would be needed before replacing that closed runtime validator
with a packaged schema resource.

## Remaining gates

This is not whole-SCOUT-05 acceptance.  Remaining work includes Luna's copied
Scout source inventory integration, release eligibility for finished Scout
domain graphs, the SCOUT-05 root wrapper/init shipping surface, central schema
inventory verification if a binding schema is introduced, and the separate
SCOUT-06--10 provider/execution/release gates.
