# G44 Requirements Baseline for G43.1 Contract Dogfood

**Status:** Public review baseline — requires separate direct operator approval
before use in a G43.1 provider-review Plan.

**Review subject:**
`docs/development/v0.1.7/goals/G44-intent-routing-and-gig-candidates.md`

## Purpose and scope

This is the normative requirements source for the first G43.1 provider-backed
review of the public G44 contract. It is a review input only: approving it does
not activate G44, create a Gig candidate, select a graph, permit a provider
call, or grant Run authority.

## Required behavior

1. G44 routes one explicit request to exactly one of `existing_gig`, `one_off`,
   `gig_candidate`, `revision_candidate`, or `needs_operator_input`.
2. An explicit operator command and selected Gig/version take precedence over
   routing heuristics. Routing reads only typed request text, explicit
   attachments, project-local package metadata, and safe summaries of approved
   Gig contracts; it never scans ambient files, conversation history, or the
   clipboard.
3. Routing performs no provider invocation, URL fetch, browser/tool use, or
   target effect. It does not create a provider prompt.
4. An `existing_gig` result names one approved Gig/version and one named graph,
   reports only mandatory missing inputs, and emits the G43.2 selection record.
   That record is not Run authority and still requires Plan sealing plus direct
   Run consent.
5. A bounded request with no safe reuse claim is `one_off`; it does not create
   a candidate, Gig, Run, or durable request-text record.
6. A Gig candidate is private, non-executable, and non-portable. It has no Gig
   ID, active version, capability grant, model/provider call, schedule, or Run
   authority. It can become an approved Gig only through the existing proposal,
   validation, review, and explicit approval lifecycle.
7. A question is permitted only when its answer materially changes required
   inputs/data-source retention, allowed effect/network policy, recurring
   behavior, output/interface, evaluation, or a material reuse boundary.
   Otherwise the router must show/use the inferred default. Ambiguity that
   changes effect, privacy, data source, or active-Gig authority returns
   `needs_operator_input`.
8. A revision candidate is linked to the selected approved Gig/version and
   declares compatibility, changed inputs/effects, evaluation impact, and a
   comparison plan. It cannot modify the active version or historical Runs.
9. G44 may declare reviewed output artifact forms including Markdown, JSON,
   HTML, and charts. It does not implement rendering, scheduling, URL/data
   access, private-reference import, or domain-specific evaluation.
10. The first delivery’s deterministic acceptance coverage includes complete
    and one-missing-input existing-Gig routing, distinct Job Search graph
    routing, ambiguous route/effect/privacy refusal, private candidate creation,
    revision immutability, selection-record validity, and raw request-text
    redaction unless candidate creation was explicitly selected.

## Review expectations

For every material finding, the reviewer must cite both this baseline’s
requirement and the corresponding G44 subject text. The verifier must check
both locators. Findings must not treat future G43.2, G45, G46, provider-backed
drafting, scheduling, or network execution as missing G44 v0.1.7 behavior.
