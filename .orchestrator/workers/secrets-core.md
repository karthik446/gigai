# secrets-core — Workstream 0 (P0-P2), pulled into 0.1.9

Status: done. All acceptance checks pass.

## What was built

- **Dependency (EXECUTED):** `uv add "python-dotenv>=1.0"` → resolved
  `python-dotenv==1.2.3`. Updated `pyproject.toml` (dependencies list only)
  and `uv.lock`. No hand-rolled `.env` parser was written; all reads/writes
  go through `dotenv_values`/`set_key`/`unset_key`. Checked the installed
  version's signatures with `inspect.signature` before using them (EXECUTED)
  — did not guess.
- **`src/gigai/secrets_store.py` (new, P1's storage half):** `secrets_path`,
  `get`, `set`, `remove`, `names_set`, all taking an optional `home_root`
  (defaults to `GIGAI_HOME`/`~/.gigai`, computed locally — see "Design
  note" below on why it doesn't import `setup.default_home_root`). Creates
  the file at mode `0600` if missing; re-`chmod`s to `0600` after every
  write (verified EXECUTED that python-dotenv's own `set_key`/`unset_key`
  already leave the file at `0600` on this version/platform, but the
  explicit re-chmod is kept per the spec as a defensive guarantee, not
  relied on implicitly). Preserves unrelated lines/comments (EXECUTED:
  verified against a real `# comment` + `OTHER_VAR=` fixture). Never logs
  or formats a value into any message.
- **`src/gigai/secrets_catalog.py` (new, P0):** `KNOWN_SERVICES` maps
  `exa`→`EXA_API_KEY`, `openrouter`→`OPENROUTER_API_KEY`,
  `openai`→`OPENAI_API_KEY` — grepped (EXECUTED:
  `grep -rn "_API_KEY\b" src/gigai`) for every `*_API_KEY` env-var gigai's
  own code actually reads (`exa_client.py:51`, `setup_interview.py:215-219`,
  `cli.py:1769-1770`) before seeding the table; no invented services.
  `env_var_for(service)` raises `UnknownServiceError` listing all known
  services on an unknown name.
- **`src/gigai/secrets_cli.py` (new, P1's CLI half) + registration in
  `src/gigai/cli.py`:** `gigai secrets add <service>` (hidden,
  confirmation-prompted `click.prompt`; `--stdin` reads one line for
  scripts/tests; rejects an empty value), `gigai secrets list` (every
  catalog service → `set`/`unset` plus `source`: `environment` or `.env`,
  json or plain; never a value), `gigai secrets rm <service>`. Followed
  `application_cli.py`/`native_records_cli.py` conventions: `--home`,
  `--json`, same error-payload shape (`{"status":"error","error":{"code":
  ...,"message":...}}}`), registered next to the other groups in `cli.py`
  (import line + `cli.add_command(secrets_group)`, placed after
  `transfer_group`) — only the registration lines in `cli.py` were touched.
- **`src/gigai/credentials.py` (P2, resolution order):**
  `resolve_reference_value` now does
  `os.environ.get(reference.reference) or secrets_store.get(reference.reference)`
  for `kind="environment"`. Kind validation, the `secret-manager` refusal
  path, and the existing `CredentialUnavailableError` type/message shape
  are all unchanged. `reference_is_available` (used by `diagnostics.py`,
  `model_execution.py`, and `cli.py`'s own availability display) was
  deliberately **left untouched** — it isn't in the owned-files list for
  this packet (those three call sites aren't), and changing its behavior
  would be a second, uncoordinated change to files another packet/owner is
  responsible for.
- **Roadmap (`docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md`,
  step 6 only):** Workstream 0's "Pull into 0.1.9?" line now leads with
  "Pulled into 0.1.9 by the operator on 2026-09-23 (P0-P3; P4 waits for
  Workstream 1)", keeping the prior reasoning below it for the record.
  Added one Change log entry (r3) naming what was pulled and pointing at
  this report. Nothing else in the roadmap was touched.
- **Tests (all new):** `tests/behaviors/secrets/__init__.py`,
  `test_secrets_store.py` (9 tests: mode-0600-on-create,
  mode-enforced-even-if-pre-existing-loose, comment/other-line
  preservation, remove-only-named-key, remove-is-noop-and-stays-0600,
  get-returns-None-when-missing, names_set tracks add/remove,
  home_root plumbing), `test_secrets_cli.py` (9 tests: `--stdin` add
  stores + never echoes, `--json` add never contains the value, unknown
  service lists known ones, `list` plain and `--json` output show
  set/unset + source and never a value — including the env-wins-for-source
  case via `monkeypatch.setenv`, `rm` removes, `rm` unknown fails, empty
  value rejected), `test_secrets_resolution_order.py` (4 tests: env wins
  over `.env`, `.env` used when env absent, neither → existing
  `CredentialUnavailableError`/`credential_unavailable` code, secret-manager
  kind still refused) — isolates `GIGAI_HOME` per test via
  `monkeypatch.setenv`, matching the existing pattern in
  `tests/behaviors/cli_surface/test_cli_and_scenario_harness.py:257`.

## Design note: avoided a circular import

`setup.py` imports `credentials.validate_reference`; `credentials.py` now
needs `secrets_store` for P2. Importing `setup.default_home_root` from
`secrets_store.py` would have closed that into a cycle
(`credentials → secrets_store → setup → credentials`), confirmed EXECUTED
by trying it (`ImportError: cannot import name 'validate_reference' from
partially initialized module`). `secrets_store.py` instead duplicates
`setup.py`'s exact home-root resolution
(`Path(os.environ.get("GIGAI_HOME", Path.home() / ".gigai")).expanduser()`,
comment cites `setup.py:47-48`) as a small private `_default_home_root()`
rather than importing it. `setup.py` was not in this packet's owned files,
so it wasn't touched to accommodate this.

## Verification (EXECUTED)

- `uv run --locked --extra test pytest tests/behaviors/secrets/test_secrets_store.py tests/behaviors/secrets/test_secrets_cli.py tests/behaviors/secrets/test_secrets_resolution_order.py -q`
  → **20 passed**.
- Existing resolver/http-adapter-touching tests (found via
  `grep -rl "resolve_reference_value\|adapters\.http\|HttpModelAdapter" tests/`):
  `tests/behaviors/runtime_model_boundary/test_model_invocation_foundation.py`
  and `tests/behaviors/cli_surface/test_setup_configuration_diagnostics.py`
  → **22 passed**. No test file directly imports `adapters.http`/
  `HttpModelAdapter`; these two were the actual hits.
- `make unit-tests` → **872 passed, 1224 deselected**.
- `gigai secrets --help` / `add --help` / `list --help` / `rm --help`:

```
Usage: gigai secrets [OPTIONS] COMMAND [ARGS]...

  Store and inspect local provider API keys.

Options:
  --help  Show this message and exit.

Commands:
  add
  list
  rm

Usage: gigai secrets add [OPTIONS] SERVICE

Options:
  --stdin           Read the secret value as one line from stdin instead of
                    prompting.
  --home DIRECTORY
  --json
  --help            Show this message and exit.

Usage: gigai secrets list [OPTIONS]

Options:
  --home DIRECTORY
  --json
  --help            Show this message and exit.

Usage: gigai secrets rm [OPTIONS] SERVICE

Options:
  --home DIRECTORY
  --json
  --help            Show this message and exit.
```

## Exclusions honored

- Never read, printed, or copied the operator's real `~/.gigai/.env` or any
  secret value. All tests use `tmp_path`-derived temp homes via
  `home_root=`/`GIGAI_HOME` monkeypatching; no test touches the real
  `~/.gigai`.
- No schema/storage migration.
- No `git add`/`commit`/`stash`/`reset`/`clean`/`push` run.
- No live/provider network calls; the only network activity was `uv add`
  resolving `python-dotenv` from PyPI (EXECUTED, reported above).
- `src/gigai/scout/**` untouched (P3/Exa is the next packet).
- Only the owned files were touched: `pyproject.toml` (dependencies only),
  `uv.lock`, `src/gigai/secrets_store.py`, `src/gigai/secrets_catalog.py`,
  `src/gigai/secrets_cli.py`, `src/gigai/cli.py` (two registration lines:
  one import, one `add_command`), `src/gigai/credentials.py`,
  `tests/behaviors/secrets/*` (all new), the roadmap's step 6 only, and
  this report.

## r1 — coordinator review fixes (2 bugs + 1 hardening)

Owned files this round: `src/gigai/secrets_store.py`, `src/gigai/secrets_cli.py`,
the three `tests/behaviors/secrets/test_*.py` files, this report (appended).
Nothing else touched.

### Bug 1 — `$`/`${VAR}` interpolation mangled stored values

EXECUTED reproduction first: `echo 'a$b${HOME}c' | gigai secrets add exa
--stdin` then reading it back with the old code (`dotenv_values(path)`,
interpolation on by default) returned `a$b/Users/karc` — the process's own
`$HOME` got substituted into a secret value that happened to contain
`${HOME}` as literal text. Confirmed via a direct `dotenv_values` call
before touching any code.

Fix: every `secrets_store` read (`get`, `names_set`) now calls
`dotenv_values(path, interpolate=False)`. EXECUTED: re-checked the
installed `python-dotenv==1.2.3` signature with `inspect.signature`
(unchanged from r0 — `interpolate: bool = True` was already a documented
parameter, just not being passed) before using it.

Also checked (per the task's second ask) whether `set_key`'s quoting
round-trips other special characters once interpolation is off on read:
EXECUTED a direct `set_key`/`dotenv_values(interpolate=False)` round-trip
for values containing double quotes, single quotes, `#`, spaces, `=`, and
a trailing backslash — all six round-tripped byte-for-byte with no
additional fix needed; `set_key`'s default `quote_mode="always"` was
already sufficient once reads stopped interpolating. Kept as regression
tests (see below) rather than trusting the one-off check.

### Bug 2 — an empty/whitespace-only value counted as "set"

EXECUTED reproduction: writing a bare `EXA_API_KEY=` line, then
`dotenv_values` returned `{"EXA_API_KEY": ""}` — a falsy-but-not-`None`
value that `get()`'s and `names_set()`'s old `is not None` checks both
treated as present, so `gigai secrets list` printed `exa: set (.env)` for
an unset-in-practice key.

Fix: a new `_non_empty()` helper (`value if value and value.strip() else
None`) is applied in both `get()` (returns `None` for empty/whitespace) and
`names_set()` (excludes empty/whitespace names from the set). In
`secrets_cli.py`'s `list_command`, `in_environ` no longer does a bare
`env_var in os.environ` membership check — it reads
`os.environ.get(env_var)` and requires a non-empty, non-whitespace value,
so an empty environment variable also no longer reports `source:
"environment"`/`set: true`. `credentials.resolve_reference_value`
(P2, r0) was not touched — it already rejected a falsy value via its
existing `if not value: raise CredentialUnavailableError(...)` check, which
this fix doesn't change or need to change.

### Hardening — restrictive umask around the write-then-chmod window

`set_key`/`unset_key` write through a temp file and rename; the file only
gets re-chmodded to `0600` immediately after, so under a permissive
process umask (e.g. `0o022`) the temp file (and briefly the renamed
target, before the explicit `chmod`) could be group/world-readable.
`secrets_store.set`/`remove` now wrap the ensure-file + dotenv write +
chmod sequence in a `_restrictive_umask()` context manager
(`os.umask(0o077)` on entry, restoring the caller's prior umask in
`finally`), so no window exists at any umask the caller had. The post-write
explicit `chmod(0600)` is kept as the r0 spec required, now as a
belt-and-suspenders guarantee rather than the only guarantee. EXECUTED:
verified under `os.umask(0o022)` that the file lands at `0600` and that
`os.umask()` afterward reports the caller's original `0o022` was restored
(not the store's own `0o077`).

### New tests (12)

`tests/behaviors/secrets/test_secrets_store.py`:
- `test_set_and_get_round_trip_special_characters_without_interpolation`
  (parametrized ×6: `dollar-and-braces`, `quotes`, `hash`, `spaces`,
  `equals`, `trailing-backslash`)
- `test_empty_stored_value_is_treated_as_unset`
- `test_whitespace_only_stored_value_is_treated_as_unset`
- `test_set_stays_0600_under_a_permissive_process_umask` (new; the r0
  `test_set_creates_file_at_mode_0600` test is kept as-is per the task's
  instruction)

`tests/behaviors/secrets/test_secrets_cli.py`:
- `test_add_via_stdin_round_trips_a_value_with_dollar_signs_and_braces`
- `test_list_treats_an_empty_dot_env_line_as_unset`
- `test_list_treats_an_empty_environment_variable_as_unset`

No new tests were needed in `test_secrets_resolution_order.py` — the
resolver's empty-value rejection already had coverage
(`test_neither_present_raises_existing_error`) and its behavior didn't
change in this round.

### Verification (EXECUTED)

- `uv run --locked --extra test pytest tests/behaviors/secrets/test_secrets_store.py tests/behaviors/secrets/test_secrets_cli.py tests/behaviors/secrets/test_secrets_resolution_order.py -q`
  → **32 passed** (was 20 before this round; +12 new).
- `tests/behaviors/runtime_model_boundary/test_model_invocation_foundation.py`
  + `tests/behaviors/cli_surface/test_setup_configuration_diagnostics.py`
  (the same two files identified in r0 as the actual
  `resolve_reference_value`/`adapters.http`-touching tests) → **22 passed**.
- `make unit-tests` → **872 passed** (deselected count shifted slightly
  from other concurrent workers' unrelated test additions in this shared
  worktree; the secrets suite itself is unaffected since it carries no
  `fast_unit` marker and is deselected from this run either way).

### Exclusions honored

No real `~/.gigai/.env` or secret value was ever read or printed; every
test and manual check used `tmp_path`-derived temp homes or explicit
`home_root=`. No new dependency was added (still just the r0
`python-dotenv` addition). No `git add`/`commit`/`stash`/`reset`/`clean`
run. Only the five owned files/dirs for this round were touched.
