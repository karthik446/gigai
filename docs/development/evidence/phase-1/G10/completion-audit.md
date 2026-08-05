# G10 Completion Audit — Phase 1 Offline Local Spine

- Goal: [G10 — Phase 1 Completion Audit](../../../goals/phase-1/G10-phase-1-completion-audit.md)
- Date: 2026-08-05
- Completion result: Go — hosted confirmation passed
- Baseline: `a1f5af49c690b90440f556591cf32fc203bd097c` (merged G09)

## Scope and decision rule

This audit proves the offline local spine only. It does not certify provider
availability, model quality, scheduling, Run execution, target mutation beyond
the documented `init` binding, or the deferred adapter matrix. G11's two local,
opt-in live checks are redacted operator evidence and are excluded from the
offline scenario set and this gate.

The local source, artifact, and audit checks below pass. Hosted
[push CI run 31031739148](https://github.com/karthik446/gigai/actions/runs/31031739148)
and [pull-request CI run 31031999965](https://github.com/karthik446/gigai/actions/runs/31031999965)
each passed every macOS, Ubuntu, built-wheel, and Debian offline-container job
for G10 source commit `b9a0e2e09c76652a80af9ba6aaef2095846b67e8`. These are
two event-path confirmations of the same source commit, not distinct
merge-candidate commits.

## Acceptance reconciliation

1. **Target delta — Pass.**
   `test_git_init_has_exact_ignored_delta_and_is_idempotent` and
   `test_git_init_preserves_dirty_bytes_and_status` prove Git targets gain only
   `.gigai/project.toml` and one idempotent `/.gigai/` exclusion. Installed
   scenarios cover both Python and non-Python fixtures.
2. **Offline reads — Pass.**
   `test_read_commands_return_stable_json_without_a_target_delta` runs every
   G09 read command against a real lifecycle-created workpad and proves the
   bound target tree is unchanged.
3. **Private workpad locality — Pass.**
   `test_create_orders_first_commit_active_selection_and_valid_proposal` proves
   G08 allocates and registers one active workpad under the configured mount;
   G05's resolver and mount-failure tests reject fallback or redirected roots.
4. **Structured active open — Pass.**
   G10's
   `test_installed_open_without_id_resolves_active_private_workpad_with_target`
   invokes the installed executable without a Gig ID, proves active-workpad
   resolution, records structured editor argv containing workpad then target,
   and proves no target, workpad, or home delta.
5. **Durable creation history — Pass.**
   G06's journal-locking and G08's lifecycle tests cover first commit,
   sequencing, semantic handoffs, feedback, revision, rejection, approval,
   and interruption recovery. Each transition is committed locally.
6. **Mount lock and atomic replacement — Pass.**
   G03 configuration diagnostics and G06's eight-process writer-race tests
   prove configured-mount atomic replacement and interprocess exclusion.
7. **No workpad remote — Pass.**
   G05's local-only workpad contract and
   `test_timeout_remote_and_identity_ownership_fail_closed` reject remotes and
   preserve repository-local identity.
8. **Rebuildable index identity — Pass.**
   `test_index_rebuild_is_disposable_deterministic_and_idempotent` proves
   delete/rebuild equivalence; semantic tampering is reconciled from committed
   journal authority before read commands or doctor return a result.
9. **Offline, token-safe scenarios — Pass.**
   The G02 process harness denies socket access and undeclared subprocesses;
   G03 and G11 canary tests prove credentials are reference-only and absent
   from ordinary doctor and installed deterministic-adapter scenarios. The G10
   Debian lane adds OS-level `--network none` and rejects passed credential
   environment names before tests start.
10. **Platform matrix — Pass.**
    The locked source matrix passes locally on macOS with Python 3.11, 3.12,
    and 3.13. CI already supplies Ubuntu for the same matrix. This change adds
    the missing Debian 12 Python 3.11 container lane, run non-root with a
    read-only root filesystem, separate writable home/target/workpad tmpfs
    mounts, and `--network none`. The local Docker daemon was unavailable, so
    the hosted lane is the authoritative Debian proof; both hosted runs passed
    all eight jobs.
11. **Requirement reconciliation — Pass.**
    [The requirement-to-evidence matrix](requirement-to-evidence-matrix.md)
    maps each V14 Phase 1 delivery item and exit gate to a passing test,
    durable artifact, or explicit non-applicability rationale.

## Local verification

Toolchain: uv 0.5.25; CPython 3.11.14, 3.12.8, and 3.13.1.

| Gate | Command or artifact | Result |
|---|---|---|
| macOS source matrix | `uv run --isolated --locked --extra test --python 3.11 pytest -q` | 279 passed, 22 subtests passed |
| macOS source matrix | same command with Python 3.12 | 279 passed, 22 subtests passed |
| macOS source matrix | same command with Python 3.13 | 279 passed, 22 subtests passed |
| fresh wheel | `uv build` plus all eleven `tools/verify_installed_*.py` verifiers | passed |
| installed scenarios | fresh wheel executable against the installed CLI scenario selection | 35 passed, 18 deselected |
| frozen schemas | `shasum -a 256 -c src/gigai/schemas/SHA256SUMS` | 8/8 OK |
| frozen vectors | `shasum -a 256 research/contract_spike/fixtures/canonical-vectors.json` | `14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f` |
| G10 static quality | Ruff on G10 Python files and `git diff --check` | passed |

## Evidence corrections made by this audit

- Added the required Debian offline-container lane rather than treating the
  existing Ubuntu matrix as a substitute.
- Added the active no-ID `open --with-target` installed scenario; the existing
  G05 scenario proved only the explicit-ID form.
- Reconciled stale README and command-sheet statements that still described the
  pre-G08 surface, and synchronized V14's Python range with ADR 0001.

## Reconciled non-blocking finding

Repository-wide Ruff reports seven pre-existing unused-import findings in
Phase 0 evidence, existing production modules, existing tests, and an existing
installed verifier. They are outside G10's changed files and are not part of
the configured source or hosted CI gates. This audit does not silently repair
completed-goal code; the finding should be handled as a focused maintenance
change before adding Ruff as a required repository-wide gate.

## Completion decision

Phase 1's completion gate is satisfied. The G10 source commit passed all eight
hosted jobs across macOS, Ubuntu, fresh-wheel, and Debian offline-container
verification before this audit was merged as `247f58f`.
