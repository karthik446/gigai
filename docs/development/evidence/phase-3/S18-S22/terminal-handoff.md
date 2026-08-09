# S18/S22 prerequisite-spike tranche — terminal handoff

- Status: Accepted research evidence; G18 implementation remains start-gated
- Scope: S18-01 through S18-05 and S22-01
- Related gate: S16-EVAL (`7968b03`)

## Verdict

The six prerequisite spikes are complete as research records and are accepted
as evidence for the next contract-review step. Their fixtures replay offline,
without live credentials, provider calls, target mutation, or background work.
The combined verification run is:

```text
uv run pytest -q tests/test_s18_01_provider_contract.py \
  tests/test_s18_02_cli_feasibility.py \
  tests/test_s18_03_api_local_feasibility.py \
  tests/test_s18_04_handoff_design.py \
  tests/test_s18_05_provider_boundary.py \
  tests/test_s22_01_interview_protocol.py
34 passed
```

`rtk git diff --check` also passes. The records are research decisions, not
claims that any new provider adapter or product interaction is supported.

## Accepted records

| Spike | Evidence | Commit | Verification |
|---|---|---|---:|
| S18-01 | [common provider contract](../S18-01/decision-record.md) | `744d638` | 3 tests |
| S18-02 | [CLI feasibility](../S18-02/decision-record.md) | `1e682a3` | 5 tests |
| S18-03 | [API/local feasibility](../S18-03/decision-record.md) | `aa2fc90` | 6 tests |
| S18-04 | [handoff and comparison](../S18-04/decision-record.md) | `65cb28c` | 6 tests |
| S18-05 | [redaction and boundary](../S18-05/decision-record.md) | `40b574c` | 7 tests |
| S22-01 | [proposal interview](../S22-01/decision-record.md) | `4d34c13` | 7 tests |

## Provider and effect disposition

No new adapter family is accepted by this tranche. Existing OpenAI and
OpenRouter G11 implementations retain only their already-evidenced scope.
Codex CLI, Claude CLI, Anthropic API, and the representative local runtime
remain deferred to G18 or separate implementation Goals. No provider fallback,
racing, retry policy, credential acquisition, target mutation, capability
execution, public server, or background activity is authorized by these
records.

S22-01 adopts only the local, offline proposal protocol: bounded questions,
explicit reference selection, explicit privacy/capability/effect choices,
clarification blocking, operator approval, and a disposable ordered trace. It
does not implement `gigai create` or prove HTMX rendering or browser security.

## Contract work before runtime implementation

The records identify candidate additive work, but do not authorize applying it:

1. If durable provider outcomes are needed, review an amendment for the common
   envelope, terminal outcomes, replay fields, and typed provider extensions.
2. If durable comparison is needed, review an amendment for Goal-edge
   handoffs, artifact parentage, disagreement, and adjudication inputs.
3. If boundary attestations are needed, review an amendment for blocked
   redaction/network outcomes without storing credential values.
4. If G22 needs durable interview records, review a separate additive amendment
   for question/answer persistence.

Every amendment must preserve the nineteen-resource baseline, existing hashes
and canonical vectors, and the installed verifier; each must be separately
accepted before implementation.

## Exact G18 start condition

G18 may start implementation only when all of the following are true:

- S16-EVAL is accepted and its fixed evaluation bar is available to cite;
- all six records above and this terminal handoff are committed and accepted;
- the contract-impact review has explicitly resolved whether each proposed
  amendment is needed, with no silent schema, authority, or transition change;
- G18 begins under its own approved implementation Goal with the S18-05
  pre-invocation boundary and S18-04 no-fallback/disagreement rules carried
  into its acceptance criteria.

Until then, G18 is not startable. A future provider probe still requires
explicit operator authorization, synthetic or separately authorized
credentials, and the adopted redaction/network boundary.
