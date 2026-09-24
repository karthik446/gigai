# Worker: p1-secrets-home

**Task:** PR #37 review P1-8 (PLAUSIBLE, confirmed by this worker): the
secrets-store fallback ignored `--home`. `secrets_store.get(name, home_root=None)`
falls back to `GIGAI_HOME`/`~/.gigai`, but every key-lookup caller
(`credentials.resolve_reference_value`, `exa_client._require_api_key`,
`discovery/openai_source._api_key`, `interview_prep/websearch`'s key lookup)
called it with no `home_root`, so `gigai secrets add exa --home X` followed
by `gigai scout run --home X` couldn't find the key unless `GIGAI_HOME` was
also set. Dispatched under Orca task `task_a9236c409bac`. Source:
`.orchestrator/reviews/pr37-review-findings.md`.

**Status:** Done. Owned files changed; reproduction tests failed before the
fix (`TypeError: unexpected keyword argument 'home_root'`, then real
`exa_missing_key`/skip behavior once the signatures existed) and pass after.
Every listed acceptance case is covered.

READ vs EXECUTED: READ `secrets_store.py`, `credentials.py`, `exa_client.py`,
`market_acquisition.py` (the `_acquire_node_body`/`acquire_node` chain and
`bindings.py`'s `_register_nodes`/`_child_worker_entry`, to confirm
`home_root` already reaches `acquire_node` and the Exa client construction
site), `discovery/__init__.py`'s `run_discovery`, `discovery/openai_source.py`,
`interview_prep/prep.py`'s `build_prep`, `interview_prep/company_research.py`,
`interview_prep/websearch.py`, `scout_cli.py` (`secrets`/`scout run`/`scout
discover`/`scout prep` `--home` plumbing), `present_api.py`'s
`ScoutFindJobsBackend.__init__`/`register_find_jobs_nodes` call (read-only,
confirms `home_root` already reaches the API child's node registration and
`run_discovery` call -- not touched, owned by other workers), and
`run_supervisor.py`'s `start`/`_run_foreground` (read-only, confirms
`home_root` is already passed to the spawned child's argv and to
`ScoutFindJobsBackend`). EXECUTED: the new/updated tests below and `make
unit-tests`.

## Root cause and fix

`secrets_store.get`/`set` already took an explicit `home_root` (used
correctly by `secrets_cli.py`'s `add`/`list`/`rm`, and already threaded down
to `acquire_node`, `run_discovery`, and `build_prep` from their CLI/API
entry points). The gap was narrower than "ignored `--home`" end to end: the
*outer* plumbing (CLI `--home` -> `home_root` -> `acquire_node`/
`run_discovery`/`build_prep`) was already correct; only the leaf key-lookup
functions never accepted or forwarded `home_root` down to
`secrets_store.get`. Fix: added an optional, keyword-only `home_root: Path |
None = None` at each leaf and threaded it from the already-available
`home_root` at each owned call site. Every signature stays backward
compatible (new param, default `None`, environment variable still checked
first).

Chain, entry point -> leaf:

- `credentials.resolve_reference_value(reference, *, home_root=None)` -- the
  generic env/secret-manager resolver used by `adapters/http.py`'s default
  credential resolver. `adapters/http.py` itself was not touched: its
  `credential_resolver` is already an injectable callable (no owned call
  site in this packet's scope constructs it with a fixed non-default home to
  wire through), so no change was needed there per the task's "only if its
  resolver needs the home" scope.
- `market_acquisition._acquire_node_body` (already had `home_root` in scope)
  -> `exa.search(active_client, input.config, home_root=home_root)` ->
  `ExaSearchClient.search(self, client, config, *, home_root=None)` ->
  `_require_api_key(home_root=home_root)` -> `secrets_store.get(...,
  home_root=home_root)`.
- `discovery.run_discovery` (already had `home_root` in scope) ->
  `openai_source.run(..., home_root=home_root)` -> `_api_key(home_root=home_root)`.
