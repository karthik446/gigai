"""U4 (0.1.10.9): the browser tests in CI: the `ui` job, the browser half of `operator-home`, the job summary.

GitHub Actions cannot be run here, so these read `.github/workflows/pull_request.yaml` as text (no
YAML library in this environment: `release_notes.parse_workflow_jobs`) and pin what the two jobs
must keep: what they run, what blocks, the browser cache key, the uploads, one build of the
operator-sized home, and no retry anywhere. `tools/ui_budgets_summary.py` (the job summary) is
tested as a function.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from tools import release_notes, ui_budgets_summary

ROOT = Path(__file__).resolve().parents[3]
ARTIFACTS = "build/ui-artifacts"
HOME_BUILT = "if: ${{ !cancelled() && steps.home.outcome == 'success' }}"


def _workflow() -> str:
    path = ROOT / ".github/workflows/pull_request.yaml"
    if not path.is_file():
        pytest.skip("workflows are excluded from the offline container build context")
    return path.read_text(encoding="utf-8")


def _job(text: str, job: str) -> str:
    """One top-level job's text, up to the next job."""

    body = text.split(f"\n  {job}:\n", 1)[1]
    following = [index for index in (body.find(f"\n  {name}:\n") for name in release_notes.parse_workflow_jobs(text)) if index != -1]
    return body[: min(following)] if following else body


def _code(job: str) -> str:
    """The job without its comment lines (a comment may say "no retry"; a step may not have one)."""

    return "\n".join(line for line in job.splitlines() if not line.lstrip().startswith("#"))


def _steps(job: str) -> list[str]:
    """The job's steps, each as its text (from its `- ` line to the next one)."""

    body = _code(job).split("\n    steps:\n", 1)[1]
    return ["      - " + step for step in ("\n" + body).split("\n      - ")[1:]]


def _step(job: str, marker: str) -> str:
    found = [step for step in _steps(job) if marker in step]
    assert len(found) == 1, f"{len(found)} steps hold {marker!r}"
    return found[0]


def _makefile() -> str:
    path = ROOT / "Makefile"
    if not path.is_file():
        pytest.skip("the Makefile is excluded from the offline container build context")
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------- the `ui` job (every PR, every release pre-check)


def test_the_ui_job_runs_make_ui_test_on_every_pr_and_release_precheck_and_not_in_the_sweep() -> None:
    text = _workflow()
    assert release_notes.parse_workflow_jobs(text)["ui"].needs == ["changes"]
    job = _job(text, "ui")
    assert "    if: needs.changes.outputs.code_changed == 'true' && (inputs.profile || 'pr') != 'full'\n" in job
    assert "    runs-on: ubuntu-24.04\n" in job and 'python-version: "3.11"' in job
    assert 'uv sync --locked --extra test --group ui --python "3.11"' in job
    tests = _step(job, "run: make ui-test")
    assert tests.rstrip().endswith("run: make ui-test")  # the small home: not ui-test-full, not ui-test-operator
    assert "if:" not in tests  # nothing lets it be skipped: it blocks from day one
    assert "continue-on-error" not in _code(job)


def test_timing_ceilings_are_reported_in_ci_for_the_first_week_by_one_switch_per_job() -> None:
    """Structure and console problems fail a test whatever the switch says; `report` only spares the timing ceilings."""

    from tests.ui import support

    text = _workflow()
    for name in ("ui", "operator-home"):
        job = _code(_job(text, name))
        assert job.count(support.BUDGETS_ENV) == 1, name
        assert "    env:\n" in job and f"      {support.BUDGETS_ENV}: {support.REPORT}\n" in job, name
        assert support.budgets_mode({support.BUDGETS_ENV: support.REPORT}) == support.REPORT


def test_the_browser_cache_is_keyed_on_the_installed_playwright_version_in_both_jobs() -> None:
    text = _workflow()
    setups = []
    for name in ("ui", "operator-home"):
        job = _job(text, name)
        version = _step(job, "id: playwright")
        assert "from importlib.metadata import version; print(version(\"playwright\"))" in version
        assert 'test -n "${version}"' in version and 'echo "version=${version}" >> "${GITHUB_OUTPUT}"' in version
        cache = _step(job, "uses: actions/cache@")
        assert "path: ~/.cache/ms-playwright" in cache
        assert "key: playwright-chromium-${{ runner.os }}-${{ runner.arch }}-${{ steps.playwright.outputs.version }}" in cache
        assert "restore-keys" not in cache  # another version's browsers are not this version's
        # The browser and its system libraries are installed before the timed test step (a cache hit: the libraries only).
        libraries = _step(job, "playwright install --with-deps chromium")
        steps = _steps(job)
        order = [steps.index(version), steps.index(cache), steps.index(libraries), steps.index(_step(job, "run: make ui-test"))]
        assert order == sorted(order), f"{name}: version, cache, system libraries, then the tests"
        setups.append([re.sub(r"\n\s+if: [^\n]+", "", step) for step in (version, cache, libraries)])
    assert setups[0] == setups[1], "the browser setup of `ui` and `operator-home` must stay the same three steps"


