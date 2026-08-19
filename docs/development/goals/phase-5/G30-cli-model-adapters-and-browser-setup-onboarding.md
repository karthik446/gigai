# G30 — CLI Model Adapters and Browser Setup Onboarding

- Status: Complete — evidence accepted; handed to G31
- Type: Release-blocking implementation goal for the v0.1.6 candidate
- Depends on: G18 adapter/evidence boundaries, G26 builder lifecycle, G27 discovery contract, G28 setup/create foundation, and accepted S18-02 process policy
- Unblocks: G31 v0.1.6 readiness and human UAT

## Outcome

G30 makes normal GigAI setup configure real model choices instead of showing detected tools as unusable decorations. A fresh operator can choose a local storage folder, see installed Codex and Claude CLIs, configure supported API providers, enable more than one usable model, assign machine-wide defaults for reviewer, verifier, and researcher work, choose a Gig-creation model, and then run:

```text
gigai setup
gigai create tailor-resume-for-job
```

The browser setup is the user-facing configuration surface. `--non-interactive` remains available for automation and exact scripted configuration.

The published package page is also a user-facing surface. Before the v0.1.6
release, its README must plainly explain what a Gig is, how setup and creation
work, and show a few concrete examples. It must not expose internal schema
inventories, evidence mechanics, or implementation history as the package's
introduction.

Setup configures the machine-wide model roster and default role assignments; it does
not define a Gig. A Gig such as `review-verify-fix-loop` owns its workflow and
Gig-specific roles at the Gig level, edited through a command such as
`gigai update review-verify-fix-loop`. Reviewer and verifier are distinct global
defaults. Planner, critic, adjudicator, implementer, and other workflow-specific
roles are not setup roles unless a later contract explicitly makes them so.

## Accepted browser setup interaction contract

The browser flow is a progressive, one-question-at-a-time setup conversation, not
an administrator-style configuration dashboard. The browser keeps a concise Gig
definition explanation beside the active question:

> A Gig is a repeatable unit of work with stable Goals, changing inputs, and
> reviewable results.

The setup sequence is explicit and revisitable:

1. **Workspace** — choose the GigAI private storage folder; derive the private
   workpad location and explain what is stored there.
2. **Access boundary** — choose `CLI only`, `API only`, or `Both CLI and API`.
3. **Available models** — show only models within the selected boundary. Model
   availability is multi-select: detected Claude and Codex CLIs may both be
   enabled, and API providers may be enabled independently.
4. **Machine defaults** — assign enabled models to the four machine defaults:
   `reviewer`, `verifier`, `researcher`, and `gig_creator`. Reviewer and verifier
   remain distinct. Gig-specific roles such as planner, critic, adjudicator, and
   implementer are not configured here. The human-facing **Gig creator** label
   maps to the existing registered `model_invocation:gig-builder` purpose; it
   does not introduce a fifth registry role or a second authority path.
5. **Ready** — present a clean sectioned summary of Workspace, Access, Models,
   and Role defaults before the operator applies setup.

Selecting an API provider expands its configuration inline, including the
environment-variable reference or protected local-secret choice permitted by
the accepted credential contract. The secret value itself is never displayed,
stored in browser state, or written to a durable record. A model choice does not
silently advance the flow; each answer has an explicit next-question action.

The first screen must feel like choosing a trusted starting point, not completing
an infrastructure form. Secondary explanation and advanced configuration remain
available without competing with the primary question. The standalone UX
prototype used to settle this direction is a local design artifact only; runtime
evidence must prove the behavior independently.

## Current implementation and evidence checkpoint

The following G30 slices are implemented and committed:

- browser-first setup with workspace, access-boundary, multi-model, role-default,
  and ready-summary steps;
- explicit readiness probes, including truthful `detected`, `configured`,
  `usable`, and `authentication_required` classifications;
- bounded Codex and Claude process adapters with explicit argv, private working
  directories, restricted environments, timeout/cancellation handling, and
  structured failure parsing;
- API-provider configuration fields, native folder selection, and setup
  persistence/idempotence coverage; and
- operator-facing package README requirements recorded as a release gate.

The current local evidence has an important split:

- Codex's explicit GigAI readiness probe is `usable`.
- A direct operator-shell Claude `-p --output-format json` probe returned a
  successful structured `READY` result; this is recorded in
  `docs/development/evidence/phase-5/G30/claude-direct-probe.md`.
- The same Claude surface under GigAI's restricted child environment reports
  `authentication_required`, so Claude has not yet met the through-the-model-port
  support criterion. This is an authentication-context boundary to resolve, not
  a missing executable or an unsupported command surface.

Consequently, acceptance criteria 2 and 3 remain open pending a safe provider
authentication-context decision and a real invocation through GigAI's model port.

## Contract gate

Before runtime adapter work, reconcile accepted S18-02 process policy with real Codex CLI and Claude Code invocations. The contract decision must settle:

