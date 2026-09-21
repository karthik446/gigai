# SCOUT R7 release integration — 2026-09-21

## Result and scope

The reviewed scratch correction was integrated into the current dirty v0.1.7 checkout and passed the user-authorized bounded release gates. Only `src/gigai/lifecycle.py`, `src/gigai/cli.py`, `tests/test_runtime_comparison.py`, and this new report were written by this task; unrelated dirty work, existing reports, schemas, resources, build metadata, workpads, and private history were preserved. No provider/model call, daemon startup, activation, commit, tag, push, upload, publication, version change, or full matrix was run.

The approved input patch was verified before application:

```
/private/tmp/gigai-r7-public-binding-correction-20260921/SCOUT-R7-public-binding-correction-transfer.patch
SHA256 9b4b59af4a437403d49921f7968a55d35c8f95602b7feca9c939b653a2a56f25
```

The three main preimage hashes matched the scratch input manifest before the transplant (`lifecycle.py` `ca4dd38257aeb18496d75c13d7ab86958f263a14b81243c8d340acd3ab675a1f`, `cli.py` `63c6989708c1a75ed4f2546e66025626b3e7cba6d341ba1241c4a29637c77f8b`, and `test_runtime_comparison.py` `2faa0863f3b228ae49ad9a8fadb616df380d1af14d58dffc71d9d77e9fbee747`). The reviewed diff was applied with `apply_patch`, not a checkout reset or wholesale copy. Post-integration hashes are:

```
src/gigai/lifecycle.py           f3e29a6713e7877c93129e67dbbd33de8894f91f35d142d8ca6996b611f4320c
src/gigai/cli.py                 297751e44bb9fc1776100410ebebe66d19a180767734639ceb1f77f326952de8
tests/test_runtime_comparison.py 04bc9963ba76b30102e83392f88d4b110fac9f1989dc7de2d8d18bfed268ae8f
```

All three postimages compare byte-for-byte with the approved scratch copy.

## Debian isolation gate

Before main modification, the coordinator supplied positive Orca-coordinated evidence for Debian worker `ctx_657a3c8ef2d5` / terminal `term_9231d753-9be1-4800-903c-aae970d90e8e`:

```
container=ecf9c4a33810 image=gigai-debian-offline-r7-final-kkbodn
Mounts=[] image=sha256:a155e8abfc8cfa7b801f082fe82d6599af523b5ddec223ab3b522744c86e6d68 WorkingDir=/workspace
```

The container used image-contained source with no host bind mounts. This exact output is retained at `/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/logs/docker-context.txt`. The Debian worker was not awaited after this gate, per the narrowed task.

## Integrated targeted checks

The one combined targeted production command was:

```
.venv/bin/pytest -q tests/test_runtime_comparison.py \
  tests/test_scout02_graph_set_flow.py \
  tests/test_scout05_first_proposal.py \
  tests/test_g08_offline_create_lifecycle.py::test_rejection_is_terminal_and_does_not_create_an_active_version
```

Result: `45 passed in 140.92s (0:02:20)`.

Changed-surface checks passed:

```
ruff check src/gigai/lifecycle.py src/gigai/cli.py tests/test_runtime_comparison.py
All checks passed!
.venv/bin/python -m compileall -q src/gigai/lifecycle.py src/gigai/cli.py tests/test_runtime_comparison.py
passed
git diff --check -- src/gigai/lifecycle.py src/gigai/cli.py tests/test_runtime_comparison.py
passed
```

The first direct `python -m build --no-isolation` attempt was not used because the installed build 1.2.2 validator rejects the existing PEP 621 shorthand `project.license = "Apache-2.0"` as a pre-existing metadata/tool mismatch. The repository workflow uses `uv build`; that supported path succeeded below and no metadata was changed.

## Integrated artifacts and package inspection

The new disposable build directory is `/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/`. It was built once with:

```
uv build --out-dir /private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/dist
```

Artifacts:

```
/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/dist/gigai-0.1.7-py3-none-any.whl
SHA256 da75dde1f9672e6e096ed4fed3f78da8b1915c6ce6b104174d46c9c2354b527e

/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/dist/gigai-0.1.7.tar.gz
SHA256 d89629aaa301432b65a003ffe91184887462d68ff26493e682393bacd512e0e3
```

