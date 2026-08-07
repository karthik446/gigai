# GigAI Local PDF PII Scanner Spike

**Date:** 2026-08-06  
**Status:** research spike complete; implementation not authorized  
**Question:** What is the smallest credible local PDF inspection boundary that can
detect likely PII before a Gig sends material to an external model, provider, or
tool?

This spike is deliberately narrower than a general de-identification product. It
does not change V15, add a schema, select a runtime dependency, or authorize
network access. It defines a local evidence pipeline and the tests required
before a privacy goal can claim an egress guard.

## Short answer

GigAI should treat a PDF as a container, not as a string. A scanner must inspect
the visible text layer, OCR text from image-only pages, and non-body surfaces
such as metadata, annotations, links, attachments, form fields, and filenames.
Email addresses and phone numbers can start with deterministic recognizers.
Names need context-aware recognition and a fail-closed policy because neither a
regex nor a local model can prove that a document is anonymous.

The first safety invariant is an egress property:

```text
supported scan complete and policy says clear
  -> sanitized payload may be offered to an external adapter
PII found, scan uncertain, or scan incomplete
  -> no external model/provider/tool call
  -> remain local-only or require explicit user action
```

The scanner may produce a sanitized derivative, but it must never overwrite the
original or send the re-identification map outside the local workpad. A local
model can adjudicate ambiguous spans; it cannot be the sole unlock for egress.

## Recommendation for GigAI

Make privacy sanitization a platform boundary in front of every external model,
provider, URL fetcher, and tool—not a feature owned by resume, tax, finance, or
research Gigs individually. The first implementation should be a narrow local
PDF scanner and egress gate, with a reusable sanitized-artifact interface that
later reference types can adopt.

Recommended order:

1. Support text-layer and image-only PDFs with explicit per-page coverage.
2. Inspect metadata and non-body objects before declaring a document clear.
3. Use deterministic email, phone, credential, and configured identifier
   recognizers first; use local NER or a local model only for ambiguous names.
4. Default resumes, tax files, financial records, and identity documents to
   `strict_sensitive`; make public-reference handling an explicit policy choice.
5. Fail closed on any finding, uncertainty, unsupported surface, OCR failure,
   or scanner failure before external egress.
6. Emit stable placeholders and a local-only redaction map when sanitization is
   possible; never send the source bytes or map to an adapter.
7. Re-scan the exact serialized payload immediately before every adapter or
   tool invocation and record only share-safe evidence.

GigAI should not begin with a cloud DLP dependency or a remote model acting as
the privacy scanner. A small pinned offline composition is safer for the first
goal; broader recognizer coverage and optional local-model adjudication can be
added after the negative egress boundary is mutation-tested. The first privacy
goal is complete only when tests prove that a fake adapter never receives raw
PDF bytes, detected values, unsupported-page content, or the local redaction
map.

## What “PDF scanned” must mean

### 1. Embedded text

Extract text page-by-page while retaining page number, bounding box when
available, extraction method, and an exact digest of the source PDF. Preserve
text that is visually hidden or outside the normal reading order where the
library exposes it. A successful extraction of zero characters is not “clean”;
it is an OCR-required or unsupported state.

