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
2. **Disclosure-isolated public brief before invocation.** Before each
   unrestricted participant invocation, GigAI materializes and seals a
   `research-participant-brief:1` artifact. It contains only
   Plan-authorized `public_research` fields, deterministic transformations,
   broker-generated public evidence views, the assigned role, and permitted
   suggestion vocabulary. It has a GigAI-generated `research_brief_<uuidv4>`
   ID, exact Plan/Run/participant bindings, and a digest of its subordinate
   public payload—not a self-digest of the enclosing document.

   GigAI launches the participant with an empty, per-invocation working root;
   fresh empty `HOME`, `CODEX_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME`,
   `XDG_CACHE_HOME`, and `TMPDIR` descendants; a fixed locale/process
   allowlist; and a previously resolved executable path rather than inherited
   `PATH`. Host home/GigAI-home configuration, active-worktree files, ambient
   instructions, memories, caches, and credentials are not process inputs.
   Provider authentication requires a separately proven adapter channel and
   may not arrive through inherited home/XDG state. This establishes disclosure
   isolation only; it does not claim physical confinement of a native CLI.
3. **Broker-generated public evidence views.** After a broker response passes
   provider validation and the sealed policy's sanitization rule, GigAI alone
   may construct a `public-research-evidence-view:1`. The view carries exact
   broker-action lineage, sanitized policy-approved public fields, and
   GigAI-minted opaque result handles. It excludes raw provider bytes, raw
   response/result identifiers, URLs, citations, credentials, and any
   undeclared field. A later participant can receive a view only through a new
   sealed public brief; it never receives a raw provider response. GigAI alone
   resolves an opaque handle back to the sealed broker lineage.
4. **Untrusted suggestions.** GigAI captures a participant response as a
   private `research-action-suggestion:1` record, with a GigAI-generated
   `research_suggestion_<uuidv4>` ID, the sealed invocation/brief references,
   role/phase assigned by GigAI, raw-output digest, and a bounded candidate
   action. Its machine-usable portion may name only an action type permitted by
   the Plan, public field keys present in the sealed brief, and opaque result
   handles exposed by a sealed public evidence view in that same brief; any
   rationale is non-authoritative text. A handle is an untrusted nomination,
   not a provider identifier. The response cannot select a Run, Plan, role,
   provider, action ordinal, payload, URL, citation, or capability. An invalid,
   replayed, cross-bound, or unexposed-handle suggestion is rejected before it
   affects a request.
5. **GigAI-owned broker.** GigAI validates an accepted candidate solely
   against the sealed Plan, public brief, generic capability policy, and
   remaining reservations. It then constructs `broker-action-request:1` and
   all provider-specific envelopes itself. A suggested URL, citation, or tool
   result is never sent as a direct fetch target and never becomes evidence.
   For a handle-backed action, GigAI resolves the handle only through its own
   same-Run broker lineage and constructs the provider-specific selector under
   the adopted policy; a participant never supplies that selector.
6. **Private input rule.** A private-sensitive reference, private/local-only
   requirement, or private prior artifact causes
   `private_input_requires_restricted_adapter` before unrestricted prompt
   assembly. GigAI may apply local-only requirements deterministically to its
   own promotion/evaluation logic, but it never places them in an unrestricted
   participant brief. A restricted adapter is a separately proven adapter path;
   no currently configured CLI qualifies merely by being detected or usable.
7. **No agent-created consent or state.** Only the existing direct
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
- Each adapter launch receives a fresh execution root and no inherited home,
  `CODEX_HOME`/XDG configuration, worktree, instruction, memory, cache, or credential
  path. Canary files in each forbidden host location and fake-adapter launch
  inspection prove that GigAI supplies only the sealed public brief and fresh
  execution root; a CLI that requires inherited configuration narrows or
  rejects.
- Fake adapters prove the GigAI wrapper only. In addition, each exact configured
  Codex CLI and Claude Code adapter must complete one bounded public marker
  invocation using the isolated launch and its separately proven authentication
  channel. The deterministic prompt contains only a fresh non-secret marker and
  succeeds only on an exact marker return; distinct sentinel instructions in
  every forbidden host root must not change that return. Evidence records the resolved
  executable/adapter identity and version, safe environment names, execution
  root facts, expected/returned marker digests with exact match,
  authentication-proof reference, and GigAI-observed accounting—never a
  credential or host path. Failure by either adapter is
  Narrow or Reject, not Adopt. The proof uses
  `urn:gigai:schema:participant-authentication-channel-proof:1`, binds the
  exact target/adapter executable digest and version, records a non-secret
  authentication-channel configuration reference/digest,
  `home_xdg_inheritance: false`, and expires. It is reusable target/adapter/
  channel evidence; each later `run-plan:2` separately seals
  `{ participant_id, proof_ref }` and validates the current configuration
  digest before consent.
- `marker_evidence` is a matching successful
  `urn:gigai:schema:marker-probe-record:1` record. Its strict fields retain
  direct-confirmation evidence, target/configuration/executable identities,
  operator actor/session identity, expected/returned marker digests with exact
  match, reservation, actual/reconciled usage, terminal state, timestamps, and
  stable refusal codes, but no credential, host path, raw prompt, raw marker,
  or provider output. Missing confirmation is a stable CLI refusal and creates
  no marker record.
- A marker probe is a provider call even though it is not a research Run. It
  runs only from direct operator command
  `gigai models --probe TARGET --s43-public-marker --max-tokens N --max-cost-usd C --confirm`.
  GigAI generates the non-secret marker after confirmation, reserves the finite
  model-call/token/cost budget before invoking the adapter, and reconciles
  actual usage using only the schema's valid terminal state/code pair. Missing
  confirmation and non-positive, non-finite, or unreservable limits are stable
  CLI refusals before probe-ID/reservation/record creation. Plan construction,
  Plan display, and consent preview can read a proof but cannot silently invoke
  or refresh a probe.
- Immediately before every participant invocation, GigAI revalidates target,
  current executable/adapter digest, current channel-configuration digest,
  matching successful marker record, and expiry. A post-consent mismatch fails
  before prompt assembly or process launch and cannot trigger an implicit probe
  or proof refresh.
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
- A broker-generated public evidence view retains exact action lineage and
  sanitized approved public fields while excluding raw response bytes. A later
  participant can nominate only an opaque handle contained in its sealed view;
  GigAI resolves the handle against same-Run lineage and rejects an unknown,
  stale, cross-Run, or unexposed handle before dispatch.
- Cancellation, duplicate delivery, timeout, retry, malformed broker result,
  and unknown usage follow the sealed reservation/reconciliation rule without
  fallback or a second unreserved dispatch.

## Decision rule

- **Adopt:** every authority, disclosure, suggestion-rejection, broker, and
  accounting proof above passes for both configured participant adapters,
  including each exact adapter's bounded public marker invocation through a
  separately proven isolated authentication channel.
- **Narrow:** identify the failing adapter or disclosure class and define the
  smaller participant set or public-only profile. It cannot authorize private
  input for an unrestricted participant.
- **Reject:** GigAI cannot prevent untrusted participant output from becoming
  authoritative broker/evidence state, cannot construct a public-only brief,
  or cannot account for its own lifecycle deterministically.

This amendment does not implement a broker, add a credential, call Exa, alter
G43 `standard@1`, or make the historical closed-runtime Reject disappear.
