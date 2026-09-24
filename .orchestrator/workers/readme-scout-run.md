# readme-scout-run — packet D part 1 for `gigai scout run` (U7)

Status: DONE. Docs only, README.md.

## READ (context, no changes)
- `.orchestrator/workers/scout-setup-cmds.md` (packet B — `gigai scout install`,
  `gigai gig use`, `gigai scout resume add`, starter `find-jobs.json` writer).
- `.orchestrator/workers/scout-run-supervisor.md` (packet C — `gigai scout run/status/stop`,
  log path `<home>/logs/scout-<project_id>.log`, state file
  `<home>/run/scout/<project_id>.json`, `--foreground`/`--no-browser`/`--port`, health check,
  `config_missing`/`resume_missing_hint` error text).
- `.orchestrator/workers/scout-ui-package.md` (packet A — UI now ships prebuilt in the wheel via
  `importlib.resources`; `ui/dist` is package-data; `scout-ui-freshness` CI job fails a PR if the
  built bundle is stale).
- `.orchestrator/workers/secrets-core.md`, `secrets-exa.md` (`gigai secrets add/list/rm`; Exa
  reads `EXA_API_KEY` from the environment first, then the `.env` secrets store; neither present →
  Exa discovery refuses, ATS unaffected).
- `README.md` (full file, before editing) — found and removed all four stale-flow sections the
  brief named: "Scout: install and approve" (internal-Python-interpreter hack +
  `gigai approve`), the `project.toml` manual-edit "Setting the active Gig" section, the manual
  `gigai record create --kind imported_reference ...` wrapper section, and "Start the API" /
  "The UI is not in the wheel" (`python -m gigai.scout.find_jobs.present_api`, `yarn dev` as the
  only path, "not bundled" claim — now false per packet A).

## EXECUTED (verification — real commands, temp GIGAI_HOME + temp non-git target)

All of this ran from this worktree via `uv run --locked gigai ...`, never against
`~/.gigai` (confirmed after: `ls ~/.gigai/run/scout/` → does not exist). Full transcript,
trimmed to the interesting output; `--home`/`--target` point at
`/private/tmp/.../scratchpad/readme-demo/{home,target}`:

```
$ gigai setup --non-interactive --home $GIGAI_HOME --workpad-root $GIGAI_HOME-workpads \
    --editor /usr/bin/true --json
{"config_changed":true,...,"schema_version":"2.0",...}

$ gigai init --target $TARGET --username "readme-demo" --json
{"adopted":false,"binding_created":false,...,"instances":[...]}

$ echo "sk-test-fake-exa-key-not-real" | gigai secrets add exa --home $GIGAI_HOME --stdin --json
{"ok":true,"service":"exa"}
$ gigai secrets list --home $GIGAI_HOME --json
{"secrets":[{"env_var":"EXA_API_KEY","service":"exa","set":true,"source":".env"},...]}

$ gigai scout install --home $GIGAI_HOME --target $TARGET --json
{"activated":true,"approved":true,"bound":true,"changed":true,
 "gig_id":"gig_2386f0ff-...","ok":true,"wrote_starter_config":true}

$ cat $TARGET/find-jobs.json     # starter, placeholders as documented
{"default_assess_cap":10,"default_model_target":"ollama_local",
 "location":"REPLACE_WITH_YOUR_LOCATION (e.g. Denver, CO, or null for any)",...}

$ gigai scout resume add $SCRATCH/resume.txt --home $GIGAI_HOME --target $TARGET --json
{"ok":true,"record_created":true,"record_id":"record_2e014bfa-...",
 "reference_created":true,"reference_id":"ref_0b05021c-...",...}

$ gigai gig use gig_2386f0ff-... --home $GIGAI_HOME --target $TARGET --json
{"active":true,"gig_id":"gig_2386f0ff-...","ok":true}

# edited find-jobs.json to real values (roles/location/etc, matching README's example)

$ gigai scout run --home $GIGAI_HOME --target $TARGET --no-browser --json
{"cleaned_stale":false,"log_path":".../home/logs/scout-project_....log","ok":true,
 "pid":20174,"port":8765,"reused":false,"url":"http://127.0.0.1:8765"}

$ gigai scout status --home $GIGAI_HOME --target $TARGET --json
{"ok":true,"pid":20174,"state":"running","url":"http://127.0.0.1:8765",...}

$ curl -s http://127.0.0.1:8765/api/health
{"status": "ok"}
$ curl -s http://127.0.0.1:8765/api/config
{"schema_version":"scout-find-jobs-config-response:1","config":{...},
 "resume_preview":{"record_id":"record_2e014bfa-...",...},"resume_missing_hint":null,...}
$ curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8765/
200

$ gigai scout stop --home $GIGAI_HOME --target $TARGET --json
{"ok":true,"stopped":true}
$ gigai scout status --home $GIGAI_HOME --target $TARGET --json
{"ok":true,"pid":null,"state":"stopped","url":null,...}
$ ps -p 20174   # empty
$ lsof -i :8765 # empty
```

