# TD-0004 — Setup UX and terminology

- Status: In progress
- Discovered during: G24 human UAT, 2026-08-13
- Affected surface: `gigai setup` interactive prompts and confirmation summary
- Ownering lane: G24 UAT / later alpha-readiness cleanup

## Observation

The first-run setup summary exposes internal terminology without explaining its
operator meaning. In particular, `Offline endpoint: offline (deterministic)`
does not tell a new user that this is GigAI's built-in local fixture mode, with
no network call and reproducible behavior.

Setup also requires the operator to type an editor executable path even when a
common editor is already installed, and the unstyled prompt/summary makes the
review boundary difficult to scan. The current flow should make the proposed
machine changes, local-only mode, and final confirmation visually distinct.

## Proposed resolution

- Replace implementation-facing terms such as “offline endpoint” with
  operator-facing labels and one-line explanations.
- Detect supported editors on `PATH` and offer a detected choice, while still
  allowing an explicit executable and structured arguments.
- Add accessible visual grouping and optional color to prompts, summaries, and
  warnings; plain output must remain usable in non-interactive terminals and
  automation.
- Preserve the existing explicit final confirmation and ensure the rendered
  summary remains truthful about what setup will write.

## Exit evidence

- A first-time operator can explain the local/offline mode without reading
  source terminology or a separate architecture document.
- On a machine with a supported editor on `PATH`, setup offers it without
  requiring manual path discovery; explicit paths still work.
- Colorized and plain-output transcripts contain the same fields and decisions.
- UAT confirms that the summary clearly separates proposed changes, local-only
  behavior, and the confirmation action.
- No credential value, network call, or unreviewed filesystem mutation is
  introduced by editor detection or presentation changes.
