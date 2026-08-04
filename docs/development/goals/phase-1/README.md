# Phase 1 Development Goals

This directory is the canonical implementation graph for GigAI Phase 1, the
offline local spine. The goals translate the approved V14 architecture into
independently verifiable development contracts.

The [V14 implementation plan](../../../architecture/v14-implementation-plan.md)
remains authoritative for product invariants and phase boundaries. When a goal
and the plan appear to disagree, implementation stops until the contradiction
is resolved explicitly; implementation does not weaken the plan by inference.

## Goal graph

```text
G00 -> G01, G02
G01 + G02 -> G03, G07
G03 -> G04 -> G05 -> G06
G03 -> G11
G06 + G07 + G11 -> G08 -> G09 -> G10
```

G01 and G02 may proceed in parallel after G00. G07 requires both. G11 depends
only on G03 and may proceed independently of G06 and G07. G08 is the join
between the persistence path completed by G06, the validation path completed by
G07, and the model-invocation foundation completed by G11.

| Goal | Outcome | Depends on | Initial state |
|---|---|---|---|
| [G00](G00-standalone-contract-baseline.md) | Standalone contract baseline | — | Ready |
| [G01](G01-canonical-serialization.md) | Canonical bytes and exact-byte digests | G00 | Blocked |
| [G02](G02-minimal-cli-and-scenario-harness.md) | Minimal CLI and installed black-box harness | G00 | Blocked |
| [G03](G03-setup-configuration-diagnostics.md) | Setup, configuration, offline adapters, and diagnostics | G01, G02 | Blocked |
| [G04](G04-target-binding.md) | Idempotent target binding with delta proof | G03 | Blocked |
| [G05](G05-workpad-private-git.md) | Workpad resolution and private Git journal | G04 | Blocked |
| [G06](G06-journal-locking-recovery.md) | Atomic journal ordering and recovery | G05 | Blocked |
| [G07](G07-contract-validators.md) | Complete proposal and Goal Graph validators | G01, G02 | Blocked |
| [G08](G08-offline-create-lifecycle.md) | Persisted offline proposal lifecycle | G06, G07, G11 | Blocked |
| [G09](G09-index-and-read-commands.md) | Rebuildable index and offline read surface | G08 | Blocked |
| [G10](G10-phase-1-completion-audit.md) | Cross-platform Phase 1 completion audit | G09 | Blocked |
| [G11](G11-model-invocation-foundation.md) | Model port, factory, and initial API adapters | G03 | Ready |

“Ready” and “Blocked” describe the graph before implementation begins. They are
not live status fields and should not be edited to simulate a tracker. Public
issue or pull-request state may track execution; committed goal documents state
the durable contract.

## Completion rule

Every goal stops only after it has:

- passed its unit, contract, and installed black-box assertions;
- captured applicable target and workpad before/after manifests;
- proved applicable idempotency, corrupt-state, interruption, and offline
  behavior;
- written a requirement-to-evidence completion audit; and
- written a durable terminal handoff before downstream work begins.

A dependency-ready goal may begin only from the committed completion evidence
of every dependency. A later goal must not opportunistically alter a serialized
contract, golden vector, or completed goal contract. Before the first public
release, serialized-contract changes follow the approved pre-release policy;
afterward they follow ADR 0003's immutable/additive regime.

## Evidence layout

Durable public evidence follows the convention in the [Phase 1 evidence
README](../../evidence/phase-1/README.md). Each completed goal contributes:

```text
docs/development/evidence/phase-1/GNN/
  completion-audit.md
  terminal-handoff.md
```

Small stable evidence artifacts may accompany those documents. Raw logs,
caches, credentials, session output, and workstation-specific paths do not.
The goal document links its evidence only after the goal actually completes.

## Git history policy

Repository initialization happens only after this graph is materialized. The
initial commit is the completed G00 baseline, including its audit and terminal
handoff. Each later goal lands as its own reviewable change set and does not mix
work from another goal. The intended terminal commit subjects are:

```text
goal(G00): establish standalone contract baseline
goal(G01): implement canonical serialization
...
goal(G10): complete phase 1 audit
```

If a goal cannot remain reviewable as one change set, revise and split the goal
before implementation rather than hiding unrelated work in one commit.
