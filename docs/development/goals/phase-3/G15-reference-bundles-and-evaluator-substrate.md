# G15 — Reference Bundles and Evaluator Substrate

- Status: Proposed for review
- Depends on: G14 (complete and merged; consumes its sequential Run path)
- Consumes: G07 artifact/schema validators, G08 approved Gig lifecycle, G09 journal/read surfaces, G11 model-port identity types
- Unblocks: G16 first Review Loop Gig, G17 proposal-time capability inspection, and later provider/evaluation goals

## Outcome

Define and implement GigAI's first provider-neutral substrate for review and
evaluation artifacts. A Run must be able to name the exact reference bytes it
reviewed, the review contract and criteria it applied, the evaluator versions
that produced findings, the trace and redaction boundary used, the feedback
and adjudication decisions that followed, and the machine/human reports that
can be regenerated from those local artifacts.

The substrate is domain-neutral. A repository, pull-request diff, article,
CSV, resume, or finance filing is a reference with a role and provenance, not
a special orchestration path. G15 defines the contracts and deterministic
fixture/evaluator interfaces; G16 owns the user-facing Review Loop and its
review → verify → feedback → address → closure lifecycle.

G15 is delivered in two reviewable stages within this one goal: an explicit
contract amendment plus Bundle/Contract materialization, followed by the
deterministic evaluator, finding, feedback, trace, and report substrate. Stage
two cannot begin until Stage one's schemas and replay fixtures pass.

## In scope

- Define a content-addressed Review Bundle envelope and materializer. Each
  reference has a stable reference ID, role, media type, exact-byte digest,
  size, provenance/acquisition metadata, and an explicit sensitivity and
  redaction status. Code is a first-class reference type and may represent a
  repository snapshot, pull-request diff, tests, schemas, configuration, or
  deployment files without relying on an ambient checkout.
- Define the initial bundle layout using workpad-relative artifacts: a bundle
  manifest, content-addressed reference objects, and an optional opaque,
  content-addressed tool-requirements reference. G15 preserves that reference
  and proves it is not executed or installed; G17 owns the capability/tool
  manifest's field semantics, availability model, security review, and
  installation path.
- Define a versioned Review Contract containing the user question, reference
  roles, criterion IDs and descriptions, severity model, required evidence and
  citations, output shape, clarification policy, cycle cap, escalation policy,
  allowed effects, and evaluator plan. Criteria and evaluator identities are
  stable inputs, not prompt text inferred during a Run.
- Define the evaluator substrate interface. A deterministic verifier accepts a
  bundle projection and Review Contract and emits versioned findings,
  evidence references, evaluator identity/version, and a trace identity. The
  interface may later be filled by a model-backed evaluator, but G15 executes
  only local deterministic fixtures and does not add a provider path.
- Define the initial finding lifecycle: `open`, `accepted`, `rejected`,
  `deferred`, `resolved`, and `unanswerable`. A Finding records a stable ID,
  criterion, severity, evidence/reference spans, evaluator identity, trace,
  confidence or disagreement metadata, and its decision state.
- Define verbatim Feedback and Adjudication records. Feedback is an operator
  decision over one or more findings, never a review or revision. Adjudication
  preserves every independent finding and disagreement, records the deciding
  actor and rationale, and cannot silently merge away provenance.
- Define a replayable Trace and two report projections. The machine report is
  canonical JSON; the human report is derived Markdown. Both identify the
  bundle, contract, evaluator versions, findings, feedback decisions, and
  evidence references. Variable fields such as timestamps and usage are
  declared explicitly; reports are never the authority over their source
  artifacts.
- Define a redaction boundary for evaluator inputs and traces. Raw references
  remain local by default; an evaluator receives only the permitted projection
  declared by the contract and redaction policy. G15 proves that redacted
  sentinel values do not enter traces or reports, without claiming universal
  person-name or PII detection.
- Build a small curated evaluation corpus spanning research articles,
  repository/PR material, and tabular data. Cases include positive and
  negative examples plus seeded missing citation, conflicting claim, scope or
  regression omission, schema drift, duplicate finding, disagreement,
  incomplete reference, partial-address, and cycle-limit conditions. G15
  defines the case/evaluator substrate; G16 executes the full loop over it.
