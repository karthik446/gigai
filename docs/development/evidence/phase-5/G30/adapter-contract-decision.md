# G30 — CLI adapter and setup contract decision

- Status: Proposed for review
- Scope: implementation contract for G30; no provider support claim until the
  real invocation evidence and completion audit are accepted
- Depends on: accepted S18-02 process policy, G18 model-invocation/model-
  exchange resources, G26 builder lifecycle, G27 discovery contract, and G28
  setup/create evidence
- Unblocks: bounded Codex/Claude adapter implementation and browser setup
  onboarding

## Decision summary

G30 adds two local process adapters, `codex_cli` and `claude_cli`, to the
existing model-port factory. The adapters use the installed non-interactive
interfaces and return the existing `InvocationResult` shape. No new durable
schema resource is required by this decision: the existing model-invocation
record already owns terminal outcome, resolved model, adapter identity,
request/response artifacts, usage, boundary status, and replay fields. CLI-
specific facts are represented only through the existing adapter identity and
sanitized extension namespace when needed.

This decision does not claim that every installed CLI version is compatible.
Version detection is read-only; invocation compatibility is proven by a real
local probe and reported as `usable` only after the probe succeeds.

## Installed command surfaces inspected

The implementation environment reported:

| CLI | Executable | Version | Non-interactive surface |
|---|---|---|---|
| Codex | `/opt/homebrew/bin/codex` | `codex-cli 0.147.0` | `codex exec --json` |
| Claude | `/Users/kar/.local/bin/claude` | `2.1.227 (Claude Code)` | `claude -p --output-format json` |

These values are local probe evidence, not a portability guarantee. A later
installation may report a different version and must pass the same readiness
and invocation checks.

The operator also completed a direct Claude `-p --output-format json` readiness
probe in the normal authenticated shell. The sanitized result is recorded in
[`claude-direct-probe.md`](claude-direct-probe.md). This confirms the installed
CLI surface and provider-owned authentication, but remains partial evidence:
the same command under GigAI's restricted child boundary currently reports
`authentication_required`, so this record remains proposed until the model-port
invocation path is proven.

## Endpoint and target representation

The existing typed configuration is extended only by admitting two endpoint
adapter values:

```toml
[[endpoints]]
name = "codex"
adapter = "codex_cli"

[[model_targets]]
name = "codex-default"
endpoint = "codex"
model = "gpt-5-codex"
capabilities = ["text"]
max_output_tokens = 512
```

Claude uses the same shape with `adapter = "claude_cli"`. The executable is
resolved by the adapter from the stable command name (`codex` or `claude`),
not stored as a user-controlled shell string in a model target. An explicit
absolute executable discovered by setup may be stored as an endpoint detail
only if the configuration contract is amended; the first implementation uses
PATH resolution and refuses a missing executable.

CLI endpoints do not use GigAI `CredentialReference` values. Authentication
belongs to the provider-owned CLI session/configuration or operating-system
credential store. GigAI does not run login commands, write auth files, or copy
API/OAuth values into its config, records, prompts, or browser responses.

## State meanings

The setup and discovery surfaces use these distinct states:

- `detected`: the command resolves on PATH; no model call has been made.
- `configured`: a typed endpoint and model target exist in GigAI config.
- `usable`: the executable version probe succeeds and a bounded readiness
  invocation succeeds with provider-owned authentication.
- `authentication_required`: the executable and command surface respond, but
  the provider-owned CLI session is not authenticated. This is a configured,
  recoverable state; it is not an unsupported adapter or missing executable.
- `selected`: the operator chose that configured usable target as the default.

No state is inferred from another state. In particular, detection never makes
an adapter usable, and selection never bypasses the readiness check.

## Process invocation contract

Both adapters follow S18-02 and share a process runner with these invariants:

1. `subprocess.Popen` receives an explicit argv sequence and `shell=False`.
   No shell parsing, interpolation, command string, or fallback executable is
   allowed.
2. The child runs in a newly created private temporary directory, never in the
   target repository or the caller's current directory.
3. stdin carries only the already-redacted provider prompt. No credential
   value is placed in stdin or argv.
4. The environment is an explicit allowlist containing ordinary process
   settings and the provider CLI's non-secret home/config location. GigAI API
   credential variables are excluded. The allowlist is tested by injecting a
   synthetic secret and proving the child cannot see it.
5. The parent captures stdout and stderr separately, enforces one timeout, and
   terminates the child on timeout or cancellation. There is no retry,
   fallback, race, or background process.
6. The process result is parsed as a complete structured response. Nonzero
   exit, malformed JSON, missing final text, timeout, and cancellation each
   produce a distinct failed terminal result; none is treated as success.
7. The adapter never writes the target, creates a Run, changes a Gig version,
   or approves a proposal. It returns only the model-port result.

## Codex command

The bounded command is structurally equivalent to:

```text
codex exec --json --ephemeral --sandbox read-only --skip-git-repo-check \
  --cd <private-workdir> --model <configured-model> -
```

The prompt is written to stdin. `--ephemeral` prevents a new persistent
session; `--sandbox read-only` prevents target mutation; `--cd` prevents
caller-directory inference. JSONL events are parsed until the final assistant
message is identified. The adapter records the configured model and resolved
model when the event contains one; missing resolved identity is not invented.

## Claude command

The bounded command is structurally equivalent to:

```text
claude -p --output-format json --no-session-persistence \
  --permission-mode plan --model <configured-model>
```

The prompt is written to stdin and the private workdir is supplied as the
child cwd. No `--fallback-model`, `--add-dir`, tool permission, or dangerous
permission flag is supplied. The JSON result must contain non-empty final text;
provider metadata is retained only when it is non-secret and structurally
useful to the existing replay boundary.

## Schema and replay impact

The current model-invocation resource already provides:

- configured selector, endpoint identity, resolved model, and adapter identity;
- terminal outcome/finish/cancellation and sanitized error;
- selected references, request artifact, response artifact, and replay digest;
- credential metadata without credential values; and
- a closed extension namespace for non-secret adapter details.

The current model-exchange resource already forbids automatic fallback and
nonzero retry count. G30 does not add a second process or exchange resource and
does not alter prior schema bytes or hashes. If real probe evidence reveals a
missing semantic field, the implementation stops and raises an additive schema
amendment before changing runtime records.

## Credential and setup contract

The first supported onboarding path is an environment reference for OpenAI or
OpenRouter. The browser may collect the variable name, never its value. A
protected local `.env` path remains disabled until a separate decision proves
atomic write, restrictive permissions, runtime-only loading, redaction, and
interruption recovery. Anthropic API remains visible as unavailable until its
own adapter contract is accepted.

Rerunning setup preserves existing endpoints, credential references, and Gig
state. It changes the default model only after an explicit operator apply. A
new home folder is never silently selected for an existing installation.

## Required evidence before acceptance

- sanitized local version and readiness probes for both installed CLIs;
- fake-process tests for argv, shell, cwd, stdin, environment, timeout,
  cancellation, malformed output, retry, and fallback guards;
- at least one real successful or fail-closed authenticated probe per CLI;
- model-invocation validation and replay evidence with no secret values;
- browser setup tests for folder selection, provider status, rerun, cancel, and
  stale-session behavior; and
- fresh-wheel installed replay from an isolated environment.

Until those artifacts exist, this record remains proposed and G30 must not
describe Codex or Claude as shipped support.
