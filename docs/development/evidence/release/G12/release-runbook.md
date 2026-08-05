# G12 release runbook

This runbook is the operator precondition for the first public GigAI release.
It is not evidence that a release has occurred.

## One-time setup

1. Confirm `https://pypi.org/pypi/gigai/json` returns `404` immediately before
   the release commit and tag. A pending publisher does not reserve the name.
2. On both TestPyPI and PyPI, create a GitHub Actions pending Trusted Publisher
   for project `gigai`, owner `karthik446`, repository `gigai`, workflow
   `release.yml`, and environment `testpypi` or `pypi` respectively.
3. In GitHub, create the named `testpypi` and `pypi` environments. GigAI is a
   sole-maintainer project, so self-review is not configured as a ceremonial
   approval gate. Do not add a PyPI API token or any other publishing secret to
   the repository.
4. Confirm the release workflow's exact-tag checks are green. The workflow
   publishes to TestPyPI first and authorizes PyPI only after both TestPyPI
   installation jobs pass.

## Release sequence

1. Start from a clean checkout and confirm PyPI name availability.
2. Set `pyproject.toml [project].version` to `0.1.1`, run `uv lock`, and verify
   `uv lock --locked`.
3. Update user-facing installation documentation for `uv tool install
   "gigai==0.1.1"`; do not alter historical Phase 1 evidence.
4. Commit the release change, create an annotated `v0.1.1` tag, and push the
   commit and tag.
5. The tag-triggered workflow runs reusable exact-tag CI, builds and attests
   the wheel and source distribution, publishes to TestPyPI, verifies clean
   macOS/Linux `uv tool install` behavior, then publishes to PyPI.
6. After production PyPI and its clean-machine checks pass, the workflow
   verifies provenance and creates the GitHub Release with distributions,
   `SHA256SUMS`, and `release-manifest.json`.

## Consumer verification

```bash
uv tool install "gigai==0.1.1"
gigai --version
gh attestation verify <downloaded-wheel> --repo karthik446/gigai
```

The completion audit records the exact URLs, digests, hosted-run IDs, and
sanitized macOS/Linux installation evidence after—not before—the release.
