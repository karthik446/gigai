# G27 Terminal Handoff — Adaptive Gig Discovery

- Status: Complete
- Next consumer: G29 v0.1.5 human UAT and dogfooding
- Release-lane consumer: G25 alpha-readiness review

## What G29 may consume

- `gig-discovery-manifest.schema.json` and its installed 31-resource package
  baseline;
- the configured-model discovery round with a hard five-question ceiling;
- capability, network, privacy, budget, and evidence-boundary disclosures;
- stable-definition and changing-Run-input projections;
- revisioned discovery manifests with immutable parent linkage;
- bounded G20 improve context containing learning IDs and the active-version
  snapshot only; and
- the recovery evidence for interrupted journal publication and malformed
  model responses.

## Boundaries that remain in force

- Discovery is subordinate evidence. It cannot allocate a proposal ID, approve
  a proposal, advance an active version, create a Run, or authorize a target
  effect.
- G20's evidence-sufficiency and improvement-quality gates remain mandatory;
  discovery context cannot replace either gate.
- The deterministic fixture is plumbing evidence, not a calibrated judge or
  evidence of production-model question quality.
- Codex and Claude executable detection does not advertise adapter support.
- Human UAT records stay outside the repository under the operator's local G29
  directory; no resume, job posting, credential, raw model output, or private
  database belongs in Git.

## G29 start condition

G29 may begin after the v0.1.5 candidate is released. It should exercise
`tailor-resume-for-job` and a structurally different Gig, inspect the rendered
questions and capability disclosures with the operator, and inspect the
SQLite/journal/workpad artifacts without treating either the discovery
manifest or a model response as approval authority.

