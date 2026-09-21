# TD-0010 — G40 Runtime-Aware First-Run Setup

- Status: In verification
- Discovered during: local v0.1.7 human UAT, 2026-08-29
- Affected surface: `gigai setup`, `gigai models`, local Codex/Claude
  discovery, model-target selection, and the default Gig-creation profile
- Owning lane: G40 runtime/setup correction before further v0.1.7 UAT claims

## Observation

On a fresh terminal setup, GigAI discovers local runtime candidates and offers
them as `codex-default` and `claude-default`, but the creation prompt remains:

```text
Model for Gig creation (offline-default, codex-default, claude-default) [offline-default]:
```

`offline-default` is GigAI's deterministic fixture (`fixture-v1`). It is
useful for CI and authority-path smoke tests, but cannot author a useful Gig.
Presenting it as the unexplained first-run default makes a human UAT appear to
have configured an agent-backed product while selecting a test fixture.

The current flow also does not show, before selection, the discovered runtime's
path, version, adapter support, authentication/readiness state, whether a
probe would make a provider call, or the consequence of selecting each choice.
This contradicts G40's setup contract: detected runtimes, compatible targets,
and usable targets must be reviewed explicitly, and role defaults must be
selected only from usable targets. Detection alone is not usability, but it
must lead to a clear, bounded next action rather than an opaque target name.

This is not a project-initialization issue. The operator must be able to run
`uv run gigai setup` as the machine/runtime setup step before choosing any
project with `gigai init`.

## Required resolution

1. During first-run `gigai setup`, show one compact keyboard-selectable
   runtime list: provider, resolved executable path, version, adapter
   support, and typed readiness/authentication state. Do not render one
   discovery list and then repeat the same runtimes as a second choice list.
2. On first-run terminal setup, prefer a detected local Codex target, then
   Claude, as the Gig-creation default. The review screen must make that
   choice and its privacy/cost boundary explicit. Detection is not a claim of
   authenticated usability.
3. If a local runtime is detected but not yet verified, make the operator
   choose an explicit bounded readiness check or explicitly select fixture
   mode. Do not label a detected executable as usable merely because it is on
   `PATH`.
4. Rename and describe fixture mode in operator language, for example
   `offline-fixture (tests only; no model call)`. It must never be the silent
   default when a usable runtime exists.
5. Preserve the offline fixture as an intentional, no-network option for CI,
   development, and deterministic authority-path UAT. Selecting it must
   clearly state that it cannot validate agent-authored Gig quality.
6. Keep `setup` machine-scoped. It configures GigAI home, workpads, runtime,
   and profile defaults; it must not require or imply a project `--target`.
   `gigai init --target ...` remains the later project-binding step.
7. The terminal selector must support arrow-key navigation and Space to select
   the highlighted runtime; Enter accepts the highlighted default.
8. Use terminal-safe color to distinguish the selected runtime, available
   local runtimes, and tests-only fixture mode. Human-facing setup output must
   abbreviate paths under the operator home as `~/...` rather than exposing a
   full `/Users/<name>` prefix.
9. Use a maintained terminal-prompt library for the interactive picker rather
   than custom ANSI redraw and key parsing. Click remains the command and
   ordinary-prompt framework.
10. Editable setup values must not look like already-completed answers. Show
    an empty input field and an explicit instruction that Enter keeps the
    displayed default while typing replaces it.

## Exit evidence

- A fresh terminal UAT with a detected local Codex or Claude runtime shows its
  identity and readiness before configuration, selects it by default without
  a model-selection prompt, and clearly distinguishes that detection from a
  successful explicit probe.
- A detected-but-unverified or authentication-required runtime produces a
  typed explanation and explicit next action; it is never silently selected.
- Fixture selection is visibly marked tests-only/no-model and is explicit when
  any usable runtime is available.
- `gigai models --json` and `gigai doctor --json` agree with the setup review
  about discovery, readiness, and the selected default profile.
- The setup transcript proves that no project binding, Gig proposal, approval,
  or Run is created. A subsequent `gigai init --target <project>` is the first
  project-scoped action.
- Regression tests cover: no runtime, verified Codex, verified Claude,
  detected-but-unauthenticated runtime, declined probe, and explicit fixture
  selection, including arrow-key and Space selection in the terminal picker.
