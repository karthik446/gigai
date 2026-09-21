# SCOUT R7 rebuilt candidate artifact readiness — 2026-09-21

Status: **artifact ready for separately bounded comparison; not release
acceptance or publication approval.**

Built from the frozen checkout at HEAD
`fda48574f8642e66c0e7d53e7303ec04f04d7bb8`, branch
`karthik446/gigai-v0.1.7`, with `pyproject.toml` and `uv.lock` verified as
GigAI `0.1.7`. The build used `uv build --out-dir` and did not overwrite the
prior candidate directory.

| Artifact | Path | SHA-256 |
|---|---|---|
| wheel | `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/dist/gigai-0.1.7-py3-none-any.whl` | `ed3b3336d8f4d0bd49f9175c1e216ccafcc7e20583ed94d1ccb550679b0a59d3` |
| sdist | `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/dist/gigai-0.1.7.tar.gz` | `fb6158b0399ef61734e43fc7ac6308271178125100d25b0ce7cf397eb628e3c7` |

The wheel was installed with dependencies (not `--no-deps`) into
`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/exact-wheel-venv`.
An isolated `python -I` import from
`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/import-cwd`
reported version `0.1.7` and origin
`.../exact-wheel-venv/lib/python3.11/site-packages/gigai/__init__.py`, with no
checkout import shadowing.

The complete non-private source/dirty provenance snapshot captured before
tests is `/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/source-before-tests.json`;
it records 1,225 non-private files and excludes `.git/**` and all `.gigai/**`
workspace/data. Artifact member paths and the explicit zero private-member
check are in
`/private/tmp/gigai-r7-rebuilt-candidate-verification-9TxZsp/artifact-members.json`.
Raw build/install/identity logs are under the same task directory's `logs/`.

This readiness handoff authorizes comparison against this exact local artifact
identity only; it does not establish source-matrix, installed-verifier,
provider/model, platform, Docker, publication, or UAT acceptance.