- Add mutation-tested deterministic verifier coverage. Removing a verifier
  rule, corrupting a reference digest, or altering a seeded defect must make
  the corresponding evaluation fail; a report's existence is not evidence of
  coverage.
- Keep all G15 artifacts local, workpad-scoped, schema-validated, and
  content-addressed. Stage one includes an explicit contract amendment that
  adds exactly seven named G15 schema resources — Bundle, Contract, Finding,
  Feedback, Adjudication, Trace, and machine Report — raising the packaged
  inventory from eight to fifteen. The amendment updates SHA256SUMS, the
  installed-resource verifier, and G15 vectors while proving the original
  eight hashes and canonical vectors are unchanged. No schema change is made
  silently.

## Out of scope

- The user-facing Review Loop Gig, review/verify/feedback/address/closure
  scheduling, cycle execution, or terminal decision orchestration; those belong
  to G16 and consume these contracts.
- OpenAI, OpenRouter, Codex CLI, Claude CLI, Anthropic, local-model, or other
  provider invocation; network access; provider fallback; model-to-model
  handoff; cancellation; and usage/cost comparison across live providers.
- Proposal-time capability discovery, package installation, activation,
  rollback, or permission changes; G17 owns the approved installation path.
- Target mutation, patches, commits in a user repository, deployment effects,
  recurring Runs, background daemons, or schedule policy.
- A universal PII classifier, URL sanitization policy, or claim that names can
  be detected perfectly. G15 defines the explicit redaction boundary and
  evidence shape; broader privacy detection requires its own reviewed goal.
- Domain-specific reviewer prompts, finance/resume/research product
  templates, or hidden assumptions that a repository is the input.
- Silent changes to approved V14/G13/G14 contracts, packaged schema counts,
  schema hashes, or canonical vectors. The explicit seven-resource G15
  amendment described in scope is the exception and must land before Stage
  two implementation; any additional resource or semantic change stops for a
  new amendment.

## Acceptance criteria

1. A valid Review Bundle materializes article, repository/PR, and CSV
   references as exact-byte content-addressed objects. Every manifest digest,
   size, media type, role, provenance record, and sensitivity/redaction field
   validates; code is represented without an implicit checkout.
2. Reopening a Bundle from local bytes reproduces the same canonical manifest
   and reference digests. Missing objects, changed bytes, symlinked objects,
   stale sizes, duplicate IDs, or digest mismatches fail closed before an
   evaluator runs.
3. A Review Contract validates with stable criterion IDs, explicit severity and
   evidence requirements, reference-role constraints, output shape,
   clarification policy, cycle cap, escalation policy, allowed effects, and a
   versioned evaluator plan. An empty or unbounded cycle cap is rejected.
4. The deterministic evaluator interface emits schema-valid Findings tied to
   real reference bytes and criterion IDs, including evaluator version, trace
   identity, evidence spans, and decision state. Invented citations or
   findings without an evidence reference fail validation.
5. Duplicate findings merge deterministically using the canonical key
   `(criterion_id, severity, sorted evidence-reference digests, normalized
   finding text)`. The merged record unions sorted evaluator, trace, and
   evidence provenance and derives its stable ID from the canonical merged
   bytes. Disagreement is a separate second grouping by
   `(criterion_id, sorted evidence-reference digests)`: distinct merge keys in
   that group are mutually marked with peer IDs and disagreement metadata,
   while identical merge-key duplicates are recorded as agreement. An
   Adjudication records the operator decision and rationale without rewriting
   either original finding.
6. Verbatim Feedback records preserve the supplied text, actor, timestamp,
   finding IDs, and decision (`accepted`, `rejected`, `deferred`, or
   `clarification_requested`). Feedback cannot be misclassified as a review,
   revision, or successful address pass.
7. Canonical machine reports and derived human reports regenerate from the
   committed Bundle, Contract, Findings, Feedback, Adjudication, and Trace.
   Repeated generation is byte-stable except for declared variable fields, and
   reports contain no absolute paths, credentials, or redacted sentinel values.
8. The evaluator pipeline enforces the cost/trust order: schema, shape, digest,
   and citation-existence checks precede deterministic assertions, which
   are the only executable evaluator tier in G15. A model-backed evaluator
   declaration is rejected as unsupported and is never invoked; the later
   model tier is owned by G18. An aggregate score without per-finding evidence
   cannot pass a case.
