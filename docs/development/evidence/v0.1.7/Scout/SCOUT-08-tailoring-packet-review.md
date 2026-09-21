# SCOUT-08 — Independent tailoring packet review

Date: 2026-09-10. Scope was source-only review of the pure tailoring candidate,
schema, focused tests, and frozen SCOUT-00 rules. No source edits, caller
integration, journal publication, provider execution, full suite, or package
proof were performed. The module remains a pure candidate and has no delivered
Plan/Run caller.

## Verdict

**Not ready to accept as a safe tailoring packet.** Requested-output exactness,
source/span digests, replayed checks, and lexical prompt inertness are useful
guards, but the candidate does not bind supported claims to draft content and
does not close dangerous HTML link schemes.

## Blocking findings

### B1 — Supported claims are not bound to the claimed draft spans

`_request` validates candidate evidence spans and marks a caller-reported
supported claim as included (`src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000075/tailoring.py:275-301`), but
`build_tailoring_packet` merely preserves supplied document bytes and stores
the claims/checks in the sidecar (`:409-436`). Neither build nor validation
requires the included claim text (or a bound, caller-declared draft span) to
occur in the resume/cover letter. Consequently a supported “Built reliable
Python tools” claim can accompany a resume saying “No relevant experience
stated,” and validation accepts it.

**Reproducer:** using the existing focused fixture inputs/request, build with
`status="supported"` and resume bytes
`b"# Resume\\n\\n## Experience\\nNo relevant experience stated.\\n"`; the probe
returned `claim_evidence[0].included == True`, and
`validate_tailoring_packet(...)` returned successfully. This violates the
requested draft-to-supported-span binding and leaves factuality as an
unchecked caller assertion. Required correction: require explicit exact draft
spans for every included claim/requirement (with quote digest and document
digest), or perform a bounded semantic validator that produces independently
reviewed evidence; preserve unsupported/conflicted claims as excluded.

### B2 — Dangerous HTML links bypass the link safety gate

`_documents` rejects only Markdown links matching
`](javascript|data|vbscript:...)` (`tailoring.py:144-162`). Raw HTML links are
accepted unchanged, so `<a href="javascript:alert(1)">click</a>` and
`<a href="data:text/html,evil">click</a>` both build and validate. The schema’s
`document` definition only checks digest/size/check fields
(`tailoring.schema.json:43`), and `check_document` is advisory, so neither
closes this control/link safety route.

**Reproducer:** build with either HTML line as the resume body; both disposable
probes returned `unsafe-html-accepted`, and no `TailoringPacketError` was
raised. Required correction: reject dangerous URI schemes in HTML attributes
and other supported link forms, or sanitize/render inert text before any
consumer can expose the bytes. Add regression coverage for HTML, case/whitespace
variants, and other supported Markdown link syntaxes.

## Nonblocking / accepted boundaries

The candidate correctly enforces exact requested outputs, closed source-role
bindings, byte/span digests, posting-versus-candidate role separation, bounded
inputs, hostile source text inertness, regenerated deterministic document
checks, and sidecar packet digest equality (`tailoring.py:166-239`, `:395-485`).
The explicit limits correctly state that semantic factuality is external-agent
reported and lexical matches are not semantic proof (`:426-433`); however,
those disclaimers do not substitute for the missing supported-claim-to-draft
binding above. The lexical blanket exclusion of “harness”/metrics is only a
conservative filter (`:37`, `:293-297`), not a semantic factuality solution.

## Verification boundary

Three disposable in-memory probes were used: supported claim with contradictory
draft, HTML `javascript:` link, and HTML `data:` link. Existing focused tests
and the implementation report were inspected but not rerun as a full or
release/package acceptance lane. Caller integration, persistence, activation,
and provider execution remain absent and are not inferred.