- `interview_prep.prep.build_prep` (already had `home_root` in scope) ->
  `company_research.research_company(..., home_root=home_root)` ->
  `websearch.api_key_present(home_root=home_root)` /
  `websearch.web_search_structured(..., home_root=home_root)` ->
  `openai_source._api_key(home_root=home_root)`.

## Protocol change (coordinator-approved, outside the original owned-files list)

`ExaSearchClient` (a `Protocol` in `contracts.py`, not originally an owned
file) declared `search(self, client, config) -> tuple[PostingRow, ...]`
with no `home_root`. Adding `home_root=home_root` at the
`market_acquisition.py` call site made Pyright flag the call against the
Protocol type. Asked the coordinator (`orca orchestration ask`); approved
adding the one keyword-only, optional `home_root: Path | None = None`
parameter to that Protocol signature, with the instruction to also update
every implementation (the real client and test fakes/stubs) so the type
check and runtime both hold. Did both:

- `contracts.py`: `ExaSearchClient.search` gained `*, home_root: Path | None
  = None`; added `from pathlib import Path` to the existing `TYPE_CHECKING`
  block (module has `from __future__ import annotations`, so this is
  type-checker-only, no runtime import).
- Test fakes implementing the Protocol (not originally owned, but broken by
  the Protocol change and fixed per the coordinator's instruction):
  `tests/behaviors/scout_find_jobs/test_acquire_network.py` (`_Exa`,
  `_FailingExa`) and `tests/behaviors/scout_find_jobs/test_progress.py`
  (`_Exa`, two `_FailingExa` locals) -- each gained `*, home_root=None` on
  `search`. Before this fix all four were failing with a caught `TypeError`
  masquerading as `exaclienterror` (see `test_missing_exa_api_key_with_only_exa_enabled_raises`'s
  before/after: asserted string changed from `exa:typeerror` back to the
  real `exa:exaclienterror`).

`ATSBoardClient`'s protocol/implementation were not touched -- no key lookup
runs through it, out of this bug's scope.

## Files changed

- `src/gigai/credentials.py` -- `resolve_reference_value(..., *, home_root=None)`.
- `src/gigai/scout/find_jobs/contracts.py` -- `ExaSearchClient.search` gains
  `home_root` (coordinator-approved, see above).
- `src/gigai/scout/find_jobs/exa_client.py` -- `_require_api_key(*,
  home_root=None)`, `ExaSearchClient.search(..., *, home_root=None)`.
- `src/gigai/scout/find_jobs/market_acquisition.py` -- one-line call-site
  change: `exa.search(active_client, input.config, home_root=home_root)`.
- `src/gigai/scout/find_jobs/discovery/openai_source.py` -- `_api_key(*,
  home_root=None)`, `run(..., home_root=None)`.
- `src/gigai/scout/find_jobs/discovery/__init__.py` -- one-line call-site
  change: `openai_source.run(..., home_root=home_root)`.
- `src/gigai/scout/interview_prep/websearch.py` -- `api_key_present(*,
  home_root=None)`, `web_search_structured(..., home_root=None)`.
- `src/gigai/scout/interview_prep/company_research.py` --
  `research_company(..., home_root=None)`, forwards to `websearch`.
- `src/gigai/scout/interview_prep/prep.py` -- one-line call-site change:
  `company_research.research_company(..., home_root=home_root)`.
- `tests/behaviors/secrets/test_secrets_home.py` (new) -- reproduction +
  regression: `resolve_reference_value` finds a key stored under an
  explicit `home_root` even with `GIGAI_HOME` pointed at a decoy home that
  never has the key; backward compatibility (omitting `home_root` keeps
  today's env-then-default-home behavior); a key in a *different* explicit
  home still isn't found; env still wins over an explicit `home_root`. Plus
  the CLI-level acceptance case: `CliRunner` invokes the real `gigai secrets
  add exa --stdin --home X` command, then `ExaSearchClient().search(...,
  home_root=X)` finds it -- proves the real CLI command, not just
  `secrets_store.set` called directly. (A full `gigai scout run --home X
  --no-browser` spawning the real background API process was not built:
  that crosses into `present_api.py`/`run_supervisor.py`, owned by other
  workers and out of this packet's file list; read-only tracing above
  confirms `home_root` already reaches that child's `run_discovery`/
  `acquire_node` calls unchanged by this fix, so the CLI-level test above is
  the acceptance proof within this packet's owned surface.)
- `tests/behaviors/scout_find_jobs/test_exa_client.py` -- two new tests:
  `search()` finds a key stored under an explicit `home_root` (with
  `GIGAI_HOME` pointed at a decoy); omitting `home_root` doesn't leak a key
  from a different explicit home.
- `tests/behaviors/scout_find_jobs/test_discovery_openai.py` -- one new
  test: `run(..., home_root=X)` finds a key stored under `X` with
  `GIGAI_HOME` pointed at a decoy.
- `tests/behaviors/scout_find_jobs/test_interview_prep_company_research.py`
  -- one new test: `research_company(..., home_root=X)` finds a key stored
  under `X`, verified via the outgoing request's `Authorization` header.
- `tests/behaviors/scout_find_jobs/test_acquire_network.py`,
  `tests/behaviors/scout_find_jobs/test_progress.py` -- test-fake signature
  fixes required by the `contracts.py` Protocol change (see above).
- `.orchestrator/workers/p1-secrets-home.md` (this file).

`src/gigai/adapters/http.py`, `present_api.py`, `run_supervisor.py` were not
touched (out of scope / owned by other workers, per the task).

## Test budget used

```
uv run pytest \
  tests/behaviors/secrets/test_secrets_resolution_order.py \
  tests/behaviors/secrets/test_secrets_home.py \
  tests/behaviors/secrets/test_secrets_cli.py \
  tests/behaviors/secrets/test_secrets_store.py \
  tests/behaviors/scout_find_jobs/test_exa_client.py \
  tests/behaviors/scout_find_jobs/test_discovery_openai.py \
  tests/behaviors/scout_find_jobs/test_interview_prep_company_research.py \
  tests/behaviors/scout_find_jobs/test_acquire_network.py \
  tests/behaviors/scout_find_jobs/test_progress.py \
  tests/behaviors/scout_find_jobs/test_interview_prep_end_to_end.py \
  tests/behaviors/scout_find_jobs/test_interview_prep_trigger.py \
  -q
```
-> 141 passed.

`make unit-tests` -> 1023 passed, 1455 deselected.

No `git add`/`commit`/`stash`/`reset`/`clean`/`push` used.

## r1: coordinator review gap -- MODEL adapters (assess/interview-prep) still ignored `--home`

**Task:** p1-secrets-home-r1 (Orca task `task_255d78123225`). Coordinator
review of the r0 packet accepted everything above, but flagged one gap: the
*model* adapters (`adapters/http.py`'s `HttpModelAdapter`, used by
`OpenAIAPIAdapter`/`OpenRouterAPIAdapter`) still resolved credentials with
no `home_root` -- `resolve_model_adapter` (`adapters/factory.py`, the sole
production adapter factory) built those adapters with no
`credential_resolver`, so they fell back to `HttpModelAdapter`'s own
default (`resolve_reference_value` with no `home_root`), regardless of
`--home`. This hits `assess` (openrouter_api targets, via
`proposal_execution.py`) and interview prep's category prediction (via
`interview_prep/categories.py`), not just the find-jobs.json-scoped
lookups r0 fixed.

READ vs EXECUTED (r1): READ `adapters/factory.py`'s `resolve_model_adapter`
in full (traced every `openai_api`/`openrouter_api` construction branch),
`adapters/http.py`/`openai_api.py`/`openrouter_api.py`'s constructors,
`proposal_execution.py`'s `_assess_node_body` (confirmed `home_root` already
in scope at its `resolve_model_adapter` call), `interview_prep/categories.py`'s
`predict_categories` (did NOT have `home_root` at all -- added it) and its
one caller in `prep.py`'s `build_prep` (confirmed `home_root` in scope),
`test_model_invocation_foundation.py` in full (found and respected the
`test_raw_credential_resolution_is_called_only_by_http_transport` boundary
test on the first attempt's revert-and-redo), and grepped every
`resolve_model_adapter(...)` call site + every test monkeypatching it, to
scope exactly which fakes needed a signature update. EXECUTED: the new/
updated tests below, the full `runtime_model_boundary` and
`scout_proposals_tools` suites (not just the acceptance-listed files, to
catch any other fake/call-site break from touching `http.py`), and `make
unit-tests`.

### False start (caught before landing): the credential-resolution boundary

First pass added `functools.partial(resolve_reference_value, home_root=...)`
directly inside `adapters/factory.py`. That broke
`test_model_invocation_foundation.py::test_raw_credential_resolution_is_called_only_by_http_transport`,
which asserts by AST walk that `resolve_reference_value` is imported by
exactly one module (`adapters/http.py`) -- a deliberate boundary keeping raw
credential resolution below the model port, out of the factory. Asked the
coordinator (`orca orchestration ask`) before proceeding differently;
approved: give `HttpModelAdapter.__init__` (already an owned file) an
optional `home_root: Path | None = None` that it uses to build the default
resolver *internally* (`functools.partial(resolve_reference_value,
home_root=home_root)`) only when the caller passes no explicit
`credential_resolver` -- an explicit resolver still wins unconditionally,
unaware of `home_root`. `OpenAIAPIAdapter`/`OpenRouterAPIAdapter` gained a
`home_root` passthrough param forwarded to `super().__init__`.
`adapters/factory.py` then just passes `home_root=home_root` into those two
constructors -- no `resolve_reference_value` import in `factory.py` at all,
boundary test intact. Verified the revert-then-reapply actually reproduced
the `TypeError`/boundary failures before landing the corrected version (not
just asserted it would).

### Fix

- `adapters/http.py`: `HttpModelAdapter.__init__` gained `home_root: Path |
  None = None`; `self._credential_resolver = credential_resolver or
  functools.partial(resolve_reference_value, home_root=home_root)`
  (previously `... or resolve_reference_value`).
- `adapters/openai_api.py`, `adapters/openrouter_api.py`: both gained a
  `home_root` passthrough param, forwarded to `HttpModelAdapter.__init__`.
- `adapters/factory.py`: `resolve_model_adapter` gained `home_root: Path |
  None = None`; the `openai_api`/`openrouter_api` construction branches pass
  `home_root=home_root` to their adapter constructors. No new import (kept
  the boundary test green). `deterministic`/`codex_cli`/`claude_cli`/
  `ollama_local` branches untouched -- none resolve a credential.
- `scout/proposal_execution.py`: one-line call-site change --
  `resolve_model_adapter(config, adapter_target, home_root=home_root)`
  (`home_root` was already a parameter of the enclosing `_assess_node_body`).
- `scout/interview_prep/categories.py`: `predict_categories` gained
  `home_root: Path | None = None`, forwarded to `resolve_model_adapter`.
- `scout/interview_prep/prep.py`: one-line call-site change --
  `predict_categories(..., home_root=home_root)`.

Every signature stays backward compatible (new param, default `None`;
omitting it keeps today's resolver). `model_execution.py`,
`question_generation.py`, `builder.py`, `model_discovery.py`,
`diagnostics.py`, `lifecycle.py` (other `resolve_model_adapter` callers) are
untouched and unaffected -- they never pass `home_root`, so they keep
getting the pre-r1 default resolver exactly as before.

### Test fakes fixed (mechanical, required by the new keyword arg)

- `tests/behaviors/scout_find_jobs/test_assess_model_policy.py`: two
  `resolve_model_adapter` monkeypatch fakes (`lambda config,
  adapter_target: binding` and `_fake_resolve(config, adapter_target)`)
  gained `**_kwargs` -- `proposal_execution.py` now calls with
  `home_root=` as a keyword.
- `tests/behaviors/scout_find_jobs/test_interview_prep_categories.py` (4
  occurrences) and `test_interview_prep_end_to_end.py` (5 occurrences):
  `lambda cfg, target: binding`/`lambda cfg, tgt: binding` fakes for
  `categories.resolve_model_adapter` gained `**_kwargs`.

### New tests (`tests/behaviors/secrets/test_secrets_home.py`)

- `test_factory_builds_home_aware_credential_resolver_for_remote_adapters`:
  a key stored via `secrets_store.set(..., home_root=X)` (`GIGAI_HOME`
  pointing at an unrelated decoy home) is found by the real
  `resolve_model_adapter(config, target, home_root=X)`-built
  `openrouter_api` adapter -- verified via the outgoing request's
  `Authorization` header on a `MockTransport` client injected onto
  `binding.port._client` (the factory has no client-override parameter for
  remote adapters, only `ollama_local`'s `transport_overrides`). Confirmed
  as a true repro: reverted the fix (`git checkout --` the 4 touched
  production files, saved as a patch first), re-ran, got the exact
  `TypeError: resolve_model_adapter() got an unexpected keyword argument
  'home_root'` failure, then reapplied the patch and re-ran to green.
- `test_factory_without_home_root_keeps_default_resolver`: omitting
  `home_root` from `resolve_model_adapter` keeps today's default resolver;
  a key stashed under a different explicit home is not found (asserts
  `CredentialUnavailableError` from calling `binding.port._credential_resolver`
  directly, since no HTTP request should even be attempted).

### Test budget used (r1)

```
uv run pytest \
  tests/behaviors/secrets/test_secrets_home.py \
  tests/behaviors/secrets/test_secrets_resolution_order.py \
  tests/behaviors/scout_find_jobs/test_assess_model_policy.py \
  tests/behaviors/scout_find_jobs/test_interview_prep_categories.py \
  tests/behaviors/scout_find_jobs/test_interview_prep_end_to_end.py \
  tests/behaviors/runtime_model_boundary/test_model_invocation_foundation.py \
  -q
```
-> 53 passed (all acceptance-listed files green, including the boundary
test).

Also ran, beyond the acceptance list, since `http.py`/`openai_api.py`/
`openrouter_api.py` are shared transport code:
- `uv run pytest tests/behaviors/runtime_model_boundary/ -q` -> 162 passed,
  1 skipped (`test_g30_live_cli.py`, opt-in real-CLI UAT, expected).
- `uv run pytest tests/behaviors/scout_proposals_tools/ -q` -- in progress
  at write time; `model_execution.py`'s `resolve_model_adapter` call site is
  untouched (no `home_root` passed), so no fake in this suite should be
  affected, but running it to be sure before `worker_done`.
- `uv run pytest tests/behaviors/secrets/ tests/behaviors/scout_find_jobs/test_exa_client.py tests/behaviors/scout_find_jobs/test_discovery_openai.py tests/behaviors/scout_find_jobs/test_interview_prep_company_research.py tests/behaviors/scout_find_jobs/test_acquire_network.py tests/behaviors/scout_find_jobs/test_progress.py -q` (the full r0 acceptance set) -> 131 passed, confirming no r0 regression.

`make unit-tests` -> pending at write time; will confirm before `worker_done`.

No `git add`/`commit`/`stash`/`reset`/`clean`/`push` used (the revert/reapply
above used `git checkout --` on unstaged files, saved to and restored from a
patch file in the scratchpad, never touching the index or history).
