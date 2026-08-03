# G00 — Standalone Contract Baseline

- Status: Approved; ready
- Depends on: None
- Unblocks: G01, G02

## Outcome

Establish a standalone, installable Python repository containing the approved
V14 design, the eight frozen schemas, the canonical golden vectors, executable
Phase 0 evidence, and an honest public pre-alpha front door.

## In scope

- A `src/` package that installs `gigai` and distributes the frozen schemas as
  package resources.
- Python support beginning at 3.11, with compatibility proved on the declared
  Phase 1 matrix rather than restricted without evidence.
- One root test command and a locked development environment.
- A separate installed-wheel resource check.
- Public documentation, contribution, security, conduct, license, and CI
  foundations.
- Separation of shipped resources, executable research evidence, and product
  behavior not yet implemented.
- Exact preservation of every frozen schema and canonical vector byte.

## Out of scope

- A placeholder `gigai` command.
- Runtime modules, setup, target binding, workpads, journals, or creation flows.
- Claims that Phase 0 research modules are supported product APIs.
- GitHub publication, package-index publication, or a hosted service.

## Acceptance criteria

1. `uv sync --extra test` followed by `uv run pytest` passes from the repository
   root.
2. The source suite discovers exactly 31 tests.
3. The suite passes under Python 3.11, 3.12, and 3.13.
4. A built wheel contains all and only the intended `gigai` package resources;
   research and tests are not shipped in it.
5. An isolated wheel installation enumerates exactly the eight expected schema
   filenames through `importlib.resources`.
6. `src/gigai/schemas/SHA256SUMS` verifies every frozen schema, and the golden
   vector digest is unchanged from the approved Phase 0 baseline.
7. Publication scans find no credentials, personal workstation paths, raw
   session output, generated caches, or unrelated repository provenance.
8. The README states that GigAI is contract-first pre-alpha software with no
   working CLI, scheduler, or execution runtime.

## Bootstrap check

The Phase 1 goal graph exists as canonical public development contracts before
G00 completes. This is a repository-bootstrap condition verified by document
existence, goal coverage, dependency consistency, and link checks; it is not a
runtime behavior test.

## Verification and evidence

- Locked Python-matrix test output.
- Wheel file listing and isolated installed-resource verifier output.
- Schema and golden-vector SHA-256 output.
- Credential, path, generated-file, and Markdown-link scan output.
- Completion audit mapping each criterion above to concrete evidence.

## Completion evidence

- [Completion audit](../../evidence/phase-1/G00/completion-audit.md)
- [Terminal handoff](../../evidence/phase-1/G00/terminal-handoff.md)

## Stop boundary

Stop after the clean standalone baseline is committed. Do not introduce product
behavior merely to make the repository appear more mature, and do not begin G01
or G02 without the G00 completion audit and terminal handoff.
