---
title: For agents
description: Discover the GigAI CLI and set up projects non-interactively.
---

It's mostly agents (Claude, Codex, and similar) driving GigAI, so these
commands are non-interactive and scriptable. The CLI commands below were run
against this build.

## Discover the CLI

`gigai agent-context` prints a one-line summary; `gigai agent-context --json`
is the full machine-readable manual (every command, its examples, effect and
external). The same data is rendered in the [CLI reference](../reference/cli/).

Each command carries an **effect** (`read` changes nothing, `write` may change
GigAI state) and an **external** marker (`none` offline, `model` spends a model
call and sends text to your model target, `network` reads the public internet).

## Reading results and failures

- `gigai run-details <run-id> --json` reads durable Run state.
- `gigai workpad path` prints the canonical path of a registered Gig
  workpad, where journal, proposal, and Run state live.
- Any command that supports `--json` prints a structured result on
  **stdout** on both success and failure (see `gigai doctor --json`). A
  malformed invocation (a bad flag or missing argument) is a Click usage
  error and goes to **stderr** as plain text; check the exit code, don't
  assume JSON is always present.

## Setup, projects and Gigs (advanced)

`gigai setup` is interactive by default (workspace, access, models, roles).
Scout users do not need it: the first `gigai scout run` (or `gigai scout
install`) writes the same settings `gigai setup` writes when you accept every
default, and never changes an existing config. Run `gigai setup` to change
them.
`gigai doctor` confirms the install is healthy. `gigai init` binds a git
repository as a GigAI project and `gigai gigs` lists the Gigs registered for
it; Scout needs neither.

```bash
gigai doctor
cd /path/to/a/git/repo
gigai init          # bind this repository as a GigAI project
gigai gigs          # list Gigs registered for this project
```

Non-interactive setup:

```bash
gigai setup --non-interactive \
  --home ~/.gigai --workpad-root ~/gigai-workpads --editor /usr/bin/true \
  --json
```

To add a hosted model target, include credential, endpoint, and
model-target flags:

```bash
gigai setup --non-interactive \
  --home ~/.gigai --workpad-root ~/gigai-workpads --editor /usr/bin/true \
  --credential-ref provider=environment:MY_PROVIDER_TOKEN \
  --endpoint remote=openai_api:provider:https://api.example.com \
  --model-target remote=remote:some-model-name \
  --create-model-target remote \
  --json
```

`--credential-ref` records only an environment-variable *name*, never a
secret value. Run `gigai setup --help` for the full reference, including
Ollama loopback endpoints and per-target reasoning-effort options.

`gigai setup`'s auto-created targets default to a 4096-token output
allowance, enough headroom for a real assessment's JSON output (5-12
requirement-matrix rows plus suggestions/questions); `codex-default` just
works here too, no `--target-output-limit` needed. If you configure a
target by hand with a small custom limit, raise it with `gigai setup
--target-output-limit codex-default=4096` (or a higher value) if assess
responses come back truncated.

## Bind a project

```bash
cd /path/to/target/repo
gigai init --username "agent" --json
```

`--target` accepts a path instead of the current directory. `gigai init` is
idempotent for an already-bound project.