9. The minimum corpus contains seeded positive, negative, ambiguous,
   incomplete-reference, duplicate-finding, disagreement, partial-address,
   and cycle-limit cases across articles, repositories/PRs, and tabular data.
   G15 executes the deterministic bundle/evidence cases and requires every
   seeded defect in that tier to be found or explicitly marked `unanswerable`.
   Cycle-limit and partial-address cases are materialized and schema-tested
   only for G16; G16 owns executing those loop behaviors. Missing context in a
   G15 deterministic case produces a durable clarification requirement.
10. Mutation tests prove that disabling each named verifier rule or corrupting
    each seeded defect causes the associated case to fail. The suite also proves
    no network, provider, credential, undeclared subprocess, target mutation,
    tool installation, or background activity occurs during G15 evaluation.
11. The explicit Stage-one amendment adds exactly the seven named G15 schema
    resources, updates SHA256SUMS and the installed-resource verifier, and
    preserves all eight pre-existing schema hashes and canonical vectors. A
    missing or altered G15 resource fails the verifier; the total packaged
    inventory is asserted as fifteen rather than passing vacuously.
12. A fresh installed wheel can build and replay the deterministic corpus from
    local fixture bytes without a source checkout or provider credentials. The
    supported matrix, installed verifier, schema/resource checks, and existing
    canonical vectors remain green.
13. Completion evidence includes a requirement-to-test matrix, corpus manifest,
    mutation report, sanitized before/after workpad manifest, replayable sample
    reports, completion audit, and terminal handoff. The evidence names which
    G16 behavior is intentionally not yet implemented.

## Verification and evidence

- Named positive and negative tests for Bundle materialization, exact-byte
  replay, stale/missing/digest-mismatched references, symlink refusal, and
  role/provenance validation.
- Named contract tests for criteria, severity, evidence requirements,
  clarification and cycle bounds, allowed effects, evaluator versioning, and
  unsupported provider/tool execution fields.
- Stage-one contract-amendment evidence names the seven schema resources,
  updated SHA256SUMS, installed-resource enumeration, and unchanged hashes for
  the original eight resources.
- Deterministic evaluator fixtures for research articles, repository/PR
  material, and CSV data, with real citation spans and seeded defects.
- Duplicate/disagreement/adjudication fixtures proving provenance retention;
  verbatim feedback fixtures proving accepted, rejected, deferred, and
  clarification decisions remain distinct.
- Finding transition fixtures prove exactly: `open` may become `accepted`,
  `rejected`, `deferred`, or `unanswerable`; `accepted` may become `resolved`;
  `deferred` may return to `open` or receive a final decision; and
  `rejected`, `unanswerable`, and `resolved` are terminal. The separate
  Feedback decision enum includes `clarification_requested` and does not add a
  Finding lifecycle state.
- Replay tests comparing canonical machine reports and normalized human
  reports, plus redaction-boundary tests with credential and PII sentinels.
- Mutation tests for every evaluator rule and seeded defect, including a
  report-exists-but-evidence-missing negative case.
- Offline process-guard tests proving no network, provider, credential,
  undeclared subprocess, target write, installation, or background activity.
- Fresh-wheel corpus replay and installed verifier on the supported Python and
  platform matrix, with schema/hash/vector preservation checks.
- `docs/development/evidence/phase-3/G15/completion-audit.md`,
  `terminal-handoff.md`, corpus/evaluator manifests, and a
  requirement-to-test matrix.

## Stop boundary

Stop before implementation if a Bundle, Contract, Finding, Feedback,
Adjudication, Trace, Report, redaction state, evaluator transition, or required
schema field is not defined precisely enough to validate and replay. Do not
invent a Review Loop command, provider call, tool installation, PII promise,
target effect, cycle behavior, or feedback state to make a fixture pass.
Stop for an explicit contract amendment whenever existing schemas or authority
rules cannot represent the artifact. For G15, the seven-resource amendment is
required Stage-one work and must be recorded before changing packaged
resources or their hashes; any further resource or semantic expansion stops
the goal for a separate amendment.
