# Fix pack 3 — destructive wheel-lane venv corruption bug

## State
Done. New pure-unit tests prove the fix; `uv run --locked --extra test
pytest tests/behaviors/ci_tooling -q` passes 11/11. No `uv` command, no
`make test`/`make test-wheel`, and no real venv creation was run anywhere in
this session, per the exclusions.

## Files touched (owned files only, both new/untracked)
- `tools/run_ci_tests.py`
- `tests/behaviors/ci_tooling/__init__.py` (new dir; matches the
  one-line-docstring convention every sibling `tests/behaviors/*/__init__.py`
  already uses, e.g. `integrity_canonical/__init__.py`)
- `tests/behaviors/ci_tooling/test_run_ci_tests_wheel_python.py`
- `.orchestrator/workers/fp3.md` (this file)

`git status --porcelain --untracked-files=all` confirms only these paths
under my ownership changed; everything else showing as modified/untracked in
this worktree (`run.py`, `scout_materialization.py`,
`scout_find_jobs_bindings.py`, `scout_market_acquisition.py`, `ui/`, various
docs) is pre-existing prior-wave state I did not touch.

## The bug, confirmed against the actual log

`.orchestrator/logs/074338-test-make-test-pre-m1-2.log`'s tail shows the
exact destructive sequence:
```
+ uv venv --allow-existing --python 3.11 /Users/kar/.local/share/uv/python/cpython-3.11.14-macos-aarch64-none
Using CPython 3.11.14
Creating virtual environment at: /Users/kar/.local/share/uv/python/cpython-3.11.14-macos-aarch64-none
...
+ uv pip install --python /Users/kar/.local/share/uv/python/cpython-3.11.14-macos-aarch64-none/bin/python3.11 ...
error: Failed to inspect Python interpreter ...
  Caused by: failed to canonicalize path `.../bin/python3.11`: Too many levels of symbolic links (os error 62)
```
`uv venv --allow-existing` was invoked directly against the **managed
interpreter's own install directory**, not a venv, and overwrote
`bin/python3.11` with a self-referential symlink.

Root cause, confirmed by reading `tools/run_ci_tests.py` before editing:
- `_resolve_wheel_python` called `path.resolve(strict=False)`, which follows
  every symlink component. `.wheel-venv/bin/python` is a symlink `uv venv`
  itself creates, pointing at the real interpreter under
  `~/.local/share/uv/python/cpython-3.11.14-.../bin/python3.11`.
- `_run_wheel` then computed `virtualenv = wheel_python.parent.parent` —
  after resolution, this landed on
  `~/.local/share/uv/python/cpython-3.11.14-.../` (the managed interpreter's
  own directory, two levels up from its `bin/python3.11`), not
  `<repo>/.wheel-venv`.
- `_run_wheel` unconditionally ran `uv venv --allow-existing --python 3.11
  <virtualenv>` against that path with no check that it was actually a venv
  (or even repo-local) beforehand.
- It only triggers on the *second* run once `.wheel-venv/bin/python` already
  exists as a symlink (a first `uv venv` from scratch creates a normal,
  non-symlinked interpreter binary or a fresh symlink that still points
  correctly the first time; a `uv python`-managed interpreter on this
  machine is symlinked, so any run against an existing `.wheel-venv` hits
  this), matching the task's description exactly.

## The fix

**(1) Never follow symlinks when deriving the venv.** Added
`_unresolved_absolute(path)`: makes a path absolute against `ROOT` using
`os.path.abspath`, which only joins against the cwd and lexically collapses
`.`/`..` — it never touches the filesystem or a symlink (unlike
`Path.resolve()`/`os.path.realpath`). `_resolve_wheel_python` now calls this
instead of `.resolve(strict=False)`.

**(2) A hard guard before any `uv venv`/`uv pip install`.** Added
`_ensure_safe_virtualenv_dir(virtualenv, *, allowed_roots=())`, called once
in `_run_wheel` right after deriving `virtualenv`, before the first `uv`
subprocess call. It fails closed (`raise SystemExit(...)`, via a small
`VirtualenvSafetyError(SystemExit)` subclass for a named type) unless *all*
of:
- `virtualenv` is inside the repository root (`ROOT`) or one of the
  caller-supplied `allowed_roots` (the parameter exists so a future
  legitimate use — e.g. an explicitly allowed temp dir — doesn't need to
  edit the guard itself; nothing in the current code passes extra roots),
- `virtualenv` is not inside any of `_forbidden_venv_roots()`:
  `~/.local/share/uv/python`, `~/.pyenv`, `/usr`, `/opt`,
