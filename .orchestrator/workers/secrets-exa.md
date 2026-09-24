# secrets-exa — Exa reads API key through gigai secrets lookup

Status: DONE

## READ (evidence gathered, no changes)
- `src/gigai/scout/find_jobs/exa_client.py:1-100` — `_require_api_key()` (was
  :83-90), `EXA_API_KEY_ENV_VAR` (:51), `ExaClientError` (:72-80).
- `src/gigai/secrets_store.py` (full) — `get(name, *, home_root=None)` reads
  `<GIGAI_HOME or ~/.gigai>/.env` via `dotenv_values(..., interpolate=False)`,
  returns `None` for missing/empty; `_default_home_root()` resolves
  `GIGAI_HOME` env var, defaulting to `~/.gigai`.
- `src/gigai/secrets_catalog.py` (full) — `KNOWN_SERVICES = {"exa":
  "EXA_API_KEY", ...}`.
- `tests/behaviors/scout_find_jobs/test_exa_client.py` (full, pre-change) —
  existing coverage for missing key, request shape, error redaction.
- `docs/development/v0.1.8/runbooks/M1-find-jobs.md:140-214` and
  `README.md:175-206` — both told the operator to `export EXA_API_KEY` by
  hand; grepped both files for every `EXA_API_KEY` occurrence and for `pip`
  near the edits (none found).
- `git show a915c24 --stat` — confirmed `gigai secrets add/list/rm` CLI
  already landed in `cli.py` on this branch, so `gigai secrets add exa` is a
  real, already-wired command to reference in docs/error text.

## EXECUTED
1. `src/gigai/scout/find_jobs/exa_client.py`: `_require_api_key()` now tries
   `os.environ.get(EXA_API_KEY_ENV_VAR)` first (falls through on `None` or
   empty string), then `secrets_store.get(EXA_API_KEY_ENV_VAR)`. Kept the
   `ExaClientError` code `exa_missing_key`; new message: `"EXA_API_KEY is not
   set; run \`gigai secrets add exa\` (or export EXA_API_KEY)"`. No secret
   value is ever interpolated into any message/log. Added `from gigai import
   secrets_store` import (scout, a gig, importing gigai core — the allowed
   direction).
2. `tests/behaviors/scout_find_jobs/test_exa_client.py`: added
   `test_api_key_used_from_secrets_store_when_env_unset`,
   `test_env_api_key_wins_over_secrets_store`, and
   `test_empty_env_api_key_falls_back_to_secrets_store`, each using
   `monkeypatch.setenv("GIGAI_HOME", str(tmp_path))` plus `secrets_store.set`
   to write into a temp `.env` (never the operator's real
   `~/.gigai/.env`), and MockTransport only (no live Exa calls). Updated
   `test_missing_api_key_raises_without_leaking` to also set `GIGAI_HOME` to
   an empty temp dir and assert the new message text
   (`"gigai secrets add exa"`) appears and no value leaks.
3. `docs/development/v0.1.8/runbooks/M1-find-jobs.md` (:146-158, :162-163):
   `gigai secrets add exa` is now the primary instruction; `export
   EXA_API_KEY=...` kept as the stated alternative in both the setup section
   and the pre-flight checklist.
4. `README.md` (:192-203 area, "Exa search" section): same — `gigai secrets
   add exa` primary, `export EXA_API_KEY=...` kept as alternative, note
   updated to "Without either, Exa discovery refuses to run...". No `pip`
   references introduced.

## Verification
- `uv run --locked --extra test pytest
  tests/behaviors/scout_find_jobs/test_exa_client.py -q` → **18 passed**.
- `make unit-tests` → **872 passed, 1239 deselected**.

## Scope discipline
- Touched only the owned files: `exa_client.py` (`_require_api_key` only),
  `test_exa_client.py`, the M1 runbook, and the `EXA_API_KEY` lines in
  README.md. Did not touch `present_api.py`, `pyproject.toml`, `cli.py`, or
  `scout_cli.py`.
- `.orchestrator/workers/specs/scout-run-command.brief.txt` shows as modified
  in `git status` but was changed by a different concurrent worker in this
  shared worktree, not by this task — left untouched.
- No `git add`/`commit`/`stash`/`reset`/`clean` run. No live Exa network
  calls (MockTransport only). Never read or printed the operator's real
  `~/.gigai/.env`.
