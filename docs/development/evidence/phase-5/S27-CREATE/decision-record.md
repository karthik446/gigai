# S27-CREATE — Browser-First Create and Model Setup Decision Record

- Status: Accepted; no runtime implementation
- Spike: [S27-CREATE](../../../goals/phase-5/S27-CREATE-browser-first-create-and-model-setup.md)
- Recorded: 2026-08-16
- Depends on: G22 loopback interview, G26 builder evidence, G18 model
  readiness, and current setup/init behavior
- Unblocks: G28 v0.1.5 readiness implementation

## Decision summary

The normal product path is:

```text
gigai setup
  -> choose a truthful model path and privacy/network policy
gigai init
  -> bind the current project/target once
gigai create tailor-resume-for-job
  -> open the local HTMX Gig-definition canvas
```

After setup and target initialization, the create command must not require
implementation-facing `--request`, `--reference`, `--open`, or `--model-target`
flags. The browser flow owns the operator interaction. The model may facilitate
questions and bounded research, but durable session/proposal/approval authority
remains in the existing G22/G26 lifecycle.

This spike does not claim Codex CLI or Claude CLI support. Discovery is useful
for explaining options; it is not adapter verification.

## Current behavior disposition

The current CLI already has these properties:

- `--request` is optional and falls back to the Gig name/commission;
- `--reference` is optional for the normal create interview;
- `--open` defaults to opening the loopback browser; and
- `--model-target` defaults to `offline-default`.

The last item is the product risk. The current command can reach HTMX while
silently using deterministic fixture behavior. `--allow-provider-network` is
also an explicit opt-in flag rather than a model/setup decision. G28 must make
the selected model path and its readiness visible before the interview begins.

## Setup model-choice contract

The interactive setup flow presents three truthful choices:

| Choice | Readiness | Meaning | Permitted behavior |
| --- | --- | --- | --- |
| Configured API target | `usable` only when the configured adapter and non-secret configuration resolve | Existing G18 target, credential reference, model, endpoint, and policy are known | Provider invocation only under the explicit configured network policy and bounded budget |
| Detected local Codex/Claude executable | `detected` or `unsupported` | `PATH` discovery found a candidate; no GigAI adapter support is implied | Explain the candidate and missing evidence; do not spawn or advertise it as usable |
| Deterministic fixture | `usable` with `deterministic_fixture` mode | Offline, installed fixture response for development and UAT plumbing | No network, credential lookup, provider claim, or production-quality claim |

The UI must distinguish `detected`, `configured`, `verified`, `usable`,
`unavailable`, and `unsupported`. A display name such as “Codex” or “Claude”
is not a model identity and cannot promote a candidate to `usable`.

Setup persists only a selected target name, readiness/mode, and policy
references. It never persists API keys, authorization headers, environment
values, cookies, resolved secret values, or model output. The selected model
target is a configuration choice, not a capability grant.

The current configuration has `model_targets` but no operator-selected default
for create. G28 must add an explicit default-target selection through the
configuration migration path, or define an equally durable existing owner
before runtime work begins. It must not continue to infer the choice from
array order or silently fall back to the fixture when the operator selected a
different path. Existing configuration versions remain readable through the
repository's explicit version/migration rules.

## Target and project initialization

`gigai init` remains the explicit target-binding operation:

- inside a Git repository, the current directory may be inferred safely;
- for a non-Git target, the operator must provide `--target` to `gigai init`;
- once initialized, `gigai create <gig-name>` resolves the bound project from
  the current target and does not ask the operator to repeat implementation
  paths; and
- create must fail clearly when no binding exists rather than creating a
  project in an unintended directory.

Initialization may write only the existing project binding/registry artifacts.
It must not mutate target content, create a Gig version, create a Run, or
authorize a target effect.

## Browser-first interaction contract

The existing local loopback HTMX session remains the only browser authority.
G28 extends it rather than creating a second UI state machine.

The first screen is plain-language and asks only:

