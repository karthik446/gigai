# G17 — Proposal-Time Capability Inspection and Installation Review

- Status: Approved / Ready
- Depends on: G15 (complete and merged); consumes the proposal, Review Bundle,
  journal, and workpad authority surfaces from G08/G09
- Unblocks: S18 provider/tool design spikes and the later G18 provider
  implementation

## Outcome

Implement a provider-neutral capability inspection and approved installation
review boundary for proposals. A proposal must be able to explain which
capabilities a Goal requests, whether each is available, what it would require,
which alternatives exist, what security checks apply, and what the user must
explicitly choose. The proposal records a requirement; it never treats a
requirement as permission to execute or install anything.

G17 also defines one narrow, auditable installation path for a user-approved,
content-addressed local artifact. Installation is isolated per Gig, records
before/after state and permission review, and refuses or rolls back on any
drift. It does not become a package manager, a provider adapter, or a general
shell runner. Remote package resolution and live provider/tool execution remain
later work.

## In scope

- Amend the serialized contract before implementation with exactly two additive
  schema resources: `capability-manifest.schema.json` for proposal-time
  requirements, inspection results, alternatives, and explicit options; and
  `capability-installation.schema.json` for approval, pinned local source,
  before/after manifests, outcome, and rollback/refusal evidence. The packaged
  inventory rises from seventeen to nineteen resources. The authoritative
  baseline is `src/gigai/schemas/SHA256SUMS`; all existing seventeen hashes and
  canonical vectors must remain unchanged.
- Define a capability record with a stable ID, requesting Goal IDs, capability
  kind, requested version/source constraints, declared effects, permissions,
  credential and network requirements, availability state, compatibility result,
  security-review state, and explicit alternatives. The record describes a
  requirement even when the capability is missing or not yet implemented.
- Store the canonical capability manifest at a workpad-relative
  `manifests/capabilities/<manifest-id>.json` path and reference it from the
  G15 Review Bundle's opaque `tool_requirements` artifact reference. Store
  installation records under `manifests/installations/`; the isolated v1 tool
  root is `tools/<capability-id>/` inside the Gig workpad. These paths are
  relative, symlink-free, and excluded from share-safe output when they reveal
  host-specific details.
- Extend the semantic workpad allowlist only for the named `tools/` root and
  preserve the existing private-Git/journal authority rules. No capability
  artifact may add an unbounded top-level directory or escape the Gig workpad.
- Define inspection states at minimum: `available`, `missing`, `installable`,
  `incompatible`, `credential_missing`, and `security_rejected`. Inspection
  may read local package metadata, executable paths, file metadata, and pinned
  local artifact bytes; it must not execute a capability, contact a provider,
  resolve a remote package, read credential values, or mutate a target.
- Define explicit proposal options with stable labels and deterministic order:
  use an available capability, install a pinned local artifact, choose a
  declared alternative, or continue without the capability. Every option has a
  pending user decision until an operator records approval or refusal; no
  default or automatic fallback may silently select one.
- Define the approved installation path for a pinned local artifact only. The
  installer verifies the exact source digest and declared package/executable
  identity, writes into an isolated per-Gig workpad tool root, and records the
  resulting artifact without executing it. The v1 fixture backend may copy or
  unpack a local artifact; remote download, package-manager invocation, and
  arbitrary installer commands are not part of this goal.
- Record an installation review containing the approving actor, selected option,
  source and version pins, requested effects and permissions, security checks,
  before/after manifests, outcome, and rollback/refusal reason. Installation is
  idempotent for the same pinned bytes and cannot silently replace divergent
  bytes.
- Keep tool provenance per Gig. A capability installed or approved for one Gig
  must not become a global default or appear as a resolved Run tool for another
  Gig without a new explicit selection and evidence record. G17 does not alter
  the existing `run-manifest` resolved-tool schema.
- Enforce the boundary with deterministic fixtures for installed, missing,
  installable, incompatible, credential-missing, security-rejected, source
  digest drift, approval refusal, installer failure, and rollback cases.
  Preserve absolute-path and credential redaction in share-safe evidence.

## Out of scope

- OpenAI, OpenRouter, Codex CLI, Claude CLI, Anthropic, local-model, or any
  provider/tool invocation; network discovery or provider requests; model
  handoff, fallback, usage comparison, or live evaluator execution. G18 owns
  provider and live execution effects.
- Remote package downloads, package-index resolution, Homebrew/apt/pip/uv
  package-manager execution, arbitrary shell strings, arbitrary subprocesses,
  installers supplied by a proposal, or executing an installed artifact. The
  local pinned-artifact fixture is the only installation backend in G17.
- Credential acquisition, authentication, secret inspection, or permission
  escalation. A missing credential is represented as a fail-closed state and a
  user-visible requirement, never as a reason to probe the environment.
- Target mutation, repository commits, deployment, schedule/daemon behavior,
  background workers, or recurring Runs. G19 and G21 own those effects.
- The full deliberative Phase 2 `create` command or a new public proposal
  command. G17 supplies durable artifacts and an inspection/installation
  substrate for future creation surfaces; it must not pretend to complete
  creation by inference.
- Universal PII detection, URL sanitization, or a claim that capability
  inspection identifies every sensitive value. G15's explicit redaction
  boundary remains authoritative.
- Silent changes to existing schemas, vectors, authority rules, Run/Goal
  transitions, or accepted defaults. The two named additive schemas are the
  only contract change permitted by this goal; any further artifact or state
  requires an explicit amendment.

## Acceptance criteria

