# S19 — Test-suite diet: measure, categorize, and add a fast `make unit-tests` lane

**Requested:** 2026-09-23 (added mid-dispatch as a 4th v0.1.9 spike, same
conventions as S16-S18; the coordinator's follow-up corrected the target
name to exactly `make unit-tests`, not `test-fast`).
**Status:** Research recorded 2026-09-23. Documentation only; no Makefile
target added, no test moved, no marker applied, no test executed beyond
small, explicitly time-boxed samples.
**READ vs EXECUTED:** file/count claims below are READ (source, `Makefile`,
`pyproject.toml`, `tools/run_ci_tests.py` inspected directly) or EXECUTED as
read-only counting scripts and a handful of sampled `pytest` runs on
individual files (never the full suite), per the brief's explicit
instruction not to run the full suite.

**2026-09-23 revision (test-lanes worker, EXECUTED):** this spike's "markers
applied to 1 of 159 files" claim (§2) and its framing that `-m fast_unit`
selection was unbuilt were **wrong about what `-m` already selects**.
`tests/conftest.py:140`'s `pytest_collection_modifyitems` hook applies a
`fast_unit`/`integration`/`release` marker to *every* collected item via its
AST classifier (`classify_source`, `tests/conftest.py:101`) — it does not
depend on any `@pytest.mark.fast_unit` decorator existing in source, so
`pytest -m fast_unit` was already a working selector before this revision,
contrary to §2's "essentially unused" framing. Measured directly: `-m
fast_unit` selects **719** tests (current `testpaths = ["tests"]`, after
dropping the two `research/*_spike/tests` testpaths below), **1106**
`-m integration`, **64** `-m release` (719+1106+64 = 1889 = full collection
count). A full `make unit-tests` run (added this revision, `pytest -m
fast_unit -q -n 0`) passes in **6.60s** wall on this Mac — nowhere near the
60s budget and not blocked by §1's unresolved 17s-invocation-overhead
finding, which did not reproduce across repeated runs this revision (see
`.orchestrator/workers/test-lanes.md` for full before/after counts, xdist
vs `-n 0` timings, and the cross-file-misclassification audit this revision
ran before the first real `-m fast_unit` execution). No classifier change
was needed: the audit found zero fast_unit tests reaching a heavy helper
through a same-file or cross-file (`tests.*` import) path. The rest of this
document (§1-§5, tasks, migration plan) is kept as originally written below
for its research value, but its "not yet built" framing and 60s-target
uncertainty are superseded by the measurements above.

## Problem

`make test` makes the operator's Mac spin. Measured directly against this
checkout (all counts recounted, not assumed from the brief):

- **159** `tests/**/test_*.py` files (`find tests -name "test_*.py" | wc -l`
  → 159, matching the brief's number exactly).
- **1,209** `def test_*`/`async def test_*` function definitions
  (`grep -rE "^\s*(async )?def test_"` → 1209; the brief said "~1,200",
  consistent).
- **1,845** cases collected by `pytest --collect-only -q tests`
  (parametrized expansion pushes function-count above 1,209; the brief said
  "1,871" — a small discrepancy from this exact number, not silently
  matched; see "Non-claims").
- **74/159 files (47%)** contain at least one of: `subprocess.run`/`Popen`/
  `check_output`/`check_call`; `git`-dependent setup (`shutil.which("git")`,
  literal `git init`, `git.Repo(`); `multiprocessing.*`; an HTTP server
  (`HTTPServer`/`socketserver`/`present_api`); `launch_run(`; or
  `wait=True` (a real, non-mocked run-process wait) — see the script and
  full output below. This matches the brief's "74/159 (~47%)" exactly.
- **24** `tools/verify_installed_*.py` scripts exist
  (`find tools -iname "verify_installed_*.py" | wc -l` → 24), matching the
  brief.
- The wheel lane (`make test-wheel` → `tools/run_ci_tests.py wheel`) builds
  a real wheel into `.wheel-venv` and runs both the AST-discovered installed
  pytest nodeids and every `verify_installed_*.py` script
  (`tools/run_ci_tests.py:280-290`, `_print_installed_selection`; Makefile
  line 39-40).

Those heavy tests caught real regressions in v0.1.8 — the fp2 worker's
`_child_worker_entry` spawn-registration bug and the wave-3 packet reruns
were caught by exactly this kind of subprocess/multiprocessing-heavy
integration test (`.orchestrator/runs/v0.1.8/workers/fp2.md:35-94`,
`ci-audit.md`, both read for this spike). The goal here is a diet — an
additional fast lane for the inner loop — not a purge of the heavy tests.

## Intended system-behavior change (proposed, not decided)

Add a new Makefile target, name **exactly** `make unit-tests` (operator's
chosen name, corrected mid-brief — not `test-fast`), that runs only pure
unit tests (no subprocess, network, git, servers, or installed package) in
under 60 seconds, using pytest's existing `-m` marker selection against the
markers already declared in `pyproject.toml:51-58` (`fast_unit`,
`integration`, `release`, `cli`, `installed`) — which are declared but,
per this spike's count, applied to only 1 of 159 test files today. This is
additive: `make test` keeps running everything it runs now.

## Tasks

1. Measure per-test/per-file cost and propose how to collect durations.
2. Propose the `make unit-tests` target: selection mechanism, target files/
   markers, and the under-60s goal.
3. Find duplication: `g03-g28 *_installed_scenarios.py` vs.
   `verify_installed_*.py`; CLI tests that spawn subprocesses where
   `CliRunner` would test the same thing in-process.
4. Propose lane policy (inner loop / pre-PR / CI) with expected runtimes.
5. Propose xdist/parallelism settings.

## Acceptance criteria

- Every claim cites `file:line` or a reproducible command.
- No full-suite run was performed; sampling is explicitly time-boxed to a
  few files.
- A migration plan in small packets, not a single big rewrite.

## 1. Per-test cost: how to collect durations, and what a small sample shows

### Proposed collection mechanism

`tools/run_ci_tests.py`'s `_run_source` (`tools/run_ci_tests.py:293-303`)
currently invokes `pytest` with only xdist options — no `--durations` flag
and no JSON report. Proposed addition (not implemented): add
`--durations=0 --durations-min=0.01` plus `pytest-json-report` (or plain
`--durations=0` piped to a parsed log, avoiding a new dependency if the
operator prefers) so `make test-source` can optionally emit a
`ci-durations.json` artifact time-stamped per run, without changing default
behavior. This gives a repeatable per-test-and-per-file cost history instead
of one-off manual sampling like this spike's.

### What the (explicitly small, few-file) sample actually showed

Per the brief's instruction to sample-time at most a few files, not the full
suite:

| File sampled | Result | Wall time | Sum of reported per-test `call` durations |
| --- | --- | --- | --- |
| `tests/behaviors/system_contracts/test_g08_offline_create_lifecycle.py` (git+subprocess heavy, 10 tests) | 10 passed | 36.93s | ~8.4s (individual tests 0.28s-1.37s each) |
| `tests/behaviors/system_contracts/test_g07_contract_validators.py` (pure validator unit tests, 22 tests, zero subprocess/git signal) | 22 passed | **17.47s** (one run) / **0.14s** (a second, differently-invoked run — see below) | <0.1s (every reported `call` duration was <0.02s) |

**Unresolved, honestly reported finding:** the same pure-unit file
(`test_g07_contract_validators.py`, zero subprocess/git/server/multiprocessing
signal by this spike's own classifier) took **17.47s wall / 12.71s system
time** in one invocation (`uv run --locked --extra test pytest
tests/behaviors/system_contracts/test_g07_contract_validators.py
--durations=0 -q`) despite every individual test's own `call` duration being
under 0.02s and `--collect-only` on the same file taking 0.08s. A second,
otherwise-similar invocation (`... -q --no-header -p no:cacheprovider`)
completed in 0.14s/0.335s total. This spike could not pin down the cause in
the time available (candidates not yet ruled out: filesystem/syscall
overhead specific to this sandboxed environment, a plugin doing per-session
I/O, or something about `-p no:cacheprovider`'s absence in the slow run) —
**this needs its own small measurement packet before committing to specific
per-file cost numbers**, because a naive "pure unit tests are always fast"
assumption is not yet proven by this sample; the *test logic* is fast, but
something in the *invocation* was not, and that gap needs to be understood
before `make unit-tests`'s "under 60s" target can be trusted at full-suite
scale.

### Proposed cost categories (for the collected-durations report)

1. **Pure unit** — no subprocess/git/network/server signal (this spike's
   classifier, below, found 85/159 files with none of those signals — the
   complement of the 74 heavy files).
2. **In-process integration** — uses real filesystem/journal/registry state
   via fixtures but no subprocess/git/server.
3. **Subprocess CLI** — spawns a real process to exercise the installed or
   source console script.
4. **Installed** — depends on a normally-installed wheel
   (`tests/behaviors/installed_release/`, 12 files, checked directly:
   `ls tests/behaviors/installed_release/*.py | wc -l` → 12).
5. **Git-heavy** — creates/manipulates a real git repo (56 of the 74 heavy
   files matched the `git` signal, per the classifier below).

## 2. Proposed `make unit-tests` target

### Selection mechanism: markers, not directory convention

`pyproject.toml:51-58` already declares `fast_unit`, `integration`,
`release`, `cli`, `installed`, `g30_live`, `scout_acquisition` markers.
Checked directly: **only 1 of 159 test files applies any of these five
non-`g30_live` markers today**
(`grep -rlE "pytest\.mark\.(fast_unit|integration|release|cli|installed|g30_live|scout_acquisition)" tests --include="test_*.py" | wc -l`
→ 4 total across all markers, and specifically `grep -rl
"pytest.mark.fast_unit"` → 0 files). The marker vocabulary exists; it is
essentially unused. This is the concrete gap `make unit-tests` needs to
close.

**Recommendation:** markers over a directory convention. S11's own
behavior-based lane table (`docs/development/v0.1.8/spikes/S11-behavior-based-test-organization.md:51-57`)
already chose "Unit / Integration / CLI / Installed / Live" as explicit
lanes with required evidence per lane, matching `pyproject.toml`'s existing
marker names almost one-for-one (`fast_unit`≈Unit, `integration`≈Integration,
`cli`≈CLI, `installed`≈Installed, `g30_live`≈Live/provider). Reusing S11's
already-agreed lane semantics avoids introducing a second, competing
classification. A directory convention (moving files into
`tests/unit/`, `tests/integration/`, etc.) would require physically moving
159 files and is exactly the "mass-rename... before any feature can be
useful" anti-pattern S11 already rejected for its own scope
(`S11-behavior-based-test-organization.md:61-62,95-96`).

### Target definition (proposed)

```makefile
# proposed addition to Makefile, NOT yet added
.PHONY: unit-tests
unit-tests:
	$(UV) run --locked --extra test pytest -m fast_unit -q
```

Selection requires every currently-unmarked pure-unit test to gain
`@pytest.mark.fast_unit` (or a `pytestmark = [pytest.mark.fast_unit]`
module-level assignment for files that are unit tests throughout) — this is
real migration work across roughly 85 candidate files (§1's "pure unit"
category), not a zero-cost flip.

### The under-60s goal, and the honest caveat from §1

Given the unresolved per-invocation-overhead finding above, this spike
cannot yet certify that marking ~85 files' ~700-900 test functions
`fast_unit` and running `pytest -m fast_unit` will land under 60 seconds —
the individual test logic is well under that budget by a wide margin (the
sampled pure-unit file's 22 tests summed to <0.1s of actual `call` time),
but the per-invocation overhead seen in one of the two sampled runs (17s+)
is large enough that it must be measured at the `-m fast_unit`-selected
scale, with `-p no:cacheprovider` and xdist settings held constant, before
the 60s target is treated as achieved rather than aspirational.

## 3. Duplication

### `g03-g28 *_installed_scenarios.py` vs. `tools/verify_installed_*.py`

Directly compared `test_g03_installed_scenarios.py` and
`verify_installed_g03.py`:

- `tests/behaviors/installed_release/test_g03_installed_scenarios.py:68-338`
  defines 8 test functions (`test_installed_fresh_setup_and_rerun_are_exactly_idempotent`,
  `test_installed_doctor_is_offline_read_only_and_proves_the_configured_mount`,
  `test_installed_setup_preserves_an_alternate_authoritative_mount`,
  `test_installed_setup_refuses_corrupt_or_incompatible_config_without_mutation`,
  `test_installed_interactive_setup_reviews_effects_before_applying`,
  `test_installed_setup_refuses_read_only_config_without_partial_mutation`,
  `test_installed_doctor_fails_on_missing_config_without_creating_state`,
  `test_installed_doctor_never_falls_back_when_configured_mount_disappears`),
  driven through `InstalledGigAI`/`ScenarioHarness` fixtures
  (`tests/behaviors/installed_release/test_g03_installed_scenarios.py:11`)
  — in-process against the installed package's Python API.
- `tools/verify_installed_g03.py:16-24` (`run()`) instead spawns the
  installed console script via `subprocess.run([executable, *args], ...)`
  and checks the `doctor` command's JSON `checks` payload
  (`verify_installed_g03.py:123-135`) for `PASS`/`WARN` status per check id.

**Overlap assessment:** both exercise "installed package's setup/doctor
idempotency and config-mount behavior," but at different boundaries — the
pytest file tests the *Python-level* behavior (via fixtures, not a spawned
process) with 8 distinct scenarios; the verifier tests the *process/exec*
boundary (spawned CLI, JSON stdout parsing) with a narrower single-pass
smoke check. This is **partial, not full, overlap**: the verifier's
process-boundary check (does the installed console script actually run
end-to-end from a fresh subprocess) is not redundant with the pytest file's
in-process fixture check, and vice versa. A full file-by-file g04-g28
coverage-overlap table was not completed in this spike (only g03 was
compared in depth) — flagged as an open item for a follow-up packet, not
resolved here.

### CLI subprocess tests: which are real candidates for `CliRunner`, and which aren't

Checked every `tests/**/test_*.py` file matching `subprocess\.(run|Popen|check_output|check_call)` for whether it invokes the **main Click `cli` app** (a `CliRunner` candidate) or something else (not a candidate):

| File:line | What it subprocesses | CliRunner candidate? |
| --- | --- | --- |
| `tests/behaviors/cli_surface/test_cli_and_scenario_harness.py:71` (`test_installed_help_version_and_goal_approved_commands_are_the_only_surface`) | The **installed console script itself**, to prove the process/exec boundary's allowed-surface guard | **No** — the process boundary IS the behavior under test (the brief's own carve-out); an in-process `CliRunner` call would not exercise the actual installed entry point or its subprocess allowlist enforcement (`test_cli_and_scenario_harness.py:322-328`, `_process_guard_fails_closed`, which deliberately spawns `subprocess.run(['curl', ...])` to prove a guard rejects it) |
| `tests/behaviors/scout_proposals_tools/test_scout05_tool_scaffold.py:30-41` (`_run`) | A **standalone per-Gig tool script** (not the main `cli.py` Click app — an argparse-based script an agent copies into its own Gig, per `tool_adapter.py`'s design) | **No** — there is no Click app to run in-process here; `CliRunner` only applies to Click-based commands, and this script is deliberately a plain-argparse standalone file per its own module docstring convention (see S17's family notes on `bundled_tools.py`/`tool_adapter.py`) |
| `tests/behaviors/system_contracts/test_g08_offline_create_lifecycle.py:28-29` (`_git`) | Real `git` commands to verify journal-backing repository state | **No** — not a CLI subprocess at all; it's direct git verification, a different heavy-test category (git-heavy, not subprocess-CLI) |
| `tests/behaviors/scout_proposals_tools/test_scout05_capability_cli.py` | (already uses `CliRunner`, confirmed at `test_scout05_capability_cli.py:10,99,139,158,181,208,231,289,306,327`) | **N/A — already migrated**; cited as the existing precedent other files should follow, not a candidate itself |

**Finding:** the sampled files show the CLI-subprocess-vs-CliRunner
candidacy is **narrower than it might look from the raw subprocess grep** —
most subprocess usage found in this pass is either (a) deliberately testing
the process/exec boundary itself (not a CliRunner candidate by the brief's
own carve-out) or (b) not actually the main Click app at all (a standalone
per-Gig script). No confirmed candidate for CliRunner migration was found in
the files sampled in this pass; a genuine candidate, if one exists, would be
a test that subprocesses `python -m gigai ...` purely to check ordinary
command output/exit-code behavior with no process-boundary claim — this
spike did not find one among the files it checked in depth, and does not
claim none exists among the ~50 unchecked subprocess-matching files.

### Classifier script and full output (EXECUTED, 2026-09-23)

```python
#!/usr/bin/env python3
"""Classify each tests/**/test_*.py file as heavy (subprocess/git/server/real
run process) or not, based on source-text signals. Prints one row per file
with which signals matched, plus a summary count."""
import re
from pathlib import Path

ROOT = Path(".")
TEST_FILES = sorted((ROOT / "tests").rglob("test_*.py"))

SIGNALS = {
    "subprocess": re.compile(r"\bsubprocess\.(run|Popen|call|check_output|check_call)\b"),
    "git": re.compile(r"\bshutil\.which\(\s*[\"']git[\"']|\bgit\s+init\b|git\.Repo\(|[\"']git[\"'],"),
    "multiprocessing": re.compile(r"\bmultiprocessing\."),
    "http_server": re.compile(r"HTTPServer|socketserver|ThreadingHTTPServer|present_api|\.serve\("),
    "launch_run": re.compile(r"\blaunch_run\("),
    "real_process_wait": re.compile(r"wait=True"),
}

total = 0
heavy = 0
rows = []
for f in TEST_FILES:
    total += 1
    text = f.read_text(encoding="utf-8", errors="replace")
    hits = [name for name, pat in SIGNALS.items() if pat.search(text)]
    if hits:
        heavy += 1
        rows.append((str(f.relative_to(ROOT)), hits))

for path, hits in rows:
    print(f"{path}\t{','.join(hits)}")

print(f"\n# total test_*.py files: {total}")
print(f"# heavy files (>=1 signal): {heavy}")
print(f"# percent: {round(heavy/total*100)}%")
```

Output summary (full 74-row listing kept in `.orchestrator/workers/v019-spikes.md`):

```
# total test_*.py files: 159
# heavy files (>=1 signal): 74
# percent: 47%
```

Signal breakdown across the 74 heavy files (a file can match multiple
signals): git 56, subprocess 55, real_process_wait 12, launch_run 11,
http_server 6, multiprocessing 5.

## 4. Lane policy (proposed)

| Lane | When it runs | Selection | Expected runtime (proposed target, not yet measured at full scale — see §1 caveat) |
| --- | --- | --- | --- |
| `make unit-tests` (new) | Worker inner loop, every save/iteration | `pytest -m fast_unit` | Under 60s (operator's stated goal; not yet verified at scale per §1) |
| Existing focused/behavior-row commands (already used per-packet in v0.1.8, e.g. the Amendment-02 wave-3 rerun commands in `v0.1.8-find-jobs-functional-roadmap.md:135-138`) | Before marking a packet done | Explicit file selectors, unchanged | Unchanged from today |
| `make test` (existing: `test-source` + `test-behavior` + `test-wheel`) | Before a PR | Unchanged, full source+behavior+wheel | Unchanged from today; this spike proposes no reduction here |
| CI full matrix (existing, includes `test-debian-offline` and `test-live` where opted in) | CI | Unchanged | Unchanged from today |

This preserves S11's already-established lane discipline
(`S11-behavior-based-test-organization.md:51-57`) and adds exactly one new,
narrower lane beneath it, rather than replacing any existing lane.

## 5. Parallelism settings

Current Makefile defaults: `TEST_XDIST_WORKERS=auto`,
`TEST_XDIST_MAX_WORKERS=14`, `TEST_XDIST_DIST=worksteal`
(`Makefile:5-7`), applied only to `test-source`
(`Makefile:17-28`, explicitly "xdist options are valid only for the source
phase" per `tools/run_ci_tests.py:495-499`). This checkout's own CPU count
is 14 (`sysctl -n hw.ncpu` → 14; `nproc` → 14), matching the Makefile
comment's "measured 14-CPU host" (`Makefile:20-21`). For the proposed
`make unit-tests` target specifically: since pure-unit tests are (per §1's
per-test `call` durations, though not yet per-invocation-overhead) very
fast individually, xdist worker startup/teardown cost could plausibly
exceed the benefit for a small `-m fast_unit` selection — this needs the
same small-scale measurement as §1's unresolved overhead question before
recommending a specific worker count for the new lane; reusing the existing
`TEST_XDIST_MAX_WORKERS=14` cap for consistency is the safe default in the
meantime, with `-n 0` (no xdist) as a fallback to test if xdist startup
itself is contributing to the unresolved overhead.

## Migration plan (small, parallelizable packets)

1. **Measurement packet:** add `--durations=0` (and, if the operator wants
   a persisted history, a JSON report) to `test-source`; resolve the §1
   overhead discrepancy with a targeted repro (compare
   `-p no:cacheprovider` present/absent, xdist on/off, cold/warm `uv`
   cache) before committing to the 60s target as achieved.
2. **Marker packet (pilot):** apply `fast_unit` to one already-clearly-pure
   directory (e.g. `tests/behaviors/system_contracts/test_g07_contract_validators.py`
   and its neighbors with zero heavy-signal matches) and add the
   `make unit-tests` target; measure actual wall time against the 60s goal
   using the resolved measurement approach from packet 1.
3. **Marker packet (expand):** extend `fast_unit` to the remaining ~85
   pure-unit files (this spike's classifier's complement set), one behavior
   directory at a time, re-measuring after each directory per S11's own
   "re-measure... migrate another area only when the evidence shows a
   ... benefit" discipline (`S11-behavior-based-test-organization.md:91-93`).
4. **Overlap packet:** complete the g04-g28 `*_installed_scenarios.py` vs.
   `verify_installed_*.py` coverage-overlap table (this spike only compared
   g03) and decide, per pair, whether both are still earning their keep.
5. **CliRunner packet:** search the remaining ~50 subprocess-matching files
   not checked in depth here for a genuine "subprocesses `python -m gigai`
   with no process-boundary claim" candidate; migrate only confirmed
   candidates, keeping every file this spike already classified as
   process-boundary-behavior or non-Click untouched.

Each packet is independently reviewable and does not block the others
except packet 2 depending on packet 1's resolved measurement approach.

## Non-claims

- No Makefile target, marker application, or test file change was made.
- The 1,845 collected-case count from this checkout does not match the
  brief's "1,871" exactly; this is reported as a discrepancy, not silently
  reconciled — a different collection environment/date could explain it,
  not verified here.
- The `make unit-tests` under-60s target is the operator's stated goal, not
  a measured, certified outcome of this spike — §1's overhead finding is an
  open blocker to that certification, not a detail to skip past.
- The CLI-subprocess-vs-CliRunner review covered a handful of files in
  depth, not all ~50 subprocess-matching files outside `installed_release`;
  no claim is made that zero migration candidates exist among the
  unchecked ones.
- The g03-only installed-scenarios-vs-verifier comparison is not a full
  g04-g28 audit; a duplication percentage across all pairs is not asserted.
- No test was deleted, marked skip, or otherwise weakened; this is a
  measurement and lane-addition proposal only.

## Change log

- 2026-09-23: Spike added mid-dispatch per coordinator follow-up (with a
  same-day correction fixing the target name to `make unit-tests`).
  Recounted the operator's cited numbers directly against this checkout
  (159 test files confirmed exact; ~1,200 test functions confirmed as 1,209;
  74/159 heavy files at 47% confirmed exact via a written, pasted
  classifier script; 24 `verify_installed_*.py` scripts confirmed exact).
  Compared `test_g03_installed_scenarios.py` against `verify_installed_g03.py`
  in depth (partial, not full, overlap). Checked CLI-subprocess candidates
  for `CliRunner` migration and found the sampled cases were either
  deliberate process-boundary tests or non-Click standalone scripts, not
  confirmed migration candidates. Found and reported, without resolving, an
  unexplained ~17s-vs-0.14s invocation-time discrepancy on an identical
  pure-unit test file, flagged as a blocker to certifying the 60s target.
  Proposed markers-over-directories for `make unit-tests` selection (since
  `pyproject.toml`'s marker vocabulary already exists but is applied to only
  1/159 files today), a lane-policy table, and a 5-packet migration plan.
