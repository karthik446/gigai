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

## Request settings

Code that calls a model through GigAI's model port can set more than the
prompt. No `gigai` command sets these yet, and a call that sets none of them
sends the same thing and writes the same `request.json` as before. Each
adapter either sends a setting, notes in the result that it could not send it
(`raw_usage["unsupported"]`), or refuses the call before it starts. A refusal
is the `model_request_field_unsupported` error, naming the settings.

- **`temperature`** (0 to 2) and **`top_p`** (above 0, at most 1) control how
  varied the answer is. The OpenRouter, OpenAI and Ollama adapters send both.
  The Claude and Codex CLIs have no such option, so they note both as
  unsupported.
- **`seed`** (an integer) asks for a repeatable answer. OpenRouter and Ollama
  send it. The OpenAI Responses API has no seed, so the OpenAI adapter notes
  it as unsupported, and so do the CLIs.
- **`output_mode`** is `text`, `json` or `schema`. With `schema` you also give
  a JSON Schema in **`schema`**. The HTTP adapters ask the provider for JSON
  (or JSON matching the schema). The CLIs add an instruction to the prompt
  and fail with `model_output_not_json` if the answer is not JSON.
- **`system`** is an instruction sent ahead of the prompt. The HTTP adapters
  send it as a system message (`instructions` on OpenAI); the CLIs put it at
  the top of the prompt.
- **`retry_policy`** (1 to 10 attempts, a 0 to 60 second wait between them)
  retries a failed call on OpenRouter and OpenAI. Without it a call is tried
  once, as before. Other adapters note it as unsupported.
- **`metadata`** is your own labels for the call. It is kept with the request
  and never sent to a provider.
- **`messages`** are earlier turns of a conversation (`system`, `user` or
  `assistant`, each with text), sent before the prompt. OpenRouter, OpenAI
  and Ollama accept them; the CLIs refuse the call.
- **`logprobs`** asks for the likelihood of each answer token, and
  **`top_logprobs`** (needs `logprobs`) for that many alternatives per token.
  OpenRouter and OpenAI accept them; Ollama and the CLIs refuse the call.
- **`tools`** lists functions the model may ask to call, and **`tool_choice`**
  (needs `tools`) steers that choice. OpenRouter and OpenAI accept them and
  return the model's requests in `tool_calls`; Ollama and the CLIs refuse the
  call.

A Gig run's model call (the step that writes `request.json` in the workpad)
takes the number and output settings only. It refuses `system`, `messages`,
`tools` and `tool_choice`, because only the prompt is checked and redacted
before it leaves the workpad: put that text in the prompt. The settings it
takes are kept in `request.json` under `settings` (the schema as its digest),
so two calls with different settings have different request digests.

Every result also says how the call went: `latency_ms` (whole milliseconds),
`started_at` and `completed_at` (UTC), `adapter_version`, `capabilities` (the
settings that adapter can send), `retry_count` and `last_error_class` (on
OpenRouter and OpenAI), `cost_usd` when the provider reports a cost, and
`logprobs` and `tool_calls` when asked for. `response.json` keeps
`latency_ms` and `adapter_version`.
