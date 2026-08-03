# Contributing

GigAI is contract-first and pre-alpha. Contributions should preserve the
difference between approved design, executable research evidence, and shipped
product behavior.

## Development setup

~~~bash
uv sync --extra test
uv run pytest
~~~

The complete source suite must run from the repository root and report 90
passing tests.

## Repository boundaries

- src/gigai/ contains only shipped package code and resources.
- research/ contains executable evidence and exposes no stable API.
- tools/ contains maintained repository checks.
- docs/ distinguishes approved design, decisions, reference material, and
  research records.
- .codex/, caches, local environments, raw session output, and workstation
  provenance must never be committed.

## Development goals and commits

Phase 1 implementation is governed by the canonical
[G00-G10 development goal graph](docs/development/goals/phase-1/README.md).
Do not begin a goal before every dependency has committed completion evidence.

Keep each goal in its own reviewable change set. A commit must not mix work
from different goals, and a goal may not opportunistically change a frozen
contract or weaken an earlier goal. If a goal is too large to review as one
change set, revise and split its contract before implementation.

## Frozen contracts

The eight files ending in .schema.json under src/gigai/schemas/ and the
canonical vectors under research/contract_spike/fixtures/ are frozen.

Do not reinterpret or opportunistically edit their field identity, defaults,
ordering, canonical bytes, or digest semantics. A proposed contract change must:

1. state why the existing contract is wrong;
2. include an explicit decision record;
3. update compatibility and golden-vector evidence deliberately; and
4. receive maintainer approval before implementation.

Pure relocation must preserve the exact filename set and SHA-256 mapping.

## Pull-request checklist

- State whether the change affects product, research, documentation, or a
  serialized contract.
- Keep runtime and test-only dependencies separate.
- Run uv run --locked pytest.
- If packaging changes, build the wheel and run
  tools/verify_installed_schemas.py with the wheel-installed interpreter.
- Scan for credentials, personal paths, session identifiers, and generated
  files.
- Update public claims only when executable evidence supports them.