1. Before runtime implementation, a recorded amendment adds exactly
   `capability-manifest.schema.json` and `capability-installation.schema.json`,
   updates SHA256SUMS and the installed verifier, asserts nineteen packaged
   resources, and proves all seventeen prior resource hashes and canonical
   vectors are unchanged.
2. A capability manifest is canonical, schema-valid, content-addressed, and
   referenced by the G15 Bundle's `tool_requirements` artifact ref. It is linked
   to its Gig and requesting Goal IDs and preserves requested capability
   kind, version/source constraints, effects, permissions, credential/network
   requirements, availability, compatibility, security-review state, and
   alternatives without absolute paths or secret values in share-safe output.
3. Proposal-time inspection deterministically distinguishes available,
   missing, installable, incompatible, credential-missing, and
   security-rejected fixtures. It reads only permitted local metadata and
   pinned bytes; it never executes a capability, contacts a provider, resolves
   a remote package, reads credential values, or mutates the target.
4. Every inspected capability produces explicit, stably ordered options. The
   proposal remains pending until an operator records approval or refusal;
   unavailable or rejected capabilities cannot be silently replaced by a
   fallback or silently omitted from the requested Goal.
5. An approved local installation requires an exact source digest, package or
   executable identity, version/source pin, selected option, approving actor,
   and passed security/permission checks. Missing approval, digest drift,
   symlink/path escape, incompatible identity, or a rejected security check
   fails closed before any installation write.
6. The local installer writes only to the isolated per-Gig
   `tools/<capability-id>/` root, emits a schema-valid installation record under
   `manifests/installations/`, and records before/after manifests that
   include exact bytes, permissions, source identity, and provenance. Repeating
   the same approved installation is idempotent; divergent bytes are refused.
7. Any failed or interrupted installation either leaves the tool root unchanged
   or restores the exact before manifest. The installation record distinguishes
   `installed`, `already_available`, `refused`, `failed`, and `rolled_back`, and
   no failed attempt is reported as available or authorized.
8. A capability installed for Gig A cannot be selected as a resolved tool for
   Gig B without a new explicit option decision and provenance record. G17 does
   not mutate or reinterpret existing Run authority, `resolved_tools`, or
   provider identity fields.
9. Negative fixtures cover missing and stale source bytes, incompatible
   versions, missing credentials, security rejection, approval refusal,
   permission mismatch, unsafe paths/symlinks, installer failure, interruption,
   rollback, duplicate capability IDs, invented alternatives, and malformed
   manifests. Each rejection has a deterministic finding/error code.
10. An adversarial proposal/installer fixture attempts network access,
    credential-value access, capability execution, arbitrary subprocess/shell
    use, target writes, global installation, and silent fallback. Static import
    checks plus the existing offline process harness prove those effects are
    absent or recorded as blocked, with no change outside the isolated tool
    root and workpad evidence.
11. Mutation tests catch removal of the source-digest check, approval gate,
    path containment check, before/after comparison, rollback path, and
    per-Gig provenance check. A report's existence is not evidence of coverage.
12. A fresh installed wheel can inspect all capability states and replay the
    approved local-installation fixture from local bytes without a source
    checkout, provider credentials, network access, or a target repository.
    The nineteen-resource verifier and existing vectors remain green.
13. Completion evidence includes the amended schemas and hashes, capability
    fixture/corpus manifest, option and installation state table,
    requirement-to-test matrix, mutation report, sanitized before/after
    manifests, refusal/rollback records, completion audit, and terminal
    handoff. The audit explicitly names that G18 live provider/tool execution,
    G19 target effects, and Phase 2 deliberative creation remain absent.

## Verification and evidence

- Contract tests prove the two-resource amendment, exact nineteen-resource
  inventory, unchanged seventeen-resource baseline, canonical vectors, and
  schema-valid positive/negative manifest and installation records.
- Inspector fixtures cover each availability/compatibility/security state and
  assert deterministic option ordering and no capability execution.
- Local package metadata, executable discovery, and pinned-artifact fixtures
  use real temporary files/metadata; no fixture relies on a shell command or a
  network response.
- Installation tests cover approval, exact-byte/source verification, isolated
  per-Gig roots, idempotent repeat, permission review, refusal, interruption,
  rollback, and cross-Gig provenance isolation.
- Negative tests name every rejection class in criterion 9 and verify that
  malformed or tampered records fail before any later state is considered.
- Offline process guards and static import-graph checks prove no network,
  credential, provider, arbitrary subprocess, shell, target, or global-install
  effect occurs.
- Mutation tests disable each load-bearing guard and require a corresponding
  fixture failure.
- A fresh-wheel verifier runs inspection and the local installation fixture
  without importing test modules or requiring a source checkout.
- Evidence lives under `docs/development/evidence/phase-3/G17/` and includes a
  completion audit, terminal handoff, corpus/option manifests, refusal and
  rollback records, and the requirement-to-test matrix.

## Stop boundary

Stop before implementation if capability identity, availability states, option
decisions, permission/security review, installation provenance, rollback
semantics, or a required schema field is not precise enough to validate and
replay. Do not invent a provider call, remote package policy, credential probe,
shell command, public creation command, target effect, fallback, or execution
path to make a fixture pass.

Stop for an explicit amendment if the two new schemas cannot represent the
manifest or installation record, if an existing Bundle/Run authority rule must
change, if a new lifecycle transition is required, or if a required artifact
would otherwise be hidden inside a Bundle or Report without durable parentage.
The two-resource amendment must land before implementation and must not change
the meaning of the existing seventeen resources.
