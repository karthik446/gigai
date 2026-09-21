# SCOUT-08 nested request-shape correction

Date: 2026-09-10. This bounded correction closes the remaining F1 nested
request validation gap before tailoring Plan publication. It preserves the
accepted sealed `tailoring_request` descriptor and canonical `.075` bundle
decoder; it does not alter inventoried `.075` bytes or broaden authority.

## Correction

The fixed `scout_tailoring` request validator now recursively closes and
bounds every pre-Run reference family supplied by the request:

- requirement `posting_ref` and `candidate_evidence_refs`;
- claim `evidence_refs`;
- gap and question `source_refs`; and
- optional draft-evidence descriptors, limited to structural shape only.

Each source span has exactly the known keys (`source_id`, `start_byte`,
`end_byte`, and `quote_sha256`), a fixed source-ID grammar, practical bounded
byte offsets, and a lowercase `sha256:` digest with exactly 64 hexadecimal
characters. Integer fields use strict integer checks so JSON booleans are not
accepted; per-collection bounds and duplicate span identities are enforced,
and requirement/claim/gap/question identities are unique in their respective
collections. Unknown nested keys, malformed digests, invalid ranges, foreign
shapes, and overlarge collections refuse as `tailoring_input_invalid`.

The validator intentionally does not check draft membership, quote bytes, or
factuality before a draft exists. Those checks remain at checkpoint, where
authenticated selected source bytes and the generated draft are available.

## Public sealing evidence

`tests/test_scout08_nested_request.py` sends five malformed nested request
families through the public fixed `tailor-application` Plan path: posting,
candidate, claim, gap, and question references. Each refusal is precise
(`tailoring_input_invalid`) and asserts unchanged journal HEAD/artifact state,
with no Plan or Run publication. The positive case uses the same disposable
public G45 posting, candidate, and canonical request inputs, then successfully
seals a Plan and starts a Run whose descriptor pins request input index 2.

The new test file also contains pure validator parameterization, but the
public broker cases are the authority-path evidence; helper-only tests are not
counted as public sealing proof. All bytes are disposable public fixtures and
no provider, private user Gig, activation, or authority bypass is used.

## Verification record

Exact focused commands and outputs:

```text
rtk .venv/bin/pytest -q tests/test_scout08_nested_request.py
11 passed in 43.52s

rtk .venv/bin/pytest -q tests/test_scout08_nested_request.py tests/test_scout08_run_integration.py
24 passed in 175.41s (0:02:55)

rtk ruff check src/gigai/scout_tailoring.py src/gigai/external_recording.py tests/test_scout08_nested_request.py
[]
```

The combined run retains the previously covered valid resume-only and
both-document flows, changed-draft successor checkpoint, exact submit replay,
and request/framing protections. No broad suite, package/wheel build,
provider/network operation, schema inventory rerun, private-user-data test,
or commit was performed for this correction.

## Remaining limits

This evidence does not claim semantic factuality, ATS or hiring outcomes,
draft content truth, application finalization, outbound submission, UI or
question-round-trip/final-selection coverage, provider execution, activation,
or package proof. Draft membership and quote-byte verification remain bounded
to checkpoint-time authenticated source checks.

## Follow-up: scalar enum guards and bounded-reference matrix

The request validator now checks that `assessment`, `status`, and
`document_kind` are strings before closed-set membership. This keeps malformed
JSON arrays and objects on the typed `tailoring_input_invalid` path instead of
leaking Python `TypeError`; the guard is limited to the request validator and
does not alter the fixed `.075` renderer or inventoried bytes.

The focused tests add a JSON-type matrix over null, boolean, number, object,
array, and invalid string replacements for all three enum fields. They also
exercise an overlarge nested reference list, an out-of-range offset, a bad
digest, and duplicate reference identity, plus a public Plan refusal for an
unhashable `assessment` value that asserts exact `tailoring_input_invalid` and
unchanged journal state. The existing valid nested Plan/Run test remains in
the bounded command.

Exact commands and results for this follow-up:

```text
rtk .venv/bin/pytest -q tests/test_scout08_nested_request.py
34 passed, 1 failed in 49.93s
```

The exploratory full-file run above failed only because the newly authored
matrix used `"unsupported"`, which is a valid claim status; the test value was
corrected to `"not-an-enum"`. The requested bounded rerun after that correction
was:

```text
rtk .venv/bin/pytest -q tests/test_scout08_nested_request.py::test_nested_enum_json_types_refuse_without_type_error tests/test_scout08_nested_request.py::test_nested_reference_bounds_and_identity_guards 'tests/test_scout08_nested_request.py::test_nested_request_shapes_refuse_public_plan_without_publication[assessment]' tests/test_scout08_nested_request.py::test_valid_nested_request_seals_plan_and_starts_run
24 passed in 14.54s

rtk ruff check src/gigai/scout_tailoring.py tests/test_scout08_nested_request.py
[]
```

The full 35-case file was not rerun after the test-only correction, and no
combined 175-second integration suite was repeated. No provider, private Gig,
activation, package build, or commit was involved.