def test_every_run_shows_and_uploads_its_budgets_and_a_failed_one_its_artifacts() -> None:
    text = _workflow()
    assert f"UI_ARTIFACTS ?= {ARTIFACTS}\n" in _makefile()  # where `make ui-test` writes; the job reads the same folder
    for name, condition, title in (
        ("ui", "if: ${{ !cancelled() }}", "small home (make ui-test)"),
        ("operator-home", HOME_BUILT, "operator-sized home (make ui-test-operator)"),
    ):
        job = _job(text, name)
        summary = _step(job, "tools/ui_budgets_summary.py")
        assert f'python3 tools/ui_budgets_summary.py {ARTIFACTS}/budgets.json --title "{title}" >> "${{GITHUB_STEP_SUMMARY}}"' in summary
        assert condition in summary  # also after a failed test step
        budgets, failure = [step for step in _steps(job) if "uses: actions/upload-artifact@v4" in step]
        assert condition in budgets and f"{ARTIFACTS}/budgets.json" in budgets and "retention-days: 30" in budgets
        assert "if: ${{ failure() }}" in failure and f"path: {ARTIFACTS}/\n" in failure + "\n"
        # A second attempt of the same run must not collide with the first one's artifact.
        assert "-attempt-${{ github.run_attempt }}" in budgets and "-attempt-${{ github.run_attempt }}" in failure
        steps = _steps(job)
        assert steps.index(_step(job, "run: make ui-test")) < steps.index(summary) < steps.index(budgets) < steps.index(failure)
    assert f"{ARTIFACTS}/operator-sized-jobs.json" in _job(text, "operator-home")


def test_no_retry_anywhere_in_the_browser_jobs() -> None:
    """The plan's rule (tests/ui/README.md): a retry turns a wrong budget or a real race into a pass."""

    text = _workflow()
    for name in ("ui", "operator-home"):
        job = _code(_job(text, name)).lower()
        for forbidden in ("retry", "rerun", "--reruns", "continue-on-error", "max_attempts", "attempt_limit", "|| make", "|| true"):
            assert forbidden not in job, f"{name}: {forbidden}"
        assert job.count("run: make ui-test") == 1, name
    for line in _makefile().splitlines():
        if "pytest tests/ui" in line:
            assert "rerun" not in line and "retry" not in line and "--lf" not in line


def test_a_step_that_reaches_its_limit_leaves_the_job_time_to_upload() -> None:
    text = _workflow()
    for name, expected in (("ui", [10]), ("operator-home", [5, 10, 5])):
        job = _code(_job(text, name))
        (limit,) = (int(value) for value in re.findall(r"^    timeout-minutes: (\d+)$", job, flags=re.MULTILINE))
        steps = [int(value) for value in re.findall(r"^        timeout-minutes: (\d+)$", job, flags=re.MULTILINE)]
        assert steps == expected, name
        assert sum(steps) + 5 <= limit, f"{name}: the steps' limits ({sum(steps)} min) leave under 5 minutes of the job's {limit}"
        for marker in ("run: make ui-test",):
            assert "timeout-minutes:" in _step(job, marker), f"{name}: the test step has no limit of its own"


# ---------------------------------------------------------------------------- the release pre-check: one home, two takers


def test_the_release_precheck_builds_the_operator_sized_home_once_for_the_gate_and_the_browser() -> None:
    from tests.support import operator_home

    text = _workflow()
    job = _job(text, "operator-home")
    assert "    if: needs.changes.outputs.code_changed == 'true' && inputs.profile == 'release'\n" in job
    code = _code(job)
    assert code.count("tests.support.operator_home") == 1, "one build"
    build = _step(job, "tests.support.operator_home")
    assert "id: home" in build and 'python -m tests.support.operator_home "${home}/op" --pristine' in build
    assert 'home="$(mktemp -d /tmp/gigai-operator-XXXXXX)"' in build  # the generator refuses a root outside the temporary directory
    assert f'echo "{operator_home.PREBUILT_ENV}=${{home}}/op" >> "${{GITHUB_ENV}}"' in build
    steps = _steps(job)
    gate, browser = _step(job, "run: make test-operator-home"), _step(job, "run: make ui-test-operator")
    assert steps.index(build) < steps.index(gate) < steps.index(browser)
    assert "if:" not in gate  # the timing gate is as it was: it runs, and it blocks
    # The browser steps run whatever the gate did (one pre-check run shows both), and only on a home that was built.
    after_gate = steps[steps.index(gate) + 1 : steps.index(browser) + 1]
    assert len(after_gate) == 5 and all(HOME_BUILT in step for step in after_gate)
    assert "uv sync --locked --extra test --group ui" in after_gate[0]
    # The Makefile target is the operator-sized half only: the `ui` job has run the small half.
    (recipe,) = (line for line in _makefile().splitlines() if "pytest tests/ui" in line and "ui and operator_sized" in line)
    assert ' -m "ui and operator_sized" ' in recipe


