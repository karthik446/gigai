# S18-03 — Anthropic API and local-model feasibility spike

- Status: Research in progress; recorded-fixture result proposed, not support
- Depends on: S18-01 common envelope and S18-05 redaction/credential/network
  boundary
- Unblocks: G18 adapter feasibility review only; no family is adopted here

## Decision

Keep Anthropic API and local-runtime feasibility as separate tracks. The
recorded fixtures demonstrate that each protocol can be reduced into the
S18-01 envelope without discarding its distinctive fields, but they do not
prove live compatibility.

Anthropic's minimum candidate shape is content-block-preserving output,
model identity, terminal `stop_reason`, raw usage, streamed text deltas,
stream terminal errors, and a cancellation probe. The representative local
track uses Ollama-shaped NDJSON and requires runtime/model identity, `done` /
`done_reason`, accumulated content, and raw evaluation counts. Discovery,
installation, resource limits, offline availability, and cancellation remain
explicit local-runtime questions.

Neither family is supported or interchangeable with the other. Both remain
deferred to G18 or a separate adapter implementation Goal.

## Recorded fixture evidence

The fixture parser is `research/s18_03/fixtures.py`. It consumes only local
dicts and recorded NDJSON strings. It covers Anthropic text and non-text
content blocks, usage, stop reasons, rate/overload errors, streaming reduction,
and malformed/incomplete streams. The local track covers NDJSON accumulation,
runtime identity, `done_reason`, evaluation counts, runtime errors, and
missing terminal records.

No Anthropic endpoint was contacted, no local model runtime was discovered or
started, no credential was resolved, and no target repository was touched.

## Adopted and rejected assumptions

- Adopt: preserve Anthropic non-text content blocks as typed extensions.
- Adopt: preserve local runtime timing/count fields as raw extension data;
  do not guess normalized provider cost.
- Adopt: require explicit terminal events before reporting a completed stream.
- Adopt: keep hosted API and local-runtime identity/availability separate.
- Reject: treating an Anthropic content block as plain text by flattening it.
- Reject: treating Ollama runtime presence as proof of installability or model
  availability.
- Reject: treating API and local-runtime fixtures as support evidence.
- Reject: fallback, retry, installation, discovery, or resource policy inferred
  from parser success.

## Contract impact

No runtime adapter, packaged schema, Goal transition, or provider claim
changed. If implementation requires durable content-block extensions, stream
terminal outcomes, or local-runtime capability records, raise a separate
additive contract amendment preserving existing hashes and canonical vectors.

## Limitations and follow-up

Live API auth/rate/cancellation behavior, real content-block versions, model
discovery, local installation, resource limits, runtime shutdown, and
reproducibility are not proven. Those require explicit operator or later Goal
evidence and must remain behind S18-05's boundary.
