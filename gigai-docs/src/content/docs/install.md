---
title: Install
description: Install GigAI with uv and check it is healthy.
---

## Requirements

- macOS or Linux, and Python 3.11+.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/):
  `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **One model CLI, installed and logged in.** Codex: `codex login`
  (check with `codex login status`). Or Claude Code: run `claude`, then `/login`.
  Scout does not start without one of the two installed. With only a local Ollama model
  or an OpenRouter key, Scout starts only after a `gigai setup --non-interactive` that
  adds that model (`gigai setup --help`: `--endpoint`, `--model-target`,
  `--create-model-target`; [For agents](../agents/#setup-projects-and-gigs-advanced) has
  an example), and assessing through them is not verified. A plain setup with only those
  is on the [roadmap](../roadmap/).

## Install

```bash
uv tool install gigai
gigai --version
```

Or pin a release tag:

```bash
uv tool install "git+https://github.com/karthik446/gigai@v0.1.11.8"
```

## Check the install

`gigai doctor` is the health check once GigAI has its settings. On a new machine the first
`gigai scout run` (or `gigai scout install`) writes them: `~/.gigai/config.toml`, with defaults.
So start Scout once, then check:

```bash
gigai scout run
gigai doctor          # confirms the install is healthy
```

Before that first run there are no settings yet, and `gigai doctor` fails with
`config.valid: configuration is missing`. That is a machine that is not set up yet, not a broken
install. The line names the way in: `gigai scout run` once (it needs Codex or Claude Code
installed), or `gigai setup`. `gigai scout status` says the same until the settings exist.

Run `gigai setup` to change the settings; every command is in the
[CLI reference](../reference/cli/).
