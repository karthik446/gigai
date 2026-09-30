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
  A local Ollama model or an OpenRouter key also work.

## Install

```bash
uv tool install gigai
gigai --version
gigai doctor          # confirms the install is healthy
```

Or pin a release tag:

```bash
uv tool install "git+https://github.com/karthik446/gigai@v0.1.10"
```

The first command that needs settings creates `~/.gigai/config.toml` with defaults.
Run `gigai setup` to change them; every command is in the [CLI reference](../reference/cli/).
