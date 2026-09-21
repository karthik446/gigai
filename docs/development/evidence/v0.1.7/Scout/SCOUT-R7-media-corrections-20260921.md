# SCOUT R7 deterministic text-media correction — v0.1.7

Date: 2026-09-21 (America/Denver)  
Status: **bounded synthetic source correction complete; not release acceptance.**

This correction addresses the G43/JSL source failure identified in
`SCOUT-R7-source-failure-triage-20260921.md`. It changes only the owned
Run-plan ingestion path and adds dedicated regression coverage; unrelated dirty
work, including G43 changes outside this lane, was preserved.

## Correction

`src/gigai/run_plan.py` now uses `_deterministic_text_media_type()` for both
requirements-baseline ingestion and ordinary input ingestion. The accepted
suffix mapping is case-insensitive and explicit:

| Suffix | Sealed media type |
|---|---|
| `.md`, `.markdown` | `text/markdown` |
| `.txt` | `text/plain` |

The host `mimetypes` database is no longer consulted. Ordinary inputs now use
the same existing `_text_artifact()` checks as baseline and review-subject
inputs: explicit regular non-symlink file, readable bytes, accepted suffix,
and valid UTF-8. The exact bytes are still used unchanged to derive
`content_sha256` and `size_bytes`; the deterministic media type is sealed in
the same reference. Existing journaled direct baseline approval, sealed-source
revalidation, provider-only execution, and authenticated terminal result rules
were not changed. Previously sealed Plans are not reclassified or mutated.

## Focused evidence

All tests used disposable synthetic fixtures and injected model transports; no
provider, model, network, personal `.gigai` records, activation, publication,
Docker startup, or model download was used.

Exact isolated Python 3.11.14 MIME probe:

```text
GIGAI_G30_UAT=0 /private/tmp/gigai-r7-candidate-20260920/matrix-venv/bin/python -I -c \
  'import mimetypes; print(mimetypes.guess_type("requirements.md")); print(mimetypes.guess_type("input.md"))'
=> (None, None)
=> (None, None)
```

The bounded acceptance suites were run with the locked matrix runtime and
`GIGAI_G30_UAT=0`:

```text
env GIGAI_G30_UAT=0 /private/tmp/gigai-r7-candidate-20260920/matrix-venv/bin/python -I -m pytest -q \
  tests/test_g43_provider_review.py tests/test_g43_provider_run_status.py tests/test_jsl_closeout_regressions.py
=> 40 passed in 122.07s (0:02:02)

env GIGAI_G30_UAT=0 /private/tmp/gigai-r7-candidate-20260920/matrix-venv/bin/python -I -m pytest -q tests/test_g43_text_media.py
=> 9 passed in 10.47s
```

The dedicated media tests cover `.md`, `.markdown`, and `.txt` deterministic
references; exact bytes/digest/size; unknown suffix refusal; and invalid UTF-8
refusal for both ordinary and baseline ingestion. The 40-test rerun shows the
previous media gate no longer short-circuits the owned provider-review,
provider-run-status, and JSL closeout assertions. It does not establish
whole-source-matrix health, installed-wheel acceptance, live provider proof,
or release authorization.

## Source identity

These hashes identify the current source files after the correction; they are
not old-candidate verification claims:

```text
ca27ebf887a4fe32e9a5a50893b49f8d8aae142e9d782973b567670814d9ae07  src/gigai/run_plan.py
16250463bce9b28af89dc4a2d634c19485500807da98243e3a7176c7a4146f39  tests/test_g43_text_media.py
```

`compileall` and `git diff --check` passed for the changed source/test paths.
The source matrix remains non-green per the triage report; its other listed
owners and environment-refused loopback cases remain separate gates.

The required Orca follow-up check returned `runtime_unavailable` because the
running Orca app was unreachable; this IPC failure did not block the bounded
source correction or its local evidence capture.
