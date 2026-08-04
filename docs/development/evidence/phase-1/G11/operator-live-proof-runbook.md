# G11 Operator Live-Proof Runbook

This runbook specifies the two local operator checks used to complete G11: one
OpenAI Platform API target and one OpenRouter target. Their 2026-08-03 results
are recorded in the completion audit; this file remains the reproducible
procedure. Do not run either command in CI, a scenario harness, or with a
credential copied into a file or shell history.

## Preconditions

- Select explicit evaluation targets at each provider.
- Set each credential only in the local process environment, using your normal
  local secret-management practice. GigAI stores the environment-variable
  names, never their values.
- Choose an explicit maximum output-token limit for each target. GigAI does
  not impose a USD ceiling at this experimental stage; account-level spend
  controls remain the operator's responsibility.
- Use a disposable GigAI home and workpad when producing evidence.

## Configure both targets

The initial G11 proof targets are `gpt-5.6-luna` through OpenAI at `high`
reasoning effort and `moonshotai/kimi-k3` through OpenRouter. Both use a
4,096-token output limit. These are experimental evaluation settings, not a
claim of a permanent provider policy or a price estimate.

```bash
gigai setup --non-interactive \
  --home <private-gigai-home> \
  --workpad-root <private-workpad-root> \
  --editor /usr/bin/true \
  --credential-ref openai=environment:OPENAI_API_KEY \
  --credential-ref openrouter=environment:OPENROUTER_API_KEY \
  --endpoint openai=openai_api:openai \
  --endpoint openrouter=openrouter_api:openrouter \
  --model-target openai-luna=openai:gpt-5.6-luna \
  --target-output-limit openai-luna=4096 \
  --target-reasoning-effort openai-luna=high \
  --model-target openrouter-kimi-k3=openrouter:moonshotai/kimi-k3 \
  --target-output-limit openrouter-kimi-k3=4096
```

First confirm that ordinary diagnostics are offline:

```bash
gigai doctor --home <private-gigai-home> --json
```

That command must report the deterministic offline adapter and must not invoke
either remote provider.

## Run the two explicit live checks

```bash
gigai doctor --home <private-gigai-home> --live --model-target openai-luna --json
gigai doctor --home <private-gigai-home> --live --model-target openrouter-kimi-k3 --json
```

Each successful report must have `scope: "live"`, a passing `adapter.live`
check, the configured target name, provider adapter name, configured and
resolved model identity, configured output limit, configured reasoning effort,
and a non-zero exit status only on failure. The report intentionally omits
generated text, raw provider usage, and credential values.

## Record share-safe evidence

For each successful check, record only:

- date and GigAI distribution version;
- provider adapter and target name;
- resolved model identity;
- configured output-token limit and reasoning effort;
- `adapter.live` status and `cost_status`;
- a statement that the credential was resolved at runtime.

Before committing evidence, scan it for the two environment-variable values,
authorization headers, absolute private-home/workpad paths, and provider
response text. Store no raw credentials, prompts, responses, or account data.
