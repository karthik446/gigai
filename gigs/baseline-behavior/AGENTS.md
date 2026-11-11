# Working on this gig (for a coding agent)

This repository is one gigai **gig**: an installable Python package that adds `gigai baseline-behavior ...` to an
installed gigai core. Core owns the home directory, keys, model adapters and the docs hub; this gig owns its
commands, its gig folder `<home>/gigs/baseline-behavior/`, and its docs.

## Rules
1. `src/gigai_baseline_behavior/gig.toml` is the contract. Change it before changing behaviour; keep it valid against
   core's `gig-manifest.schema.json` (`gigai gig check .`).
2. No imports at module level in `cli.py` beyond `click`, `json`, `tomllib`, `importlib`, `typing` (other
   imports only under `TYPE_CHECKING`). Heavy imports go inside the command function. `gigai --help` must stay fast.
3. Never name a provider or read an API key yourself. Core hands every command a `gigai.sdk.GigContext` as
   the Click context object (`@click.pass_obj`): `ctx.folder` is the gig folder, `ctx.store.db(...)` opens a
   SQLite store in it, `ctx.model(step_id, prompt)` calls the model a `[steps]` entry resolves to.
4. Every new command: add it to `provides` in `gig.toml`, add a stanza in `docs/commands.yaml` (JSON), mention
   it in `docs/index.md`, and extend `tests/test_contract.py` and `tests/test_verbs.py`.
5. Do not implement `status`, `doctor`, `config`, `export`, `import`, `eval`, `steps`: they are core's verbs. A gig
   with a `[server]` block of mode `process` also gets core's `run`, `status` and `stop`; do not shadow them.
6. Version lives in `pyproject.toml` only; `gig.toml` has no version field unless you want the check to
   assert equality.
7. This gig's work lives in `baseline.py`, standard library only (no new dependency). The `baseline` store's SQL
   stays in the DoltLite-compatible subset: a primary key on every table, no rowid writes. A schema change is a
   new step appended to `MIGRATIONS`, never an edit of an old one. Snapshot rows are never updated or deleted.
8. The gig sends requests only to the target the operator named: no proxy from the environment, no redirect
   followed, request paths always appended to the target. Tests use a loopback `http.server`, never the network.

## Checks before a PR
```
gigai gig check .
gigai gig install .
pytest
```
Working in a source checkout with its own venv? `uv pip install -e '.[test]'` installs the gig editable instead.
