# TD-0007 — Central namespaced role registry

- Status: Open; v0.1.5 release blocker
- Discovered during: G27 contract review, 2026-08-16
- Affected surfaces: model invocation, adapters, Goal Graph executors, review
  reference roles, run front matter, and model-invocation schemas
- Owning lane: v0.1.5 contract/runtime foundation

## Observation

Production boundaries currently accept unconstrained `role: str` values such
as `gig-builder`, `proposal-questioner`, `reviewer`, `diagnostic`, and
`create`. The same word “role” is also used for review-reference roles and
Goal Graph executor roles, which are different namespaces with different
owners and meanings.

This makes typos, unsupported roles, and accidental cross-namespace reuse
possible. It also prevents an operator or evaluator from answering which
roles are built in, which are extensions, and which model capabilities each
role requires.

## Required resolution

Create one central, versioned role registry with separate namespaces at
minimum:

- `model_invocation` — GigAI-owned model purposes such as
  `gig_builder`, `proposal_questioner`, `researcher`, `reviewer`, and
  `diagnostic`;
- `reference` — domain/review roles such as `primary_source` or
  `subject_repository`; and
- `executor` — Goal Graph execution roles and capability bindings.

Persist a structured role reference rather than a free string at new
boundaries:

```json
{
  "namespace": "model_invocation",
  "id": "proposal_questioner",
  "version": 1
}
```

The registry must support explicit extension entries for domain roles without
letting a model or arbitrary payload invent a role. Existing serialized role
strings need a compatibility decoder and an additive migration/contract plan;
they must not change meaning silently.

## Exit evidence

- One central registry owns built-in role IDs and definitions.
- Invocation, reference, and executor roles cannot be mixed without an
  explicit namespace conversion.
- Unknown built-in roles fail closed; declared extensions are validated and
  versioned.
- Model invocation schemas and runtime request types use the structured role
  reference at the new contract boundary.
- Existing persisted records replay through an explicit compatibility path.
- Role registry vectors cover typo, unknown namespace, version mismatch, and
  valid extension cases.

