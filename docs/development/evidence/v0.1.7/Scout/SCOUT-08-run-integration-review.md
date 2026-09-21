# SCOUT-08 initial tailoring Run integration — independent review

Date: 2026-09-10

## Verdict

**Blocked for SCOUT-08 Run-integration acceptance.** The fixed bridge and
normal v2 recording path preserve important closed authority properties, but
two directly reproduced gaps violate the frozen requirement that the tailoring
request be an exact sealed G45 input before a Run and that logical bundle
framing be canonical. This is a source/probe review only; the concurrently
changing `tests/test_scout08_run_integration.py` is not treated as final
acceptance evidence.

## Accepted subset

The tailoring domain is a closed v2 Graph Set member. `graph_set.py` admits
only the literal tailoring schema/validator pair and strict nested refs, while
`external_recording._validate_domain_binding` checks the approved schema and
source bytes against the fixed package before validation. The v1.4 template /
compiler:5 source inventory includes the `.075` source and schema, and its
normal inventory reconciliation refuses changed member bytes; retained
research and discovery pairs remain in the same closed dispatch table.

At checkpoint and submit the ordinary public recording path is retained:
typed domain transport is decoded, the fixed validator is chosen only from the
closed schema ID table, the normal output/check contracts are enforced, and
there is no tailoring-specific success, submit, or finalization bypass.
`_tailoring_source_bytes` uses the sealed selected-input index and its
authenticated G45 `snapshot_ref` under the writer-held snapshot. The base64
copies in the sidecar are consequently only equality/digest evidence: the
bridge passes the committed source bytes into `.075`, which rechecks source
inventory, role membership, spans, document checks, and the independently
reviewed HTML/link safeguards. The origin and whole selected-input tuple are
also canonical-equality checked against trusted Run/Plan values.

One packaging boundary is correctly narrow: the output is one logical
`tailoring` Markdown bundle, but `decode_tailoring_bundle` can recover exact
document bytes. That establishes provisional storage/recovery only; it is not
a user-ready standalone resume/cover-letter publication, UI flow, selected
final document, or application effect. No finalization/applied side effect was
found in this slice.

The `.075` renderer imports `gigai.scout_checks`, which is outside the
Gig-owned `.075` inventory. It is a fixed installed-package dependency, not a
sidecar-controlled or Gig-path import, so it does not create an arbitrary-code
or source-authority bypass here. It does mean the v1.4 inventory is an
attestation of the package renderer/source pair rather than a complete
runtime-package execution lock; installed-package/wheel/version semantics
remain a separate deferred acceptance boundary and must not be inferred from
the source inventory alone.

## Blocking findings

### 1. The request is neither required nor typed as a request before Run

**Severity: blocking — violates the frozen request-before-Run authority
boundary.**

`plan_v2` resolves and seals generic input references but has no
`tailor-application` request-input gate. `_tailoring_request_bytes` runs only
at checkpoint/submit. A disposable public probe imported only posting and
candidate G45 run inputs, then called normal public `plan_v2` and `start_v2`
for `tailor-application`; both returned `created=True`. The temporary workpad
was removed by `TemporaryDirectory` after the probe. A later checkpoint would
refuse for missing request bytes, but that is after an active Run has already
been allocated, contrary to the requested product boundary.

The checkpoint resolver also accepts a request-shaped JSON snapshot from any
selected G45 family. An in-memory probe supplied only a sealed
`g45_reference` plus canonical request JSON to `_tailoring_request_bytes`; it
returned those bytes (`accepted_g45_reference=True`). Thus the code does not
require the documented explicit `g45_run_input`, and it can reinterpret a
selected record based on content shape instead of a sealed request role. The
sidecar cannot substitute arbitrary bytes — `_validate_request_bytes` requires
canonical equality with the selected bytes — but there is no Plan-sealed typed
role identifying that generic selected item as the request. The content-shape
search and later sidecar equality consequently occur only after the Run has
started. This does not meet the stricter “not a mutable sidecar role label /
not a resume reinterpreted as request” contract.

**Minimal fix direction:** at Plan sealing, for `tailor-application`, require
exactly one distinct `g45_run_input` whose committed snapshot is a canonical,
strict `scout-tailoring-request:1` request. Persist/reuse that resolved input
identity as part of the already sealed ordered inputs (or an explicit strict
request ref), require it to be disjoint from `source_roles`, and make
checkpoint/submit redeem exactly that sealed run-input rather than searching
all selected snapshots by JSON shape. Add public Plan/start refusal coverage
for absent, duplicate, `g45_reference`, wrapped Scout-record, source-role
reuse, foreign, and changed request inputs; every refusal should leave HEAD
and Run state unchanged.

### 2. Length-delimited bundle framing is not canonical

**Severity: medium, blocking for the stated canonical-framing claim.**

`decode_tailoring_bundle` uses `int()` for the declared byte length without
requiring its ASCII token to be canonical. A no-write in-memory probe changed
the encoder's `Byte length: 9` header to `Byte length: +9`; the decoder still
returned the exact resume bytes (`noncanonical_length_accepted=True`). The
decoder otherwise rejects extra bytes, missing requested documents, duplicate
kinds, and malformed payload delimiters, so this is a narrow alternate-wire
encoding rather than hidden document data.

**Minimal fix direction:** require an ASCII decimal token with no sign,
whitespace, or leading zero (except `0`, which remains invalid for a document)
before conversion, bound its digit length, and add direct bridge tests for
`+9`, `09`, whitespace, duplicate/missing markers, and payload bytes that
resemble a document header. Preserve the existing byte-exact document
recovery behavior.

## Evidence and remaining work

No 62/58 suite, pure `.075` 26-test lane, provider/network operation, private
user data, wheel build, activation, commit, or production/test edit was
performed in this review. The three bounded probes were: one in-memory
request-family check, one `TemporaryDirectory` public Plan/start lifecycle
probe (cleaned automatically), and one in-memory bundle-decoding check.

Before acceptance, implement the two narrow fixes and add focused public
Plan/start/checkpoint/submit regressions. Still outside this review remain the
frozen M1 question-driven successor iteration, UI/question round trip,
user-ready document selection/publication, application-event/finalization,
installed-package or wheel proof, provider execution, and semantic factuality
or hiring/ATS claims.