1. Endpoint and model-target representation for a CLI executable, selected model, detected version, and provider-owned authentication.
2. Codex `exec --json` and Claude `-p --output-format json` envelopes, including default-model behavior and version drift handling.
3. Explicit working-directory, stdin, argv, timeout, cancellation, and environment inheritance rules. No shell strings, broad inherited credential values, automatic fallback, retry, or target mutation are allowed. Claude's optional `CLAUDE_CODE_OAUTH_TOKEN` handoff is explicit, transient, and scoped to the Claude child only.
4. Whether existing model-invocation/model-exchange resources represent CLI terminal and replay fields without semantic overload; any amendment is additive and preserves prior resource bytes and hashes.
5. Credential onboarding semantics: environment references first, and a protected local `.env` option only if atomic write, restrictive permissions, runtime loading, redaction, and recovery are specified.

The accepted decision must distinguish `detected`, `configured`, `usable`, and `selected`. Executable detection alone never grants invocation authority.

## First implementation boundary

1. Add bounded Codex and Claude model-port adapters using the installed non-interactive command surfaces; preserve provider-specific output only in the existing replay boundary.
2. Add real local probes for the installed CLIs, plus fake-process tests for timeout, malformed output, credential non-inheritance, cancellation, and no-fallback behavior.
3. Extend setup to display OpenAI, OpenRouter, Codex, and Claude with truthful status, allow multiple usable models to be enabled, and collect separate default assignments for reviewer, verifier, researcher, and Gig creation.
4. Add native local-folder selection on supported hosts, derive the private workpad folder from the chosen GigAI home, and retain an absolute-path fallback.
5. Add supported API-provider onboarding through credential references. Raw values must never enter config, manifests, logs, or browser responses. Claude setup must explain and detect the optional `claude setup-token` environment handoff without storing its value.
6. Keep Anthropic API visible as disabled until its adapter contract is accepted; do not represent planned support as configured support. Offline/demo fixtures remain internal developer and contract-test paths, not a normal operator model choice.
7. Refresh the HTMX setup/create surfaces with the accepted one-question-at-a-time
   setup flow, plain-language labels, contextual Gig-definition explanation,
   adaptive follow-up behavior, proposal-build/review states, and no
   implementation flags required for ordinary use.
8. Provide an explicitly opt-in local UAT test for named real CLI targets; the
   normal test suite must remain provider-free.

## In scope

- Codex CLI and Claude Code adapters through the existing model port;
- installed executable/version/readiness discovery;
- selected model/default-model configuration;
- multi-model enablement and machine-wide default assignments for reviewer, verifier,
  researcher, and Gig creation;
- OpenAI and OpenRouter credential-reference onboarding;
- protected local `.env` onboarding only if its runtime contract is accepted;
- native folder selection and derived home/workpad paths;
- setup reruns that preserve existing providers and update one explicit default;
- the accepted five-step browser setup flow, including access-boundary branching,
  multi-select model enablement, inline API configuration, role-default assignment,
  and sectioned final summary;
- HTMX setup and Gig-definition/create usability fixes;
- real local CLI probes, mutation tests, integration tests, and installed replay;
- sanitized evidence for provider/auth/readiness behavior.
- a concise package README for the published PyPI page, with a plain-language
  Gig definition, the normal setup/create path, and representative examples;
  internal schemas and goal/evidence mechanics remain linked only from the
  repository documentation where appropriate.

## Out of scope

- automatic `codex login`, `claude login`, or credential creation;
- copying or serializing OAuth/API secret values into Gig artifacts;
- automatic provider fallback, retries, racing, or background invocations;
- Anthropic API support before its own accepted implementation contract;
- target mutation, Run execution authority, or changes to G20/G21/G23;
- silently migrating an existing `~/.gigai` installation to a new folder;
- release publication, which belongs to G31/G12 release mechanics.
- defining or editing a Gig workflow, including `review-verify-fix-loop` roles;
- normal-user selection of an offline/demo fixture as a model.

## State and authority contract

1. Setup may record an enabled model roster, model preferences, and machine-wide
   role defaults; only the model adapter factory may authorize an invocation.
2. `detected` means an executable was found without invoking it. `configured` means GigAI has a typed endpoint/target. `usable` requires readiness and authentication availability. A failed probe must classify provider-owned authentication as the explicit `authentication_required` reason, not as a missing or incompatible executable. `selected` means the operator chose it as the current default; these states cannot be inferred from one another.
3. Setup may enable multiple usable model targets, but each default role assignment names one explicitly configured target. No role silently falls back to another target.
4. Setup-level role defaults are machine configuration only. A Gig definition may override or extend its own workflow roles through the existing Gig lifecycle; setup cannot create or mutate a Gig definition.
5. CLI child processes receive an explicit environment allowlist. GigAI credential values never enter argv, stdin, records, or logs. The only credential exception is the explicitly named `CLAUDE_CODE_OAUTH_TOKEN`, passed transiently to Claude when present; it is never persisted or included in generic environment inheritance.
6. CLI invocations run in an explicitly selected, non-target work directory unless the accepted contract proves a narrower read-only target context.
7. A CLI terminal result is normalized through the existing model port and model-invocation record. It cannot create a proposal, Run, target effect, or active-version transition by itself.
8. Setup folder selection changes ownership only after explicit operator submission and successful atomic configuration publication.
9. A browser page is a local projection. It cannot approve a Gig or grant a capability outside the existing lifecycle and authority contracts.
10. The setup browser may collect and summarize machine configuration, but it
    cannot define a Gig, assign Gig-specific workflow authority, or approve a
    proposal. The final setup summary is informational until the existing
    configuration publication path succeeds.