> What should this Gig repeatedly accomplish?

It explains that the operator is defining a reusable Gig, not asking GigAI to
execute the work immediately. Local references are optional and are selected
only when they help define the Gig. The page shows the selected model path,
privacy/network boundary, and deterministic-versus-model-backed status in
plain language without exposing raw schema names.

The interaction then proceeds through these durable lifecycle layers:

1. **Define:** record the operator's Gig intent and optional exact references.
2. **Clarify:** the selected usable model may ask bounded typed follow-ups;
   the model chooses content, while GigAI validates shape, dependencies, and
   the per-round ceiling.
3. **Research/build:** after explicit operator continuation, perform only the
   configured bounded model/research work and retain citations/assumptions.
4. **Review:** present the stable Gig definition, reusable Goals, changing Run
   inputs, expected outputs, research boundary, assumptions, and unresolved
   decisions.
5. **Revise/reject/approve:** operator decisions use the existing G22/G26
   events and proposal lifecycle. Approval seals the ordinary proposal only;
   it does not run the Gig or mutate the target.

Every browser action is a typed event against durable session state. Browser
state, HTMX fragments, model memory, and client retries are disposable. A
replayed action is idempotent or refused; it cannot duplicate a question,
proposal, approval, Run, or target effect.

## Boundaries shown to the operator

The first screen and review screen must make these distinctions understandable:

- **Local references:** exact files selected by the operator; no arbitrary
  directory sweep or implicit reference inclusion.
- **Model path:** deterministic fixture, configured API target, or detected-only
  executable candidate; these are not interchangeable.
- **Network:** local-only by default; configured-provider network requires the
  explicit configured policy and model readiness.
- **Credentials:** references are stored; credential values never enter the
  session, browser, proposal, or report.
- **Proposal:** reviewable Gig definition and evidence; not an approved version
  until the existing approval event succeeds.
- **Approval:** creates/seals the proposal through the existing authority; does
  not execute a Run or authorize target mutation.

The UI must say what “offline” means: deterministic installed fixture behavior,
not a remote model, not web research, and not evidence of production model
quality.

## Required evidence for G28

G28 must produce all four evidence tiers for the exact command
`gigai create tailor-resume-for-job`:

1. contract vectors for setup choices, readiness labels, optional references,
   target-binding refusal, and approval boundaries;
2. integration fixtures covering configuration selection, project binding,
   loopback session, durable events, model refusal, revision, and recovery;
3. fresh-wheel installed replay from setup through browser launch and
   path-safe result reporting; and
4. S27-EVAL behavioral cases proving that the operator sees truthful model
   status, the initial Gig-definition screen is understandable, optional
   references remain optional, and no approval/Run/target authority is gained
   from model or browser output.

At least one negative case must cover each boundary: detected executable shown
as usable, fixture mode mislabeled as production, missing target binding,
credential value persistence, implicit reference inclusion, and browser/model
approval bypass.

## Decision and stop boundary

This spike accepts browser-first create, explicit one-time target initialization,
truthful model-choice presentation, and reuse of G22/G26 session authority as
the G28 product contract. The default-target persistence mechanism remains a
G28 configuration implementation obligation; it cannot be inferred from
current array order or the fixture default.

Stop G28 if `gigai create tailor-resume-for-job` still needs hidden
implementation flags, setup advertises a detected executable as usable, the
operator cannot tell fixture from configured-model behavior, or a browser/model
event can advance proposal, Run, target, credential, or version authority.

## Evidence references

- [S27-CREATE spike](../../../goals/phase-5/S27-CREATE-browser-first-create-and-model-setup.md)
- [G26 model readiness matrix](../G26/model-readiness-matrix.md)
- [G26 contract amendment](../G26/contract-amendment.md)
- [G22 interview evidence](../../phase-3/S22-01/decision-record.md)
- [G18 model exchange goal](../../../goals/phase-3/G18-provider-comparison-and-model-handoff.md)
