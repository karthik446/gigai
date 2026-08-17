# S27-ROLE — Central Role Registry Decision Record

- Status: Proposed for acceptance; no runtime implementation
- Spike: [S27-ROLE](../../../../goals/phase-5/S27-ROLE-central-role-registry.md)
- Recorded: 2026-08-16
- Depends on: G18 model invocation contracts, G15/G16 review-reference
  contracts, Goal Graph executor contracts, and persisted role-bearing fixtures
- Unblocks: G28 v0.1.5 readiness implementation

## Decision summary

The bare `role: string` shape is not a sufficient authority-boundary type.
GigAI will introduce a versioned structured role reference:

```json
{
  "namespace": "model_invocation",
  "id": "proposal-questioner",
  "version": 1
}
```

The registry will use separate namespaces. A role identifies a purpose and its
contract; it never grants a capability, credential, network permission,
target effect, approval, or active-version transition.

The initial registry decision is additive: G28 should add one reusable
`role-reference.schema.json` resource rather than silently changing the
meaning of existing string fields. The existing 30 packaged resources remain
byte-identical and their hashes remain unchanged. Existing records are read
through an explicit compatibility decoder until each consuming resource has an
accepted additive migration.

## Namespace ownership

| Namespace | Owner and use | Current examples | Not included |
| --- | --- | --- | --- |
| `model_invocation` | Model purpose passed through `ModelRequest` and persisted invocation/front-matter records | `gig-builder`, `proposal-questioner`, `live-diagnostic`, `offline-diagnostic`, `create`, plus contract/fixture values `reviewer` and `researcher` | provider family, model identity, endpoint, credential, capability |
| `reference` | Review-contract and Review Bundle reference-role semantics | `primary`, `primary-source`, `subject-repository`, `comparison-data`, `source` | local path, reference ID, sensitivity, provenance |
| `executor` | Goal Graph executor-purpose semantics | Goal Graph executor `role` values, interpreted with executor `kind` and `capability` | executor process identity, installed bytes, capability grant |
| `occurrence` | G21 occurrence/input role semantics | `input` | model purpose, reference role, approval actor |
| `protocol` | External protocol message roles | `user`, `assistant` in provider/message fixtures | GigAI authority or registry extensibility |

`protocol` values are explicitly not GigAI roles and must not be registered as
model, reference, or executor roles. `provider_family`, `capability`, and
`executor.kind` are also separate typed fields, not role aliases.

## Observed role inventory and compatibility disposition

The following inventory is derived from current source, schemas, and fixtures:

| Observed value | Field/context | Disposition |
| --- | --- | --- |
| `gig-builder` | builder model invocation | built-in `model_invocation` role |
| `proposal-questioner` | adaptive question generation | built-in `model_invocation` role |
| `live-diagnostic` | live diagnostic invocation | built-in `model_invocation` role |
| `offline-diagnostic` | offline doctor probe | built-in `model_invocation` role |
| `create` | lifecycle diagnostic/create invocation | built-in `model_invocation` role pending caller split in G28 |
| `reviewer` | G18/model-contract fixtures and model invocation tests | built-in `model_invocation` role; not a reference-role alias |
| `researcher` | contract-spike/model invocation fixtures | built-in `model_invocation` role for compatibility; no provider grant |
| `diagnostic` | older model invocation fixtures | legacy `model_invocation` alias; preserve spelling and do not infer live/offline |
| `doctor` | older model invocation fixture | legacy `model_invocation` alias; preserve spelling and do not infer capability |
| `test` | test-only setup fixture | fixture-only legacy value; not a production built-in |
| `primary` | review-loop reference role and review bundle | built-in `reference` role |
| `primary-source` | G15 review reference fixture | built-in/extension-candidate `reference` role, depending on contract owner |
| `subject-repository` | G15 review reference fixture | extension-candidate `reference` role |
| `comparison-data` | G15 review reference fixture | extension-candidate `reference` role |
| `source` | contract-spike reference fixture | fixture/extension-candidate `reference` role |
| `input` | G21 occurrence fixture | built-in `occurrence` role |
| `user`, `assistant` | provider message protocol | not registry roles; protocol enums only |

