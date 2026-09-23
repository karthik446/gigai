# release-docs worker report — v0.1.8 release PR

## 1. Version bump + release checks

### Files changed (all in OWNED scope)

- `pyproject.toml` — `[project].version` `0.1.7` → `0.1.8`.
- `uv.lock` — refreshed with `uv lock` (own-package `gigai` entry `0.1.7` →
  `0.1.8`). Diff also picked up `pytest-xdist`/`execnet` records: these were
  already declared in `pyproject.toml`'s `test` extra (`pytest-xdist>=3.6`)
  but missing from the committed lockfile before this refresh — not a change
  I introduced, `uv lock` just caught up the lockfile to pyproject.
- `src/gigai/catalog.py:20` — `CATALOG_REVISION = "v0.1.7"` → `"v0.1.8"`.
  This is a provenance/build tag baked into `source_revision` and hashed
  into `package_id` for built-in catalog entries (`sync-references`,
  `plan-a-research`, `review-plan` — **not** Scout, which the catalog does
  not list). Checked the hash input in `package_id` (`catalog_id@definition_
  version`, not `CATALOG_REVISION`) — the bump does not change any
  `package_id`. Confirmed by running
  `tests/behaviors/runtime_run_authority/test_g42_catalog.py`, which
  hardcodes the three `package_id` UUIDs: still passes after the bump.

### Version constant search (READ, not executed beyond grep/pytest listed below)

- Searched all non-`__pycache__` `.py` files for `0.1.7` and for
  `__version__`. No `__version__` constant exists anywhere in `src/gigai`;
  `gigai --version` reads installed package metadata via
  `click.version_option(package_name="gigai")` at `src/gigai/cli.py:147-151`,
  which derives from `pyproject.toml` automatically — no separate constant to
  bump there.
- Other `0.1.7`/`0.1.6` hits are **not** current-version pins and were left
  alone:
  - `src/gigai/cli.py:2246,2252,2263`, `src/gigai/package.py:524-973`,
    `tests/behaviors/installed_release/test_g41_package_boundary.py:465-620`,
    `tools/verify_installed_g22.py:13` — all reference the historical
    **v0.1.6→v0.1.7 migration** flow (`gigai upgrade`), a past migration
    name, not a live version pin.
  - `research/local_model_eval/...harness.py`,
    `research/scout-local-boundary-demo/scripts/run_native_policy_probe.py`
    — local path/report strings under `research/`, out of scope and not
    release-gating.
- No test pins the literal current package version string. Confirmed with
  `grep -rn "0\.1\.7"` restricted to non-`__pycache__` `.py` files (shown
  above) and a scan of every test/tool file referencing `README`/`CHANGELOG`
  content — all are Git-fixture files (`target/README.md` written by tests
  as throwaway fixtures), not the repo-root docs.
- `tests/behaviors/installed_release/test_release_check.py` uses synthetic
  `"0.1.0"` fixtures throughout; it tests `release_check.py`'s logic, not
  the real project version. Ran it — still 8/8 passing.

### Every release_check.py / release.yml / pull_request.yaml condition, and how it's met

