# TD-0002 — Owner-controlled release workflow

- Status: In verification
- Discovered during: G24 UAT and `0.1.3` release preparation
- Affected surface: `.github/workflows/release.yml`
- Ownering lane: G25 alpha release readiness

## Observation

The previous release workflow was triggered by pushing a `v*` tag, which made a
local tag creation and push part of the release procedure. The branch now
implements the intended private, manual GitHub Action: it accepts an explicit
version, verifies the selected `main` source, checks the actor, and creates the
tag from GitHub before publication. GitHub workflow evidence remains pending.

## Proposed resolution

Convert release initiation to `workflow_dispatch`. Require an explicit version
input that matches `pyproject.toml`, verify the workflow actor is the approved
repository owner, run the release checks against the selected `main` commit,
then create and push the annotated tag from the workflow using narrowly scoped
contents permission. Keep publication and provenance checks fail-closed.

The action must reject pull-request refs, mismatched versions, duplicate tags,
and unauthorized actors before any tag or package publication occurs.

## Exit evidence

- No local tag/push is required for a normal release.
- An unauthorized manual dispatch is rejected before mutation.
- An authorized dispatch creates exactly one annotated `v<version>` tag from
  the verified `main` commit.
- TestPyPI, PyPI, provenance, and GitHub Release steps remain gated by the
  existing release checks.
- A dry-run or disposable repository test demonstrates the refusal paths
  without publishing a package.
