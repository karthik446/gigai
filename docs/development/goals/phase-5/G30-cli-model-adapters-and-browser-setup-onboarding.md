# G30 — CLI Model Adapters and Browser Setup Onboarding

- Status: Active — implementation underway
- Type: Release-blocking implementation goal for the v0.1.6 candidate
- Depends on: G18 adapter/evidence boundaries, G26 builder lifecycle, G27 discovery contract, G28 setup/create foundation, and accepted S18-02 process policy
- Unblocks: G31 v0.1.6 readiness and human UAT

## Outcome

G30 makes normal GigAI setup configure real model choices instead of showing detected tools as unusable decorations. A fresh operator can choose a local storage folder, see installed Codex and Claude CLIs, configure supported API providers, select a default Gig-creation model, and then run:

```text
gigai setup
gigai create tailor-resume-for-job
```

The browser setup is the user-facing configuration surface. `--non-interactive` remains available for automation and exact scripted configuration.

## Contract gate

Before runtime adapter work, reconcile accepted S18-02 process policy with real Codex CLI and Claude Code invocations. The contract decision must settle:

1. Endpoint and model-target representation for a CLI executable, selected model, detected version, and provider-owned authentication.
2. Codex `exec --json` and Claude `-p --output-format json` envelopes, including default-model behavior and version drift handling.
3. Explicit working-directory, stdin, argv, timeout, cancellation, and environment inheritance rules. No shell strings, inherited credential values, automatic fallback, retry, or target mutation are allowed.
4. Whether existing model-invocation/model-exchange resources represent CLI terminal and replay fields without semantic overload; any amendment is additive and preserves prior resource bytes and hashes.
5. Credential onboarding semantics: environment references first, and a protected local `.env` option only if atomic write, restrictive permissions, runtime loading, redaction, and recovery are specified.

The accepted decision must distinguish `detected`, `configured`, `usable`, and `selected`. Executable detection alone never grants invocation authority.

## First implementation boundary

1. Add bounded Codex and Claude model-port adapters using the installed non-interactive command surfaces; preserve provider-specific output only in the existing replay boundary.
2. Add real local probes for the installed CLIs, plus fake-process tests for timeout, malformed output, credential non-inheritance, cancellation, and no-fallback behavior.
3. Extend setup to display OpenAI, OpenRouter, Codex, and Claude with truthful status and a selectable default only when usable.
4. Add native local-folder selection on supported hosts, derive the private workpad folder from the chosen GigAI home, and retain an absolute-path fallback.
5. Add supported API-provider onboarding through credential references. Raw values must never enter config, manifests, logs, or browser responses.
6. Keep Anthropic API visible as disabled until its adapter contract is accepted; do not represent planned support as configured support.
7. Refresh the HTMX setup/create surfaces with plain-language labels, adaptive follow-up behavior, proposal-build/review states, and no implementation flags required for ordinary use.

## In scope

- Codex CLI and Claude Code adapters through the existing model port;
- installed executable/version/readiness discovery;
- selected model/default-model configuration;
- OpenAI and OpenRouter credential-reference onboarding;
- protected local `.env` onboarding only if its runtime contract is accepted;
- native folder selection and derived home/workpad paths;
- setup reruns that preserve existing providers and update one explicit default;
- HTMX setup and Gig-definition/create usability fixes;
- real local CLI probes, mutation tests, integration tests, and installed replay;
- sanitized evidence for provider/auth/readiness behavior.

## Out of scope

- automatic `codex login`, `claude login`, or credential creation;
- copying or serializing OAuth/API secret values into Gig artifacts;
- automatic provider fallback, retries, racing, or background invocations;
- Anthropic API support before its own accepted implementation contract;
- target mutation, Run execution authority, or changes to G20/G21/G23;
- silently migrating an existing `~/.gigai` installation to a new folder;
- release publication, which belongs to G31/G12 release mechanics.

## State and authority contract

1. Setup may record a provider selection and model preference; only the model adapter factory may authorize an invocation.
2. `detected` means an executable was found without invoking it. `configured` means GigAI has a typed endpoint/target. `usable` requires readiness and authentication availability. `selected` means the operator chose it as the current default; these states cannot be inferred from one another.
3. CLI child processes receive an explicit environment allowlist. GigAI credential values never enter argv, stdin, records, or logs.
4. CLI invocations run in an explicitly selected, non-target work directory unless the accepted contract proves a narrower read-only target context.
5. A CLI terminal result is normalized through the existing model port and model-invocation record. It cannot create a proposal, Run, target effect, or active-version transition by itself.
6. Setup folder selection changes ownership only after explicit operator submission and successful atomic configuration publication.
7. A browser page is a local projection. It cannot approve a Gig or grant a capability outside the existing lifecycle and authority contracts.

## Acceptance criteria

1. The contract-impact/contract-amendment decision is accepted and cites S18-02, G18, G26, G27, and G28 evidence.
2. Installed Codex and Claude binaries are discovered read-only with version evidence; unsupported or unauthenticated states fail closed.
3. A real Codex invocation and a real Claude invocation complete through the model port using explicit non-interactive commands and sanitized replay records; no target mutation occurs.
4. Fake-process and mutation tests kill argv, shell, cwd, environment, timeout, cancellation, malformed-output, retry, and fallback guards.
5. Setup lets an operator choose a storage folder, derives workpads, preserves existing state, and never silently moves an existing home.
6. Setup displays OpenAI, OpenRouter, Codex, Claude, and offline-demo choices with truthful detected/configured/usable/selected status.
7. OpenAI/OpenRouter credential references can be configured without exposing values. Any `.env` path is atomic, mode-restricted, runtime-loaded, and excluded from records and diagnostics, or the option remains disabled.
8. `gigai create <gig-name>` opens the adaptive HTMX flow after setup without request/reference/open/model implementation flags.
9. Browser setup and create flows cover validation errors, cancellation, rerun/idempotence, stale sessions, and interrupted setup without partial configuration.
10. Fresh-wheel installed replay proves setup, CLI readiness, model selection, and create entry behavior from an isolated environment.
11. Completion audit and terminal handoff identify G31 as the next consumer; no release or alpha claim is made by G30 alone.

## Verification and evidence

Evidence belongs under `docs/development/evidence/phase-5/G30/` and includes the accepted contract decision, sanitized installed CLI version/probe results, adapter conformance and mutation reports, setup/create browser captures, credential-boundary fixtures, folder-selection fixtures, installed-wheel replay, completion audit, and terminal handoff.

## Stop boundary

Stop if either real CLI cannot be invoked through the bounded model port, if provider authentication or default-model behavior cannot be reported truthfully, if a secret can enter a durable record, if folder selection can silently replace an existing home, or if the ordinary setup/create flow still requires implementation-facing flags. Do not ship a disabled CLI card as evidence of CLI support.
