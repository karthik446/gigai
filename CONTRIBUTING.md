# Contributing

GigAI is contract-first and pre-alpha. Contributions should preserve the
difference between approved design, executable research evidence, and shipped
product behavior.

## Development setup

~~~bash
uv sync --extra test
uv run pytest
~~~

The complete source suite must run from the repository root and report 128
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
[G00-G11 development goal graph](docs/development/goals/phase-1/README.md).
Do not begin a goal before every dependency has committed completion evidence.

Keep each goal in its own reviewable change set. A commit must not mix work
from different goals, and a goal may not opportunistically change a serialized
or completed contract or weaken an earlier goal. If a goal is too large to
review as one change set, revise and split its contract before implementation.

## Pre-release serialized contracts

Until GigAI deliberately declares its first public release, the eight schema
files under `src/gigai/schemas/` and the canonical vectors under
`research/contract_spike/fixtures/` are editable pre-release source contracts.
Changes update affected bytes, tests, and `SHA256SUMS` together.

Versioned schema identifiers, exact-version readers, closed schemas, package
resource delivery, and installed verification remain required. Canonical-byte
identity, immutable approved Gig versions, and journal authority are unchanged.

At the deliberately declared first public release, the immutable/additive
versioning regime in [ADR 0003](docs/adr/0003-schema-distribution-versioning-and-extension.md)
becomes mandatory. Then a contract change requires an explicit decision,
compatibility evidence, and a new published version rather than an in-place
edit.

Pure relocation always preserves the exact filename set and SHA-256 mapping.

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
