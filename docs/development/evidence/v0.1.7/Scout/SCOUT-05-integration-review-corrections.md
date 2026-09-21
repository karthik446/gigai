# SCOUT-05 source/proposal integration review corrections

Date: 2026-09-09

## Scope and disposition

This bounded follow-up addresses R1--R3 from
`SCOUT-05-source-proposal-integration-review.md`. Changes are limited to
`src/gigai/default_init.py`, `src/gigai/schemas/README.md`,
`tests/test_scout05_materialization.py`, and
`tests/test_scout05_first_proposal.py`; no schema bytes, schema hashes,
registry, lifecycle, materialization, provider, or user-state changes were
made.

## R1: registered v2 binding validation

The Scout v2 `template-instance-binding.json` bytes are now validated with the
registered `template-instance-binding.schema.json` immediately before the
`template_instance_bound` journal transition. Invalid bytes raise typed
`DefaultInitError` with code `template_reconciliation_required`, so malformed
binding bytes cannot become journal authority. Existing v1 bindings retain
their prior compatibility path.

The v2 reconciliation path also validates the committed binding against the
registered schema before applying its identity/provenance checks. This remains
an authentication check only; registry rows remain cache projections of the
committed workpad binding and are never treated as approval authority.

Tests prove both sides: a real written candidate binding validates against the
registered schema, while an injected malformed candidate result is refused
before the binding path is published and has no binding-path journal commit.

## R2: schema documentation

`src/gigai/schemas/README.md` now documents the v2 prepared Scout binding,
including its project/Gig identity, source-inventory reference/digest,
pending create-proposal reference, unapproved state, nullable customization /
approved-version fields, and the registry-as-cache boundary.

## R3: committed multi-publisher conflicts

The journal reader deliberately treats more than one committed publisher for
an immutable artifact as `JournalConflictError`; a no-op replay would weaken
that authority rule. Disposable tests therefore create a second committed
publisher in a synthetic workpad, then verify typed refusal and an unchanged
commit set after the refused attempt for:

* the first-proposal `manifests/gig-proposal.json`; and
* the Scout `source-inventory.json` snapshot during recovery before binding
  publication.

The first-proposal test exercises the existing
`JournalConflictError`--`LifecycleError` boundary. The source-inventory test
interrupts after source snapshot publication, introduces the second committed
publisher, and resumes; `_read_snapshot` refuses authentication before any
new source/proposal/binding publication. The tests use only disposable Git
workpads; their fixture commits are adversarial journal-state setup, not a
repository commit or release action.

## Exact focused verification

```text
ruff check src/gigai/default_init.py tests/test_scout05_materialization.py tests/test_scout05_first_proposal.py && python -m py_compile src/gigai/default_init.py tests/test_scout05_materialization.py tests/test_scout05_first_proposal.py && .venv/bin/pytest -q tests/test_scout05_materialization.py tests/test_scout05_first_proposal.py
All checks passed!
.....................                                                    [100%]
21 passed in 31.95s
```

The Markdown README was reviewed as documentation and was not passed to the
Python linter. No full suite, wheel rebuild, provider/network call, actual
init/approval/activation, private `.gigai` state, or repository commit was
performed. The coordinator's existing broader baseline remains separate from
this 21-test focused run.
