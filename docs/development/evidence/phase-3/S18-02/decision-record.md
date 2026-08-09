# S18-02 — Codex CLI and Claude CLI feasibility spike

- Status: Research in progress; fake-process result proposed, not CLI support
- Depends on: S18-01 common envelope and S18-05 redaction/credential/network
  boundary
- Unblocks: G18 adapter feasibility review only; no provider family is adopted

## Decision

The process-boundary shape is feasible in a disposable fake-CLI harness:
explicit argv without a shell, isolated working directory, stdin disabled,
environment allowlist, structured stdout capture, separate stderr, exit-code
mapping, timeout, cancellation, and malformed-output rejection.

This does not prove Codex CLI or Claude CLI compatibility. Both real CLI
families remain deferred pending an implementation Goal with explicit local
operator authorization. Fake-process success is feasibility evidence only.

## Adopted process policy

- Invoke an explicit argv sequence with `shell=False`; never concatenate a
  shell command string.
- Run in a pre-created isolated working directory and do not infer a target
  repository from the caller's current directory.
- Pass only an explicit environment allowlist. Credential values are not
  inherited; later credential resolution follows S18-05 and G11.
- Capture stdout and stderr separately. Parse structured stdout as a complete
  record, not as prose heuristics.
- Map nonzero exit, timeout, cancellation, and malformed output to distinct
  terminal outcomes. No fallback, retry, racing, or background process is
  implied.
- Preserve argv, cwd, exit code, terminal outcome, model identity, usage, and
  provider-specific fields as replay evidence after redaction.

## Fake-process evidence

The fake CLI is `research/s18_02/fake_cli.py`. It is the only process started
by the tests. Scenarios cover structured success, credential non-inheritance,
nonzero exit, timeout, cancellation, and malformed JSON. The harness never
starts `codex`, `claude`, or any provider process and never writes a target.

## Contract impact

No runtime adapter, packaged schema, Goal transition, or provider claim
changed. If a later implementation needs durable process terminal outcomes or
CLI replay records, it must raise a separate additive contract amendment that
preserves existing hashes and canonical vectors and updates the installed
verifier.

## Limitations and follow-up

This spike does not prove real CLI discovery, version stability, authentication
behavior, model identity semantics, provider usage/cost reporting, cancellation
behavior of a real CLI, or target isolation on a real installation. Those
questions remain implementation- and operator-evidence work. Any real CLI
probe must first pass the S18-05 boundary and remain outside CI.
