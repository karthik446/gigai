# SCOUT-08 Run integration implementation

**Status:** bounded implementation evidence; offline only

This slice registers the `tailor-application` graph as a v2 closed domain and
completes the public Plan -> start Run -> checkpoint -> submit -> exact replay
flow over explicitly selected G45 `run_input` records. The selected records
are the posting, candidate resume/evidence, and one JSON tailoring-request
input. The request is therefore part of the sealed Plan rather than mutable
submit-time caller state.

## Source and compiler version

Adding the fixed `.075` source/schema inventory members advances the bundled
Scout candidate from `1.3` to `1.4` and the candidate compiler from
`scout-candidate-compiler:4` to `:5`. Existing materialized instances remain
historical; the source compiler refuses an inventory whose definition,
compiler, digest, or member bytes do not match the pinned authority.

The output contract has one closed v2 domain field, `tailoring`, bound to
`urn:gigai:scout:tailoring-packet:1`, the fixed `scout_tailoring` bridge, and
the exact inventoried `.075` `tailoring.py` and `tailoring.schema.json` bytes.
The only required completion check is `tailoring-completion`.

## Exact source-byte path

At checkpoint and submit, `external_recording` resolves every declared source
role to the selected input's authenticated G45 `snapshot_ref` while holding
the same journal writer. It rejects missing/foreign/changed refs, mixed input
families without a G45 snapshot, duplicate source IDs, and a missing or
ambiguous request input. The tailoring bridge receives these authenticated
bytes; sidecar `source_artifacts.content_base64` is checked only as a digest
and byte-equality assertion, never as source authority.

The bridge dispatch table, Graph Set contract validator, schema binding, and
renderer import are all closed literal mappings. No arbitrary import, path,
network lookup, provider call, validator bypass, auto-approval, application
event, outbound submission, or ATS/hiring truth claim is introduced.

## Bundle packaging

The public output is one logical `tailoring` artifact. Its Markdown bytes use
a deterministic length-delimited framing (`# Scout tailoring bundle`) for the
requested `resume` and/or `cover_letter` documents. The fixed bridge decodes
the exact document bytes, validates them with `.075`, and records one generic
output sidecar plus the strict tailoring domain sidecar. The domain sidecar
contains the requirement/evidence matrix, claims, gaps, questions, per-document
checks, selected input refs, and run origin; original imported G45 records are
not rewritten.

## Verification

Focused integration evidence is in `tests/test_scout08_run_integration.py`:

- resume-only success and exact submit replay;
- both-document success;
- no-publication refusal for missing tailoring inputs.

The existing `.075` packet tests continue to cover changed draft bytes,
unsupported claims, source mismatch, unsafe Markdown, and exact check
regeneration. This evidence is offline fixture evidence only: it does not prove
provider execution, activation, UI flow, question-round-trip/final-selection,
application finalization, or semantic factuality/ATS outcomes.

Remaining gates are the coordinator's combined SCOUT-08 review, full UI and
interview/question-round-trip slices, and any separately authorized provider
dogfood or activation work.

## Run-integration tightening evidence

The owned integration file now requires exact submit replay: `replay.created`
must be `False`, the replay payload must equal the original receipt at the
decoded value level, and the journal HEAD and complete artifact map must be
unchanged. The both-document case now creates a legitimate successor Run
after the initial receipt with a changed resume draft; it asserts new
draft/document and regenerated-check hashes, changed bundle bytes, and
preservation of every pre-successor artifact byte.

Malformed producer/domain cases are exercised through the public fixed bridge
and must fail before publication with precise guards: unsupported included
claim (`tailoring_domain_invalid`), source artifact bytes inconsistent with
committed selected inputs (`tailoring_packet_artifact_mismatch`), wrong Run
origin (`tailoring_domain_origin_mismatch`), unsupported domain
(`external_domain_unsupported`), ambiguous pinned request
(`tailoring_input_mismatch`), and requested-output mismatch
(`external_authority_mismatch`). Each case snapshots the journal HEAD and
artifact map and requires both to remain unchanged. The fixtures use only
disposable public approval setup and imported G45 input snapshots; they do not
bypass source validation or use private user data.

