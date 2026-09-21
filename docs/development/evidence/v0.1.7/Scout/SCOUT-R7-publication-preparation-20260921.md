# SCOUT R7 publication preparation — 2026-09-21

## Disposition

This is a read-only publication handoff. No file was staged, no commit/tag was created, and no push/upload/publication or workflow run was started; unrelated dirty work and private state were preserved. The integrated personal-use candidate remains the bounded candidate documented in `SCOUT-R7-release-integration-20260921.md`, with experimental offline comparison quality and no new live-quality gate.

## Current candidate agreement

Read-only Git state:

```
branch: karthik446/gigai-v0.1.7
HEAD: fda48574f8642e66c0e7d53e7303ec04f04d7bb8
index: clean
worktree: dirty
```

`pyproject.toml` reports package/version `gigai 0.1.7`; the editable `gigai` record in `uv.lock` also reports `0.1.7`. The exact disposable artifacts supplied for publication preparation agree with both:

```
/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/dist/gigai-0.1.7-py3-none-any.whl
SHA256 da75dde1f9672e6e096ed4fed3f78da8b1915c6ce6b104174d46c9c2354b527e

/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/dist/gigai-0.1.7.tar.gz
SHA256 d89629aaa301432b65a003ffe91184887462d68ff26493e682393bacd512e0e3
```

Wheel and sdist metadata both read `Name: gigai` and `Version: 0.1.7`. The release-integration report records the integrated postimage hashes, targeted checks, installed Scout smoke, and package inventory; this task did not repeat those checks or rebuild.

## GitHub and tag prerequisites

Remote is `https://github.com/karthik446/gigai.git`. Read-only checks found:

```
local v0.1.7 tag: absent
remote refs/tags/v0.1.7: absent
GitHub release v0.1.7: not found
named environments: ["pypi", "testpypi"]
```

The actual `.github/workflows/release.yml` is triggered by pushing a `v*` tag. Its `preflight` requires an annotated tag object, `verify-tag`, `verify-lockfile`, and both named publication environments; exact-tag full CI must pass before build and publication. The older G12 runbook is consistent on trusted publishers, TestPyPI-first sequencing, PyPI-after-TestPyPI verification, attestation, and GitHub release creation; it is a runbook, not evidence that this release has occurred.

## Proposed commit include manifest

These are the minimum paths directly owned by the integrated correction and this publication evidence:

```
src/gigai/lifecycle.py
src/gigai/cli.py
tests/test_runtime_comparison.py
pyproject.toml
uv.lock
MANIFEST.in
README.md
LICENSE
docs/development/evidence/v0.1.7/Scout/SCOUT-R7-publication-preparation-20260921.md
```

`pyproject.toml` and `uv.lock` are required for the version/package-data and lockfile agreement, but their dirty diffs are shared release metadata rather than authored by this preparation task. The exact minimum list is retained at `/private/tmp/gigai-r7-publication-prep-20260921-DHJPNw/include-owned-and-release.txt`.

The built wheel also requires the complete current shipped package closure: 218 current files under `src/gigai/**` (Python modules, schemas, data, and Scout shipped assets), listed exactly at `/private/tmp/gigai-r7-publication-prep-20260921-DHJPNw/include-package-closure.txt`. This is a candidate-closure proposal, not an ownership claim: the coordinator must explicitly reconcile every path in that list before staging, because many are mixed changes from prior lanes. `MANIFEST.in`, `README.md`, and `LICENSE` remain package/build inputs even when unchanged against HEAD.

Tests/tools beyond the three owned regression paths must not be pulled into this commit by glob. Add them only by an explicit owner-approved allowlist if exact-tag CI requires them; the current tree contains many untracked tests and modified installed verifiers.

## Mixed and unclear ownership requiring reconciliation

The current dirty candidate contains source evidence that the package closure cannot be treated as one worker-owned change:

- G43/G44/provider lane: `src/gigai/provider_review.py`, `src/gigai/run.py`, `src/gigai/run_plan.py`, `tests/test_g43_provider_review.py`, `tests/test_g43_provider_run_status.py`, `tests/test_g43_response_framing.py`, `tests/test_g43_text_media.py`, and the G43.1/G43.2/G44 docs. The tracked source diff is large (`provider_review.py` +433/-22, `run.py` +3196/-595, `run_plan.py` +1396/-243), and these bytes were not authored or reviewed by this preparation task.
- Parallel capability-refusal lane: untracked `src/gigai/capability_review.py` and `tests/test_scout05_capability_review.py`; their owner and acceptance must be resolved independently before inclusion.
- Shared runtime/base changes: tracked diffs in `canonical.py`, `journal.py`, `index.py`, `registry.py`, `workpad.py`, `package.py`, `model_execution.py`, `config.py`, `validators.py`, and related modules are required by portions of the current package closure but are not attributable to this publication task.
- Current package/data/schema additions include `src/gigai/runtime_comparison.py`, Ollama and Scout modules/assets, runtime/Graph Set schemas, and the shipped comparison pack. They are present in the candidate wheel and therefore must be owner-reconciled rather than silently omitted or claimed here.
- `pyproject.toml` changes version `0.1.6` to `0.1.7`, expands shipped data globs, and adds pytest markers; `uv.lock` changes the editable project record. These are necessary release inputs but are shared metadata changes.