`tools/release_check.py --dist ... verify-artifacts`, `write-checksums`, and `verify-checksums` each passed. The wheel has 224 members and the sdist 246; inventory checks found no `.gigai` workspace paths, `.env` files, or `credentials.json`, `secret.json`, or `token.json` state files. Product module `gigai/credentials.py` is ordinary shipped source, not private credential state, and contains no credential bytes. Extracted packaged lifecycle and CLI members match the post-integration hashes above; tests remain intentionally outside the distribution.

Checksum manifest:

```
da75dde1f9672e6e096ed4fed3f78da8b1915c6ce6b104174d46c9c2354b527e  gigai-0.1.7-py3-none-any.whl
d89629aaa301432b65a003ffe91184887462d68ff26493e682393bacd512e0e3  gigai-0.1.7.tar.gz
```

## Fresh installed checks and Scout smoke

The wheel was installed with dependencies (including pytest and pydantic) into the fresh environment `/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/venv` using `uv pip install`, with no shared environment changes. From `/private/tmp`, isolated import identity was:

```
version 0.1.7
/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/venv/lib/python3.12/site-packages/gigai/__init__.py
```

Installed checks passed:

```
tools/verify_installed_cli.py       verified installed CLI surface
tools/verify_installed_schemas.py   verified 82 installed GigAI schemas
tools/verify_installed_g03.py       verified setup, idempotency, standard pack, offline doctor
tools/verify_installed_g08.py       verified installed offline proposal lifecycle
```

The raw outputs are retained under `/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/logs/installed-*.log`.

Installed public Scout acquisition used synthetic rows through the supported CLI path and fresh status reload:

```
cd /private/tmp
venv/bin/pytest -q .../tests/test_scout_acquisition_progress.py::test_normal_cli_import_and_fresh_status
1 passed in 1.78s
```

Installed public Scout report used the shipped Scout template/CSS assets, synthetic journal-backed data, deterministic offline transport, and fresh report read:

```
cd /private/tmp
venv/bin/pytest -q .../tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey
1 passed in 223.22s (0:03:43)
```

Its raw output is `logs/installed-scout-report.log`; acquisition is `logs/installed-scout-acquisition.log`. No real resume, preference, repository secret, provider, or network data was used.

Installed production comparison/rejection smoke ran five selected regressions from a fresh process environment and passed:

```
cd /private/tmp
venv/bin/pytest -q .../tests/test_runtime_comparison.py \
  -k 'first_graph_proposal or reject_preserves_active or optional_output_canonical or false_optional or historical_v1_pending'
5 passed, 23 deselected in 15.46s
```

This covered explicit Gig rejection, wrong-Gig/proposal refusal and legacy compatibility, authenticated optional canonical output reference preservation, false-digest refusal before publication, and fresh v2 binding. Raw output is `logs/installed-comparison-reject.log`. The installed CLI version and import path were independently checked outside the checkout.

## Release handoff and limits

This candidate is suitable for the user-authorized personal-use bounded integration handoff, but no publication was performed. The comparison remains experimental synthetic/offline evidence: there was no live Qwen/Luna execution in this task, no model quality claim, and no broad claim from the prior full matrix. The earlier full matrix and its wheel are historical evidence for a different candidate; these new artifact hashes supersede it for this integrated source snapshot.

The concrete next release workflow is the existing `.github/workflows/release.yml`: after an authorized source commit and review, verify the exact annotated `v0.1.7` tag and lockfile, push that tag to trigger `preflight` and exact-tag full-matrix CI, then let the workflow run `uv build --no-sources`, `verify-artifacts`, `write-checksums`, `verify-checksums`, fresh wheel/sdist setup/doctor smoke, named `testpypi` publication and clean-install verification, named `pypi` publication and clean-install verification, and GitHub attestation/release creation. Those commit/tag/push/publication and remote environment gates are the only remaining release blockers in this bounded task; they were intentionally not executed here.

Raw command evidence and hashes are under `/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/logs/`.
