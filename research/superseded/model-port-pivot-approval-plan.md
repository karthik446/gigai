# Model port pivot — approval plan

- Status: Approved
- Date: 2026-08-03
- Scope: planning only; no implementation or contract change is authorized by
  this document.

## Decision summary

GigAI will introduce one transport-neutral model-invocation port. Domain code
will resolve a configured model target through a factory and use the resulting
port; it will not select a provider, inspect a transport, or construct a
concrete adapter.

The first implementation slice includes the deterministic fixture adapter,
OpenAI API, and OpenRouter API. Anthropic API, Codex CLI, and Claude CLI remain
planned v1 adapters, but are deferred to separately evidenced goals. The port
is designed for all five; only the first three are implemented by this pivot.

## Proposed G11: Model invocation foundation

Add G11 without renumbering completed or approved G06–G10 contracts. G11
depends on G03. G06 and G07 continue independently, and G08 becomes a join:

```text
G06 + G07 + G11 -> G08
```

G11 owns:

- structured invocation request and result types, including the resolved target,
  role, capability requirements, normalized output/status, resolved model
  identity, and raw plus normalized usage;
- declared capability data rather than transport-specific method signatures;
- model-target resolution and a factory as the only concrete-adapter selection
  point;
- a strict configuration evolution for endpoint credential references,
  endpoint settings, and target inference/capability policy. The versioned,
  exact configuration parser requires an explicit migration and migration
  tests; it is not silently extended in place. This is TOML configuration, not
  a new packaged serialized contract: G11 adds no schema or canonical vector;
- the deterministic adapter's migration behind the factory, plus OpenAI and
  OpenRouter adapters using an internal HTTP implementation base; and
- a static ownership test: only adapter wiring/factory code may import concrete
  adapter implementations. Diagnostics and other domain services use the port
  or factory only.

Credential values resolve only inside the runtime adapter path. Configuration,
domain objects, workpads, manifests, logs, fixtures, and share-safe diagnostics
contain references or redacted metadata, never values.

G11 stops before Anthropic, Codex CLI, Claude CLI, planner/critic/adjudicator
orchestration, Gig creation, Runs, target mutation, and any general provider
matrix claim.

## Live evidence

OpenAI Platform API and OpenRouter live checks run only through an explicit
local command such as `gigai doctor --live --model-target <name>`. They require
an operator-selected target and an explicit maximum output-token limit; this
experimental evaluation policy has no GigAI-enforced USD ceiling.

They are excluded from CI, installed offline scenarios, G03 diagnostics, and
G08's offline lifecycle. Each result records only redacted, share-safe identity,
capability, usage, cost-status, and outcome evidence. It does not certify the
remaining three v1 adapters.

The V14 addendum must name this as limited adapter-local evidence. It must not
refer to the nonexistent “Gate D” at the current plan line 942 or imply the
five-adapter matrix is complete.

## Pre-release contract posture

Until GigAI's deliberately declared first public release, schemas and canonical
golden vectors are editable source artifacts. A pre-release contract change
updates the affected schema/vector bytes, tests, and `SHA256SUMS` together;
versioned identifiers, exact-version readers, closed schemas, package-resource
delivery, and installed verification remain in place.

At that first public release, ADR 0003's immutable/additive versioning regime
becomes applicable. The trigger is the release event, not an undefined “v2”
label or an incidental development tag. This relaxation does not weaken
canonical-byte identity, immutable approved Gig versions, or journal authority.

The addendum will amend ADR 0003's applicability and reconcile the prospective
freeze language in contributor guidance and future-goal contracts. Completed
G00/G01 evidence remains historical evidence for its commit; it is not
rewritten to claim future source bytes are unchanged.

## Identifier prefixes

Commands may accept an unambiguous, type-qualified ID prefix in every mode,
including noninteractive use. A prefix must include its canonical kind and at
least six UUID hexadecimal characters, for example `gp_a1b2c3`.

Ambiguous prefixes fail closed and list candidates. Full canonical IDs remain
the only serialized identity in JSON output, journals, manifests, and sealed
bytes. Automation should retain the full ID returned by `--json`; short forms
are an input convenience, not a stable automation identifier.

The approval addendum states this grammar. The binding command sheet changes
with the implementing goal and parser tests, rather than as a detached
documentation-only update.

## Documentation amendment

This documentation-only change set:

1. add the G11 contract and update the Phase 1 graph so G08 depends on G11;
2. add a V14 addendum covering the model boundary, G11 scope, limited live
   evidence, the deferred provider matrix, and the pre-release contract policy;
3. amend ADR 0003 with an applicability clause; and
4. update the command sheet's planned identifier grammar.

The same documentation change set must also:

5. state that G11 adds no packaged schema resource or canonical vector, leaving
   G07's exactly-eight schema enumeration and the installed schema verifier
   unchanged; and
6. clarify that G09's offline adapter-health check resolves the deterministic
   adapter through the factory, while `doctor --live` is supplied by G11 and is
   not a G09 live-adapter feature. G10 must audit all Phase 1 goals including
   G11, and distinguish its network-denied scenario suite from G11's separate,
   local-only, redacted live evidence.

Implementation begins only after those contracts are reviewed and committed.

## Retention

This is an approved planning record, not a durable implementation contract. It
remains under `docs/development/plans/` while G11 is in progress. When G11
completes, move it with G11's durable evidence so the active development root
contains only current contracts and evidence.
