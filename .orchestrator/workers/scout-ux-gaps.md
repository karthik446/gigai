# scout-ux-gaps — one-command Scout flow

Worker task: close the two usability gaps found by the installed-wheel acceptance
(`.orchestrator/workers/scout-run-acceptance.md` "Findings" 1): make `setup` -> `init`
-> `secrets add` -> `resume add` -> `run` an actual one-command flow from inside the
target folder, with no `gigai scout install` step needed first and no `--target`
needed after `init`. All work EXECUTED unless marked READ.

## Gap 1: `resume add` didn't install Scout

**READ**: `src/gigai/scout/template.py` (`install_scout`, idempotent, returns
`ScoutInstallResult(gig_id, bound, approved, activated)`); `src/gigai/scout/
run_supervisor.py`'s `ensure_scout_ready` (calls `install_scout` then writes the
starter `find-jobs.json`) — the exact sequence `gigai scout run` uses to make itself
"install if needed."

**EXECUTED**: `resume_add_command` in `src/gigai/scout/scout_cli.py` now calls
`install_scout(...)` first (same call `scout install` and `ensure_scout_ready` make),
then writes the starter `find-jobs.json` via the already-owned
`write_starter_find_jobs_config` (mirrors `ensure_scout_ready`'s install -> write-config
order), before importing the resume reference and creating its record wrapper. The
JSON payload gained two fields: `scout_installed` (true only when install actually
changed something) and `gig_id`. The plain-text output prints an extra line when
Scout was freshly installed. Rerunning is unchanged/idempotent: `install_scout` is
already idempotent, and the reference/record calls already deduped by content digest.

## Gap 2: implicit (no `--target`) commands failed from a bound non-Git cwd

**READ**: `src/gigai/workpad.py` `resolve_bound_project` -> `_resolve_bound_project`,
and what it calls: `target_binding.resolve_target` (raises `GitTargetError` for an
implicit non-Git path — by design, it has no registry context) and
`registry.RegistryTransaction.find_target` (exact-locator + `os.path.samefile` lookup,
no ancestor walk). `_resolve_bound_project` already had a fallback for `GitTargetError`
that checks the registry for `cwd` **exactly** — but that's not what broke `scout
install`/`resume add`/`run`.

**Root cause traced** (not in the brief, found while reproducing the stated symptom):
`install_scout` (`src/gigai/scout/template.py`) calls `resolve_bound_project` once to
find the owner, but then passes the *original* `requested_target` (`None`, when the
CLI got no `--target`) into `default_init.initialize_defaults`, which re-resolves the
target itself via the **raw** `resolve_target(requested_target)` — the function that
has no registry context and always raises `GitTargetError` for an implicit non-Git
path, bound or not. That is the actual call that failed for `scout install` / `resume
add` / `scout run` (which all funnel through `install_scout`). `scout status`/`stop`
never hit this because they only call `run_supervisor.status`/`stop`, which use
`resolve_bound_project` directly and already worked from cwd == target.

`template.py`/`default_init.py` are outside this task's owned files, so the fix stays
at the CLI boundary in `scout_cli.py`: a new `_resolved_target(target_value, home_root)`
helper resolves an implicit target through `resolve_bound_project` *before* calling
`install_scout`/`import_reference`/`create_record`/`run_supervisor.start`, and passes
the resulting **explicit** absolute path down. An explicit path never falls into the
raw `resolve_target`'s "no --target given" branch, so every downstream helper that
re-resolves the target on its own now succeeds. `install_command`, `resume_add_command`,
and `run_command` (the three that route through `install_scout`) now call it; `stop`/
`status` didn't need it (already worked) but get the same resolution for free since
`run_supervisor.stop`/`.status` call `resolve_bound_project` internally regardless of
what `scout_cli.py` passes.

**Subfolder support** (explicitly asked for): `_resolve_bound_project` in
`workpad.py` used to look up only the *exact* cwd in the registry after a
`GitTargetError`. Changed it to walk `cwd` and each of its parents
(`current, *current.parents`) and use the first one that's a registered non-Git
target — git-target resolution (the `resolve_target` call itself, which does its own
git-root discovery) is untouched; only the non-Git-cwd registry fallback gained the
walk. No registry schema/storage change; no new registry method was needed —
`RegistryTransaction.find_target` (already read-only, already existed) is just called
once per ancestor instead of once for `cwd` alone.

## Files touched (all owned)

- `src/gigai/scout/scout_cli.py` — `_resolved_target` helper; wired into
  `install_command`, `resume_add_command` (+ install-if-needed + starter config +
  `scout_installed`/`gig_id` payload fields), `run_command`.
- `src/gigai/workpad.py` — `_resolve_bound_project`'s non-Git cwd fallback now walks
  ancestors instead of checking only the exact cwd; comment updated to explain why.
- `src/gigai/registry.py` — **not touched**. `find_target` already existed, already
  read-only, and already does the per-path check the ancestor walk needed; no new
  helper was required.
- `tests/behaviors/scout_find_jobs/test_scout_setup_cli.py` — added
  `test_scout_resume_add_installs_scout_when_not_yet_installed` (git + non-git,
  fresh install + idempotent rerun), `test_scout_commands_resolve_a_bound_non_git_target_from_cwd`
  (full install -> resume add -> run --no-browser -> status (from a subfolder) -> stop,
  no `--target` anywhere), `test_scout_install_still_requires_target_from_an_unbound_non_git_cwd`
  (regression guard).
- `tests/behaviors/runtime_run_authority/test_g28_setup_create.py` — identified via
  grep as the existing test file covering `resolve_bound_project` directly (it already
  had `test_initialized_non_git_target_resolves_implicitly_from_its_directory`). Added
  `test_initialized_non_git_target_resolves_implicitly_from_a_subfolder` and
  `test_unbound_non_git_cwd_still_errors_without_target`.
- `.orchestrator/workers/scout-ux-gaps.md` — this report.

## Tests run

Exact files:
```
tests/behaviors/scout_find_jobs/test_scout_setup_cli.py       18 passed
tests/behaviors/runtime_run_authority/test_g28_setup_create.py 5 passed
```
(Also ran `tests/behaviors/scout_find_jobs/test_scout_run_supervisor.py` alongside
them as a belt-and-suspenders check since it's the other direct `resolve_bound_project`
caller near this code — 30 passed combined, no regressions.)

```
make unit-tests
922 passed, 1330 deselected in 7.67s
```

## EXECUTED: brief's flow from a temp non-Git target

Isolation: `GIGAI_HOME` pointed at a scratchpad temp dir, cwd a scratchpad temp dir
outside the repo, fake Exa key (`fake-exa-key-not-real`), no `~/.gigai`, no operator
uv tool install, no `git add/commit/stash/reset/clean`. Ran against the source
checkout (not a wheel install — this brief didn't ask for that proof; installed-wheel
proof already exists in `.orchestrator/workers/scout-run-acceptance.md`). No
`--target` flag anywhere after `init`. Sources (`exa`/`ats`/`hiringcafe`) set to
`false` in `find-jobs.json` before `scout run` so no live network call happens.
Trimmed transcript (cwd shown per command; JSON bodies elided to the fields that
matter):

```
$ gigai setup --non-interactive --workpad-root .../workpad --editor /usr/bin/true --json
  (cwd = finalflow2/)
{"config_changed":true,...}
[exit 0]