Exact read-only status and numstat evidence is retained in `/private/tmp/gigai-r7-publication-prep-20260921-DHJPNw/mixed-shared-changes.txt`. This task does not authorize staging any mixed path merely because it is needed by the locally built wheel.

## Explicit exclude list

The proposed release commit must exclude, unless a separate owner-approved allowlist explicitly says otherwise:

```
.git/**
.gigai/**                    # private workspace state; not inspected
.venv/**, dist/**, build/**, *.egg-info/**
__pycache__/**, *.pyc, raw session output, credentials, tokens, secrets, personal config/workpads
all docs/development/v0.1.8/**
all research/** and research-only fixtures/helpers
all unrelated Scout/evidence/followup/roadmap documents except this new report
all unrelated tests and tools not required by the final exact-tag workflow
all G43/G44/provider and parallel capability-review paths until their owners explicitly reconcile them
```

The machine-readable exclusion proposal is `/private/tmp/gigai-r7-publication-prep-20260921-DHJPNw/exclude.txt`. No `.gigai` contents were opened or inventoried.

## Debian and release blockers

`task_36db593d7ef3` / dispatch `ctx_9b822d919a27` has a settled worker status, but its durable first-failure evidence remains a blocker and must not be reported as Debian acceptance. `SCOUT-R7-debian-first-failure-20260921.md` records the immutable image first failure `tests/test_g28_setup_create.py::test_initialized_non_git_target_resolves_implicitly_from_its_directory`, with `1 failed, 404 passed, 1 skipped`; the failure is the provider-free offline model-runtime setup prerequisite. That report explicitly says it does not establish corrected-candidate or Debian release acceptance.

Concrete release blockers at this preparation point are the dirty mixed worktree requiring a deliberate allowlist, absent local/remote `v0.1.7` tag and GitHub release, and unresolved Debian first-failure evidence. No claim is made that any of these gates is green.

## Action-ready workflow after owner approval

The coordinator should first reconcile the allowlist and obtain a clean release commit, then run only the authorized release operations:

```bash
# from the deliberately selected clean release checkout, after owner review
git add -- src/gigai/lifecycle.py src/gigai/cli.py tests/test_runtime_comparison.py pyproject.toml uv.lock MANIFEST.in README.md LICENSE docs/development/evidence/v0.1.7/Scout/SCOUT-R7-publication-preparation-20260921.md
git diff --cached --check
git diff --cached --name-status
git commit -m "Prepare GigAI 0.1.7 personal release"

# bind the tag to that exact commit and push the branch/tag deliberately
RELEASE_COMMIT=$(git rev-parse HEAD)
python tools/release_check.py verify-lockfile
git tag -a v0.1.7 "$RELEASE_COMMIT" -m "GigAI 0.1.7"
python tools/release_check.py verify-tag --tag v0.1.7
git push origin "$RELEASE_COMMIT":refs/heads/karthik446/gigai-v0.1.7
git push origin refs/tags/v0.1.7
```

The pushed tag triggers `.github/workflows/release.yml`: preflight and exact-tag full CI; `uv build --no-sources`; artifact/checksum verification; fresh wheel/sdist setup and doctor smoke; TestPyPI publication and clean-install verification on Ubuntu/macOS; PyPI publication and clean-install verification; provenance attestation; and GitHub release creation. Do not run these mutation commands from this dirty worker checkout until the proposed include/exclude manifest is accepted and the Debian limitation is deliberately dispositioned.

## Evidence paths

- Integrated candidate report: `docs/development/evidence/v0.1.7/Scout/SCOUT-R7-release-integration-20260921.md`
- Integrated wheel/sdist: `/private/tmp/gigai-r7-release-integrated-20260921-xAlnmQ/dist/`
- Publication manifests and mixed ownership evidence: `/private/tmp/gigai-r7-publication-prep-20260921-DHJPNw/`
- Debian first-failure report: `docs/development/evidence/v0.1.7/Scout/SCOUT-R7-debian-first-failure-20260921.md`
- Actual workflow: `.github/workflows/release.yml`
