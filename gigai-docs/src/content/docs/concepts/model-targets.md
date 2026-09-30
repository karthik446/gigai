---
title: Model targets
description: How GigAI names and resolves the models a Gig calls.
---

A model target names an adapter (`codex_cli`, `claude_cli`, `ollama_local`,
`openrouter_api`) and a model. `gigai setup` creates one per CLI it finds
(`codex-default`, `claude-default`).

A Gig can ask for a *sealed* target by adapter name: Scout's find-jobs resolves
`ollama_local`, `codex_cli`, `claude_cli` or `openrouter_api` to whichever
enabled configured target uses that adapter. Only having two enabled targets on
the same adapter, with neither named exactly the sealed value, is an error:
disable or remove one (`gigai setup` or edit `config.toml`).

## Credentials

`--credential-ref` records only an environment-variable *name*, never a secret
value. Store a key with `gigai secrets add <name>`. Ollama is reached only on a
numeric loopback address (`127.0.0.1`).

## Output limit

`gigai setup`'s auto-created targets default to a 4096-token output allowance.
If you configure a target by hand with a small custom limit, raise it with
`gigai setup --target-output-limit <target>=4096` (or a higher value) if
responses come back truncated.

`gigai setup --help` is the full reference, including Ollama loopback endpoints
and per-target reasoning-effort options.
