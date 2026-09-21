# SCOUT-05 public successor preparation CLI

## Scope and result

This bounded lane adds the thin public `gigai capability prepare-successor`
adapter in `src/gigai/capability_cli.py` and focused regressions in
`tests/test_scout05_capability_prepare_cli.py`.  It does not change the
successor service, lifecycle, journal, schemas, bundled source, CLI approval
implementation, or default initialization.

The command requires explicit `--gig`, `--base-version`, `--base-proposal-id`,
`--capability-id`, `--reviewed-manifest-id`, and `--operation-key`, with the
existing `--home`, `--target`, and `--json` options.  UUID identities are
validated before workpad or authority paths are built.  It resolves the exact
registered workpad without creating, migrating, or selecting a Gig, then reads
only the manifest-keyed committed reviewed manifest and passed decision via
the existing journal reader; the service performs its own locked
revalidation.

The adapter returns the pending proposal, proposal and sidecar binding refs,
exact decision/reviewed-manifest refs, and `replayed`.  It also returns a
separately runnable approval argv (`gigai approve <proposal-id> --gig ...
--capability-manifest-id ... --home ... --target ... --json`) and never calls
approval, activation, provider execution, installation, or project selection.
Normal output is an explicit pending/approval-separate diagnostic; JSON uses
the established redacted `{ok:false,error:{code,message}}` refusal shape.

## Focused evidence

The real bundled disposable fixture was reviewed through the public CLI, then
prepared through this public adapter.  The test invokes the returned approval
argv as a separate public command, verifies the active pointer and active Gig
selection remain unchanged until that command, and performs a fresh-process
wrapper create smoke test against the newly approved version.  The same-key
prepare replay returns the original pending proposal/binding without a new
journal head, and stale, changed-working-copy, missing, foreign,
malformed-key, and mismatched manifest-key authority paths refuse before
publication.

Commands and exact results:

* `rtk proxy .venv/bin/pytest -q tests/test_scout05_capability_prepare_cli.py --tb=short` — **5 passed in 15.95s**.
* `rtk proxy .venv/bin/pytest -q tests/test_scout05_capability_cli.py tests/test_scout05_capability_prepare_cli.py --tb=short` — **20 passed in 41.91s**.
* `rtk proxy ruff check src/gigai/capability_cli.py tests/test_scout05_capability_prepare_cli.py` — **All checks passed**.
* `rtk proxy .venv/bin/python -m py_compile src/gigai/capability_cli.py tests/test_scout05_capability_prepare_cli.py` — **passed**.

The combined run includes the unchanged public review command regressions and
the new preparation cases.  No full repository suite, wheel build, provider,
network, commit, or private user `.gigai` state was used.

## Remaining gate

Preparation intentionally creates only a pending successor.  The explicit
operator must separately run the returned approval command; approval remains
responsible for its existing two-commit/recovery and capability-binding
checks.  No automatic activation, provider execution, install, or public
end-to-end shell dogfood is claimed by this adapter evidence.
