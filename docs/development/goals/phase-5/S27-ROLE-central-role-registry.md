# S27-ROLE — Central Role Registry Spike

- Status: Proposed for review; not activated
- Type: Research and contract-design spike; no runtime implementation
- Depends on: G18 model invocation contracts, G15/G16 reference-role contracts,
  Goal Graph executor contracts, and the G28 readiness goal
- Unblocks: G28 v0.1.5 readiness implementation

## Outcome

S27-ROLE defines a central, versioned, namespaced role registry so model
invocation roles, reference roles, and Goal Graph executor roles cannot be
confused or silently invented as arbitrary strings.

## Decisions required

The spike must produce an accepted decision record covering:

1. the namespace model for `model_invocation`, `reference`, and `executor`;
2. the structured role-reference shape, including namespace, ID, and version;
3. the built-in role catalog and each role's purpose, required capabilities,
   allowed callers, and evidence requirements;
4. the extension mechanism for domain-specific reference roles without allowing
   model output to register a role;
5. the schema impact on model invocation, Goal Graph, review bundles, and run
   front matter;
6. compatibility decoding for existing persisted string roles; and
7. unknown-role, unknown-namespace, version-mismatch, and valid-extension
   refusal behavior.

## Required boundary

“Role” is not actor identity, model identity, provider identity, capability,
or permission. A role may select a purpose and contract, but it cannot grant a
network, credential, target, or approval capability.

## Out of scope

- implementing the registry;
- renaming every historical role string in one migration;
- changing provider adapter support;
- changing evaluator ownership; and
- granting model output permission to select or register roles.

## Acceptance criteria

1. Every current production role use is classified into one namespace.
2. The registry and structured role-reference shape are specified.
3. Built-in and extension ownership, versioning, and compatibility behavior are
   explicit.
4. Schema/resource amendment impact and preserved-resource requirements are
   decided.
5. G28 receives a bounded implementation and migration checklist.

## Stop boundary

Stop if the design still requires an unconstrained role string at a new
authority boundary, conflates reference and invocation roles, or has no safe
compatibility path for existing records.
