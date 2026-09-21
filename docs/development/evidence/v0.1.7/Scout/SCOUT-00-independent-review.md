# SCOUT-00 — Independent contract review

> Documentation rename only: `SCOUT-*` refers to the same historical `JSL-*`
> delivery work. Findings, verdicts, test commands and measured results below
> retain their original scope; they do not review or accept the new
> [user-owned Gig amendment](SCOUT-00-user-owned-gig-amendment.md).
> Historical line-number labels below describe the reviewed snapshot; links
> into renamed/expanded documents now target the corresponding sections.

**Review date:** 2026-09-07  
**Scope:** Read-only review of the SCOUT-00 amendments against the approved Scout
roadmap and the current G41/G42/G43.2/G44/G45/G46.1 contracts and callers.
No provider, Gig, configuration, or `.gigai` state was used or changed.

## Verdict

**Not yet ready to close SCOUT-00.** The amendments make the right product-level
separation between a Graph Descriptor selector and a Goal Graph UUID, and between
external-agent recording and GigAI-managed provider execution. Four contract
gaps below still leave an implementation worker able to choose incompatible
authority or storage behavior; each has a narrow correction.

## Confirmed coherent decisions

- **Selector versus UUID is explicitly and correctly separated.** The amendment
  says the Descriptor `graph_id` is a semantic selector and is not the Goal
  Graph UUID ([amendment:17-22](SCOUT-00-contract-amendments.md#1-authority-and-compatibility)). This
  matches G43.2, whose Descriptor selector is a slug ([G43.2:34-55](../../../v0.1.7/goals/G43.2-multi-graph-gig-versions-and-graph-selected-run-plans.md#L34-L55)), while the current Goal Graph schema imports the UUID-shaped
  common `graph_id` ([goal graph schema:21-25](../../../../../src/gigai/schemas/goal-graph.schema.json#L21-L25); [common schema:26-29](../../../../../src/gigai/schemas/common.schema.json#L26-L29)). The proposed seal of selector, Goal Graph UUID, Graph Set digest, and Gig version is the required non-ambiguous bridge.
- **External recording is not counterfeit managed execution.** The amendment
  preserves direct confirmation for Gig approval and provider execution, limits
  the recording path to private-workpad effects, and calls external tool/cost
  activity unobserved or reported rather than fully accounted
  ([amendment:146-177](SCOUT-00-contract-amendments.md#4-external-agent-execution-versus-managed-execution)). That is
  consistent with the roadmap's required distinction ([roadmap:57-67](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md#product-boundary)) and does not waive G46.1's provider boundary for private inputs
  ([G46.1:65-90](../../../v0.1.7/goals/G46.1-tailor-resume-for-job.md#L65-L90)).
- **Dynamic inputs have the right authority direction.** Selected defaults are
  sealed by revision; changed required inputs create a successor Plan/Run rather
  than mutate starting inputs ([amendment:123-130](SCOUT-00-contract-amendments.md#3-private-data-and-progressive-preferences)). This preserves the G43.2 rule that a prior Run cannot become an
  input without an explicit digest-pinned binding ([G43.2:111-117](../../../v0.1.7/goals/G43.2-multi-graph-gig-versions-and-graph-selected-run-plans.md#L111-L117)).

## Blocking findings

### B1 — The external recording Plan/Run family is not frozen enough to implement safely

**Severity: blocking (authority and compatibility).** The amendment requires a
"dedicated sealed Plan contract" and lists start/checkpoint/submit semantics,
but does not define its strict schema identity, identity projection, authority
chain, terminal-state vocabulary, journal transitions, or the exact evidence
that proves an `agent_explicit` invocation is bound to the submitted artifacts
([amendment:146-170](SCOUT-00-contract-amendments.md#4-external-agent-execution-versus-managed-execution)). Deferring even
the command spelling to SCOUT-04 also leaves SCOUT-00 without the requested command
map ([amendment:163-164](SCOUT-00-contract-amendments.md#4-external-agent-execution-versus-managed-execution); [roadmap:166-175](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md#development-goals)).

This cannot safely reuse the current G43 Plan/Run implementation: the existing
Run Plan schema requires G43 review phases, provider participants, discovery
snapshots, and capability IDs ([run-plan schema:7-33](../../../../../src/gigai/schemas/run-plan.schema.json#L7-L33)), while the present `run` path requires
direct `--confirm` for every Plan handoff before allocating a Run
([run.py:94-171](../../../../../src/gigai/run.py#L94-L171); [cli.py:2464-2577](../../../../../src/gigai/cli.py#L2464-L2577)).

**Minimal correction:** Add a separately versioned `external-agent-recording-plan`
and recording Run/receipt schema family before SCOUT-04 starts. Define its
deterministic ID projection; Graph Set/Descriptor selector/Goal Graph UUID and
Gig-version bindings; invocation ID and agent actor; selected-input/output
digest references; checkpoint and terminal transitions; idempotency key scope;
and an effects enum constrained to `write_workpad`. Its semantic validator must
reject provider targets, provider invocation records, network/local-capability
execution, `direct_cli_confirm`, and any transition whose declared result is a
GigAI-managed invocation. Then name the CLI namespace and JSON error codes here,
even if SCOUT-04 owns the implementation and help text.

### B2 — The generic private-record layout has no declared compatibility bridge to G45 records

**Severity: blocking (privacy, input binding, and migration).** The amendment
introduces a new generic `records/<record-id>/revisions/...` authority layout
covering references, sources, job snapshots, documents, and application events
([amendment:90-114](SCOUT-00-contract-amendments.md#3-private-data-and-progressive-preferences)). G45 instead
defines immutable private reference and Run-input records at exact,
digest-pinned `references/ref_...` and `run-inputs/input_...` paths
([G45:93-144](../../../v0.1.7/goals/G45-private-references-and-pasted-run-inputs.md#L93-L144)), and its Plan contract requires callers to name those IDs
([G45:173-188](../../../v0.1.7/goals/G45-private-references-and-pasted-run-inputs.md#L173-L188)). The amendment neither replaces those contracts nor specifies a
read-only adapter or migration, so a worker could create two authoritative
stores for the same résumé/posting or make a Plan bind one while context shows
the other.

**Minimal correction:** State one of two choices: (a) G45 records remain the
canonical v1 family and Scout records reference their immutable IDs/digests, or
(b) define a versioned Scout record schema plus an atomic, idempotent G45-to-Scout
migration and compatibility readers. In either choice, define the canonical
reference and Run-input IDs accepted by Plan sealing, forbid duplicate content
authority, and add a fixture proving a prior G45 input remains readable and
cannot be silently substituted.

### B3 — Multi-package named initialization lacks a durable instance-binding and migration contract

**Severity: blocking (package/registry compatibility).** The amendment rightly
replaces the one-package rule and describes a private binding keyed by
`(project_id, canonical_template_id, instance_name)` ([amendment:55-81](SCOUT-00-contract-amendments.md#2-named-initialization-and-package-ownership)), but it leaves the binding's schema,
uniqueness constraints, active-Gig interaction, transaction/recovery protocol,
and upgrade path unspecified. Those details are material: G42 expressly limits
projects to one installed catalog package ([G42:66-73](../../../v0.1.7/goals/G42-built-in-gig-catalog.md#L66-L73)); the current package code refuses multiple
roots before binding mutation ([package.py:821-831](../../../../../src/gigai/package.py#L821-L831)) and otherwise returns only the first inspected package
([package.py:327-367](../../../../../src/gigai/package.py#L327-L367)); and the
registry/workpad model has one active workpad per project
([registry.py:32-45](../../../../../src/gigai/registry.py#L32-L45)).

**Minimal correction:** Specify a versioned private registry migration with a
`template_instances` record: canonical template ID, package digest, project ID,
default-only instance name, Gig ID, proposal/approved-version reference, and
customization lineage. Require unique `(project, template, instance)` and an
explicit selected-package argument whenever package ambiguity affects an
operation; define whether named init changes the existing active Gig (default:
no). Require preflight validation of every sibling, recoverable publication of
package + binding + workpad changes, and post-recovery reconciliation; retain
the existing G41 preservation/refusal rules ([G41:323-355](../../../v0.1.7/goals/G41-project-local-gigai-package-boundary.md#L323-L355)).

### B4 — Application-event request evidence is only labelled, not validation-bound

**Severity: blocking (authority).** The amendment allows an agent to record an
application event from an explicitly selected conversation/request reference,
but labels it `agent_reported_user_request` and says it is not proof or direct
CLI consent ([amendment:230-235](SCOUT-00-contract-amendments.md#6-application-events)). It
does not define a strict event/evidence schema that binds the selected record's
ID, digest, scope, selector, and the exact event payload. This leaves a route
for an agent-generated label or free-text declaration to be treated as adequate
request evidence, contrary to the roadmap's user-recorded/no-auto-applied
requirement ([roadmap:295-309](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md#scout-08--application-tailoring-and-checks)).

**Minimal correction:** Define a strict append-only application-event schema
whose request-evidence field is either (1) a sealed, explicitly selected private
conversation/request record reference plus digest and selection actor, marked
`agent_reported_user_request`, or (2) an explicit direct event command receipt.
Reject bare agent assertion, output completion, check results, or a source URL;
keep the label as provenance rather than consent. The acceptance fixture must
prove that a missing/mismatched evidence record leaves status unchanged.

## Non-blocking acceptance addition

**M1 — Test the successor-Run question path.** The dynamic-input rule is sound,
but A03 only names saved/task-only preferences and prior input immutability
([amendment:281-284](SCOUT-00-contract-amendments.md#8-caller-ownership-and-acceptance-matrix)). Add one
acceptance case: a checkpoint records a required question, the user saves an
answer, the original Run remains inspectable/blocked, a successor Plan seals the
new revision, and the agent receives a typed next action without ambient-record
disclosure. This implements the roadmap's missing-experience continuity case
([roadmap:436-446](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md#review-verification-and-evidence)).

## Accepted limitations (not findings)

- `external_agent_recording` records declared work and validates stored outputs;
  it neither observes nor authenticates external tools, model identity, cost,
  source freshness, or truth ([amendment:172-177](SCOUT-00-contract-amendments.md#4-external-agent-execution-versus-managed-execution)). It is therefore not a managed provider Run and cannot redeem provider consent.
- The shipped workflow accepts bounded UTF-8 Markdown/text and JSON; it does
  not fetch URLs or parse PDF/DOCX/OCR/binary inputs ([amendment:179-182](SCOUT-00-contract-amendments.md#4-external-agent-execution-versus-managed-execution); [roadmap:456-464](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md#review-verification-and-evidence)).
- Named initialization supports only the default instance in v0.1.7
  ([amendment:74-81](SCOUT-00-contract-amendments.md#2-named-initialization-and-package-ownership)); more instances are
  explicitly deferred, not silently synthesized.
- No application submission, outreach, email, scheduling, unattended discovery,
  background agent, or hidden-reasoning capture is authorized by this release
  ([roadmap:456-471](../../../v0.1.7/roadmaps/v0.1.7-scout-roadmap.md#review-verification-and-evidence)).

## Review evidence

Inspected the amendment and approved Scout roadmap; G41, G42, G43.2, G44, G45, and
G46.1 contracts; and current `package.py`, `registry.py`, `invocation.py`,
`run_plan.py`, `run.py`, `workpad.py`, CLI, and strict schemas. No tests were
run: this was a document/authority review and concurrent implementation testing
was explicitly out of scope.