The decoder must be field-owner aware. The string `source` in a review bundle
cannot be interpreted as a model role, and `reviewer` in a model invocation
cannot be interpreted as a reference role. Unknown values fail closed at new
authority boundaries.

## Structured role-reference contract

The additive role-reference resource must require exactly:

- `namespace`: closed enum of registered namespaces;
- `id`: closed/declared identifier for the selected namespace; and
- `version`: positive registry contract version.

The resource must reject unknown namespaces, unknown built-ins, non-positive
versions, extra properties, and namespace/field-owner mismatches. A role
definition may additionally declare purpose, allowed callers, required
evidence, deprecation state, and extension owner, but those definitions are
registry metadata—not permissions.

The registry must distinguish these states:

1. `registered`: known built-in or accepted extension with a compatible
   version;
2. `deprecated`: known but not selectable for new records; readable for
   history;
3. `legacy_unresolved`: an old string was preserved but cannot be safely
   mapped to one structured role; and
4. `refused`: unknown namespace, ID, version, or field-owner conversion.

`legacy_unresolved` is readable evidence, not a pass for a new invocation,
approval, executor, or target-effect boundary.

## Extension and authority rules

Extensions must be declared by the owning namespace registry and versioned in
the same additive contract. A model response, browser payload, prompt, or
untrusted artifact cannot register an extension or redefine a built-in role.
The extension declaration must identify its owner, purpose, allowed callers,
and required evidence. It must not contain a capability grant.

Role selection is validated against the caller and field owner before the
existing provider, reference, executor, or occurrence contract runs. It does
not replace those checks. In particular:

- a model role cannot authorize provider selection or credential resolution;
- a reference role cannot authorize reading a path or sharing its contents;
- an executor role cannot authorize an effect absent the Goal Graph effect
  and operator gate; and
- an occurrence role cannot authorize a Run, target mutation, or approval.

## Compatibility strategy

Existing persisted string records remain readable through a decoder that takes
the owning field and record version as input. The decoder behavior is:

1. known exact string in the correct namespace → structured role reference;
2. known legacy alias with one safe mapping → structured role reference plus
   legacy spelling retained in the audit projection;
3. ambiguous or context-free string → `legacy_unresolved`, readable but
   refused at a new authority boundary; and
4. unknown or malformed value → stable refusal with no inferred namespace.

No migration rewrites historical bytes in place. A future additive resource or
consumer amendment must preserve the 30-resource baseline, register its hash,
and include vectors for old-string replay, new structured references, unknown
roles, version mismatch, namespace mismatch, and extension acceptance.

## G28 implementation obligations

G28 must implement, in order:

1. the additive role-reference schema and registry definition;
2. a field-owner-aware compatibility decoder;
3. built-in entries for the observed production roles and explicit treatment
   of fixture/legacy values;
4. validation at model invocation, review reference, Goal Graph executor, and
   occurrence boundaries without silently changing prior schema meaning;
5. vectors for typo, unknown namespace, unknown ID, version mismatch, safe
   legacy replay, ambiguous legacy refusal, valid extension, and attempted
   capability escalation; and
6. installed replay proving old persisted records remain readable.

## Decision and stop boundary

This spike accepts the namespace separation, structured role-reference shape,
dedicated additive resource direction, and compatibility policy as the design
for G28 review. It does not claim the registry or migration is implemented.

Stop G28 if a new authority boundary still accepts an unconstrained role
string, if a decoder guesses across namespaces, if a role can grant a
capability, or if existing records become unreadable without an explicit
refusal classification.

## Evidence references

- [S27-ROLE spike](../../../goals/phase-5/S27-ROLE-central-role-registry.md)
- [G18 model invocation schema](../../../../../src/gigai/schemas/model-invocation.schema.json)
- [G15 review contract tests](../../../../../tests/test_g15_review_substrate.py)
- [Goal Graph schema](../../../../../src/gigai/schemas/goal-graph.schema.json)
- [G21 occurrence tests](../../../../../tests/test_g21_occurrence.py)
