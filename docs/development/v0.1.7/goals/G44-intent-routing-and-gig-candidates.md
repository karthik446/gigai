# G44 — Intent Routing and Gig Candidates

**Version:** v0.1.7
**Status:** Proposed — first dogfood review input
**Depends on:** G43.2 complete and ratified; G43.1 for provider-backed
document-review dogfood
**Unblocks:** G45, G46 Job Search Lifecycle, and later user-authored local Gigs

## Outcome

G44 makes GigAI start from an operator's intent rather than forcing every
request into a Gig-creation interview. It routes one explicit request to one
of four bounded work routes, or asks for operator input when no safe choice
can be made:

```text
explicit request
  -> existing approved Gig Graph selection
  -> one-off local result
  -> private Gig candidate
  -> private revision candidate for an approved Gig
  -> needs operator input
```

An existing Gig Run asks only for required inputs that are absent or invalid.
A candidate asks only questions whose answers materially change its input,
effect, output, evaluation, or reuse contract. An approved Gig is never
silently changed by a Run, candidate, model response, or evaluation result.

## Routing contract

The operator's explicit command and selected Gig/version take precedence over
all routing heuristics. Otherwise the router uses only the typed request text,
explicit attachments, project-local package metadata, and safe summaries of
approved Gig input/output contracts. It may not search ambient files, inspect
conversation history or the clipboard, invoke a provider, fetch a URL, use a
browser or tool, cause a target effect, or create a provider prompt while
routing.

The router emits exactly one of:

- `existing_gig`: one approved Gig/version and one named graph satisfy the
  request; G44 emits G43.2's typed `g44_routed` graph-selection record and
  reports only that graph's mandatory missing inputs;
- `one_off`: the request is bounded but there is no safe reuse claim; this
  result creates no candidate, Gig, Run, or durable request-text record;
- `gig_candidate`: the operator explicitly asked to create a Gig, or declared
  durable inputs, reusable output, recurring execution, evaluation, or
  configurable behavior;
- `revision_candidate`: the operator explicitly asked to change a named
  approved Gig; or
- `needs_operator_input`: more than one safe route remains and choosing would
  change effect, privacy, data source, or active-Gig authority.

The router records its rule/version, candidate routes considered, safe reason,
and only labels/IDs/digests in G43.2's graph-selection-record schema. It does
not persist the request text for an `existing_gig`, `one_off`, or
`needs_operator_input` result. Only separately and explicitly selected candidate
workpad creation may retain request text within the private candidate boundary;
explicit selection of `one_off` does not permit retention. A selection record
is not Run authority; a Plan must seal it and direct Run consent remains required.

## Candidate workspace

A Gig candidate is private, non-executable, non-portable workpad material. It
does not have a Gig ID, active version, capability grant, selected model,
provider call, schedule, or Run authority. It contains only:

- a bounded intent summary;
- inferred input and output contract drafts;
- declared unresolved decisions and their reason;
- a proposed effect/data-source/privacy boundary;
- optional operator-supplied sample inputs and locally generated sample output
  placeholders;
- a proposed evaluation outline; and
- lineage to an existing Gig/version for a revision candidate.

Candidate creation is explicit: `gigai create <intent>` creates one candidate;
an `existing_gig` or `one_off` route merely reports its result and may suggest
candidate creation. A candidate may not become an approved Gig except through
the existing proposal, validation, review, and `gigai approve` lifecycle.

## Conditional questions

Questions are an exception, not a ceremony. The candidate engine must first
infer a complete safe draft from declared intent and attachments. It asks one
bounded question only when the answer changes one of these fields:

- required input/data-source type or retention;
- allowed effect or network policy;
- recurring/scheduled behavior;
- output artifact type or operator-visible interface;
- evaluation target/metric; or
- a material reuse boundary.

It must show the inferred default and the reason for each question. It may not
ask for a preference that has a declared default, re-ask an answer available in
the selected Gig version, or use questions to gather ambient personal data.
A user can explicitly accept all inferred defaults, reject candidate creation,
or supply a bounded override. Missing safety-critical information produces
`needs_operator_input`, never an invented default.

## Existing-Gig and revision behavior

For an approved Job Search Lifecycle Gig, a request to tailor an explicit job
description emits `tailor-application`; a request to find jobs from an explicit
requirements profile emits `find-jobs-for-requirements`. If both graphs could
safely apply, the router returns `needs_operator_input`; it does not choose a
graph. A request to add job discovery to a named tailoring-only Gig creates an
`add_graph` revision candidate linked to that exact approved version; it does
not alter the active Gig or reuse prior Run data as new input.

Revision candidates must declare compatibility impact, added/removed inputs or
effects, evaluation cases affected, and a comparison plan. Only a fresh
proposal/version with passed validation and explicit approval can become
active. Existing Runs remain attributable to the prior approved version.

## Output and local-app surface

A Gig may declare Markdown, JSON, HTML, charts, or another reviewed artifact
form as an output. HTML is an artifact type, not a mandatory web application or
network service. The Gig contract names the output schema/renderer, any
operator-visible controls, and the private paths where Run artifacts appear.
G44 does not implement rendering, background Runs, schedules, URL fetching,
or domain-specific evaluation; it establishes the candidate information those
later capabilities require.

## First acceptance evidence

The first G44 delivery proves, with deterministic fixtures and G43.1's public
contract dogfood:

1. an existing Gig request with complete inputs returns `existing_gig` and asks
   zero questions and a valid G43.2 graph-selection record;
2. a request with exactly one missing required input asks exactly that input;
3. a Job Search Lifecycle request routes tailoring and discovery to their
   distinct graph IDs, while an ambiguous two-graph request stops at
   `needs_operator_input`;
4. an explicit create request produces a private candidate, not a Gig or Run;
5. ambiguous route/effect/privacy choices stop at `needs_operator_input`;
6. a revision candidate cannot modify an active version or historic Run;
7. all human/JSON output redacts raw request text unless candidate creation was
   explicitly selected;
8. a `one_off` result creates no candidate, Gig, Run, or durable request-text
   record, including when the operator explicitly selects that route; and
9. routing neither reads ambient files, conversation history, or the clipboard
   nor invokes providers, fetches URLs, uses browsers/tools, creates provider
   prompts, or causes target effects.

## Out of scope

Provider-backed candidate drafting, prompt optimization, URL/data-source
execution, background scheduling, HTML rendering, PII detection/redaction,
and G45 private-reference import are separate reviewed increments. G44 may
describe their future contract requirements but cannot perform them.
