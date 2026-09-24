# scout-setup-cmds — packet B of 4 for `gigai scout run` (U6, U14, U15)

## READ (context, no changes)
- `.orchestrator/workers/specs/scout-run-command.brief.txt` (operator-approved brief)
- `v0.1.8-uat.md` rows U6, U13, U14, U15, U23
- `default_init.py` (`default_inventory`, `initialize_defaults`, `DefaultInstanceResult`,
  `_authoritative_bindings`, the rerun/idempotency branch)
- `scout/template.py` (`scout_candidate_inventory`, `scout_catalog_candidate`)
- `workpad.py` (`NoActiveGigError`, `resolve_workpad`, `select_active_workpad`,
  `resolve_bound_project`, `_resolve_registered`)
- `project_binding.py` (`ProjectBinding`, `load_project_binding`, `write_project_binding_atomic`)
- `registry.py` (`select_active_workpad`, `find_active_workpad`, `find_workspace_owner`)
- `listing.py` (`list_gigs`, `GigListingEntry.status` values)
- `lifecycle.py` (`approve_offline` signature only)
- `private_records.py` (`import_reference`, `create_record`, `_content_for_reference` — confirms
  the `g45_reference` wrapper contract)
- `run.py:150-232` (`resolve_newest_resume` — confirms the exact wrapper shape find-jobs reads)
- `cli.py` (`gigs_command`, `reference_add_command`, `approve_command`, `_raise_cli_error`,
  existing `scout_*_cli` registration block)
- `scout/acquisition_cli.py`, `secrets_cli.py` (CLI module style reference)
- `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` (the exact
  `initialize_defaults` → `approve_offline` → `select_active_workpad` reference sequence,
  and the `import_reference` + `create_record(kind="imported_reference", content_family="g45_reference")`
  resume-wrapper sequence)
- `.github/workflows/release.yml` (non-interactive `gigai setup` smoke-step flags)
- `tests/behaviors/runtime_run_authority/test_g28_setup_create.py`,
  `tests/behaviors/installed_release/test_g41_package_boundary.py` (CliRunner + non-git-target
  test patterns)

## EXECUTED (changes)

1. **`src/gigai/workpad.py`** — both `NoActiveGigError` raise sites now name the fix:
   `"...; run \`gigai gig use <gig_id>\` to select one"`. No new active-gig setter was needed:
   `select_active_workpad` (module-level, already existed) is the correct, target-kind-agnostic
   primitive and is reused everywhere below.

