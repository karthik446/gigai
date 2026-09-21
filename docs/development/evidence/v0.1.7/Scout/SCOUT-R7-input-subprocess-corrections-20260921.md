# SCOUT R7 input and subprocess corrections — 2026-09-21

Status: **bounded synthetic source correction complete; not release acceptance.**

This correction resolves R7-CR-01 while preserving the accepted public Run Plan
classification surface and the separate text-only provider-review consumer. It
also batches every fixed-argv `subprocess.run` site that omitted a literal
`shell=False` in the current `src/gigai` tree. The unrelated dirty G43/runtime,
inventory, and other work was preserved; no provider/model call, private-data
access, activation, publication, model download, commit/reset, or full-suite
run was performed.

## Ordinary input contract

`src/gigai/run_plan.py` now separates `_ordinary_artifact()` from the existing
`_text_artifact()` helper. Ordinary `create_run_plan` inputs retain the
pre-regression byte-oriented contract: every explicit regular non-symlink file
is read as exact bytes, including opaque binary data, with no content decode or
new suffix allowlist. `.md`, `.markdown`, and `.txt` receive deterministic
`text/markdown` or `text/plain` media identities without consulting the host
MIME database; every other suffix remains accepted with stable
`application/octet-stream` fallback.

Requirements-baseline and review-subject ingestion continue using
`_text_artifact()`. Those deliberately text-only lanes still require the
deterministic text suffix mapping and valid UTF-8, so invalid UTF-8 and a code
suffix remain baseline/review-subject refusals. Provider review retains its
downstream gate: unsupported sealed media is refused before provider execution;
ordinary plan creation does not silently narrow the accepted `code`,
`structured_data`, or `mixed` classifications.

The dedicated public `create_run_plan` regressions cover:

- `code_review` plus `code` with a `.py` input, including opaque bytes;
- `code_review` plus `structured_data` with a `.json` input;
- `code_review` plus `mixed` with `.py` and `.json` inputs;
- exact digest and byte-size sealing for each accepted input;
- deterministic `.md`, `.markdown`, and `.txt` media;
- unknown-suffix ordinary acceptance with opaque media;
- baseline refusal for non-text suffixes and invalid UTF-8; and
- generic ordinary acceptance of invalid UTF-8 without forced decoding.

Historical sealed Plans, approvals, consent, source path checks, exact bytes,
digest/size identity, and provider-review authority boundaries were not
changed. Existing sealed Plans are not reclassified or rewritten.

## Subprocess shell lint

The AST inventory used the same diagnostic contract as
`tests/test_setup_configuration_diagnostics.py::test_product_subprocesses_are_literal_argv_with_shell_disabled`:
every `subprocess.run` call in `src/gigai` was enumerated, and fixed literal
argv calls were checked for a literal `shell=False`. Before this correction the
complete violation set was exactly:

```text
src/gigai/application_events.py:86
src/gigai/application_events.py:97
src/gigai/application_events.py:111
```

Those three journal `git log`/`git show` calls now carry only the missing
`shell=False` keyword. Their argv, capture/text/check behavior, and journal
identity logic are unchanged. The post-change AST inventory reports no
fixed-argv `subprocess.run` violations; no explicit `shell=True`, dynamic argv,
timeout, environment, permissions, capabilities, or command content was
altered.

## Focused verification

All execution used disposable synthetic fixtures and injected/offline
transports:

```text
rtk proxy .venv/bin/pytest -q tests/test_g43_text_media.py tests/test_g43_run_plan.py
=> 20 passed in 29.03s

GIGAI_G30_UAT=0 /private/tmp/gigai-r7-candidate-20260920/matrix-venv/bin/python -I -m pytest -q \
  tests/test_g43_provider_review.py tests/test_g43_provider_run_status.py \
  tests/test_jsl_closeout_regressions.py tests/test_g43_text_media.py
=> 53 passed in 126.12s (0:02:06)

rtk proxy .venv/bin/pytest -q \
  tests/test_setup_configuration_diagnostics.py::test_product_subprocesses_are_literal_argv_with_shell_disabled \
  tests/test_scout09_application_events.py
=> 13 passed in 22.03s

rtk ruff check src/gigai/run_plan.py src/gigai/application_events.py tests/test_g43_text_media.py
=> clean

rtk .venv/bin/python -m py_compile \
  src/gigai/run_plan.py src/gigai/application_events.py tests/test_g43_text_media.py
=> passed

git diff --check
=> passed
```

The coordinator independently passed the five earlier loopback-refused tests
(`5 passed in 7.86s`, `coordinator-loopback-20260921.xml`); they are not
pending source failures in this correction. That evidence remains separate
from this worker's local synthetic proof.

## Remaining release gates

This report establishes only the bounded source slice above. Rebuilt-wheel
verification is still required because source changes are not installed-wheel
proof. Source-matrix reconciliation, installed-wheel acceptance, live-provider
proof, Linux/Windows coverage, Docker, publication, and UAT remain separate
gates; no release or activation claim is made here.