Read `tools/release_check.py` in full, and both `.github/workflows/release.yml`
and `.github/workflows/pull_request.yaml` (pull_request.yaml is invoked by
release.yml's `ci` job via `workflow_call` with `full_matrix: true`).

| # | Condition | Where enforced | How satisfied |
|---|---|---|---|
| 1 | Push is to a tag ref (`GITHUB_REF_TYPE == tag`) | `release.yml:31` | Operator action at tag-push time (see §3). |
| 2 | Tag is **annotated**, not lightweight (`git cat-file -t refs/tags/<tag>` == `tag`) | `release.yml:32` | Operator must run `git tag -a`, not `git tag` (see §3). Verified locally: `git cat-file -t` on a lightweight tag returns `commit`, not `tag`. |
| 3 | Tag string exactly equals `v{pyproject version}` | `release.yml:33` → `release_check.py verify-tag --tag <tag>` | `pyproject.toml` version is now `0.1.8`; ran `uv run --locked python tools/release_check.py verify-tag --tag v0.1.8` → `verified release tag v0.1.8 for gigai 0.1.8`. |
| 4 | `uv.lock`'s single editable `gigai` package record has `version == pyproject version` | `release.yml:34` → `release_check.py verify-lockfile` | Ran `uv run --locked python tools/release_check.py verify-lockfile` → `verified uv.lock project entry for gigai 0.1.8`. |
| 5 | `testpypi` and `pypi` GitHub environments exist on the repo | `release.yml:36-42` (`gh api repos/.../environments/<env>`) | Repo-admin/GitHub-settings concern, not code in this worktree; cannot verify or fix from here — flagging as an operator precondition. |
| 6 | Full pull_request.yaml CI (`full_matrix: true`) passes against the tag ref | `release.yml:47-52` | Out of my test budget (`make test` excluded); not run here. Coordinator/CI will run it. |
| 7 | `uv build --no-sources` produces exactly one wheel matching `gigai-{version}-*.whl` and one sdist `gigai-{version}.tar.gz` | `release.yml:74` → `release_check.py verify-artifacts` | Built into a scratch outdir (not `dist/`): `uv build --out-dir <scratch>` succeeded, produced `gigai-0.1.8-py3-none-any.whl` and `gigai-0.1.8.tar.gz`. Ran `release_check.py --dist <scratch> verify-artifacts` → `verified release artifacts for gigai 0.1.8`. |
| 8 | `SHA256SUMS` manifest matches exactly the verified wheel+sdist | `release.yml:75-76` → `write-checksums` / `verify-checksums` | Ran both against the scratch outdir → wrote and then verified `SHA256SUMS` cleanly. |
| 9 | Attestation verifies against the repo | `release.yml:83-85`, `github-release` job | CI/publish-time only (needs GitHub OIDC); not reproducible locally, not in scope. |
| 10 | Fresh-venv smoke test: `gigai --version` prints `gigai {version}`, `--help` works, `setup --non-interactive` (with the now-required `--credential-ref`/`--endpoint`/`--model-target`/`--create-model-target` flags) succeeds, `doctor --json` succeeds | `release.yml:88-119` (`smoke-artifacts`), duplicated in `verify-testpypi`/`verify-pypi` | Ran the equivalent commands directly (not via a built wheel install, to stay in budget): `gigai --version` → `gigai 0.1.8`; `gigai setup --non-interactive --home ... --workpad-root ... --editor /usr/bin/true --json` → succeeded; `gigai doctor --home ... --json` → `"overall_status":"PASS"`, `"gigai_version":"0.1.8"`. |
| 11 | `pull_request.yaml`'s own `changes`/`source-tests`/`behavior`/`wheel` jobs pass under `full_matrix: true` | `pull_request.yaml` (called by release `ci` job) | Not run (out of test budget: `make test` explicitly excluded). |

All locally-checkable conditions (3, 4, 7, 8, 10, plus the annotated-tag
mechanics for 1–2) pass for tag `v0.1.8` against the current tree. Conditions
5, 6, 9, 11 require CI/GitHub-side execution or repo-admin settings and are
out of this worker's reach/budget.

### CHANGELOG.md

Reconciled the file: the existing "0.1.7 candidate" entry was still sitting
under **Unreleased** even though `v0.1.7` was already tagged and merged
(`git log` shows `b01675d Ship GigAI 0.1.7 ... (#34)`, and `git tag -l`
confirms `v0.1.7` exists). Moved it into **Released versions** as `### 0.1.7`
(trimmed the release-candidate caveat line, since it shipped), and added a
new `### 0.1.8` entry above it in the same list, covering: Scout `find-jobs`
(acquire/assess/present, as described in the task), the `gigai.scout`
package move, the S11 behavior-test layout + `make test` runner, the release
CI setup-verifier/model-target fix, and the wheel-lane interpreter safety
guard. Each claim was checked against real diffs/files before writing it —
see the evidence in §2's CLI-diff and Makefile/`run_ci_tests.py` findings
below.

## 2. README.md rewrite

Full rewrite, 203 lines (slightly over the ~120–180 target; the required
"For agents" content — exact flags, the full `find-jobs.json` schema, the
UI-not-in-wheel note — didn't compress further without cutting required
material). Absolute GitHub links only (`https://github.com/karthik446/gigai/
blob/main/...`, confirmed remote is `github.com/karthik446/gigai`), no
relative links, no images.

### Runbook cross-check

Task said to cross-check against
`docs/development/v0.1.8/runbooks/M1-find-jobs.md`. **That file does not
exist** — searched the whole `docs/` tree (`find docs -iname "*M1*"`,
`find docs -ipath "*runbooks*"`) and found no runbook at that path or any
other M1/find-jobs runbook. I did not invent or create one (not in my
ownership). Flagging this as a gap for the coordinator: either the runbook
was never written, or it lives somewhere else and the path in my dispatch is
stale. I verified README commands directly against the CLI and the
`gigai.scout.find_jobs` source instead.

### Command verification (EXECUTED — every command in the README was run)

```
$ uv run --locked gigai --version
gigai 0.1.8

$ uv run --locked gigai --help          # top-level command list checked
$ uv run --locked gigai setup --help    # --credential-ref/--endpoint/--model-target/--create-model-target flags confirmed
$ uv run --locked gigai reference --help
$ uv run --locked gigai reference add --help   # --kind {resume,project_evidence,role_history,cover_letter} confirmed
$ uv run --locked gigai catalog list --json
[{"catalog_id":"sync-references",...},{"catalog_id":"plan-a-research",...},{"catalog_id":"review-plan",...}]
# confirms Scout is NOT in `gigai catalog` — corrected the README's "install
# Scout" language away from `catalog install` to the real auto-materialize +
# capability-review + approve path (traced through src/gigai/default_init.py
# and tests/behaviors/scout_find_jobs/test_m1_end_to_end.py).

$ uv run --locked gigai capability --help
$ uv run --locked gigai capability review --help   # exact required IDs (--gig, --base-version, --base-proposal-id, --manifest-id, --capability-id, --operation-key, --input, --confirm) confirmed
$ uv run --locked gigai approve --help
$ uv run --locked gigai init --help
$ uv run --locked gigai gigs --help
$ uv run --locked gigai workpad --help   # `gigai workpad path`
$ uv run --locked gigai run-details --help

$ uv run --locked gigai setup --non-interactive --home <scratch>/home --workpad-root <scratch>/workpad --editor /usr/bin/true --json
{"config_changed":true,...,"schema_version":"2.0",...}   # succeeded

$ uv run --locked gigai doctor --home <scratch>/home --json
{"checks":[...all PASS...],"gigai_version":"0.1.8","overall_status":"PASS",...}

$ uv run --locked gigai doctor --home /tmp/nonexistent --json
{"checks":[{"id":"config.valid","status":"FAIL",...}],"overall_status":"FAIL",...}   # exit 1, JSON still on stdout

$ uv run --locked gigai reference add --kind bogus --file /tmp/nope --json
(stdout empty; stderr: "Usage: ...\nError: Invalid value for '--kind': ...")  # exit 2
# This is why the README's JSON-convention note distinguishes: --json
# commands print structured JSON on stdout even on a domain-level failure,
# but a Click usage error (bad flag/missing arg) goes to stderr as plain
# text before any JSON handling runs. The task's "CLI errors print JSON on
# stdout" framing was too broad; I narrowed it to what's actually true and
# said so here rather than restating the imprecise claim.
```

### find-jobs.json schema

Read `src/gigai/scout/find_jobs/contracts.py`'s `FindJobsConfig`/
`SourceToggles` classes directly (fields, types, the
`"find-jobs-config:1"` schema_version literal, the `ModelTarget` enum:
`ollama_local`/`codex_cli`/`openrouter_api`). The example JSON in the README
is copied verbatim from
`tests/behaviors/scout_find_jobs/fixtures/fixture-find-jobs-config-v1.json`,
a real fixture that exercises this exact contract in
`test_m1_end_to_end.py`.

### EXA_API_KEY

Confirmed in `src/gigai/scout/find_jobs/exa_client.py:38,67,71`:
`EXA_API_KEY` read from `os.environ`; raises if unset, refusing Exa
discovery specifically (ATS-board acquisition is a separate code path and
unaffected).

### Present API invocation

Read `src/gigai/scout/find_jobs/present_api.py:479-512` (`main()`): confirms
`python -m gigai.scout.find_jobs.present_api --target <path> --home <path>`
is the real, only entry point; no `--endpoint`/`--model-target` flags on the
server itself (those live in `gigai setup`). Also confirms the loopback-only
binding / non-loopback-peer refusal described in the module docstring.

### UI not in the wheel

Confirmed `src/gigai/scout/ui/` is a real Vite/React source tree
(`package.json`, `yarn.lock`, `src/App.jsx`, `vite.config.js`) present in
`src/gigai/` but **not** listed in `[tool.setuptools.package-data]` for
`gigai.scout` in `pyproject.toml` (only `data/*.md`, `data/goalgraphs/*.md`,
`data/ui/*.html`, `data/ui/*.css`, `data/tools/*/*.json` — a separately
pre-built static `data/ui` HTML/CSS pair, not the `ui/` source checkout).
Verified the built wheel's file listing during `uv build` does not include
`gigai/scout/ui/*.jsx` or `package.json`.

### Consent dialog

Confirmed `consent` handling exists in
`src/gigai/scout/find_jobs/contracts.py`, `present_api.py`,
`src/gigai/scout/ui/src/api.js`, and
`src/gigai/scout/ui/src/components/ResultsView.jsx` — present across both
the API contract layer and the UI, supporting the README's consent-dialog
claim.

### twine check (EXECUTED)

```
$ uv build --out-dir <scratch>/dist-check
Successfully built .../gigai-0.1.8.tar.gz
Successfully built .../gigai-0.1.8-py3-none-any.whl

$ uvx twine check <scratch>/dist-check/*
Checking .../gigai-0.1.8-py3-none-any.whl: PASSED
Checking .../gigai-0.1.8.tar.gz: PASSED
```

Both artifacts pass — the rewritten README renders correctly as the PyPI
long description. Built into a scratch temp dir, never touched the repo's
own `dist/`.

## 3. Exact release commands for the operator

Confirmed against `release.yml:28-33` (annotated-tag + `GITHUB_REF_TYPE`
checks) and `release_check.py`'s `assert_release_tag` (tag must equal
`v{pyproject version}` exactly):