[PyMuPDF page extraction](https://pymupdf.readthedocs.io/en/latest/page.html)
provides `Page.get_text()` and page-level objects. [pypdf's user
guide](https://pypdf.readthedocs.io/en/stable/user/metadata.html) is a useful
stdlib-like alternative for metadata and structural inspection, but extraction
quality varies with font encoding and layout. The spike should compare both on
fixtures rather than assume that one extractor sees every text object.

### 2. Scanned and image-only pages

Detect pages with no reliable text layer, render them locally, and run a pinned
OCR engine. Record per-page OCR status, language/model version, confidence (if
available), and failures. OCR output is evidence with lower confidence, not a
replacement for the source bytes.

[OCRmyPDF's cookbook](https://ocrmypdf.readthedocs.io/en/stable/cookbook.html)
documents a local Tesseract-backed OCR path and a sidecar text file. Its own
warning is important: a sidecar may omit pages that already had text or pages
skipped because of limits/timeouts. Therefore GigAI must merge original-text
and OCR findings and mark skipped pages as `scan_incomplete`, never silently
drop them.

### 3. Non-body surfaces

The scanner must enumerate and inspect, or explicitly fail closed when it cannot
inspect:

- document metadata: author, creator, producer, title, subject, keywords, dates;
- page annotations, widget/form fields, comments, and annotation contents;
- hyperlinks, including URI query strings and fragments;
- embedded files, attachments, portfolios, and attachment filenames;
- bookmarks/outlines and document-level names where exposed;
- visible and hidden form values;
- the input path and filename, without treating them as safe merely because
  they are outside the PDF body.

PyMuPDF documents page annotations and links in its
[page API](https://pymupdf.readthedocs.io/en/latest/page.html) and
[link API](https://pymupdf.readthedocs.io/en/latest/link.html). pypdf's
[annotation guide](https://pypdf.readthedocs.io/en/stable/user/reading-pdf-annotations.html)
and [attachments guide](https://pypdf.readthedocs.io/en/stable/user/handle-attachments.html)
are useful cross-checks. A parser exception, encrypted object, malformed xref,
or unsupported attachment format is an inspection failure, not a clean result.

## Detection layers

### Deterministic recognizers first

The first pass should be local, deterministic, and cheap:

- email addresses, including obfuscated forms such as `name [at] domain`;
- phone numbers normalized across punctuation, country codes, and extensions;
- credit-card-like numbers with checksum validation;
- government/tax identifiers only where a jurisdiction-specific recognizer is
  explicitly configured;
- credentials and tokens using high-signal prefixes and entropy checks;
- account numbers and dates of birth only with contextual rules, not naked
  digit matching.

Every finding should carry entity type, source surface, page/object location,
matched span or a privacy-safe fingerprint, recognizer version, and confidence.
Raw matched values belong only in a local protected report. Share-safe reports
must use stable labels such as `[EMAIL_1]` and never echo the value.

### Names and contextual entities

Names are materially harder than emails and phones. A name may be a common word,
an author of a public article, a company, a fictional character, or the subject
of a private resume. The scanner should combine:

- local NER;
- capitalization and nearby labels (`Name:`, `Applicant`, `Taxpayer`);
- document role and user policy;
- repeated occurrence and co-occurrence with email, address, or phone;
- an allowlist for explicitly public references.

The result is a confidence-ranked candidate, not proof of identity. A local
model may review only ambiguous candidate spans and must return a structured
decision with model/version and rationale. A model saying “no PII” never clears
an unsupported page or overrides a deterministic hit.

### Policy profiles

The Gig selects a policy before scanning:

| Policy | Intended use | External egress rule |
| --- | --- | --- |
| `strict_sensitive` | resumes, tax files, financial records, identity documents | Any PII or uncertainty blocks external egress. User may approve a local-only run or review a sanitized derivative. |
| `public_reference` | public research articles and public reports | Public author/citation names may be allowed only when the source provenance supports that classification; email/phone/secrets still block. |
| `local_only` | maximum privacy or unsupported/ambiguous inputs | Raw and sanitized material stay local; no remote model, provider, or tool receives it. |

Policy is part of the sealed review/run evidence. It cannot be changed by a
provider response or by the document being scanned.

## Sanitization and egress boundary

Detection and sanitization are separate decisions. A sanitized derivative should
use stable surrogates to preserve relationships (`[PERSON_1]`, `[EMAIL_1]`,
`[PHONE_1]`) while removing the original bytes. A local redaction map may be
encrypted or otherwise protected for the user, but it is never included in a
prompt, tool payload, trace export, URL, or provider request.

Immediately before every adapter or tool invocation, GigAI should:

1. identify the exact payload bytes and their digest;
2. verify that the payload is the sanitized derivative authorized by policy;
3. run a final deterministic egress scan over serialized text, files, headers,
   environment-derived values, and tool arguments;
4. reject if a raw PII finding, unresolved candidate, unsupported scan result,
   or credential-shaped value remains;
5. record a share-safe decision and the scanner/evaluator versions.

No provider, URL fetcher, shell command, browser profile, or arbitrary tool is a
scanner. Tools are downstream capabilities and are blocked by the same egress
decision. A local-only path may retain raw bytes in the workpad, subject to the
Gig's target and retention policy.

## Candidate components

These are spike candidates, not selected dependencies:

- [PyMuPDF documentation](https://pymupdf.readthedocs.io/en/latest/) — fast
  page text, geometry, annotations, links, and rendering in one local library;
- [pypdf documentation](https://pypdf.readthedocs.io/en/stable/) — pure-Python
  structural access and attachment/annotation cross-checks;
- [OCRmyPDF documentation](https://ocrmypdf.readthedocs.io/en/stable/) — local
  OCR orchestration and sidecar output; requires a separately managed OCR
  engine/language pack;
- [Tesseract documentation](https://tesseract-ocr.github.io/) — OCR engine
  option for rendered pages, with language/model and quality limitations;
- [Microsoft Presidio analyzer](https://microsoft.github.io/presidio/analyzer/)
  and [Presidio anonymizer](https://microsoft.github.io/presidio/anonymizer/)
  — recognizer registry, regex/checksum/rule/NER layers, and replace/redact
  operators. Presidio's [installation guide](https://microsoft.github.io/presidio/installation/)
  makes clear that its NLP model is an additional local dependency;
- [spaCy named-entity recognition](https://spacy.io/usage/linguistic-features)
  — local NER candidate for names and organizations, not a privacy guarantee.

The spike should prefer a small, pinned, offline-capable composition over a
remote DLP API. Sending raw content to a cloud “privacy scanner” would violate
GigAI's default egress rule before scanning even begins.

## Evaluation fixture matrix

The future privacy goal needs fixtures that exercise every surface, not only a
plain text resume:

| Fixture | Expected result |
| --- | --- |
| text-layer resume with email and phone | deterministic findings; strict policy blocks |
| scanned resume with email/phone in image only | OCR findings; OCR evidence recorded |
| mixed PDF with text pages and scanned pages | both extraction paths represented; no page silently omitted |
| PII in author metadata and annotation | blocked even when body is clean |
| PII in link query, attachment name, and form field | blocked; raw URL/filename never in share-safe output |
| ambiguous common name and public author name | policy-dependent candidate; no unconditional clearance |
| malformed, encrypted, password-protected, and unsupported PDF | `scan_incomplete`; fail closed |
| false-positive-heavy public article | public policy allows only explicitly classified public names |
| credential-shaped value and secret in metadata | deterministic secret finding; external egress blocked |
| sanitized derivative | stable placeholders, no original values, digest differs from source |

Tests must assert both findings and the absence of egress. A fake adapter should
record payload bytes; tests then prove it never receives the original PDF,
matched values, or the local redaction map. Add mutation tests that disable each
extractor, metadata walker, OCR result merge, deterministic recognizer, policy
gate, and final egress scan; each mutation must make at least one fixture fail.

## Proposed evidence shape

Before schemas exist, the conceptual local report should include:

- source PDF digest and size;
- extractor/OCR versions and per-page statuses;
- inspected surfaces and unsupported surfaces;
- findings by type and privacy-safe location;
- policy and decision (`clear`, `sanitize_required`, `local_only`, or
  `scan_incomplete`);
- sanitized artifact digest, if produced;
- redaction-map location kept local and excluded from share-safe output;
- final egress decision, payload digest, and adapter/tool identity.

The report must never contain raw PII by default. A user-facing local report may
show a carefully bounded preview only after an explicit local confirmation.

## Stop boundaries

Stop and amend the contract rather than guessing if any of these are unresolved:

- whether a new PDF surface is inspected or explicitly unsupported;
- whether a policy permits a class of public names or identifiers;
- whether OCR coverage is sufficient for a supported language/document type;
- whether sanitization preserves enough semantics for the Gig's rubric;
- whether a tool or provider receives raw bytes, metadata, headers, or filenames;
- whether a local model is being used as an unlock instead of a secondary review;
- whether a failure can leave a raw payload in a trace, journal, manifest, or
  subprocess argument.

The first implementation should prove the negative boundary—no unsanitized
egress—before optimizing detector recall or adding a broad library stack.