- if `virtualenv` already exists on disk, it contains a `pyvenv.cfg` file
  (i.e. it already looks like a venv, not an arbitrary directory such as an
  interpreter's own install tree).

Both checks use `Path.relative_to` for containment (`_is_within`), which is
purely lexical/string-based on the already-unresolved path — the guard
itself never calls `.resolve()` either, so a symlink can't be reintroduced
between deriving the path and checking it.

**(3) Same unresolved-path rule for `_run_installed`.** `_run_installed`
already called `_resolve_wheel_python` as its very first line — since that
function itself no longer resolves symlinks, `_run_installed` is fixed by
the same change with no separate edit needed. `_run_installed` never
constructs a venv path or calls `uv venv`/`uv pip install` itself (it only
runs verifiers and pytest against the given `wheel_python` executable
directly and reads `wheel_python.parent / "gigai"`), so no additional guard
call was needed there — the unresolved input is all it uses.

I did not touch the `uv export` call in `_run_wheel` (it only reads the
lockfile and writes a `requirements.txt` to a temp build dir; it never
receives `virtualenv` or `wheel_python` as a venv target).

## Verification against the actual live corrupted artifact

This repo still has the real symlink from the incident
(`.wheel-venv/bin/python -> /Users/kar/.local/share/uv/python/cpython-3.11.14-macos-aarch64-none/bin/python3.11`)
and a legitimate `.wheel-venv/pyvenv.cfg`. I ran the fixed functions
directly against it (read-only — no `uv` invocation, nothing written):
```
$ uv run --locked python -c "
from pathlib import Path
from tools import run_ci_tests
resolved = run_ci_tests._resolve_wheel_python(Path('.wheel-venv/bin/python'))
print('resolved:', resolved)
virtualenv = resolved.parent.parent
print('derived virtualenv:', virtualenv)
run_ci_tests._ensure_safe_virtualenv_dir(virtualenv)
print('guard: PASSED (safe, repo-local)')
"
resolved: /Users/kar/orca/workspaces/gigai/gigai-v0.1.8/.wheel-venv/bin/python
derived virtualenv: /Users/kar/orca/workspaces/gigai/gigai-v0.1.8/.wheel-venv
guard: PASSED (safe, repo-local)
```
And, simulating the exact real managed-interpreter path from the incident
log directly against the guard:
```
$ uv run --locked python -c "
from pathlib import Path
from tools import run_ci_tests
virtualenv = Path('/Users/kar/.local/share/uv/python/cpython-3.11.14-macos-aarch64-none')
run_ci_tests._ensure_safe_virtualenv_dir(virtualenv)
"
SystemExit: refusing to create/use a venv outside the repository: /Users/kar/.local/share/uv/python/cpython-3.11.14-macos-aarch64-none
```
This is a direct proof the fix would have prevented the exact incident that
happened on this machine, without needing to reproduce it destructively.

## Test file — what it proves

`tests/behaviors/ci_tooling/test_run_ci_tests_wheel_python.py`, 11 tests, all
pure unit tests on `tmp_path` — no `uv`, no real venv, no network:

- `test_resolve_wheel_python_does_not_follow_symlink_outside_repo` — builds a
  real symlink (`os.symlink` via `Path.symlink_to`) at
  `<fake-repo>/.wheel-venv/bin/python` pointing at a directory shaped exactly
  like a `uv`-managed CPython install outside the repo, and proves
  `_resolve_wheel_python` returns the symlink's own unresolved path, and
  that deriving `.parent.parent` from it lands on `<fake-repo>/.wheel-venv`,
  never the symlink target. This is the exact acceptance scenario named in
  the task.
- `test_resolve_wheel_python_normalizes_relative_and_dotted_paths` —
  confirms relative and `..`-bearing input still normalize correctly
  (lexically, not via the filesystem).
- `test_guard_refuses_a_managed_interpreter_looking_directory` — a temp dir
  shaped like `.../uv/python/cpython-3.11.14-macos-aarch64-none/bin/` with
  no `pyvenv.cfg`, `Path.home` monkeypatched to point at the temp fixture;
  proves `_ensure_safe_virtualenv_dir` raises `SystemExit`. This is the
  exact second acceptance scenario named in the task.
- `test_guard_refuses_a_pyenv_root_directory` — same shape for
  `~/.pyenv/versions/...`.
- `test_guard_refuses_a_directory_outside_the_repo_entirely` — a dir under
  an unrelated temp path, `ROOT` monkeypatched to a different fake repo;
  proves the repo-containment check independently of the forbidden-roots
  check.
- `test_guard_refuses_an_existing_non_venv_directory_inside_the_repo` — an
  existing repo-local `.wheel-venv` directory that contains an arbitrary
  file but no `pyvenv.cfg`; proves the "must look like a venv if it already
  exists" rule fires even inside the repo.
- `test_guard_allows_a_normal_repo_local_wheel_venv_that_does_not_exist_yet`
  and `..._that_already_looks_like_a_venv` — the third acceptance scenario:
  a normal `<repo>/.wheel-venv`, both as a not-yet-created path and as an
  existing directory with a real `pyvenv.cfg`, both pass with no exception.
- `test_run_wheel_invokes_the_guard_before_any_uv_venv_call` — the strongest
  proof: builds the full realistic symlink chain (`.wheel-venv/bin/python`
  → fake managed-interpreter dir), monkeypatches `run_ci_tests._run` to
  raise `AssertionError` if `uv` is ever invoked, calls `_run_wheel(...)`
  directly, and asserts it raises `SystemExit` with **zero** calls recorded
  — proving the guard runs and stops execution strictly before the first
  subprocess call (`uv build`), not just before `uv venv` specifically.
- `test_run_wheel_proceeds_past_the_guard_for_a_safe_repo_local_target` —
  the inverse: a safe repo-local target reaches the first real `_run` call
  (`uv build`) without the guard raising, proving the guard doesn't
  false-positive on the normal path. The monkeypatched `_run` returns `1`
  immediately after recording the call, so `_run_wheel` returns before
  actually invoking any subprocess — no real `uv` command ever executes in
  this test.
- `test_unresolved_absolute_never_touches_the_filesystem` — a nonexistent
  nested path still resolves lexically with no error and no filesystem
  access.

## Acceptance output

```
$ uv run --locked --extra test pytest tests/behaviors/ci_tooling -q
...........                                                              [100%]
11 passed in 0.17s
```

## READ vs EXECUTED

**READ:**
- `.orchestrator/logs/074338-test-make-test-pre-m1-2.log` (tail, including
  the exact `uv venv`/`uv pip install`/"Too many levels of symbolic links"
  failure sequence).
- `tools/run_ci_tests.py` (full file, before editing): `_rooted`,
  `_resolve_wheel_python`, `_run_wheel`, `_run_installed`, and every other
  function, to confirm no other call site derives or uses a venv path.
- `tests/behaviors/__init__.py` and `tests/behaviors/integrity_canonical/__init__.py`
  (to match the existing one-line-docstring `__init__.py` convention for the
  new `ci_tooling` package).
- `tests/behaviors/installed_release/test_release_check.py` (to confirm
  `from tools import <module>` is an established, working import pattern in
  this test tree despite `tools/` having no `__init__.py` — PEP 420
  namespace packages).

**EXECUTED:**
- `ls -d ~/.pyenv`, `ls -d ~/.local/share/uv/python` (read-only, to confirm
  both forbidden-root shapes actually exist on this machine and are worth
  guarding against explicitly, not just guessed).
- `uv run --locked python -c "from tools import release_check; ..."` and the
  same for `run_ci_tests` (read-only import checks, confirming the
  namespace-package import works before relying on it in the new test
  file).
- `uv run --locked --extra test pytest tests/behaviors/ci_tooling -q` (run
  twice: 11 passed both times, 8.76s cold / 0.17s warm).
- `uv run --locked python -c "..."` twice more (read-only): once resolving
  the actual live `.wheel-venv/bin/python` symlink already present in this
  repo and running the guard against its derived path (proving the fix
  against the real artifact, no `uv` invoked, nothing written); once
  simulating the exact real managed-interpreter path from the incident log
  directly against the guard (also read-only).
- `uv run --locked python -c "from tools import run_ci_tests; print('import ok')"`
  and `uv run --locked python tools/run_ci_tests.py --help` (both read-only,
  confirming the module imports cleanly and the CLI still parses).
- `uv run --locked python tools/run_ci_tests.py installed --list-installed`
  (the one non-destructive, non-`uv`-shelling code path through
  `_run_installed`; it prints node IDs and exits — no venv, no `uv`
  subprocess, confirmed by reading the code path before running it: with
  `list_only=True` it returns before ever touching `wheel_python`).
- `ls -la .wheel-venv/bin/python`, `ls -la .wheel-venv/`, `cat
  .wheel-venv/pyvenv.cfg` (read-only, confirming `.wheel-venv` itself is a
  legitimate, untouched venv — I did not modify it).
- `git status --porcelain -- tools/run_ci_tests.py tests/behaviors/ci_tooling/`
  and `git status --porcelain --untracked-files=all tests/behaviors/ci_tooling/`
  (to confirm exact scope of new/changed files).
- `mkdir -p tests/behaviors/ci_tooling`.
- **No** `uv venv`, `uv pip install`, `uv build`, `make test`, or `make
  test-wheel` command was ever run. **No** file under
  `~/.local/share/uv` was read, written, or listed beyond the two `ls -d`
  existence checks above (which only checked the directory exists, did not
  enter or list its contents). No git stash/reset/clean/add/commit was run.
  No permission prompt blocked this run, so no `orca orchestration ask` was
  needed.

## What's left

Nothing outstanding for fp-3 as scoped. Two things worth flagging to the
coordinator, not mine to act on:
- The operator's `~/.local/share/uv/python/cpython-3.11.14-macos-aarch64-none`
  install is still corrupted (symlink loop) as of this session — the task
  said the operator repairs it, and I did not touch it.
- This repo's own `.wheel-venv` is a real, valid venv (confirmed above) that
  happens to point at that same corrupted interpreter via its
  `bin/python`/`pyvenv.cfg` `home =` entry; once the operator repairs the
  managed interpreter, `.wheel-venv` itself may need a fresh `uv venv
  --allow-existing` run to relink — but this fix's guard will now correctly
  allow that specific repo-local recreation while still refusing to touch
  the managed interpreter directly if the same symlink-following bug were
  ever reintroduced elsewhere.
