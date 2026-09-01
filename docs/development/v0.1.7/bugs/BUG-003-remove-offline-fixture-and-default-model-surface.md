# BUG-003 — Remove the offline fixture and default model surface

**Status:** Implemented — awaiting review and agent-backed UAT

**Affected surfaces:** `gigai setup`, `gigai models`, `gigai create`, model
target configuration, default profile selection, upgrade behavior, and release
UAT documentation

## Problem

The current release surface still exposes and relies on the deterministic
offline model path:

- the persisted target name `offline-default`;
- the human label `Offline fixture`;
- the public `create --offline` option;
- setup fallback to an offline target when no local agent is selected; and
- lifecycle fallback to `offline-default` when no configured create target is
  available.

That is not the intended v0.1.7 operator experience. Gig creation must use an
explicit agent-backed path, and the absence of a usable Codex/Claude/API target
must be reported as a typed, actionable configuration or readiness failure. A
deterministic fixture must not masquerade as a configured runtime or become a
silent fallback.

## Product decision

Remove the offline fixture/default model from the v0.1.7 product surface:

1. `gigai setup` must not create, select, or persist `offline-default`.
2. `gigai models` must not render `offline-default`, `Offline fixture`, or an
   equivalent normal-operation fixture state.
3. `gigai create --offline` must be removed from the public command surface.
4. Create must resolve an explicitly configured, detected, and usable agent or
   return a stable error that explains the missing configuration/readiness step.
   It must never silently fall back to deterministic output.
5. Existing v0.1.6/v0.1.7 configurations containing `offline-default` need an
   explicit migration decision. Upgrade must either produce a clearly typed
   reconfiguration requirement or apply a documented, reversible mapping to a
   selected real target. It must not silently convert an offline fixture into
   agent-backed authority.
6. Historical evidence and old tests may retain their original identifiers as
   provenance, but those identifiers must not be emitted by ordinary release
   commands or copied into new project configuration.

The deterministic adapter may remain as isolated test infrastructure while
agent-backed creation and release UAT are being established. If it remains, it
must be unreachable from ordinary setup/create defaults, clearly marked as
test-only in internal code and evidence, and excluded from claims about live
runtime readiness. Removing the adapter itself is a separate decision after the
replacement test substrate exists.

## Required contract work

Before implementation, settle:

- the exact error code and next action when no usable create target exists;
- whether `gigai setup` requires at least one detected local CLI or permits a
  configured API reference without probing it;
- the upgrade behavior for persisted `offline-default` targets and profiles;
- whether existing proposals/runs that cite the deterministic adapter remain
  readable without making that adapter selectable for new work;
- the internal-only name and access boundary for deterministic test support; and
- the replacement UAT path using Codex or Claude, including authentication,
  consent, proposal validation, approval, and Run evidence.

## Acceptance evidence

- Clean setup creates no offline endpoint, target, profile assignment, or
  offline-named configuration entry.
- Public help contains no `--offline` create option and no offline fixture
  terminology.
- Human and JSON model output contains no offline target/default and reports
  only real detected/configured/readiness states.
- Create with no usable agent fails closed with a stable code and actionable
  next step; it does not generate a deterministic proposal.
- Agent-backed create succeeds through the explicit invocation envelope or
  supported local CLI seam, with no browser dependency and no transcript
  capture beyond the declared proposal input.
- Existing configurations and historical artifacts remain readable, while no
  new offline default is introduced.
- Focused, release, migration, redaction, and full-suite evidence are recorded.

## Out of scope

- Rewriting historical completion audits or research evidence solely to remove
  identifiers that accurately describe past runs.
- Removing deterministic test helpers before an equivalent agent-backed test
  seam exists.
- Changing proposal, approval, journal, workpad, capability, or Run authority.

## Implementation record

The public CLI boundary now applies this decision:

- `gigai create --help` no longer exposes `--offline`;
- terminal setup no longer creates, selects, or falls back to
  `offline-default`;
- setup removes deterministic targets from the public configuration path and
  assigns all required default roles to the selected real runtime;
- `gigai models` omits deterministic targets from public JSON and terminal
  output, and rejects direct probes of test-only targets; and
- default create-target resolution ignores deterministic targets and returns
  actionable failure when no real target is configured.

The deterministic adapter and its historical identifiers remain only for
existing internal tests and historical evidence. They are not selectable by
the public setup, model inventory, or create command.

Verification so far:

```text
93 focused CLI/setup/model tests passed
677 full-suite tests passed, 1 skipped, 70 subtests passed
Ruff: clean on changed source and tests
git diff --check: clean
```

Remaining work is review and agent-backed human UAT using Codex or Claude.
This implementation changes no proposal, approval, journal, workpad,
capability, or Run authority.
