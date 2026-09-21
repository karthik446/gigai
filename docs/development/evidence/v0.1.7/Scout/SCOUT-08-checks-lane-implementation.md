# SCOUT-08 — Bounded deterministic document-check lane

**Status:** Implemented in the worker-owned lane on 2026-09-08. This is a
pure library check surface and is not a schema registration, CLI integration,
provider Run, persistence operation, or application event.

## Owned artifacts

- `src/gigai/scout_checks.py`
- `tests/test_scout08_checks.py`
- this evidence note

No shared or existing worker-owned files were edited. The checker accepts exact
`bytes`, a requested `resume` or `cover_letter` kind, and optional caller-owned
posting terms, length bounds, contact requirements, heading requirements, and
cover-letter job/company values. It returns a bounded JSON-shaped report with:

- `rule`/`rule_version`;
- exact imported document-byte digest and byte size;
- a digest of normalized supplied posting terms, without copying the terms into
  findings;
- measured words, characters, lines, and Markdown headings;
- bounded findings containing code, severity, static message, and line-only
  evidence references;
- a canonical requirements digest binding effective caller settings without
  echoing contact values;
- an output digest over the complete report-shaped object with only
  `output.sha256` omitted;
- explicit `semantic_factuality_not_assessed` and
  `requirement_to_evidence_judgment_not_assessed` limits.

## Deterministic checks and safety boundaries

The lane checks strict UTF-8 decoding, empty/unreadable text, conservative
Markdown ATX and Setext headings plus standalone title-case/plain headings,
caller-declared word/character bounds, contact presence without echoing
supplied values, obvious placeholders, and optional cover-letter role and
company specificity. A declared heading matches an exact supported heading
title, never a substring in prose. Supplied posting terms produce only lexical
`term_present` or `term_absent` findings; lexical presence is not treated as
competence, factuality, evidence support, an ATS score, hiring probability, or
an application guarantee.

Malformed/non-byte input, invalid kinds and requirement shapes, malformed UTF-8,
oversize documents, hostile control/formatting characters, and overlong term or
requirement collections are reported in bounded output. Invalid document kinds
use the fixed `invalid` marker; raw caller strings are never copied into the
report. Requirement key/value oversize and invalid shapes are reported, and a
`findings_truncated` marker plus `output.checks_complete: false` makes a capped
result explicit. The service does not read files, execute source text, call
models or networks, write private Gig state, use SQLite, infer candidate
claims, or perform semantic factuality or requirement-to-evidence judgment.

## Coordinator-reproduced gaps corrected

The coordinator reproduced four defects after the initial 9-test lane result:

1. `heading_requirements=["Experience"]` no longer passes on
   `Experience mentioned in prose.`; exact ATX/Setext/standalone plain heading
   parsing now distinguishes present and absent sections.
2. A 100,000-character invalid `document_kind` is represented by the bounded
   non-sensitive `invalid` marker, and requirement key/value bounds are audited
   without echoing raw input.
3. `output.digest_scope` now states that only `output.sha256` is omitted, and
   the digest covers the rest of the output object; tests reconstruct that exact
   preimage from the returned report.
4. A canonical `requirements_sha256` binds effective length, contact, heading,
   cover-letter, and posting-term settings (with contact values represented only
   inside the digest); equivalent mapping order remains stable, and a capped
   finding set is explicitly incomplete.

## Focused verification

Commands were run directly against only the owned module and test file:

```text
rtk ruff check src/gigai/scout_checks.py tests/test_scout08_checks.py
Ruff: 0 issues

rtk proxy .venv/bin/python -m py_compile src/gigai/scout_checks.py tests/test_scout08_checks.py
exit 0

rtk proxy .venv/bin/python -m pytest -q tests/test_scout08_checks.py
14 passed in 0.03s
```

Coverage includes exact input/requirements/output-digest binding, exact heading
matching for Markdown and conservative plain text, bounded invalid kind
handling, term-present labeling, non-echoing contact values, cover-letter
specificity and placeholders, malformed UTF-8, oversize and bounded output,
hostile controls, malformed caller requirements, deterministic repeatability,
and invalid kind/non-byte input.

## Remaining integration limits

This lane intentionally has no top-level CLI, persistence, schema family,
journal transition, source/evidence record resolver, model invocation, or
factuality reviewer. A later coordinator-owned integration may bind the report
to immutable posting/candidate revisions and register a sidecar only after the
corresponding contract and authority decisions are explicit. Passing this
focused suite is lane evidence, not full SCOUT-08 acceptance or live/provider
evidence.
