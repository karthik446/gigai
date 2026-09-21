# SCOUT-R7 Debian fixture correction — 2026-09-21

## Scope and correction

The diagnosed Debian first failure was `tests/test_g28_setup_create.py::test_initialized_non_git_target_resolves_implicitly_from_its_directory`: its setup invocation supplied no model target, so the frozen CLI correctly refused setup when no host Codex/Claude/API runtime was available. Only this test was changed. Its substantive non-Git target initialization and implicit directory-resolution assertions are unchanged, and `test_setup_rejects_unconfigured_create_model_target` remains unchanged.

The corrected test now supplies the same supported explicit setup shape used by its neighboring test:

- credential reference `openai=environment:OPENAI_API_KEY` (a reference string only; no credential value is read)
- synthetic endpoint `openai=openai_api:openai:https://api.example.test`
- synthetic model target `remote=openai:gpt-test`
- explicit `--create-model-target remote`

This makes setup configuration explicit without executing a provider, making a network request, reading credentials, weakening runtime behavior, skipping the test, or applying a suite-wide mock.

## Focused current-environment verification

Command: `.venv/bin/pytest -q tests/test_g28_setup_create.py`

Result:

```text
...                                                                      [100%]
3 passed in 1.76s
exit=0
```

Raw focused evidence is retained under `/private/tmp/gigai-r7-debian-fixture-focus-jOV4tK/`:

- `pytest.stdout.log`
- `pytest.stderr.log` (empty)
- `pytest.status` (`exit=0`)

## Existing immutable Debian image verification

The exact previously failing test was run once against the existing image; no Docker rebuild occurred. The modified public test file was supplied as a read-only bind mount at `/workspace/tests/test_g28_setup_create.py`; no main source checkout or other source mount was used. Isolation remained `--network none`, `--read-only`, non-root `10001:10001`, and only the established writable `/tmp`, `/audit/home`, `/audit/target`, and `/audit/workpad` tmpfs roots.

Image:

- tag: `gigai-debian-offline-r7-final-kkbodn`
- digest: `sha256:a155e8abfc8cfa7b801f082fe82d6599af523b5ddec223ab3b522744c86e6d68`

The command preserved the Debian preflight, then ran:

```text
python -m pytest -q tests/test_g28_setup_create.py::test_initialized_non_git_target_resolves_implicitly_from_its_directory
```

Result:

```text
verified Debian offline non-root, network-isolated audit mounts
.                                                                        [100%]
1 passed in 0.75s
exit=0
```

Raw Docker evidence is retained under `/private/tmp/gigai-r7-debian-fixture-docker-KnXB6I/`:

- `run-command.txt` — exact bounded command, including read-only test-file mount
- `docker.stdout.log`
- `docker.stderr.log` (empty)
- `docker.status` — image digest and actual `exit=0`

## Artifact and release boundary

The candidate product artifacts were not rebuilt or changed. Their current SHA256 values remain:

- wheel `0371662823de1b963455f50a967d226c95cfe2fadbefe03557630b3f844ca5b7`
- sdist `11fb92ba696cc1ffc67808930857dee6c0c7f5f91f1972e5cc5f867ec638a9a5`

This evidence establishes the corrected focused test in the current environment and on the existing immutable Debian image only. It does not rerun or turn the prior Debian full-lane failures into a pass, and it makes no claim about the whole Debian lane, the corrected integration candidate, providers/models, or other release/platform gates.
