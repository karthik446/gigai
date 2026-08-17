# TD-0008 — Browser-first create and model setup

- Status: Open; v0.1.5 release blocker
- Discovered during: G24/G26 UAT preparation, 2026-08-16
- Affected surfaces: `gigai setup`, `gigai init`, `gigai create`, model
  readiness, and the local HTMX session
- Owning lane: G26/G27 runtime before G24 final UAT

## Observation

The useful product flow should be:

```text
gigai setup -> choose model path -> gigai create <gig-name> -> HTMX discovery
```

Today, the operator can encounter implementation-facing requirements such as
explicit target binding, `--request`, `--reference`, `--open`, and an offline
fixture label. The model choice is not yet a clear setup decision between a
configured API target and an installed local executable. That makes
`gigai create tailor-resume-for-job` fail before the browser can facilitate the
Gig definition.

## Required resolution

`gigai setup` must present a clear model choice:

- configured API target using a credential reference, never a raw key;
- detected local Codex or Claude executable, clearly labeled as detected-only
  unless an accepted GigAI adapter exists; or
- deterministic/offline fixture mode, clearly labeled as non-production.

The setup flow itself may use the existing local HTMX session where a
multi-step choice or explanation is needed. It must show readiness, adapter
support, privacy/network boundary, and what the selected model may do.

After one-time project/target initialization, the normal command
`gigai create tailor-resume-for-job` must open the browser-first flow without
requiring domain-specific request/reference flags. The browser must collect
the Gig definition, optional local references, adaptive follow-up questions,
and later research/build decisions. It must not hide authority or capability
choices behind CLI flags.

No installed executable may be advertised as supported merely because it is on
`PATH`. Unsupported candidates remain selectable only as a transparent
detected/unavailable state until an adapter contract and evidence exist.

## Exit evidence

- A fresh configured project can run `gigai create tailor-resume-for-job` and
  reach the HTMX session without implementation flags.
- Setup clearly distinguishes API, detected local executable, and deterministic
  fixture choices.
- Credentials remain references and never enter browser/session records.
- The initial page asks for the Gig definition; optional references and model
  policy are explained rather than dumped as raw protocol fields.
- Adaptive question, research, proposal-review, rejection, and approval flows
  are covered by the eval and integration tiers.
- The exact command is exercised in the v0.1.5 candidate before G24 UAT.

