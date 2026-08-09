# S18-04 — Handoff, comparison, cancellation, and unavailable provider spike

- Status: Research in progress; deterministic design proposed, not runtime
- Depends on: S18-01 common envelope, S18-02/03 feasibility boundaries, and
  S18-05 redaction/credential/network boundary
- Unblocks: G18 handoff/comparison contract review only

## Decision

Comparison and handoff remain explicit bounded transitions over independent
artifacts. A Goal edge names the source and receiver Goals and has a fixed
handoff cap. A received artifact points to the source artifact as its parent;
it does not replace or mutate the source output.

Two independent outputs are compared without selecting a winner. Equal output
is an agreement. Different output produces a visible disagreement record with
both artifact IDs, per-output usage/cost attribution, and an adjudication input
whose winner is unset. Human adjudication, not fallback or model racing,
resolves the disagreement later.

Cancellation and receiver unavailability are terminal outcomes. Unavailability
does not trigger fallback, retry, racing, background work, or an implicit
alternate provider. A handoff beyond the edge cap is rejected.

## Transition table

| Condition | Result | Artifacts/evidence |
|---|---|---|
| valid edge, valid parent, receiver available | `received` | child points to source parent; usage/cost copied distinctly |
| source parent mismatch | `blocked` | no child artifact |
| handoff count exceeds cap | `blocked` | no retry or child artifact |
| operator cancellation | `cancelled` | no child artifact |
| receiver unavailable | `unavailable` | no fallback; no child artifact |
| outputs equal | `agreement` | both independent IDs remain available |
| outputs differ | `disagreement` | both IDs plus adjudication input; no winner |

## Usage and cost

Usage and cost status remain attached to each independent artifact and are
copied to a received child only as provenance. `provider_reported`, `derived`,
and `unavailable` remain distinct; unavailable is never rendered as zero.
Comparison does not aggregate away per-output attribution.

## Contract impact

No runtime orchestration, provider fallback, Goal transition, packaged schema,
or target authority changed. If G18 requires durable edge/handoff,
disagreement, or adjudication-input records, it must raise a separate additive
contract amendment preserving existing hashes and canonical vectors.

## Fixture boundary and limitations

`research/s18_04/handoff.py` is a pure local state model. The tests do not
invoke a provider, start a process, access credentials, or write a target.
This spike does not prove runtime scheduling, persistence, real cancellation,
provider availability, or adjudicator behavior. It defines the decisions those
later implementations must preserve.
