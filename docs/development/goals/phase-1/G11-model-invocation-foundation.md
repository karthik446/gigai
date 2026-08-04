# G11 — Model Invocation Foundation

- Status: Approved
- Depends on: G03
- Unblocks: G08

## Outcome

Provide one transport-neutral model-invocation boundary, configured model-target
resolution, and a factory that selects adapters without exposing provider or
transport choices to GigAI domain code.

## In scope

- Structured invocation requests and results carrying the resolved target, role,
  capability requirements, normalized output/status, resolved model identity,
  and raw plus normalized usage.
- One model port. API and local-process differences remain below that port;
  capabilities are declared data, not transport-specific method signatures.
- User configuration evolution for endpoint credential references, endpoint
  settings, and model-target inference/capability policy. A recognized v1
  configuration migrates explicitly, atomically, and idempotently; corrupt,
  ambiguous, or unknown versions fail closed.
- A factory as the only production selection point for concrete adapters.
  Domain services, including diagnostics, depend on the factory or port rather
  than importing or constructing an adapter directly.
- Migration of the deterministic fixture adapter through that factory, plus
  OpenAI API and OpenRouter API adapters sharing an internal HTTP base.
- Forward evolution of the G03 diagnostic implementation only. G03's completed
  contract and evidence remain historical evidence for the G03 commit; G11 owns
  the replacement wiring and its proof.
- Explicit local `doctor --live --model-target <name>` checks for configured
  OpenAI Platform API and OpenRouter targets, bounded by the selected target's
  configured budget policy.
- Raw credential resolution only inside the runtime adapter path. Serialized
  configuration, workpads, manifests, logs, fixtures, and share-safe output
  contain references or redacted metadata only.
- Static ownership enforcement proving that only adapter wiring/factory code
  imports concrete adapter implementations.

## Out of scope

- Anthropic API, Codex CLI, and Claude CLI adapters or claims that their
  compatibility is verified.
- Planner, critic, adjudicator, research, Gig creation, Run execution, target
  mutation, or provider fallback policy.
- A second transport-specific model port or caller-selected adapter class.
- New packaged schemas, changes to the eight existing schema resources, or
  changes to canonical golden vectors.
- Network access in CI, installed offline scenarios, G03 diagnostics, or G08's
  offline lifecycle.

## Acceptance criteria

1. One domain-facing port accepts structured resolved invocation input and
   returns structured normalized output, identity, status, and usage without a
   caller selecting API versus local-process transport.
2. The factory resolves `configuration -> model target -> endpoint -> adapter`.
   Outside approved adapter wiring, production modules neither import a concrete
   adapter nor construct one directly.
3. The configuration v1-to-v2 migration is explicit, atomic, idempotent, and
   preserves valid configured state; malformed, ambiguous, and unknown state
   fails before mutation. Credential values never enter persisted state.
4. The deterministic adapter conforms to the same port and powers offline
   diagnostics through factory resolution.
5. OpenAI API and OpenRouter API conform to the same port, retain raw and
   normalized usage distinctly, and report unavailable cost without rendering it
   as zero.
6. `doctor --live --model-target <name>` is an explicit local action. It uses a
   configured credential reference and target budget policy, emits only
   redacted/share-safe evidence, and is absent from CI and offline scenarios.
7. Operator-run live proofs pass for one configured small/cheap OpenAI Platform
   API target and one OpenRouter target. Those proofs certify only those target
   identities and adapter versions.
8. Static ownership, secret-canary, network-denial, installed-wheel, and
   configuration-migration tests pass. The installed schema verifier still
   enumerates exactly the existing eight resources.

## Verification and evidence

- Port/factory conformance and negative ownership tests, including an injected
  direct concrete-adapter import.
- v1-to-v2 configuration migration, interruption, rerun, malformed-state, and
  secret-canary scenarios.
- Installed offline deterministic-adapter and network-denial scenarios.
- Local, opt-in, redacted OpenAI Platform API and OpenRouter live-proof records
  kept outside CI and the Phase 1 scenario suite.
- Requirement-to-evidence completion audit and terminal handoff.

## Stop boundary

Stop after the one port, factory, deterministic adapter, OpenAI API, and
OpenRouter API are independently evidenced. Defer Anthropic API, Codex CLI,
and Claude CLI to their own adapter goals. Do not create a Gig, start a Run, or
claim the five-adapter matrix is complete.