2. **`src/gigai/cli.py`**
   - New `gigai gig` group with `gigai gig use <gig_id> [--target] [--home] [--json]`: looks the
     gig up via `list_gigs` (installed check), requires `status == "Approved"` (approved check),
     then calls `select_active_workpad(..., allow_semantic_state=True)`. Errors name
     `gigai scout install` / `gigai approve` as appropriate, with stable codes
     (`gig_not_installed`, `gig_not_approved`).
   - Registered `scout_group` from the new module next to the other `scout_*_cli` groups, with a
     comment recording the registration seam as debt (matches the brief's accepted-pattern note).

3. **`src/gigai/scout/template.py`** — new `install_scout(home_root, requested_target)`:
   - Calls `initialize_defaults(inventory=scout_candidate_inventory())` (unchanged;
     `default_inventory()` for other callers was **not** touched — Scout stays out of the release
     inventory, only this explicit candidate-inventory path is used).
   - Resolves the already-bound workspace owner from the registry directly (`find_workspace_owner`)
     instead of `--username`, because `initialize_defaults`'s own saved-username lookup reads
     `<target>/.gigai/project.toml`, which doesn't exist for non-git targets (addendum fix, see
     below).
   - Approves via `approve_offline` only when status is `"approval_required"`.
   - Activates via `select_active_workpad` only when the registry's `active_workpads` row doesn't
     already point at this gig (checked directly against the registry, not
     `<target>/.gigai/project.toml`, so it's correct for both git and non-git targets).
   - Returns `ScoutInstallResult(gig_id, bound, approved, activated)` — each flag `False` on a
     rerun with nothing left to do.
   - New `ScoutInstallError` for the one unreachable-in-practice invalid-state case.

4. **`src/gigai/scout/scout_cli.py`** (new) — `gigai scout` group:
   - `gigai scout install [--target] [--home] [--json]`: calls `install_scout`, then writes the
     starter `find-jobs.json` if missing. Reports `bound`/`approved`/`activated`/
     `wrote_starter_config`/`changed`; plain output names the next step
     (`gigai scout resume add <file>`) or says everything was already in place.
   - `gigai scout resume add <file> [--target] [--home] [--gig] [--json]`: one call —
     `import_reference(kind="resume")` then
     `create_record(kind="imported_reference", content_family="g45_reference", content_id=<reference_id>)`
     — the exact wrapper `run.resolve_newest_resume` requires. Idempotent: `import_reference`
     dedupes by content digest, `create_record` uses a digest/reference-id-derived
     `operation_key`. Prints both ids.
   - `write_starter_find_jobs_config(target_root)`: writes a placeholder
     `find-jobs.json` (obvious `REPLACE_WITH_...` placeholders) only if none exists; the value is
     round-tripped through `FindJobsConfig.from_json` before writing so a future contract change
     fails the test suite instead of shipping a file the API can't parse. Never overwrites.
     Exported for packet C to reuse.

5. **`tests/behaviors/scout_find_jobs/test_scout_setup_cli.py`** (new, 14 cases) — all through
   `click.testing.CliRunner` against `gigai.cli`, from the same non-interactive `gigai setup`
   invocation `release.yml`'s smoke step uses, then `gigai init --target`:
   - install binds+approves+activates+writes starter config from clean; rerun is a full no-op;
     never overwrites an existing `find-jobs.json`.
   - `gig use` errors with `gig_not_installed` (names `gigai scout install`) for an unknown id;
     selects an installed+approved gig.
   - `resolve_workpad` raises `NoActiveGigError` naming `gigai gig use <gig_id>` when Scout is
     bound but not yet active.
   - `resume add` creates the wrapper `run.resolve_newest_resume` resolves; idempotent for the
     same file bytes (same reference id and record id, `created: False` on rerun).
   - starter config round-trips through `FindJobsConfig.from_json` and is never clobbered.
   - **Parametrized `git`/`non-git` for install, rerun, `gig use`, and `resume add`**, per the
     0.1.8.1 addendum (see below).

### Addendum (received mid-task, applied)

The coordinator's addendum flagged that a non-git target has no `<target>/.gigai/project.toml`,
so `gigai gig use` / `gigai scout install` must set the active gig for both target kinds via the
registry (`workpad.select_active_workpad`), not by writing `project.toml` directly. On inspection
this was already true for the CLI command and `select_active_workpad` itself, but **`install_scout`
had two real non-git bugs**, both fixed and covered by the parametrized tests:
- its "already active" idempotency check only looked at `project.toml`, so on a non-git target it
  always reported `activated: True` even on a no-op rerun;
- it required `username=None` to resolve from `project.toml`'s saved binding, so on a non-git
  target `initialize_defaults` always raised `--username is required for non-interactive
  initialization`, even on an already-`gigai init`-bound project.

Both are now resolved via the registry directly (`find_active_workpad`, `find_workspace_owner`),
which is correct and idempotent for both target kinds.

## Verification run

- `uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_scout_setup_cli.py -q`
  → **14 passed** (temp `GIGAI_HOME`/target throughout; `GIGAI_HOME` pointed at a nonexistent path
  to prove no dependency on the operator's real `~/.gigai`).
- Existing tests for every changed/touched function, run explicitly:
  `tests/behaviors/integrity_state/test_workpad_private_git.py`,
  `tests/behaviors/installed_release/test_g05_installed_scenarios.py` (both assert only the
  `"no_active_gig"` substring — preserved),
  `tests/behaviors/installed_release/test_g41_package_boundary.py`,
  `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py`
  → **33 passed, 1 xfailed** (the xfail is the pre-existing, unrelated 0.1.8.1 M1 regression noted
  in that file's own marker).
- `tests/behaviors/cli_surface/` + `tests/behaviors/scout_proposals_tools/` (broader collateral
  check for the `cli.py` group registration and `scout/template.py` addition) → **all passed**.
- `make unit-tests` → **883 passed**, 1269 deselected, 0 failed.

```
$ gigai scout --help
Usage: gigai scout [OPTIONS] COMMAND [ARGS]...

  Install and configure the Scout gig for this project.

Options:
  --help  Show this message and exit.

Commands:
  install  Bind, approve, and activate Scout for the bound project; safe...
  resume   Manage the resume Scout uses for find-jobs runs.

$ gigai gig use --help
Usage: gigai gig use [OPTIONS] GIG_ID

  Make one already-installed, approved Gig the active Gig for this project.

Options:
  --target DIRECTORY
  --home DIRECTORY
  --json
  --help              Show this message and exit.
```

## Debt recorded
- `cli.add_command(scout_group)` in `cli.py` is a core module importing and registering a gig's
  Click group — the same accepted seam as every other `scout_*_cli` group already there. A real
  gig-plugin registration mechanism is 0.2.0 roadmap Workstream 3 scope, not built here (comment
  left in `cli.py` at the registration line).

## What's left (for packet C / others)
- `gigai scout run` itself (starting the API + UI, background/foreground, stop/status, browser
  open, logs) — not in scope here; packet C will call `install_scout`,
  `write_starter_find_jobs_config`, and `gigai gig use` from this packet.
- README "Scout: install and approve" section update to the one-command flow — not an owned file
  here; flagging for whichever packet documents the end-to-end `gigai scout run` flow.
- UI ships in the wheel / CI freshness check (packet A, `present_api.py`/`pyproject.toml`) — out of
  scope, not touched.
