# S16-EVAL contract-impact decision

## Decision

The current G15/G16 packaged schemas are sufficient for the S16-EVAL research
evidence. No packaged schema, canonical vector, Goal transition, journal
authority rule, or installed verifier is changed by this spike.

The S16-EVAL corpus manifest, case labels, quality report, calibration record,
assertion matrix, and mutation report are research evidence under the phase-3
evidence directory. They do not become GigAI runtime artifacts or provider
invocation records.

## Existing substrate used

- `review-contract.schema.json` supplies criteria, severity, evidence
  requirements, evaluator identity/version, and redaction policy.
- `finding.schema.json` supplies criterion, evidence, severity, confidence,
  disagreement, and evaluator provenance for actual Review Findings.
- `adjudication.schema.json` preserves human decisions without rewriting
  independent findings.
- `trace.schema.json` supplies replay identity and variable-field boundaries.
- `review-loop.schema.json` and G16's artifacts supply lifecycle and closure
  evidence.

Harness-only assertion IDs, case labels, metric rows, and calibration records
remain outside those runtime schemas. They must not be forced into a Finding or
Report merely to avoid an amendment.

## Amendment trigger

If a later implementation requires durable runtime storage for a judge
calibration result, corpus label version, or provider-specific evaluation
artifact, it must raise a separate additive contract amendment. That amendment
must preserve all current resource hashes and vectors and update the installed
resource verifier before runtime code changes.
