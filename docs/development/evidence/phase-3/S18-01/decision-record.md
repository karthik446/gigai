# S18-01 — Common provider-port and evidence contract

- Status: Research in progress; decision proposed, not an adapter support claim
- Depends on: G11 model port/factory, G16/G17 substrate, and S18 tranche
  redaction boundary
- Unblocks: S18-02, S18-03, S18-04, and the G18 contract review

## Decision

Keep G11's domain-facing port as the baseline and define the S18 common
contract as a research envelope around it. The envelope has nine common
concerns: request, identity, output, finish, error, cancellation, usage, cost,
and replay. Provider-specific values are preserved under a typed, namespaced,
redacted extension boundary; they are not flattened into guessed common fields.

The current G11 port is sufficient for the implemented deterministic,
OpenAI-API, and OpenRouter-API success path. It is not sufficient by itself to
represent streaming, cancellation, partial output, typed terminal finish,
provider-specific error identity, or durable replay metadata. That gap is a
candidate additive contract amendment for G18, not an excuse to change G11 in
this spike.

No candidate family is supported by this record. Codex CLI and Claude CLI are
deferred to S18-02; Anthropic API and the representative local runtime are
deferred to S18-03. OpenAI API and OpenRouter API remain existing G11
implementations whose conformance is limited to their already-evidenced scope.

## Proposed common envelope

```text
request: redacted request payload, target/endpoint identity, role, budget
identity: provider family, resolved model, adapter/runtime version
output: normalized text plus typed non-text extension values
finish: completed | partial | failed | cancelled | timeout | unavailable
error: stable category, provider code, redacted message, phase (pre/mid-stream)
cancellation: requested | acknowledged | forced | unsupported
usage: raw provider object plus normalized token counts
cost: provider_reported | derived | unavailable, never inferred as zero
replay: stable artifact digest plus declared variable fields
extensions: namespaced, typed, redacted provider-specific values
```

The `finish` and `error` values are research vocabulary. They must not be
added to a packaged schema or runtime result until an additive amendment names
the affected resource, preserves existing vectors and hashes, and updates the
installed verifier.

## Replay decision

Replay records preserve redacted request bytes, target and model identity,
normalized output/result, raw usage, cost status, typed extensions, and a
stable digest. Request IDs, timestamps, latency, process IDs, and route
metadata are variable fields and cannot participate in the stable digest.
Secrets, raw authorization headers, unselected references, and unredacted
provider payloads are prohibited. The fixture is deterministic and offline;
S18-02 and S18-03 must prove process/API-specific capture separately.

## Evidence inputs and observed outputs

Inputs were the current G11 port/factory symbols, the existing G11 adapter
tests, the candidate protocol documentation listed in `sources.md`, and the
local fixture at
`research/s18_01/matrix.py:build_replay_fixture`. No provider endpoint or CLI
was invoked for this spike.

The exact local fixture output is a completed `local_ollama` record with model
identity `fixture/model@runtime-1`, output `hello back`, finish `stop`, raw
usage `{ "eval_count": 3 }`, normalized output-token count `3`, and
`cost_status=unavailable`. Its stable digest excludes the declared variable
fields. There are therefore no live provider observations to interpret as
support evidence; the limitations are deliberate and assigned to S18-02 and
S18-03.

## Adopted and rejected assumptions

- Adopt: one domain-facing G11 port; no caller-selected concrete adapter.
- Adopt: raw usage and normalized usage remain distinct.
- Adopt: provider cost may be unavailable and must not be rendered as zero.
- Adopt: pre-stream and mid-stream failures are distinct evidence states.
- Adopt: cancellation and timeout are terminal outcomes, not hidden retries.
- Reject: treating OpenAI-compatible wire shape as proof of semantic
  compatibility.
- Reject: silently discarding provider-specific content blocks, finish reasons,
  routing, or usage fields.
- Reject: provider fallback, racing, retry policy, or adapter support claims
  inferred from this matrix.

## Contract impact

No runtime or packaged contract changed. If G18 requires the proposed envelope
to be durable, raise one separate additive amendment for the replay/extension
record and terminal outcome vocabulary. The amendment must name affected
resources and fields, preserve all existing canonical vectors and resource
hashes, and update the installed verifier before implementation.

## Limitations and next probes

This record is based on current public protocol documentation and the local
G11 implementation. It does not prove CLI process behavior, Anthropic API
behavior, local-runtime availability, cancellation, streaming capture, or
pricing. Those are explicitly assigned to S18-02 and S18-03.
