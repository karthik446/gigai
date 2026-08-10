# S22-01 Proposal-Interview Contract Amendment

- Status: Accepted additive amendment for G22; no runtime implementation
- Supersedes: the S22-01 limitation that durable question/answer records
  require a future amendment
- Preserves: all existing 21 schema resources, hashes, and canonical vectors

## Decision

Add `proposal-interview.schema.json` as the twenty-second packaged resource.
It defines the durable, schema-validated interview snapshot that G22 may
persist and replay. The resource covers the S22-01 protocol without shipping
the HTMX server or changing the proposal lifecycle:

- session identity and project/Gig/proposal linkage;
- explicit snapshot revision and parent-revision linkage;
- the accepted question state machine and bounded clarification round;
- typed question definitions and typed answer values;
- exact reference bytes/digests and explicit selected/excluded decisions;
- privacy, capability, and effect boundary choices;
- ordered redacted event identities; and
- operator approval evidence, when the session is approved.

Raw session tokens and event payloads remain outside the record; only their
digests and safe typed values may be persisted. Semantic checks that require
cross-field knowledge — for example, that a selected reference exists in the
reference decisions or that an answer option belongs to its question — remain
G22 validator responsibilities. The schema rejects unknown fields and rejects
wrong primitive types, states, effects, approval shapes, and answer shapes.

## Amendment invariants

1. The prior 21 schema files are byte-identical. Their `SHA256SUMS` entries
   remain unchanged.
2. The new resource is additive and versioned at schema `1.0`; it does not
   alter `gig-proposal`, journal, Goal Graph, Run, invocation, exchange, or
   capability meanings.
3. A non-approved state cannot carry an approval object. A blocked state must
   carry a terminal reason. Pending states may have an empty reference
   selection while the operator is answering the references question, but an
   approved state must carry at least one selected reference. The only G22
   effects are `read_local` and `write_workpad`.
4. Approval is represented as an operator decision and proposal digest. The
   schema provides no model-approval or target-mutation state.
5. The resource is a contract for G22's durable snapshot; S22-01 remains a
   protocol/design prerequisite and does not claim that `gigai create` is
   implemented.

## Verification obligations

- The source schema is valid Draft 2020-12 and is registered by the packaged
  validator.
- A valid approved snapshot and valid blocked snapshot pass.
- Wrong answer primitives, approval on a pending state, a disallowed effect,
  missing blocked reason, unknown fields, and invalid session/reference IDs
  fail closed.
- The installed schema verifier reports exactly 22 resources.
- The prior 21 resource bytes and hashes remain unchanged.