$ gigai init --target . --username acceptance-test --json
  (cwd = finalflow2/target/)
{"target_kind":"non-git","scout_status":"binding_only_unready",...}
[exit 0]

$ gigai secrets add exa --stdin
  (cwd = finalflow2/target/, no --target)
Stored secret for exa.
[exit 0]

$ gigai scout resume add .../r.md --json
  (cwd = finalflow2/target/, no --target)
{"ok":true,"scout_installed":true,"gig_id":"gig_6e4ee867-...",
 "reference_created":true,"record_created":true,...}
[exit 0]

$ gigai scout run --no-browser --json
  (cwd = finalflow2/target/, no --target; find-jobs.json sources all false)
{"ok":true,"pid":53218,"port":8765,"url":"http://127.0.0.1:8765",
 "reused":false,...}
[exit 0]

$ gigai scout status --json
  (cwd = finalflow2/target/, no --target)
{"ok":true,"state":"running","pid":53218,"url":"http://127.0.0.1:8765",...}
[exit 0]

$ gigai scout stop --json
  (cwd = finalflow2/target/, no --target)
{"ok":true,"stopped":true}
[exit 0]
```

Teardown verified: `ps -p 53218` -> no such process; `lsof -nP -iTCP:8765 -sTCP:LISTEN`
-> nothing.

## Acceptance checklist

- [x] `resume add` on a fresh bound target installs Scout and wraps the resume
      (`test_scout_resume_add_installs_scout_when_not_yet_installed`, git + non-git).
- [x] From cwd = a bound non-Git target (and a subfolder of it): `scout install`,
      `resume add`, `run --no-browser`/`status`/`stop` all work without `--target`
      (`test_scout_commands_resolve_a_bound_non_git_target_from_cwd`, plus the
      EXECUTED transcript above).
- [x] Git-target resolution unchanged: existing parametrized `git=True` cases in
      `test_scout_setup_cli.py` still pass; `resolve_target`'s git-discovery path in
      `target_binding.py` was never touched.
- [x] An unbound non-Git cwd still errors
      (`test_scout_install_still_requires_target_from_an_unbound_non_git_cwd`,
      `test_unbound_non_git_cwd_still_errors_without_target`).
- [x] No schema/storage change; no new registry helper needed.
