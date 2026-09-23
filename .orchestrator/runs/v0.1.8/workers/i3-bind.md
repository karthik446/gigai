# I-3 bind and M1 integration handoff

## State

Implemented the final Scout integration seam in the owned files. The real
acquire, assess, and present callables are registered for
`find-jobs-functional` v1 and for the sealed compiled graph identity; the
registration hook runs again in I-2's spawned child before scheduler goals
execute. The localhost API now uses the real Run backend, and the new M1
behavior test proves two complete offline traversals including unchanged-row
deduplication.

## Files changed

- `src/gigai/scout_find_jobs_bindings.py` (new)
  - Builds one real timeout-configured `httpx.Client`, `ExaSearchClient`,
    `ATSBoardClients`, and journal-backed A-5 watchlist per binding.
  - Registers A-6, B-2, and C-1 with the frozen capabilities/effects via
    `functools.partial`; `EXA_API_KEY` remains a call-time Exa concern.
  - Installs a module-level child-process wrapper around I-2's worker entry;
    the child re-registers the process-local registry before executing goals.
  - Adds the compiled `graph_*` identity as an execution alias because I-1's
    selector (`find-jobs-functional`) and the sealed graph's canonical ID are
    distinct.
  - Provides test-only environment seams for child-local `httpx.MockTransport`
    fixtures for Exa/Greenhouse and Ollama-compatible model responses.
  - Keeps B-2 and C-1 unchanged while adapting I-2's current artifact paths and
    the narrow-vs-full posting DTO boundary; unchanged A-6 rows are surfaced as
    `not_assessed: unchanged` on a repeat run.
- `src/gigai/scout_present_api.py`
  - Adds `ScoutFindJobsBackend`: canonical target config bytes, newest-resume
    preview, `launch_find_jobs_run(..., ui_loopback_verified=True)`, and C-1
    payload/status/results reads.
  - Maps typed pre-allocation digest mismatch to 409 and consent/loopback Run
    errors to 403; the required 504 route declaration is in the contracts.
  - Wires the `--target` `__main__` path to the real backend; the existing
    injected handler routes remain unchanged except for the required typed
    boundary mapping.
- `src/gigai/scout_find_jobs_contracts.py`
  - Adds 504 to `POST /api/run` route statuses.
  - Removes unused `NotAssessedReason` compatibility aliases after a repo grep.
- `tests/behaviors/scout_find_jobs/test_m1_end_to_end.py` (new)
  - Creates a fresh Git target/home/workpad, installs the Scout candidate with
    the same normal `scout_candidate_inventory()` helper used by sibling
    behavior tests, imports a committed resume through the reference path,
    starts the real API on an ephemeral loopback port, and launches real
    multiprocessing Run children.
  - Requires all three real node receipts and asserts rows, matrix,
    suggestions, questions, reasons, pinned resume, and second-run unchanged
    deduplication. Provider/model traffic is entirely `MockTransport` and is
    injected through environment seams so the spawned child constructs it.
- `docs/development/v0.1.8/runbooks/M1-find-jobs.md` (new)
  - Documents setup, Scout install/approval, resume/config, Ollama/API/UI
    commands, failure reading, and the verified-vs-untested boundary.

`src/gigai/scout_materialization.py` was already dirty in the shared worktree;
I did not edit it. No other unowned files were changed, and I did not run
`git add`, `commit`, `stash`, `reset`, or `clean`.

## READ

- `AGENTS.md`, RTK instructions, `.claude/skills/gigai-orchestrator/SKILL.md`.
- `.orchestrator/decisions.log`, `.orchestrator/status.md`,
  `.orchestrator/workers/spec-i3-bind.txt`, and the I-2, C-2, C-2b, A-6, B-2,
  C-1, and Terra handoffs.
- I-2's `run.py` scheduler/child entry and sealed output paths; the real A-1,
  A-3, A-5, A-6, B-2, and C-1 signatures; the contracts and route DTOs; the
  setup/reference/init helpers used by sibling Scout behavior tests.

## EXECUTED

```text
.venv/bin/pytest -q tests/behaviors/scout_find_jobs/test_m1_end_to_end.py
1 passed in 27.67s

uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_present_ui.py -q
18 passed in 7.63s

uv run --locked --extra test pytest tests/behaviors/scout_find_jobs -q -k \
  'not test_routes_bind_types_methods_paths_and_statuses_exactly'
270 passed, 1 deselected in 61.69s

uv run --locked python -c \
  "import gigai.scout_find_jobs_bindings, gigai.scout_present_api"
passed (the final retry used escalation because the shared uv cache was
intermittently unreadable inside the sandbox)

.venv/bin/python -m py_compile \
  src/gigai/scout_find_jobs_bindings.py \
  src/gigai/scout_present_api.py \
  tests/behaviors/scout_find_jobs/test_m1_end_to_end.py
passed
```

The exact required directory command was also run:

```text
uv run --locked --extra test pytest tests/behaviors/scout_find_jobs -q
270 passed, 1 failed in 61.66s
```

The one failure is the pre-existing unowned exact assertion at
`tests/behaviors/scout_find_jobs/test_contracts.py:477`, which still expects
`POST /api/run` statuses without 504. The implementation correctly includes
504 as required by I-3; I left that test untouched because the task explicitly
limited ownership to the new M1 test and the listed source files. Orca was
unavailable during the run (`Could not connect to the running Orca app`), so
the required coordinator `check`/`ask` could not be delivered; no provider,
model, or live external network calls were made, and the ephemeral test server
was shut down in the test's `finally` block.

## Evidence boundary

The M1 pass is offline source-checkout proof only: child process registration,
real node execution, durable receipts, C-1 projection, and deterministic
provider/model seams are proven. Live Exa/ATS/Ollama calls, the visible Vite
click, installed-wheel behavior, and release acceptance remain untested.