11. The enabled model roster is a set, not a single choice. Role defaults may
    reference only enabled, usable targets; changing the roster cannot silently
    rewrite a Gig's own role definition.

## Acceptance criteria

1. The contract-impact/contract-amendment decision is accepted and cites S18-02, G18, G26, G27, and G28 evidence.
2. Installed Codex and Claude binaries are discovered read-only with version evidence; unsupported states and missing provider authentication fail closed with distinct classifications. Claude setup reports whether `CLAUDE_CODE_OAUTH_TOKEN` is available and explains `claude setup-token` when it is absent.
3. A real Codex invocation and a real Claude invocation complete through the model port using explicit non-interactive commands and sanitized replay records; no target mutation occurs.
4. Fake-process and mutation tests kill argv, shell, cwd, environment, timeout, cancellation, malformed-output, retry, and fallback guards.
5. Setup lets an operator choose a storage folder, derives workpads, preserves existing state, and never silently moves an existing home.
6. Setup displays OpenAI, OpenRouter, Codex, and Claude with truthful detected/configured/usable/selected status, permits multiple enabled targets, and records distinct reviewer, verifier, researcher, and Gig-creation defaults.
7. OpenAI/OpenRouter credential references can be configured without exposing values. Any `.env` path is atomic, mode-restricted, runtime-loaded, and excluded from records and diagnostics, or the option remains disabled.
8. `gigai create <gig-name>` opens the adaptive HTMX Gig-definition flow after setup without request/reference/open/model implementation flags. The flow does not assume a fixed Gig such as review-verify-fix-loop; Gig-specific roles are edited at Gig level.
9. Browser setup and create flows cover validation errors, cancellation, rerun/idempotence, stale sessions, and interrupted setup without partial configuration.
10. Fresh-wheel installed replay proves setup, CLI readiness, model selection, and create entry behavior from an isolated environment.
11. Completion audit and terminal handoff identify G31 as the next consumer; no release or alpha claim is made by G30 alone.
12. Browser setup implements the five explicit steps—Workspace, Access boundary,
    Available models, Machine defaults, and Ready—with one active question at a
    time, clear Back/Continue actions, and revisitable prior answers.
13. Access-boundary selection branches the model question correctly: CLI-only
    exposes local CLIs, API-only exposes configured API providers, and Both
    exposes the combined roster. Local CLI entries support independent selection;
    API provider selection expands the corresponding configuration fields inline.
14. The role-default question assigns enabled models independently to reviewer,
    verifier, researcher, and Gig creator. The Ready screen summarizes the actual
    workspace, access boundary, enabled models, and role assignments without
    stale or inferred values. The Gig creator assignment uses the existing
    `model_invocation:gig-builder` role reference at invocation time.
15. Browser captures and interaction tests prove the first screen is plain-language
    and Gig-oriented, no secret value enters the page or durable state, no
    implementation-facing model flags are required, and setup cannot create or
    mutate a Gig definition.
16. Before v0.1.6 publication, the package README renders as a simple operator
    introduction on PyPI: it defines a Gig in plain language, explains the
    setup/create flow, and includes concrete examples such as
    `tailor-resume-for-job` and `review-verify-fix-loop`. It does not lead with
    schema counts, schema filenames, internal evidence directories, goal
    numbering, or implementation-history detail.
17. An opt-in `g30_live` UAT test runs readiness probes for explicitly named
    configured local targets, reports authentication failures distinctly, and
    remains skipped unless the operator enables it explicitly; ordinary CI never
    invokes a provider.

## Verification and evidence

Evidence belongs under `docs/development/evidence/phase-5/G30/` and includes the accepted contract decision, the accepted browser setup interaction record, sanitized installed CLI version/probe results, adapter conformance and mutation reports, setup/create browser captures for each access branch, credential-boundary fixtures, folder-selection fixtures, installed-wheel replay, package-README rendering/content checks, the opt-in local UAT result, completion audit, and terminal handoff. The direct operator-shell Claude probe is recorded separately as a partial checkpoint in `claude-direct-probe.md`; it does not substitute for a successful invocation through GigAI's model port. The UAT instructions live in `uat-runbook.md`.

## Stop boundary

Stop if either real CLI cannot be invoked through the bounded model port, if provider authentication or model/role-default behavior cannot be reported truthfully, if a secret can enter a durable record, if folder selection can silently replace an existing home, if the ordinary setup/create flow still requires implementation-facing flags, if the browser collapses multiple setup decisions into one opaque form, if the Ready summary is incomplete or stale, if setup starts defining Gig-specific workflows, or if the published package README still presents internal schemas and development history as the primary user experience. Do not ship a disabled CLI card as evidence of CLI support.
