# SCOUT-08 — Tailoring packet corrections

Date: 2026-09-10. This correction lane owns only the pure `.075` tailoring
candidate, its sidecar schema, focused tests, and this evidence note. It does
not register the candidate, add a caller, publish a Run, migrate historical
packets, modify user resume files, invoke providers, or claim semantic
factuality.

## Corrections

The reproduced supported-claim/contradictory-draft case now refuses unless each
included supported claim and supported requirement carries explicit
`draft_evidence_refs`. Each reference binds the requested document kind, exact
draft byte offsets, draft quote digest, complete document digest, candidate
source ID/role, exact source offsets, and source quote digest. The validator
checks source-role membership against the existing source-side spans, bounds,
digest equality, and UTF-8 code-point boundaries; changed or missing draft,
source, or document bytes refuse. Unsupported and conflicted assertions are
excluded and cannot carry draft evidence.

The arbitrary lexical exclusion of “harness” and “metrics” was removed. A
caller may report such a claim as supported, but the packet now proves only the
explicit byte linkage; semantic factuality remains external-agent reported,
and no hiring or ATS outcome score is produced.

The remaining claim-linkage gap is closed with an exact UTF-8 relation: every
included claim's statement must equal the decoded bytes of each referenced
draft span. This is intentionally exact (no whitespace normalization, fuzzy
matching, NLP, or lexical heuristics); source wording remains an independently
validated, caller-reported evidence association and is not required to equal
the tailored claim. Requirement statements remain posting requirements and do
not use this claim-text equality rule.

The document gate now rejects raw HTML entirely, including dangerous HTML
links, while validating all accepted Markdown link destinations in inline,
reference-definition, and URI-autolink forms. Only parsed `http`/`https`
destinations without credentials, protocol-relative paths, controls,
backslashes, or other schemes are accepted; entity decoding and case/whitespace
normalization occur before scheme validation.

The conservative raw-HTML gate also refuses declarations, processing
instructions, and comments, including case/whitespace/entity-obfuscated forms.
This is a packet-input rejection boundary, not a claim of general browser
sanitization; safe HTTP(S) Markdown links and URI autolinks remain accepted.

The sidecar schema now states the straightforward conditional invariants that
supported requirements/claims have nonempty draft refs and excluded claims or
non-supported requirements have none. Cross-field source-span membership and
byte equality remain Python-validator responsibilities because JSON Schema
cannot establish those relationships here.

## Verification

The focused owned lane passed:

```text
rtk .venv/bin/pytest -q tests/test_scout08_tailoring_packet.py
26 passed in 0.08s

rtk ruff check src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000075/tailoring.py tests/test_scout08_tailoring_packet.py
All checks passed!
```

Coverage includes exact claim/draft statement equality, changed-statement stale
refs, different source wording, document replacement, explicit draft spans,
missing/changed draft bytes, source/draft digest linkage, UTF-8/bounds
validation, resume-only, cover-letter-only and both outputs, unsupported
exclusion, changed documents, source tampering, contradictory drafts, raw HTML
declarations/processing instructions/comments, dangerous inline/reference/
autolink variants, and allowlisted reference/autolinks. Ruff also passed for
the owned Python module and focused test file.

This is focused candidate evidence only. Central schema/hash inventories,
Plan/Run caller integration, persistence, activation, package/wheel proof,
full-suite acceptance, and provider execution remain outstanding.
