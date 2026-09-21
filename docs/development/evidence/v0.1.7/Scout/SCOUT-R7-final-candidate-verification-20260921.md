# SCOUT R7 final candidate verification — 2026-09-21

Status: **the exact frozen candidate passed the bounded local release matrix;
publication is not approved.** The full offline source matrix, exact-wheel
installed verifiers/scenarios, synthetic Scout smoke, and sdist smoke are
recorded below. No source, test, tool, schema, metadata, commit, tag, push,
upload, provider/model call, or private-data access was performed.

## Inputs, freeze, and evidence inventory

The current checkout was verified at branch `karthik446/gigai-v0.1.7`, HEAD
`fda48574f8642e66c0e7d53e7303ec04f04d7bb8`. `pyproject.toml` and the editable
GigAI entry in `uv.lock` both report version `0.1.7`; the read-only
`tools/release_check.py verify-lockfile` check passed. The five requested
release/correction reports and all Scout evidence documents were read. The
durable all-document inventory is
`/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/evidence-docs-inventory.json`:
241 files, 2,233,528 bytes, and 37,827 lines.

The pre-test non-private manifest is
`/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/source-manifest-before-tests.json`;
the completion manifest is `source-manifest-after-tests.json`. Both exclude
`.git/**` and `.gigai/**`. The scoped drift result is
`source-drift-scoped-summary.json`: generated virtual-environment/cache paths
are excluded from the drift comparison, protected runtime paths
`src/**`, `tests/**`, `tools/**`, `.github/**`, `pyproject.toml`, `uv.lock`,
and setup metadata had **no drift**, and concurrent changes were limited to
Scout evidence/research paths listed in that JSON.

## Exact artifact identity and source comparison

The supplied artifacts were used without rebuilding:

| Artifact | Exact path | SHA-256 |
|---|---|---|
| wheel | `/private/tmp/gigai-r7-contract-coherence/dist/gigai-0.1.7-py3-none-any.whl` | `0371662823de1b963455f50a967d226c95cfe2fadbefe03557630b3f844ca5b7` |
| sdist | `/private/tmp/gigai-r7-contract-coherence/dist/gigai-0.1.7.tar.gz` | `11fb92ba696cc1ffc67808930857dee6c0c7f5f91f1972e5cc5f867ec638a9a5` |

The wheel has 218 product package members and every product member's bytes
match the current `src/gigai/**` source, both before testing and at completion.
The sdist has 230 members; its checkout comparison has no product mismatch,
with the sole difference being generated `setup.cfg` metadata. Artifact
member and byte evidence is in
`/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/product-byte-compare.stdout.log`,
`product-byte-compare-after.stdout.log`, `wheel-product-members.json`, and
`sdist-product-members.json`.

Read-only release metadata checks passed for the exact candidate:

```text
tools/release_check.py verify-lockfile       PASS
tools/release_check.py --dist ... verify-artifacts PASS
tools/release_check.py --dist ... write-checksums PASS
tools/release_check.py --dist ... verify-checksums PASS
```

The generated checksum manifest is retained in the disposable
`release-dist/` directory and contains the two exact hashes above. The wheel
was installed with all 17 runtime dependencies into
`.../exact-wheel-venv` (Python 3.11.14); isolated import from outside the
checkout resolved to that environment's `site-packages/gigai/__init__.py`,
reported version `0.1.7`, and loaded `r7-output-contract:1`. The sdist was
similarly installed with 17 dependencies into `.../sdist-venv`; isolated
import, `gigai setup --non-interactive`, and `gigai doctor` all passed.

## Full offline source matrix

An ephemeral loopback preflight succeeded with HTTP 200 on `127.0.0.1`; no
external endpoint was contacted. The locked Python 3.11.14 matrix environment
was created with `uv sync --locked --extra test --python 3.11` and the source
suite was run **exactly once** with `GIGAI_G30_UAT=0`:

```text
1574 passed, 1 skipped, 1 warning, 269 subtests passed
1844 collected JUnit test cases; failures=0, errors=0, skipped=1
duration 3226.82s (0:53:46), exit 0
```

The one skip is the explicit G30 live-model gate (`GIGAI_G30_UAT=1`); no
provider or model was invoked. Raw evidence is retained at:

```text
/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/logs/full-source-matrix.stdout.log
/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/logs/full-source-matrix.stderr.log
/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/logs/full-source-matrix.status
/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/logs/full-source-matrix.junit.xml
/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/logs/full-source-matrix.provenance
```

The matrix includes the current capability-refusal correction tests and the
contract-coherence correction tests; both are therefore verified in the
current source without converting the prior `1564 passed, 1 failed, 1
skipped` result into a historical pass.

## Installed wheel CI checks

All 22 full-matrix installed verifiers passed against the exact wheel,
including schema/resource, canonical, CLI, G03–G09, G11, G13–G17, G19–G21,
G23, G27, and G28. Individual raw stdout/stderr/status files and the summary
are under
`/private/tmp/gigai-r7-final-candidate-verification-pSYvKY/installed-verifiers/`.

The CI-derived installed scenario selection ran once with
`GIGAI_TEST_EXECUTABLE` set to the exact wheel console script:

```text
35 passed, 20 deselected in 60.17s, exit 0
```

JUnit, raw streams, status, and provenance are in
`.../logs/installed-scenarios.{stdout,stderr,status,junit.xml,provenance}`.

## Synthetic installed Scout smoke

Using only disposable synthetic public rows and no model/private input, the
exact wheel completed the public Scout acquisition path on a disposable
Git-backed target. Four rows produced one considered row, one duplicate, one
recorded failure, and one exclusion; fresh acquisition status matched the
committed batch (`processed=4`, `stop_reason=completed`). Report generation
and fresh report status then passed with `stale=false`.

The final smoke logs are `log-scout3-{setup,catalog-install,init,import,
status,report-generate-final,report-status-final}.*` under the task directory.
Two earlier disposable setup attempts intentionally lacked a report UI source
and retained `report_source_missing`; they did not alter the checkout or
candidate and are not counted as product acceptance. The final target copied
only the shipped UI source into its disposable workpad before the successful
report generation.

## Docker, release workflow, and remaining gates

The single Docker availability check succeeded (Docker Desktop server was
available). The existing Debian workflow was attempted once, but the build
did not start because the disposable image tag derived from the task directory
contained uppercase `K` and Docker rejected it as an invalid lowercase
repository tag. Raw evidence is `docker-availability.*`,
`docker-debian-build.{stdout,stderr,status}`. No daemon was started and the
Debian workflow therefore has no result.

The release workflow prerequisites were inspected read-only. Exact-tag CI on
Ubuntu/macOS with Python 3.11/3.12/3.13, annotated `v0.1.7` tag provenance,
TestPyPI/PyPI named environments, attestations, publication, clean-index
installs, and personal UAT remain unproven and were not attempted. The
parallel worker's real local/subscription comparison remains separate; this
worker made no live comparison call. No approval, publication, or release
acceptance is claimed from this local candidate verification.
