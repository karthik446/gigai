# Scout run acceptance — packet D part 2

Worker task: prove `gigai scout run` from an installed wheel (not source), and finish the
README config example. Brief: `.orchestrator/workers/specs/scout-run-command.brief.txt`
(ACCEPTANCE clause). All work EXECUTED unless marked READ.

## Part 1: installed-wheel proof

**Isolation.** All commands ran with `UV_TOOL_DIR`/`UV_TOOL_BIN_DIR` pointed at
scratchpad temp dirs, `GIGAI_HOME` pointed at a scratchpad temp dir, and cwd set to a
scratchpad temp dir outside the repo (`.../scratchpad/workdir`, `.../scratchpad/target`).
No `~/.gigai`, no operator uv tool install, no `git add/commit/stash/reset/clean`.

1. **Build.** `uv build --wheel --out-dir <scratch>/wheel_dist` (visible tab, EXECUTED) →
   `gigai-0.1.8.1-py3-none-any.whl`. Wheel RECORD includes the prebuilt UI
   (`gigai/scout/ui/dist/index.html`, `assets/index-DypzEW5p.js`,
   `assets/index-DDMMrjdp.css`, etc.) — no Vite build needed at install time.
2. **Install.** `uv tool install <wheel>` into isolated `UV_TOOL_DIR`. Confirmed import
   path:
   ```
   python -c "import gigai; print(gigai.__file__)"
   → <scratch>/uv_tool_dir/gigai/lib/python3.13/site-packages/gigai/__init__.py
   ```
   Points into the isolated tool dir, not the source checkout. `gigai --version` →
   `gigai 0.1.8.1`.
3. **Setup.** `gigai setup --non-interactive --home <scratch>/gigai_home --workpad-root
   <scratch>/workpad --editor /usr/bin/true --json` → `{"config_changed":true,...}`.
   (release.yml's `--credential-ref`/`--endpoint`/`--model-target remote=...` flags are
   for a remote adapter smoke test; omitted here since Scout's `default_model_target` is
   `ollama_local` by default — see finding below.)
4. **Init non-git target.** `gigai init --target <scratch>/target --username
   acceptance-test --json` → `"target_kind":"non-git"`, binding created, `scout_status:
   "binding_only_unready"`. Confirmed `<scratch>/target` has no `.git`.
5. **Secrets.** `echo "test-not-a-real-key" | gigai secrets add exa --stdin` → `Stored
   secret for exa.` Verified written under the correct name: `EXA_API_KEY=` (not
   `EXA_SECRET_KEY`) in `<scratch>/gigai_home/.env`.
6. **Scout install (finding, see below).** `gigai scout resume add` first failed with
   `no_active_gig` because Scout wasn't installed/activated yet for this project. Ran
   `gigai scout install --target <scratch>/target --json` →
   `{"activated":true,"approved":true,"bound":true,"changed":true,"wrote_starter_config":true}`.
   This matches U6/U14 in the brief (install + activate in one step) — see Finding 1.
7. **Resume add.** `gigai scout resume add <scratch>/resume.md --json` (a ~10-line
   `.md`) → `{"ok":true,"record_created":true,"reference_created":true,...}`. No
   `gigai record create`, no manual TOML edit.
8. **Edit find-jobs.json (no live network).** Read the starter config Scout wrote, set
   `sources: {exa:false, ats:false, hiringcafe:false}` to disable every live source per
   `FindJobsConfig`/`SourceToggles` in `src/gigai/scout/find_jobs/contracts.py` (READ).
9. **Run.** `gigai scout run --target <scratch>/target --no-browser --port 55417 --json`
   → `{"ok":true,"pid":80662,"port":55417,"url":"http://127.0.0.1:55417",...}`.
10. **Endpoints** (curl, trimmed):
    - `GET /` → `200`, `Content-Type: text/html; charset=utf-8`, body is the Vite
      `index.html` referencing `/assets/index-DypzEW5p.js` and
      `/assets/index-DDMMrjdp.css`.
    - `GET /assets/index-DypzEW5p.js` → `200`, `Content-Type: text/javascript;
      charset=utf-8`.
    - `GET /assets/index-DDMMrjdp.css` → `200`, `Content-Type: text/css; charset=utf-8`.
    - `GET /api/config` → `200`, the starter config plus `resume_preview` (pointing at
      the record/revision from step 7) and `config_digest`.
    - `GET /api/runs/does-not-exist` → `404`,
      `{"error":{"code":"not_found","message":"run not found"}}`.
    - `GET /api/runs/run_00000000-0000-4000-8000-000000000000/progress` → `404`, same
      not-found shape.
11. **Status.** `gigai scout status --target <scratch>/target --json` → `state:
    "running"`, matching `pid`/`url`.