# ---------------------------------------------------------------------------- the job summary


def _budgets(*rows: dict, mode: str = "report", run: dict | None = None) -> dict:
    data: dict = {"mode": mode, "ceilings": len(rows), "over": sum(1 for row in rows if row["over"]), "budgets": list(rows)}
    if run is not None:
        data["run"] = run
    return data


def _line(name: str, kind: str, measured: float, limit: float, *, unit: str = "s", test: str = "test_flow") -> dict:
    return {"test": test, "name": name, "kind": kind, "measured": measured, "limit": limit, "unit": unit, "over": measured > limit, "blocking": False}


def test_the_summary_lists_what_is_over_then_the_closest_then_everything() -> None:
    rows = [
        _line("Jobs first load", "wall", 0.31, 5.0),
        _line("Jobs first load", "cpu", 3.4, 3.0),
        _line("the server's peak memory", "rss", 229.4, 600.0, unit="MB"),
        _line("a chip | New", "wall", 1.5, 2.0),
    ]
    text = ui_budgets_summary.render(_budgets(*rows, run={"seconds": 71.2, "small_home_build_seconds": 24.9}), title="small home (make ui-test)")
    lines = text.splitlines()
    assert lines[0] == "## UI budgets: small home (make ui-test)"
    assert "**4 ceilings measured, 1 over.** Mode `report`: a ceiling that is over is listed here and is not a failure (`GIGAI_UI_BUDGETS=enforce` makes it one)." in lines
    assert "The run took 71.2 s; small home built in 24.9 s." in lines
    over = lines[lines.index("### Over their limit") :]
    assert over[4] == "| cpu | 3.40 s | 3.00 s | 113% **OVER** | Jobs first load | test_flow |"
    closest = lines[lines.index("### The 4 closest to their limit") + 4 :][:4]
    assert [row.split(" | ")[3] for row in closest] == ["113% **OVER**", "75%", "38%", "6%"]  # nearest first
    assert "| wall | 1.50 s | 2.00 s | 75% | a chip \\| New | test_flow |" in lines  # a pipe in a name does not break the table
    assert "| rss | 229 MB | 600 MB | 38% | the server's peak memory | test_flow |" in lines
    everything = lines[lines.index("<details><summary>Every ceiling, in the order it was measured</summary>") :]
    assert [row for row in everything if row.startswith("| wall") or row.startswith("| cpu") or row.startswith("| rss")] == [ui_budgets_summary._row(row) for row in rows]
    assert lines[-1] == "</details>"


def test_the_summary_says_enforce_when_the_run_enforced_and_names_a_prebuilt_home() -> None:
    run = {"seconds": 58.0, "operator_home_seconds": 7.4, "operator_home_prebuilt": True, "operator_server_start_seconds": 1.3}
    text = ui_budgets_summary.render(_budgets(_line("Jobs cold load", "cpu", 12.3, 45.0), mode="enforce", run=run), title="operator-sized home")
    assert "**1 ceilings measured, 0 over.** Mode `enforce`: a ceiling that is over failed its test." in text
    assert "The run took 58.0 s; operator-sized home ready in 7.4 s; its server up in 1.3 s; the home was built once by an earlier step, not by this run." in text
    assert "### Over their limit" not in text


def test_the_summary_of_a_run_that_wrote_no_budgets_says_so_and_never_fails_the_step(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    missing = tmp_path / "budgets.json"
    assert ui_budgets_summary.main([str(missing), "--title", "small home"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("## UI budgets: small home\n") and "was not written: the run ended before pytest's summary" in out
    missing.write_text("{not json", encoding="utf-8")
    assert ui_budgets_summary.main([str(missing)]) == 0
    assert "could not be read" in capsys.readouterr().out
    missing.write_text(json.dumps(_budgets()), encoding="utf-8")  # a run that measured nothing
    assert ui_budgets_summary.main([str(missing)]) == 0
    assert "**0 ceilings measured, 0 over.**" in capsys.readouterr().out


def test_the_summary_reads_the_file_the_harness_writes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The two ends of budgets.json agree: what `BudgetLog.to_json` writes is what the summary reads."""

    from tests.ui import support

    monkeypatch.delenv(support.BUDGETS_ENV, raising=False)
    log = support.BudgetLog()
    recorder = support.Recorder(support.Network(), lambda: 0.0, budgets=log, test="test_smoke")
    recorder.wall_budget_of("a second tab", 10.0, 12.0)
    recorder.memory_budget_of("server memory", 600, 229.4)
    path = tmp_path / "budgets.json"
    path.write_text(json.dumps(log.to_json(enforced=False, run={"seconds": 70.0})), encoding="utf-8")
    text = ui_budgets_summary.summary(path, title="small home")
    assert "**2 ceilings measured, 1 over.** Mode `report`" in text and "The run took 70.0 s." in text
    assert "| wall | 12.00 s | 10.00 s | 120% **OVER** | a second tab | test_smoke |" in text
    assert "| rss | 229 MB | 600 MB | 38% | server memory | test_smoke |" in text
