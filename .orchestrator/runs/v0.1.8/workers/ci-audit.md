# ci-audit worker report

READ vs EXECUTED: READ the failing CI run's log (`gh run view 35660274375
--repo karthik446/gigai --log-failed`), `.github/workflows/*.yml`,
`tools/run_ci_tests.py`, `tools/verify_installed_g0{3,4,22}.py`,
`src/gigai/cli.py`, `src/gigai/model_discovery.py`,
`src/gigai/standard_pack.py`, `src/gigai/invocation.py`,
`tests/behaviors/cli_surface/test_g22_cli_create.py`, `containers/debian-offline/Dockerfile`,
`tools/verify_debian_offline.py`, `tools/run_debian_offline.sh`, and
`pyproject.toml`. EXECUTED `make test-wheel`, `make test-installed`, every
`tools/verify_installed_*.py` standalone (both in-place and in a hostile
`HOME=/nonexistent PATH=/usr/bin:/bin` environment to force the CI condition),
a manual step-by-step replay of the release-workflow smoke-test shell blocks,
`uv build --wheel`/`uv build --sdist` plus wheel/sdist content inspection,
and a Python-3.11 import check of the Scout find-jobs modules.

## Root cause (ACCEPTANCE requirement)

`src/gigai/cli.py:1213-1216` — `setup_command` correctly raises
`ValueError("no usable model runtime is configured; install or configure
Codex, Claude, or an API target, then rerun \`gigai setup\`")` when
`gigai setup --non-interactive` finds no Codex/Claude CLI on the (login-shell
rehydrated) PATH and was given no explicit `--endpoint`/`--model-target`.
That `ValueError` is caught at `src/gigai/cli.py:1317-1319` and turned into a
JSON error payload on **stdout** by `_raise_cli_error` (`src/gigai/cli.py:170-182`,
`as_json=True` branch: `click.echo(json...)` then `click.exceptions.Exit(1)`).

The bug was in the verifier, not the CLI: `tools/verify_installed_g03.py`
(pre-patch, at HEAD) called `gigai setup --non-interactive --json` with no
model target, then on failure raised
`SystemExit(f"installed {label} failed: {result.stderr}")` — reading only
**stderr**, which is empty for this failure mode. That produced exactly the
observed `installed first setup failed: ` with no message in CI run
35660274375. `tools/verify_installed_g04.py` and 6 other verifiers already
avoid this by passing an explicit `--endpoint`/`--model-target`/
`--create-model-target` and pinning `HOME`/`PATH`; `verify_installed_g03.py`
(and, separately, `verify_installed_g22.py`) were the only ones that didn't.

Reproduced directly:
```
$ env -i HOME=/nonexistent PATH="/usr/bin:/bin" \
    .wheel-venv/bin/gigai setup --non-interactive --home ... --workpad-root ... \
    --editor /usr/bin/true --json
{"error":{"code":"setup_invalid","message":"no usable model runtime is configured; ..."}}
EXIT=1   # stdout has the message, stderr is empty
```

A second, independent instance of the **same root cause** was found in
`.github/workflows/release.yml`: `smoke-artifacts`, `verify-testpypi`, and
`verify-pypi` all call `gigai setup --non-interactive ... --json` with no
model target either. These run on real clean GitHub-hosted runners (no
Codex/Claude installed) and would have failed release publishing even after
`verify_installed_g03.py` was fixed. Confirmed locally with the same
`env -i HOME=... PATH=/usr/bin:/bin` reproduction.

## Fixes (OWNED FILES only)

1. **`tools/verify_installed_g03.py`** — `run()` now takes an explicit `env`;
   `setup --non-interactive` is given `--credential-ref
   provider=environment:GIGAI_PROVIDER_TOKEN --endpoint
   remote=openai_api:provider:https://api.example.test --model-target
   remote=remote:gpt-test --create-model-target remote` (the same recipe
   `verify_installed_g04.py` already uses) and `HOME`/`PATH`/`TMPDIR` are
   pinned, so it never depends on Codex/Claude being on the runner's PATH.
   Also added a `_diagnostics()` helper reading both stdout and stderr (this
   part was already present as an uncommitted local patch before I started;
   I kept it and it's what made the underlying bug legible in the first
   place). Consequence: the offline `doctor` after this setup now correctly
   reports `overall_status: WARN` (an unset `GIGAI_PROVIDER_TOKEN` credential
   reference — expected, and asserted the same way in
   `verify_installed_g11.py`) instead of `PASS`; updated the doctor
   assertions accordingly, still requiring `config.valid`, `path.home`,
   `path.workpad`, `mount.atomic_replace`, `mount.interprocess_lock`, and
   `editor.resolved` to be `PASS` and `credential.provider` to be `WARN`.

2. **`tools/verify_installed_g22.py`** — full rewrite (coordinator-approved
   mid-task after I found it independently broken while continuing down the
   verifier list). The original file drove `gigai create --reference PATH
   --request TEXT`, then answered questions through a local interactive HTTP
   interview server (`InterviewHTTPServer`) it expected `create` to launch.
   That flow does not exist for `create` in the shipped v0.1.7 CLI any more:
   `create` (`src/gigai/cli.py:2277`) now takes `NAME` plus a validated
   `--invocation` JSON envelope and produces a proposal directly via
   `create_offline` (`src/gigai/cli.py:2362`) — no interview, no HTTP server,
   no question/answer loop. `InterviewHTTPServer` is reachable only from
   `gigai internal improve` now (`src/gigai/cli.py:2476`). Full mapping from
   old assertions to current equivalents, and what was dropped as "no longer
   exists," is documented in the new file's module docstring. New flow:
   `setup` (same explicit-endpoint fix as above) → `init --username` →
   write an invocation envelope → `create NAME --invocation FILE --json`
   (assert `status: proposed`, `proposal_id` starts with `gp_`) → `approve
   PROPOSAL_ID --json` (assert `status: approved`, sealed `version`/`tag`
   present) → assert no `workpads/**/runs` directory exists. Verified this
   exact flow by hand against the live CLI before writing the file, and it
   matches `tests/behaviors/cli_surface/test_g22_cli_create.py`'s envelope
   shape.

3. **`.github/workflows/release.yml`** — added the same explicit
   `--credential-ref`/`--endpoint`/`--model-target`/`--create-model-target`
   flags to the three `gigai setup --non-interactive ... --json` calls in
   `smoke-artifacts` (line ~111), `verify-testpypi` (line ~169), and
   `verify-pypi` (line ~220), so none of them depend on Codex/Claude being
   present on the runner. Confirmed the edited YAML parses
   (`python3 -c "import yaml; yaml.safe_load(...)"`) and manually replayed
   each block's shell logic locally under `env -i HOME=... PATH=/usr/bin:/bin`
   — both `setup` and the following `doctor` now exit 0.

No `src/gigai/*` changes were needed or made; the root cause is a correct
CLI refusal, not a CLI bug, so the fix is entirely in the verifiers/workflow
that must not rely on ambient tool discovery.

## ACCEPTANCE: every CI Built-wheel resources verifier run locally (all pass)

Full `make test-wheel` output (fresh `uv build` → fresh `.wheel-venv` →
install → every verifier → every installed pytest node), then
`make test-installed` on the same venv, both exit 0:

```
verified installed GigAI canonical identity API
verified installed GigAI CLI: help, version, setup, doctor, init, create, feedback, revise, approve, reject, gigs, proposals, status, show, history, plan, run, run-details, workpad path, check, and open only
verified installed GigAI G03 setup, idempotency, pack, and offline doctor
verified installed GigAI G04 Git and non-Git target binding
verified installed GigAI G05 private unborn workpad and read/open surface
verified installed GigAI G06 journal first commit and trailer sequencing
verified installed GigAI G07 proposal validation and digest-pinned check
verified installed GigAI G08 offline proposal lifecycle
verified installed GigAI G09 rebuildable index and read commands
verified installed GigAI G11 port, factory, offline doctor, and live refusal
verified installed GigAI G13 deterministic Run lifecycle
verified installed GigAI G14 sequential Goal scheduler
verified installed GigAI G15 Review Bundle and evaluator substrate
verified installed GigAI G16 deterministic Review Loop
verified installed GigAI G17 capability inspection and local installation
verified installed GigAI G19 target effect
verified installed GigAI G20 improve lifecycle
verified installed GigAI G21 daily, weekly, and monthly occurrences
verified installed GigAI G22 create and approve
verified installed GigAI G23 portability replay
verified installed GigAI G26 builder contract
verified installed GigAI G27 adaptive discovery manifest
verified installed GigAI G28 evaluation, roles, setup, and browser-first create
verified 82 installed GigAI schemas
============================= 38 passed in 40.66s ==============================   # make test-wheel's pytest phase
============================= 38 passed in 42.52s ==============================   # make test-installed
=== EXIT 0 ===
```

Full logs: `.orchestrator/logs/094550-test-final-v2-make-test.log` (this
clean run) and `.orchestrator/logs/093737-test-make-test-wheel-v2.log`,
`.orchestrator/logs/093937-test-make-test-installed.log` (earlier separate
runs, same result).

`make test-installed` exit 0: confirmed directly (`$?` checked after the
Make invocation) and via the visible-tab `=== EXIT 0 ===` marker above.

## Workflow audit (task item 4) — no further fixes needed beyond release.yml above

- **Python 3.11/3.12/3.13 matrix** (`pull_request.yaml` `source-tests`,
  `full_matrix: true`): only affects `source-tests` (library/CLI tests via
  `run_setup`/`build_config` fixtures, not the CLI's ambient-discovery
  `setup` path), so the root cause above doesn't reach it. Confirmed the
  Scout find-jobs modules (`scout_find_jobs_contracts.py`,
  `scout_find_jobs_bindings.py`) import cleanly under the 3.11 wheel venv and
  contain no 3.12+-only syntax (no `type` statements, no `match` blocks).
- **Debian 12 offline container** (`debian-offline` job, `full_matrix`-only):
  `containers/debian-offline/Dockerfile` copies only `src`, `tests`, `tools`,
  `docs`, `research` — never top-level `ui/`. `tools/run_debian_offline.sh`
  runs `verify_debian_offline.py` (a pure mount/env sandbox audit, no `gigai
  setup` call) then `pytest -q`, so it's unaffected by the root cause.
  Reviewed, not executed (needs Docker; out of the reproduction budget for
  this pass and not implicated by any change made here).
- **`ui/` packaging**: `[tool.setuptools.packages.find] where = ["src"]`
  already scopes the wheel to `src/`. Built both wheel and sdist locally and
  inspected their contents directly (`unzip -l`, `tar tzf`): neither contains
  the top-level `ui/` directory or `node_modules`; the only `.../ui/*` paths
  present are the intentional `gigai/data/scout/ui/{style.css,template.html}`
  package-data files declared in `pyproject.toml`. No packaging change
  needed.

## Known pre-existing flake (not fixed, out of scope)

`tests/behaviors/installed_release/test_g04_installed_scenarios.py::test_two_installed_init_processes_converge_without_lock_or_duplicate`
failed once (`[0, 1]` vs expected `[0, 0]` process return codes) during one
of several full-suite runs, then passed on immediate re-run and 3/4 standalone
reruns. It exercises a real two-process concurrent-`init`/lock race,
unrelated to any file touched here (no g03/g22/release.yml overlap) and
predates this session (same test file/behavior). Flagging for whoever owns
`src/gigai` locking/registry code; not fixed here since it's outside OWNED
FILES and outside the g03 root-cause mandate.

## Files changed

- `tools/verify_installed_g03.py`
- `tools/verify_installed_g22.py` (full rewrite; see docstring for the
  original-vs-current behavior mapping)
- `.github/workflows/release.yml`
- `.orchestrator/workers/ci-audit.md` (this file)

No `git add`/`commit`/`stash`/`reset`/`clean` was run. No network access
beyond what `uv build`/`uv venv`/`uv pip install` needed for package-index
downloads. No pushing.