### Newly run focused evidence

Exact command and output:

```text
rtk .venv/bin/pytest -q tests/test_scout08_run_integration.py
8 passed in 99.37s (0:01:39)

rtk ruff check tests/test_scout08_run_integration.py
[]
```

Prior bounded evidence is retained as provenance, not rerun for this tightening
slice: `rtk .venv/bin/pytest -q tests/test_scout05_source_bundle.py
tests/test_scout06_source_contract.py tests/test_scout08_tailoring_packet.py
tests/test_scout08_run_integration.py` previously reported 62 passed in 25.66s;
the affected-source integration command covering discovery/research/reuse and
this file previously reported 58 passed in 221.38s (0:03:41). Those prior runs
predate the present replay/successor/malformed-producer assertions and are not
claimed as current reruns.

## Request-before-Run and canonical-framing corrections

The tailoring Plan path now identifies exactly one canonical
`scout-tailoring-request:1` among explicitly selected `g45_run_input` records
while holding the existing journal writer snapshot. The sealed Plan carries a
strict `tailoring_request` descriptor with the resolved input index,
`run_input_id`, record ref, and snapshot ref; start, checkpoint, and submit
redeem that exact descriptor and never rescan selected JSON shape. The request
validator bounds bytes, requires canonical JSON, closes top-level keys and
source-role structure, rejects duplicate role IDs/indices, and prevents the
request input from also serving as posting/candidate evidence; legacy Plan
schemas remain version-aware and do not gain a global required field.

The bundle decoder now accepts only bounded ASCII canonical decimal length
tokens: signs, leading zeroes, whitespace, Unicode digits, oversized values,
duplicate/missing markers, and trailing bytes refuse while payload bytes that
resemble literal document headers round-trip byte-for-byte.

New public no-publication coverage includes missing, duplicate, wrong-family,
source-role reuse, foreign, and changed request inputs, plus requested-output
mismatch. Existing resume-only, both-document changed-draft successor, exact
replay, and malformed producer tests remain passing. The new focused combined
command reported `23 passed in 136.26s (0:02:16)` for
`rtk .venv/bin/pytest -q tests/test_scout08_run_integration.py
tests/test_scout08_request_framing.py`; the pure `.075` packet file separately
reported `26 passed in 0.08s`. Ruff reported `[]` over the changed Python
files, and `verified 64 installed GigAI schemas` from the one allowed schema
inventory check. No provider, network,
private-user-data, wheel, full-suite, or historical 62/58 suite rerun is
claimed.

## Nested request-shape correction

The remaining F1 gap is closed at the fixed tailoring request validator. Plan
sealing now recursively closes each supplied posting reference, candidate
evidence reference, claim evidence reference, and gap/question source
reference: only the inventoried span keys are accepted, source IDs must use
the fixed grammar, byte offsets are bounded and strictly integer (JSON booleans
are not accepted), and quote digests use the fixed `sha256:` form. Collections
and nested draft evidence are bounded, duplicate identities are refused where
applicable, and the validator performs no draft membership, quotation, or
factuality check before a draft exists; those remain checkpoint checks.

Public Plan tests exercise malformed posting, candidate, claim, gap, and
question nested references through the fixed broker path. Every refusal uses
`tailoring_input_invalid` and leaves the journal HEAD and artifact map
unchanged; a valid nested request still seals a Plan and starts a Run with the
exact request descriptor. The fixtures contain only disposable public approval
records and imported G45 input snapshots.

New focused evidence:

```text
rtk .venv/bin/pytest -q tests/test_scout08_nested_request.py
11 passed in 43.52s

rtk .venv/bin/pytest -q tests/test_scout08_nested_request.py tests/test_scout08_run_integration.py
24 passed in 175.41s (0:02:55)

rtk ruff check src/gigai/scout_tailoring.py src/gigai/external_recording.py tests/test_scout08_nested_request.py
[]
```

This remains bounded offline public-fixture evidence. It does not establish
draft semantic truth, provider execution, UI/question round-trip, document
finalization, activation, outbound submission, or ATS/hiring outcomes.