```bash
git tag -a v0.1.8 -m "GigAI v0.1.8"
git push origin v0.1.8
```

`-a` is required — a lightweight tag (`git tag v0.1.8`) fails
`release.yml:32`'s `git cat-file -t refs/tags/v0.1.8` check (returns
`commit` instead of `tag`) and the release workflow's `preflight` job hard
`test`-fails before anything else runs. The tag name must be exactly
`v0.1.8` (matches `pyproject.toml`'s now-bumped `0.1.8`); pushing any other
spelling fails `verify-tag`.

I did not run `git tag` or `git push` — out of OWNED scope per the dispatch
(no git tag/add/commit/push).

## READ vs EXECUTED summary

**EXECUTED** (all within test budget: version/lockfile/README/CHANGELOG-
adjacent checks, plus `release_check.py` subcommands and CLI `--help`/smoke
calls, no `make test`, no git tag/add/commit/push):

- `uv lock`
- `uv run --locked python tools/release_check.py verify-tag --tag v0.1.8`
- `uv run --locked python tools/release_check.py verify-lockfile`
- `uv run --locked python -m pytest tests/behaviors/runtime_run_authority/test_g42_catalog.py tests/behaviors/installed_release/test_release_check.py -q` (13 passed)
- `uv build --out-dir <scratch>` (scratch dir, not repo `dist/`)
- `uv run --locked python tools/release_check.py --dist <scratch> verify-artifacts / write-checksums / verify-checksums`
- `uvx twine check <scratch>/*`
- Every `gigai <cmd> --help` documented in the README, plus live
  `gigai setup --non-interactive ...`, `gigai doctor --json` (pass and
  induced-fail cases), `gigai reference add` (induced usage-error case)

**READ** (no execution needed / out of budget):
`tools/release_check.py` (full), `.github/workflows/release.yml`,
`.github/workflows/pull_request.yaml`, `Makefile`, `tools/run_ci_tests.py`
(interpreter-safety-guard section), `src/gigai/catalog.py`,
`src/gigai/default_init.py`, `src/gigai/scout/find_jobs/contracts.py`,
`src/gigai/scout/find_jobs/present_api.py`, `src/gigai/scout/find_jobs/
exa_client.py`, `src/gigai/cli.py` (version_option, JSON-error paths),
`tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` and its fixtures,
`tests/behaviors/installed_release/test_g41_package_boundary.py`,
`tools/verify_installed_g22.py`, `git log`/`git tag -l`/`git diff main`
(CHANGELOG reconciliation and CI-fix verification evidence).

## Open item for the coordinator

`docs/development/v0.1.8/runbooks/M1-find-jobs.md` (named in my dispatch as
the cross-check target) does not exist anywhere in the repo. I did not
create it — flagging so the coordinator can decide whether it's missing,
mis-pathed, or the dispatch text was simply wrong.
