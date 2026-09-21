# SCOUT R7 rebuilt candidate verification — 2026-09-21

Status: **candidate verification complete; release is not accepted or
approved for publication.** The rebuilt local artifacts, dependency-complete
installed lane, synthetic Scout smoke, and one full offline source matrix are
recorded below. No source/test/tool/version/CI correction, provider/model call,
commit, tag, push, upload, activation, or real-user-data access was performed.

## Frozen source and provenance

The checkout remained at branch `karthik446/gigai-v0.1.7`, HEAD
`fda48574f8642e66c0e7d53e7303ec04f04d7bb8`, with the pre-existing large dirty
tree preserved. `pyproject.toml` and the editable GigAI record in `uv.lock`
both verified as `0.1.7` before tests (`tools/release_check.py verify-lockfile`
exit 0). The source manifest captured after the build and before tests is
`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/source-before-tests.json`;
it contains SHA-256 and byte-size records for 1,225 non-private files and
explicitly excludes `.git/**` and all `.gigai/**` workspace/data. The final raw
manifest is
`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/source-after-tests.json`.

Raw comparison reports an exit-1 difference because concurrent permitted
evidence/fallback work added or changed documentation/research artifacts while
this lane ran, including the final input report, release-push report, live
comparison preparation notes, and `research/orchestration` fallback state. A
path-by-path comparison found **no difference under `src/**`, `tests/**`,
`tools/**`, `.github/**`, `pyproject.toml`, `uv.lock`, or setup metadata**. The
raw status is retained at
`logs/source-drift-after-installed.status` and
`logs/source-drift-after-tests.status`; the concurrent paths remain visible in
the manifests and were not reverted or hidden.

## Rebuilt artifacts

Artifacts were built once with `uv build --out-dir` into a new task directory,
not the prior candidate directory. Release metadata, artifact metadata, and
the generated checksum manifest all passed:

| Artifact | SHA-256 | Size |
|---|---|---:|
| `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/dist/gigai-0.1.7-py3-none-any.whl` | `ed3b3336d8f4d0bd49f9175c1e216ccafcc7e20583ed94d1ccb550679b0a59d3` | recorded in `artifact-members.json` |
| `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/dist/gigai-0.1.7.tar.gz` | `fb6158b0399ef61734e43fc7ac6308271178125100d25b0ce7cf397eb628e3c7` | recorded in `artifact-members.json` |

The artifact member inventory is
`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/artifact-members.json`:
224 wheel members and 246 sdist members were inspected, with **zero** member
paths containing `.gigai`. The artifacts retained these exact hashes after the
matrix. Raw build, release-check, inventory, and checksum logs are in the
task directory `logs/`.

The exact wheel was installed with dependencies (not `--no-deps`) into
`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/exact-wheel-venv`.
From cwd outside the checkout, `python -I` reported `gigai 0.1.7` from that
venv's `site-packages/gigai/__init__.py`; this is recorded in
`logs/exact-import-identity.stdout.log`. The readiness handoff for a separately
bounded model comparison is
`SCOUT-R7-rebuilt-candidate-artifact-20260921.md`; readiness is not release
acceptance.

## Installed wheel and sdist verification

All 22 CI-required installed verifier scripts from the pull-request workflow
completed with exit 0: schemas, canonical, CLI, G03, G04, G05, G06, G07, G08,
G09, G11, G13, G14, G15, G16, G17, G19, G20, G21, G23, G27, and G28. Their
individual raw stdout/stderr/status files are `logs/installed-*.{stdout,stderr}.log`
and `.status` in the task directory. The installed scenario selection from CI
completed **35 passed, 20 deselected** with JUnit at
`logs/installed-scenarios.junit.xml`.

The corrected exact-wheel synthetic API smoke passed with `scout_status:
approval_required`, materialized candidate setup/init, four public rows
processed to `acquisition_complete: true`, and report generation/status with
`stale: false`. Its raw output is in
`logs/installed-scout-api-smoke-rerun.*`; the first disposable command-shape
attempt used an invalid `init --workpad-root` option and is retained separately
as `logs/installed-init.*` (exit 2), while the corrected explicit-target init
passed in `logs/scout-init.*`. No private rows or model/provider transport was
used.

The sdist was installed with dependencies into
`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/sdist-venv`.
Version and non-interactive setup smoke passed (venv, install, and smoke exit
0); raw evidence is `logs/sdist-*.log` and `logs/sdist-status`.

## Full offline source matrix

Loopback preflight succeeded before testing. The locked Python 3.11 matrix
environment was used with `GIGAI_G30_UAT=0`; no provider/model tests were
enabled. The required source command ran exactly once and finished with:

```text
1566 collected
1564 passed, 1 skipped, 1 warning, 1 failed
duration 3208.71s (0:53:28), exit 1
```

Durable raw evidence:

- stdout: `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/logs/full-source-matrix.stdout.log`
- stderr: `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/logs/full-source-matrix.stderr.log`
- JUnit: `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/logs/full-source-matrix.junit.xml`
- status/provenance: matching `.status` and `.provenance` files

The sole failure is:

```text
tests/test_scout05_capability_review.py::test_review_refuses_before_writing_without_confirmation_or_boundary[operator_confirmed-False-capability_review_operator_consent_required]
```

At `tests/test_scout05_capability_review.py:352`, the refusal path's
before/after workpad listing differed: the after listing included
`ui/template.html` and a temporary Git pack path. The full traceback and exact
assertion are retained in the stdout log. This is a concrete retained source
matrix failure, not excluded or classified away, and no source fix was made.
The one skipped test is the explicitly gated `tests/test_g30_live_cli.py` case;
no live runtime was invoked.

## Debian and platform gates

Docker availability was checked once. The Docker client is present, but the
`desktop-linux` daemon socket is unavailable (`.../.docker/run/docker.sock`),
so the existing Debian offline workflow was not started and no unrelated
daemon was launched. The gate is recorded in
`docker-environment-gate.txt` and `logs/docker-availability.*`; no Docker
product result is claimed.

This is local macOS Python 3.11 evidence only. The CI-required future exact-tag
matrix remains Ubuntu/macOS × Python 3.11, 3.12, and 3.13; no Linux, Windows, or
Python 3.12/3.13 result is established by this run.

## Release and UAT handoff

The current dirty checkout and local artifacts are not an immutable release
commit. The authorized workflow in `.github/workflows/release.yml` requires a
matching annotated `v0.1.7` tag, exact-tag CI using the full Ubuntu/macOS and
Python 3.11/3.12/3.13 matrix, artifact build/checksum/provenance and smoke,
then named `testpypi` and `pypi` publication environments. TestPyPI and PyPI
clean-install verification precede the GitHub release. None of those
publication, tag, commit, push, upload, or remote-environment gates was run.

The next authorized handoff is therefore: retain the exact artifact and all
raw logs, investigate/correct the one capability-review refusal side effect in
a new source cycle, rebuild/reconcile the candidate, then use the proper
authorized exact-tag workflow. After publication, perform normal disposable
install and the synthetic-first `SCOUT-12-user-uat-checklist.md`; personal UAT,
provider choice, activation, and live comparison remain separate later gates.
