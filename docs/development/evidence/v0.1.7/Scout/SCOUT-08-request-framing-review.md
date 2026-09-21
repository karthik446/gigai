# SCOUT-08 F1/F2 request and framing correction — independent re-review

Date: 2026-09-10

## Verdict

**F2 accepted; F1 remains blocked.** The original missing Plan-time request
identity and noncanonical bundle-length findings are corrected in the inspected
source, but the new pre-Run request validator still accepts arbitrary nested
evidence shapes. That leaves a malformed request able to seal a
`tailor-application` Plan and start a Run, contrary to F1's stated strict,
bounded request-before-Run contract.

## Accepted correction subset

`external_recording._seal_tailoring_request` now runs during v2
`tailor-application` Plan sealing. It considers only resolved
`g45_run_input` envelopes, authenticates each snapshot under the held writer,
uses the fixed `validate_tailoring_request`, refuses zero or multiple valid
candidates, and rejects request/source-role index reuse. The resulting
`tailoring_request` descriptor seals index, run-input ID, record ref, and
snapshot ref into both Plan identity and payload. The strict v2 Plan schema
conditionally requires that descriptor only for `tailor-application`.

Start revalidates every sealed input and redeems only that descriptor. The
descriptor must still match the selected index, family, ID, record ref, and
snapshot ref; checkpoint and submit carry the same descriptor into persisted
domain validation. The bridge continues to compare the sidecar's reconstructed
request to the actual selected bytes, while source artifacts are redeemed from
sealed G45 snapshots rather than sidecar base64. There is no JSON-shape search,
sidecar source-role selection, wrapper, or `g45_reference` fallback in this
new redemption path.

F2 is corrected in `scout_tailoring.decode_tailoring_bundle`: its token must
be ASCII digits, at most six bytes, positive, no leading zero, and no more
than 256000 bytes. It still requires exact requested document kinds and exact
trailing delimiters, so extra bytes, duplicate/missing headers, signs,
whitespace, Unicode digits, and oversize lengths refuse; length-delimited
payloads containing header-like bytes remain recoverable exactly. The same
fixed `.075` renderer, source-byte checks, HTML/span safeguards, and semantic
limits remain in force.

## Remaining F1 blocker — nested request evidence is not strict before Run

**Severity: blocking for F1, because Plan/Run allocation can precede typed
nested request validation.**

`scout_tailoring._validate_request_shape` closes the top-level request and
source-role objects, but it only requires `requirements[*].posting_ref` to be
a mapping, `candidate_evidence_refs` and `evidence_refs` to be lists, and
`source_refs` to be lists. It does not close or bound the nested span/ref
objects at this boundary. A no-write in-memory probe built the ordinary
canonical request fixture, replaced its required `posting_ref` with
`{"unexpected_nested_key":"accepted"}`, and called
`validate_tailoring_request`; it returned successfully
(`unexpected_nested_posting_ref_accepted=True`).

`_seal_tailoring_request` calls that validator before Plan publication, so the
same canonical malformed request is a valid F1 candidate and can pass into
Plan/Run allocation. The later `.075` packet validator may refuse it once
documents and actual source bytes exist, but that is not a substitute for the
required strict request-before-Run shape proof. The currently inspected
focused integration negatives cover missing, duplicate, wrong-family,
source-role reuse, foreign, and changed-index cases, but not this malformed
nested-request case.

**Minimal fix direction:** make the fixed request validator recursively close
and bound each supplied evidence reference/span and `source_refs` item at Plan
time, including known keys, source ID grammar, integer/range fields, digest
shape, list limits, and duplicate IDs where applicable. Do not require
draft-byte membership, document spans, source-byte quotation equality, or
claim/requirement semantic outcomes before a draft exists; those remain the
normal checkpoint renderer's responsibility. Add a public Plan refusal test
for unknown/malformed nested `posting_ref`, candidate/claim evidence refs,
and gap/question source refs, asserting no Plan, Run, or HEAD change.

## Historical-plan and schema behavior

The conditional schema is additive for v1 and non-tailoring v2 Plans: they do
not require `tailoring_request` and retain their existing parse path. A
previously sealed v2 tailoring Plan without the new descriptor is fail-closed,
not silently reinterpreted: a disposable schema probe removed the descriptor
from a current otherwise-valid tailoring Plan and received
`legacy_descriptor_schema_valid=False`. There is no automatic migration.

This is nonetheless a same-`2.0` schema-shape change, not a separately
versioned historical wire format. Callers reading such an old tailoring Plan
therefore receive their context's ordinary strict-record refusal (for example
authority/reconciliation handling), and plan enumeration can encounter that
refusal while scanning historical Plan records. Treat this as an explicit
unsupported historical-tailoring boundary, not as retained v2 tailoring
continuation evidence. It does not affect the closed retained research and
discovery domain tuples, whose source/schema identities remain separately
recognized and byte-checked.

## Verification provenance

I did not rerun the reported focused commands: 23 integration/framing tests
in 136.26s, 26 pure `.075` tests in 0.08s, Ruff, or the 64-schema verifier.
The two probes in this review were disposable: a temporary valid Plan payload
with its descriptor removed (cleaned by `TemporaryDirectory`) and the
no-write malformed nested-request validator call above. No production or test
file, provider/network service, private user data, wheel, activation, or
commit was used.

Still outside this re-review are UI/question iteration, user-ready document
selection/publication, application finalization, provider execution,
package/wheel proof, and semantic factuality or hiring/ATS claims.
