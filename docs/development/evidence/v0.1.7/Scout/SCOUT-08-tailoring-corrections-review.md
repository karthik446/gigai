# SCOUT-08 — Independent tailoring-correction review

**Review date:** 2026-09-10  
**Scope:** Fresh, read-only review of the frozen SCOUT-00 tailoring rules,
the prior SCOUT-08 packet review, the correction note, the pure `.075`
tailoring module, its sidecar schema, and `tests/test_scout08_tailoring_packet.py`.
No source or test files, central registries, inventories, materialization,
hydration, application callers, private data, providers, or network state were
used or changed.

## Verdict

**Not ready to accept as a fully safe tailoring packet; acceptable only as a
bounded pure candidate after recording the residual findings below.** The
correction closes the original contradictory-draft reproducer when the caller
provides a draft span, rejects the listed raw HTML and Markdown URL cases, and
preserves the explicit semantic-factuality abstention. It does not establish
that the span text supports the claim, and its raw-HTML rejection misses HTML
declarations/processing instructions; the sidecar schema also does not express
the conditional linkage invariants that the Python validator enforces.

## Blocking findings

### B1 — Draft/source spans are associated, but claim text is not bound to either span

`_draft_spans` verifies that a draft span and a candidate-evidence span are
individually bounded and digest-correct, and that the source span is already in
the claim's `evidence_refs` (`tailoring.py:290-323`). It never compares either
span's bytes with the claim statement or with each other. Thus an included
supported claim can name `Built reliable Python tools` while its source and
draft evidence both point at unrelated bytes; the packet still accepts it.

**Reproducer:** start from the focused fixture's `_request()`, change
`claim_evidence[0]["statement"]` to `"Managed a global space program"`, leave
the existing source/draft refs unchanged, and call `_packet(request=request)`.
The call succeeds and marks the claim included. This is not semantic
factuality (which the contract correctly leaves external), but it means the
correction proves only caller-declared locations, not that the included claim
is actually represented by the draft or supported by the cited source.

**Required disposition:** either document this as an intentionally weaker
caller assertion and rename the evidence relation accordingly, or require a
bounded deterministic relation (for example exact normalized claim text or an
explicit caller-supplied claim-to-span digest) before calling the claim
“bound.” Do not describe byte linkage as semantic factuality.

### B2 — Some raw HTML forms bypass the stated raw-HTML gate

`_RAW_HTML_TAG` only recognizes tags beginning with an alphabetic name, and
`_validate_links` only rejects those matches after removing safe URI
autolinks (`tailoring.py:47-51, 190-218`). HTML declarations and processing
instructions therefore pass unchanged, despite the correction note saying raw
HTML is rejected entirely.

**Reproducer:** with the existing fixture, build each document body below;
both calls return a packet rather than `TailoringPacketError`:

```text
# Resume
<!DOCTYPE html>

# Resume
<?xml-stylesheet href="javascript:alert(1)"?>
```

These are raw HTML/XML constructs and the second carries a dangerous URI.
Either reject all supported HTML declaration/processing/comment forms (or use a
small Markdown parser with an explicitly closed grammar), and add regressions
for declarations, processing instructions, comments, and case/whitespace
variants. The existing `<a href=...>` and entity-obfuscated cases are covered
and refused.

## Schema and contract consistency

The Python path correctly requires explicit draft refs for included supported
requirements/claims, excludes refs from unsupported/conflicted claims, checks
source role membership, exact source/draft bounds and digests, UTF-8
boundaries, changed documents, and regenerated document checks. It accepts
HTTP(S) inline links, reference definitions, and URI autolinks while refusing
credentials, protocol-relative paths, controls, backslashes, and other
schemes. The limits correctly say semantic factuality is external-agent
reported and lexical checks are not semantic proof.

The JSON schema is closed at the object level, but `draft_span` only describes
fields; it cannot require the source span to be a member of the claim's
`evidence_refs`, and `claim`/`requirement` do not encode the conditional rule
that supported items need nonempty draft refs while excluded items need none.
`gap.source_refs` and `question.source_refs` are unconstrained arrays, and
document `checks` is an unconstrained object. This is acceptable only if the
Python validator is the authoritative boundary and schema validation is not
presented as independently proving linkage; otherwise tighten the schema and
add schema-level negative fixtures.

## Verification

Focused owned lane, run once:

```text
rtk .venv/bin/pytest -q tests/test_scout08_tailoring_packet.py
15 passed in 0.05s
```

Bounded scratch probes reproduced the B1 unrelated-statement acceptance and
B2 declaration/processing-instruction acceptance; existing raw-anchor,
entity-obfuscated, reference, autolink, changed-draft, source-tamper,
UTF-8-boundary, role, and unsupported-exclusion tests behaved as expected.
No full suite, wheel/package proof, Plan/Run caller, persistence, activation,
or provider execution was attempted or inferred. Byte linkage in this review
is an integrity/provenance check only, never a semantic factuality judgment.
