# Contributing

GigAI is contract-first and pre-alpha. Contributions should preserve the
difference between approved design, executable research evidence, and shipped
product behavior.

## Development setup

~~~bash
uv sync --locked --extra test
make test
~~~

`make test` is the complete portable offline command. It runs one unfiltered
source pytest discovery pass, the deterministic G28 behavior evaluation, then
builds a wheel and runs every `tools/verify_installed_*.py` verifier plus the
direct installed-test node selection. Debian's direct-mount container gate is
explicitly `make test-debian-offline`; real local-model/provider/UAT execution
is explicitly `GIGAI_G30_UAT=1 make test-live` and is never part of `make test`.

The source pass defaults to bounded resource-aware xdist: `TEST_XDIST_WORKERS=auto`
with `TEST_XDIST_MAX_WORKERS=14` and `TEST_XDIST_DIST=worksteal`; the cap matches
the measured 14-CPU host, while actual workers are clamped by the host CPU count.
The runner records the requested, cap, CPU, and actual worker counts before
pytest starts. For a controlled local measurement, use `make test-source
TEST_XDIST_WORKERS=14 TEST_XDIST_MAX_WORKERS=14 TEST_XDIST_DIST=worksteal`; the
worker override applies only to the source lane, while behavior and wheel
verifier lanes retain their own sequencing. The runner forces
`GIGAI_G30_UAT=0` for ordinary offline lanes; `make test-live` remains a separate
explicit opt-in and is not affected by source xdist settings.

## Repository boundaries

- src/gigai/ contains only shipped package code and resources.
- research/ contains executable evidence and exposes no stable API.
- tools/ contains maintained repository checks.
- docs/ distinguishes approved design, decisions, reference material, and
  research records.
- .codex/, caches, local environments, raw session output, and workstation
  provenance must never be committed.

## Development goals and commits

Phase 1 implementation is governed by the canonical G00-G11 development goal
graph (kept in the maintainers' local orchestrator docs, `docs/goals/phase-1/README.md`).
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
- Run `make test` for the complete portable offline coverage. `make
  test-source` is the source-only subtarget used by each CI OS/Python lane.
- If packaging changes, build the wheel and run
  tools/verify_installed_schemas.py with the wheel-installed interpreter.
- Scan for credentials, personal paths, session identifiers, and generated
  files.
- Update public claims only when executable evidence supports them.