No live Exa/ATS/provider call was made (`--no-browser`, never hit "Run" in the UI; the
`sk-test-fake-exa-key-not-real` value was never used against a live endpoint). Confirmed no
orphan process or bound port after `stop`.

Also executed (help text used to write the docs, not narrative):
`gigai scout --help`, `gigai scout install/run/status/stop/resume/resume add --help`,
`gigai secrets --help` / `secrets add --help`, `gigai gig use --help`, `gigai init --help`,
`gigai setup --help`. Confirmed `gigai scout install` alone already activates Scout when it's
the project's only bound Gig (no separate `gig use` call needed in the common case) — `gig use`
is documented as the multi-Gig-switch command, not a required step.

## CHANGE (README.md only)

- Replaced "Scout: install and approve" with "Scout: install and run": `gigai scout install` →
  `gigai secrets add exa` → `gigai scout resume add` → edit `find-jobs.json` (same example JSON
  kept, now under this section) → `gigai scout run` / `status` / `stop`. Documented
  `--foreground`/`--no-browser`/`--port`, and where logs/state live (from packet C's report).
  Kept `gigai gig use` documented for the multi-Gig-switch case, with a note that `scout install`
  already activates it in the common single-Gig case.
- Removed "Setting the active Gig" (the manual `project.toml` edit) — superseded by `gig use`.
- Removed "Wrapping an imported resume for find-jobs" (the manual `record create`) — superseded
  by `gigai scout resume add`.
- Removed "Start the API" (`python -m gigai.scout.find_jobs.present_api`) and "The UI is not in
  the wheel" (stale — packet A now ships it) — replaced with a labelled "From a source checkout
  (contributors)" note: the wheel ships the built UI, `yarn dev` is only for contributors editing
  `src/gigai/scout/ui/` itself, plus a pointer at the `scout-ui-freshness` CI check.
- Kept "Exa search" section as-is (already correct from the `secrets-exa` packet); referenced it
  from the new quickstart instead of duplicating its content.
- Changed one now-inaccurate forward-reference ("v0.1.9 replaces this with `gigai scout
  setup/run`") on the *model-target-naming* callout under "Setup" to "0.1.8.x limit." — that
  callout is about `gigai setup`'s target-naming behavior, not the install/run flow, and isn't
  superseded by anything in this packet; left its actual content untouched (not an owned-file
  section per the brief, only the stale forward-reference wording was wrong).
- Not touched: "Add a resume reference" (`gigai reference add --kind resume`) — this is still a
  real, valid path for other reference kinds (`project_evidence`, `role_history`,
  `cover_letter`); only the resume-specific find-jobs wrapper step was replaced by `scout resume
  add`, which itself calls `reference add` internally per packet B.

## Acceptance

```
$ grep -nE 'pip install|python -m gigai.scout.find_jobs.present_api|yarn dev|record create' README.md
198:yarn dev
```
The one hit is inside the labelled "From a source checkout (contributors)" section (verified by
inspection — line 198 sits between the `### From a source checkout (contributors)` heading and
the `yarn build`/CI-freshness note that follows it). No other matches.

## Exclusions honored
Temp homes only (`GIGAI_HOME`/`--target` under the session scratchpad); never `~/.gigai`
(confirmed via `ls ~/.gigai/run/scout/` → not found). No live Exa/ATS/provider call — the UI's
"Run" button was never clicked, only `/api/health` and `/api/config` were curled. No code
changes. No `git add`/`commit`/`stash`/`reset`/`clean`. No tests run (not required; verification
was command execution, per the brief). Only owned files touched: `README.md`,
`.orchestrator/workers/readme-scout-run.md`.
