# BUG-002 — Release CLI surface and runtime configuration are not human-UAT ready

**Status:** Open — v0.1.7 local human UAT blocker

**Affected surfaces:** top-level `gigai --help`, project-scoped read commands,
`gigai setup`, `gigai models`, and API-backed model-target configuration

## UAT observations

The release help exposes a large set of commands including `history`,
`status`, `show`, `plan`, `run`, `occurrence`, `workpad`, `invoke`, and
`upgrade`. From a normal UAT directory, apparently ordinary discovery commands
fail immediately:

```shell
uv run gigai history
# Error: target is not bound to a GigAI project

uv run gigai status
# Error: target is not bound to a GigAI project
```

The help text does not clearly divide public, UAT-ready workflows from
internal/lifecycle commands that require a pre-existing bound project, Gig,
proposal, approved version, Run, or fixture. The result is a release CLI that
looks much more complete than a new operator can actually use.

The same UAT cannot discover a clear, supported place to enter or reference an
API key for configured API model targets. `models` can report configured
targets, but a human needs an explicit setup/configuration path that explains
whether credentials are absent, referenced, or usable without printing secret
values.

Internal fixture terminology also remains visible to the operator:

```text
offline-default
```

This is an internal target identifier, not human-facing product language. It
continues to make a test fixture appear to be a normal agent/runtime choice.

## Product decision

The release CLI must expose only commands and options that are intentionally
supported for the current human-UAT/release workflow. Unsupported, incomplete,
or developer-only lifecycle surfaces must remain callable only through an
explicit internal/developer interface; they must not appear in ordinary
`gigai --help` or be presented as a normal next step.

This is not permission to delete lifecycle functionality. It is a release
surface decision: preserve the code and its tests behind an explicit internal
boundary until its operator workflow, prerequisites, error recovery, and UAT
are complete.

## Required resolution

### 1. Define and enforce the public command contract

1. Audit every current top-level command and each public subcommand. Record:
   owner/goal, intended operator, prerequisites, destructive or external
   effects, UAT readiness, and whether it belongs in the v0.1.7 public CLI.
2. Publish one intentional public command set for v0.1.7. Do not infer this
   set from what happens to have a Click decorator.
3. Move every non-public command behind an explicit developer/internal entry
   point, for example `gigai internal ...`, or mark it hidden from normal help
   under a documented developer-mode gate. The exact mechanism must retain
   test access without silently expanding the release surface.
4. For every retained public command, either make its prerequisite discoverable
   in the normal workflow or replace the raw failure with a short actionable
   message that identifies the next public command. A user typing a discovery
   command must not need to understand registry, workpad, proposal, or active
   Gig internals to learn why it cannot run.
5. Do not hide `catalog list` or change G42 catalog separation. Do not make
   `gigs` a global fallback; BUG-001 remains the contract for its
   project-scoped listing behavior.

### 2. Provide a supported credential-configuration flow

6. `gigai setup` must offer a clear, optional API-runtime configuration path
   for each supported API provider/target. It must state the target/provider,
   credential reference mechanism, and whether entering or changing a value
   will make a network call. It must never echo a key.
7. Credentials must remain outside repository targets, packages, journals,
   proposals, catalog artifacts, and normal human output. Prefer an existing
   OS/keychain or environment-reference mechanism where one is already
   supported; if v0.1.7 cannot securely accept a value, setup must explicitly
   say where the operator sets the required external reference instead of
   implying that the target is ready.
8. `gigai models` must distinguish `not configured`, `credential reference
   missing`, `configured but unprobed`, `ready after explicit probe`, and
   `fixture`. It must show a concise next action for the first two states and
   must not disclose credential values or filesystem locations that identify
   secret stores.

### 3. Remove internal target identifiers from ordinary UX

9. Never render `offline-default`, `codex-default`, or `claude-default` in
   ordinary interactive prompts, setup review, standard help, or default human
   output. Use the established display vocabulary: `Offline fixture`, `Codex
   CLI`, and `Claude Code`, including detected CLI version where available.
10. The offline fixture remains an explicit tests-only/no-model-call option;
    it must never be silently chosen when an operator-selected usable runtime
    exists. Internal target IDs remain valid only in stable configuration,
    `--json`, and developer diagnostics.

## Acceptance evidence

- A clean v0.1.7 `gigai --help` shows only the approved public command set;
  the command inventory documents every omitted/internal command and its
  explicit access boundary.
- For each public command requiring a project, Gig, proposal, approval, or
  Run, UAT transcripts prove either a complete public path to that prerequisite
  or a concise error pointing to the next public action.
- A new operator can configure an API-backed target through the documented
  setup flow, or is clearly directed to the supported external credential
  reference. No transcript, JSON output, project file, package, journal, or
  test fixture contains the raw key.
- `gigai models` reports the defined credential/readiness states and a safe
  next action without printing secrets.
- Interactive UAT never displays `offline-default`, `codex-default`, or
  `claude-default`; fixture mode remains explicit and clearly tests-only.
- Regression tests cover the public help snapshot, internal-command access
  gate, no-bound-project guidance, API credential-reference setup, secret
  redaction, model-state output, and internal-ID suppression.

## Non-goals

- This bug does not implement model-provider calls, relax consent boundaries,
  or make every historical lifecycle command public in v0.1.7.
- This bug does not place credentials in `.gigai`, project configuration,
  catalog packages, or version-controlled files.
- This bug does not replace BUG-001's `gigs` listing, project-resolution,
  registry-authority, or catalog-separation contract.