12. **Start a run, no live network.** Read `src/gigai/scout/ui/src/api.js`
    (`buildRunRequest`/`buildConsentEnvelope`) and `RunRequest`/`UIConsentEnvelope` in
    `contracts.py` (READ) to build the POST body: `schema_version
    "scout-find-jobs-run-request:1"`, `consent` (operator/direct_local_ui_confirm,
    generated `invocation_id`/`occurrence_id`), the real `config_digest` from
    `/api/config`, `selection_cap: 10`, `selection_rule:
    "new_or_edited_role_match"`, `model_target: "ollama_local"`. `POST /api/run` → `202`,
    `{"run_id":"run_dc27...","status":"pending","node_receipts":[]}` — accepted.
13. **Outcome.** Polled `/api/runs/{id}` to a terminal state:
    - `acquire` node: `status: "complete"`, `outcome: "COMPLETE"`. Its output evidence
      (`outputs/acquire.json` on disk) shows `"rows":[]`, `"selected_postings":[]`,
      `"failures":[]` — 0 postings, 0 provider calls, consistent with every source
      disabled.
    - `assess` node: `status: "failed"` — `"ScoutProposalExecutionError: no configured
      model target uses adapter 'ollama_local'; run \`gigai setup\` to configure one...
      before assessing with this target"`. This is because the temp `gigai setup` in
      step 3 didn't configure a `model_target` (I omitted release.yml's remote-adapter
      flags since they're for a different adapter kind than Scout's default). The
      overall run status is `"failed"`.
    - `GET /api/runs/{id}/progress` → `{"steps":{"acquire":{"status":"done",...}},
      "postings":[],"assessments":[],"candidate_count":0,"not_assessed_counts":{}}` —
      consistent, no hidden network activity.
    - **No live Exa/ATS/HiringCafe calls at any point** (all three toggles were off).
14. **Teardown.** `gigai scout stop --target <scratch>/target --json` →
    `{"ok":true,"stopped":true}`. Verified: `ps -p 80662` → no such process. `lsof -nP
    -iTCP:55417 -sTCP:LISTEN` → nothing (only a `TIME_WAIT` TCP remnant from the curl
    client side, not a listening/orphan process). `pgrep -f "uv_tool_dir/gigai"` → no
    matches.

### Findings (reported, not fixed — no product code changed)

1. **`gigai scout resume add` (and by extension the "one command" acceptance) requires
   `gigai scout install --target <dir>` to run first** on a fresh non-git target — it
   is not implicit in `gigai init` or in `gigai scout run` when called from a plain
   working directory outside the target. `gigai scout install` without `--target` also
   fails with `GitTargetError` even when cwd *is* the (non-git) target — it needs the
   explicit flag. This is a real gap against the brief's "no Python helper, no manual
   TOML edit, no record create" bar: it's satisfied (no Python/TOML/record-create step
   was needed), but the exact one-shot sequence from the brief text (`setup` → `init` →
   `secrets add` → `resume add` → `run`) is not the sequence that actually works from a
   fresh non-git target; `scout install --target` has to be inserted before `resume
   add`. Not a blocker for the acceptance (still zero manual/Python steps), but the
   brief's flow list is incomplete as written.
2. **Run ends `failed`, not a clean success, when no model target is configured for
   assess** — expected given the temp environment didn't configure
   `default_model_target`'s adapter. This is consistent with the brief's fallback:
   "If the API refuses a run with no sources, report exactly what it says; don't add
   product code to force it." Here the refusal is one node deep (acquire succeeds with
   0 rows; assess is the one that fails, cleanly, with a clear operator-actionable
   message). No provider network call happened at any point.

## Part 2: README

Read `src/gigai/scout/find_jobs/contracts.py` (`FindJobsConfig`) for the exact field
names: `countries: tuple[str, ...]` (ISO-3166-1 alpha-2, validated by
`_COUNTRY_CODE = re.compile(r"\A[A-Z]{2}\Z")`), `visa_sponsorship_required: bool`. Read
`src/gigai/scout/find_jobs/selection.py` for `DEFAULT_PER_COMPANY_CAP = 2`.

Added to the "Scout: install and run" example in `README.md`:
- `"countries": ["US"]` and `"visa_sponsorship_required": false` in the JSON example.
- One line each: countries are ISO codes and a region-only location (e.g. `AMER`,
  `EMEA`) never matches; visa-sponsorship-required excludes "not offered" postings.
- One sentence on per-company diversity (cap of 2 assessed per company).
- One clause noting results appear as cards while the run progresses.

**Validated the exact README example against `FindJobsConfig.from_json()` from the
installed wheel** (EXECUTED, not just read):

```
$ <scratch>/uv_tool_dir/gigai/bin/python -c '...FindJobsConfig.from_json(readme_example)...'
VALIDATED OK: FindJobsConfig(roles=('software engineer', 'data engineer'),
  merged_queries=('software engineer OR data engineer',), location='Denver, CO',
  remote=True, published_after='2026-09-15T00:00:00Z',
  sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
  default_assess_cap=10, default_model_target=<ModelTarget.OLLAMA_LOCAL: 'ollama_local'>,
  countries=('US',), visa_sponsorship_required=False)
```

No parse errors; the example round-trips through the installed package's own contract
class.

## Files touched

- `README.md` — Scout config example + prose (see diff).
- `.orchestrator/workers/scout-run-acceptance.md` — this report.
- No product code, no tests, no git history operations.
