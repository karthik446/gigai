# S43.3 — Authority-Focused Agent Mediation Amendment

**Status:** Proposed — requires a fresh S43.3 Adopt decision before G43.3 activation

**Supersedes:** the closed-runtime *acceptance criterion* in S43.3.

**Preserves:** the historical closed-runtime Reject in
[decision-record.md](decision-record.md), committed as `5861c5d`. That record
correctly establishes that the current CLI adapters cannot prove physical tool
confinement or complete internal-action observability.

## Revised question

Can GigAI safely run configured Codex CLI and Claude Code participants as
untrusted public-research suggestion sources while GigAI remains the sole
authority for research effects, evidence, accounting, lineage, and consent?

This is not a claim that an unrestricted CLI cannot browse, invoke a native
tool, or make an unobserved internal action. Those actions are outside GigAI's
authority and must not become a GigAI fact by assertion, URL, citation, tool
result, or model claim.

## Authority and disclosure contract

1. **GigAI constructs authority.** GigAI alone creates Run IDs, Plan bindings,
   participant-invocation IDs, suggestion-record IDs, broker-request IDs,
   event IDs, action IDs, evidence references, citation records, lineage edges,
   idempotency keys, reservations, and consent records. A participant-supplied
   ID, URL, citation, tool result, source text, or factual claim is an
   untrusted suggestion and cannot populate an authoritative field directly.
2. **Public brief before invocation.** Before each unrestricted participant
   invocation, GigAI materializes and seals a `research-participant-brief:1`
   artifact. It contains only Plan-authorized `public_research` fields and
   deterministic transformations of those fields, the assigned role, and
   permitted suggestion vocabulary. It has a GigAI-generated
   `research_brief_<uuidv4>` ID, exact Plan/Run/participant bindings, and an
   imported-byte digest. It contains no private/local-only material.
3. **Untrusted suggestions.** GigAI captures a participant response as a
   private `research-action-suggestion:1` record, with a GigAI-generated
   `research_suggestion_<uuidv4>` ID, the sealed invocation/brief references,
   role/phase assigned by GigAI, raw-output digest, and a bounded candidate
   action. Its machine-usable portion may name only an action type permitted by
   the Plan and public field keys present in the sealed brief; any rationale is
   non-authoritative text. The response cannot select a Run, Plan, role,
   provider, action ordinal, payload, URL, citation, or capability. An invalid,
   replayed, or cross-bound suggestion is rejected before it affects a request.
4. **GigAI-owned broker.** GigAI validates an accepted candidate solely
   against the sealed Plan, public brief, generic capability policy, and
   remaining reservations. It then constructs `broker-action-request:1` and
   all provider-specific envelopes itself. A suggested URL, citation, or tool
   result is never sent as a direct fetch target and never becomes evidence;
   a later policy may reacquire public information through an allowed action
   and independently pin the returned bytes.
5. **Private input rule.** A private-sensitive reference, private/local-only
   requirement, or private prior artifact causes
   `private_input_requires_restricted_adapter` before unrestricted prompt
   assembly. GigAI may apply local-only requirements deterministically to its
   own promotion/evaluation logic, but it never places them in an unrestricted
   participant brief. A restricted adapter is a separately proven adapter path;
   no currently configured CLI qualifies merely by being detected or usable.
6. **No agent-created consent or state.** Only the existing direct
   `gigai run --confirm` path starts a Run. A suggestion, model output, or
   broker response cannot approve a Gig, alter a Graph Set, create a lead,
   select a lead, advance an application state, or redeem consent.

## Honest accounting and observability

GigAI records every adapter invocation it starts and every broker lifecycle
event it executes in a monotonic Run sequence. It accounts for its own model
invocation reservation, provider-reported usage when supplied, the sealed
no-report rule when it is not, and every broker reservation/dispatch/result/
reconciliation it performs.

It does **not** claim to observe every internal CLI turn, hidden native action,
or direct tool use. Such material is untrusted, unaccounted external behavior;
it cannot be used as GigAI evidence or to increase available budget. A Run
report may state only GigAI-observed invocation and broker facts.

## Required feasibility evidence

The amended S43.3 decision must use deterministic fake adapter and broker
fixtures to prove all of the following:

- GigAI-generated public briefs contain only approved public fields; attempts
  to include a private reference, local-only requirement, path, credential, or
  undeclared text fail before the adapter is invoked.
- Suggestions that spoof a Run/Plan/participant/action ID, URL, citation,
  provider result, role, or action ordinal cannot write or influence an
  authoritative request, citation, candidate, lead, evidence record, or
  consent record.
- GigAI generates every durable identifier and lineage edge, validates the
  suggestion against the sealed public brief, and refuses replay,
  cross-Run/Plan/participant, malformed, over-budget, unsupported, and
  post-cancellation suggestions before broker dispatch.
- Every GigAI-started adapter invocation and broker lifecycle event is ordered,
  durable private Run evidence; reports distinguish provider-reported usage
  from unknown usage and make no claim about unobservable internal CLI actions.
- A broker result is the sole source of provider/citation evidence. A model
  URL, citation, tool-result claim, or purported research finding remains a
  non-authoritative suggestion until a permitted broker action reacquires and
  validates it.
- Cancellation, duplicate delivery, timeout, retry, malformed broker result,
  and unknown usage follow the sealed reservation/reconciliation rule without
  fallback or a second unreserved dispatch.

## Decision rule

- **Adopt:** every authority, disclosure, suggestion-rejection, broker, and
  accounting proof above passes for both configured participant adapters.
- **Narrow:** identify the failing adapter or disclosure class and define the
  smaller participant set or public-only profile. It cannot authorize private
  input for an unrestricted participant.
- **Reject:** GigAI cannot prevent untrusted participant output from becoming
  authoritative broker/evidence state, cannot construct a public-only brief,
  or cannot account for its own lifecycle deterministically.

This amendment does not implement a broker, add a credential, call Exa, alter
G43 `standard@1`, or make the historical closed-runtime Reject disappear.
