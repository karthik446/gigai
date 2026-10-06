"""0110-10-hf2: the core-flow smoke (`make test-core-flow`, tools/core_flow.py) and the release pre-check step that runs it.

The smoke itself is the proof (the installed `gigai` command on the operator-sized home, cold and warm: about
6 minutes, so it is not part of this suite). These tests keep what makes it worth running:

* its rule, on real processes: a step fails on a non-zero exit, on a traceback or an error by name on stderr
  whatever the exit code, on stdout that is not JSON, on an error answer, and on its own check;
* the flow holds every command of the release rule, and runs once cold and once with the server running;
* the fixture boards change 500 or more boards each round (what makes `gigai scout new` match that many again);
* the make target exists and the release pre-check runs it, on the prebuilt home, with a limit, and blocking.
"""

from __future__ import annotations

import inspect
from pathlib import Path
import re
import sys

import pytest

from tests.support import core_flow_boards, operator_home
from tools import core_flow, release_notes

ROOT = Path(__file__).resolve().parents[3]
HOME_BUILT = "if: ${{ !cancelled() && steps.home.outcome == 'success' }}"


def _read(relative: str, why: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        pytest.skip(why)
    return path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------- the rule, on real processes


@pytest.fixture()
def flow(tmp_path: Path) -> core_flow.Flow:
    made = core_flow.Flow(out=tmp_path / "out", scratch=tmp_path, postings=10, companies=2)
    made.logs.mkdir(parents=True)
    made.built = operator_home.OperatorHome(
        root=str(tmp_path), home=str(tmp_path / "home"), target=str(tmp_path / "home" / "scout"), postings=10, companies=2,
        matched_titles=1, distinct_titles=1, assessed=0, profiles={"default": "profile_a"}, build_seconds=0.0,
    )
    made.env = {"PATH": ""}
    return made


def _command(code: str) -> list[str]:
    """A command that ignores its arguments (the step adds --home and --target) and does ``code``."""

    return [sys.executable, "-c", code]


def _row(flow: core_flow.Flow, code: str, check=lambda _answer, _lines: None) -> core_flow.Row:
    flow.step("cold", "gigai something --json", _command(code), check)
    return flow.rows[-1]


def test_a_step_that_exits_zero_with_json_and_a_quiet_stderr_passes_and_its_output_is_kept(flow: core_flow.Flow) -> None:
    row = _row(flow, "import sys; print('{\"ok\": true}'); print('preparing your postings: 0% (0 of 600 companies)', file=sys.stderr)", lambda _a, _l: "ok: fine")

    assert row.ok and (row.exit, row.stderr_lines, row.mode, row.note) == (0, 1, "cold", "fine")
    kept = sorted(path.name for path in flow.logs.iterdir())
    assert kept == ["01-cold-gigai-something-json.stderr", "01-cold-gigai-something-json.stdout"]
    assert (flow.logs / kept[1]).read_text(encoding="utf-8") == '{"ok": true}\n'


def test_a_non_zero_exit_fails_the_step(flow: core_flow.Flow) -> None:
    row = _row(flow, "print('{\"ok\": true}'); raise SystemExit(3)")

    assert not row.ok and row.exit == 3 and row.problems == ["exit 3"]


def test_a_traceback_on_stderr_fails_the_step_even_when_the_exit_code_is_zero(flow: core_flow.Flow) -> None:
    """What 0.1.10.9 printed. A command that caught it and exited 0 would be as broken."""

    said = "Traceback (most recent call last):\\n  File \\\"scout_cli.py\\\", line 2710, in build_progress\\nNameError: name 'time' is not defined"
    row = _row(flow, f"import sys; print('{{}}'); print(\"{said}\", file=sys.stderr)")

    assert not row.ok and row.exit == 0 and row.stderr_lines == 3
    assert row.problems == ["stderr: Traceback (most recent call last):"]


def test_an_error_named_on_stderr_fails_the_step(flow: core_flow.Flow) -> None:
    row = _row(flow, "import sys; print('{}'); print('Error: no such option', file=sys.stderr)")

    assert not row.ok and row.problems == ["stderr: Error: no such option"]


def test_stdout_that_is_not_json_fails_the_step(flow: core_flow.Flow) -> None:
    row = _row(flow, "print('preparing your postings: 10%'); print('{\"ok\": true}')")  # a progress line that leaked into stdout

    assert not row.ok and row.problems == ["stdout is not a JSON object"]


def test_an_error_answer_fails_the_step(flow: core_flow.Flow) -> None:
    row = _row(flow, "print('{\"status\": \"error\", \"error\": {\"code\": \"master_not_found\"}}')")

    assert not row.ok and "master_not_found" in row.problems[0]


def test_a_step_that_did_nothing_fails_its_own_check(flow: core_flow.Flow) -> None:
    """Exit 0 and valid JSON are not enough: each step says what it must have done."""

    row = _row(flow, "print('{\"assessed\": null}')", lambda answer, _lines: None if answer["assessed"] else "nothing was assessed")
    missing = _row(flow, "print('{}')", lambda answer, _lines: str(answer["assessed"]))

    assert not row.ok and row.problems == ["nothing was assessed"]
    assert not missing.ok and "does not have the expected shape" in missing.problems[0]


def test_the_table_names_every_step_and_says_failed_when_one_did(flow: core_flow.Flow) -> None:
    _row(flow, "print('{}')")
    _row(flow, "raise SystemExit(1)")

    table = flow.table()

    assert table.startswith("### Core-flow smoke: FAILED (1 of 2 steps)\n")
    assert "| # | command | mode | exit | seconds | stderr lines | result |" in table
    rows = [line for line in table.splitlines() if line.startswith(("| 1 |", "| 2 |"))]
    assert rows[0].startswith("| 1 | `gigai something --json` | cold | 0 | ") and rows[0].endswith("| 0 | ok |")
    assert "| cold | 1 | " in rows[1] and "FAILED: exit 1; stdout is not a JSON object" in rows[1]


# ---------------------------------------------------------------------------- the flow


def test_the_flow_runs_every_command_of_the_release_rule_cold_and_then_with_the_server_running() -> None:
    steps = inspect.getsource(core_flow.Flow.flow)
    assert "gigai = str(self.gigai)" in steps  # the console script of the environment the wheel was installed in
    for command in (
        '"scout", "new", "--no-assess", "--json"',
        '"scout", "new", "--yes", *since, "--json"',
        '"scout", "jobs", "list", "--json"',
        '"scout", "assess", "--job-url", job_url, "--json"',
        '"scout", "resume", "master", "init", "--dry-run", "--json"',
        '"scout", "resume", "master", "init", "--json"',
        '"scout", "resume", "master", "selection", "refresh", "--all", "--json"',
        '"scout", "status", "--json"',
    ):
        assert steps.count(f"[gigai, {command}]") == 1, command
    # The update is the same installed CLI behind the fixture boards (tests/support/core_flow_boards.py), run by the environment's interpreter.
    assert steps.count('"scout", "sources", "update", "--json"') == 1
    assert '[str(self.python), "-m", "tests.support.core_flow_boards", "update", str(round_number)' in steps

    main = inspect.getsource(core_flow.main)
    cold, later, warm = main.index('flow.flow("cold", 1)'), main.index("flow.a_day_later()"), main.index('flow.with_server(lambda: flow.flow("warm", 2))')
    assert cold < later < warm
    assert "return 1 if crashed or not flow.rows or any(not row.ok for row in flow.rows) else 0" in main
    # The wheel, in a clean environment, with uv only.
    install = inspect.getsource(core_flow.Flow.install_wheel)
    assert '["uv", "build", "--wheel"' in install and '["uv", "venv", "--python"' in install and '["uv", "pip", "install", "--python"' in install
    assert "pip" not in re.sub(r'"uv", "pip", "install"|uv pip install', "", install)


def test_each_round_changes_over_500_boards_and_other_boards_than_the_round_before() -> None:
    companies = operator_home.OPERATOR_COMPANIES
    first, second = core_flow_boards.changed_boards(1, companies), core_flow_boards.changed_boards(2, companies)

    assert len(set(first)) == len(first) == core_flow_boards.CHANGED_PER_ROUND > 500  # over the "preparing" lines' 500
    assert all(0 <= board < companies for board in first + second) and not set(first) & set(second)
    # A few of a round's new postings have a title the default profile matches: `scout new --yes` has something to assess.
    titles = [core_flow_boards.extra_job(board, 1, position)["text"] for position, board in enumerate(first)]
    assert titles.count(core_flow_boards.MATCHED_TITLE) == core_flow_boards.MATCHED_PER_ROUND == len(core_flow_boards.matched_urls(1, companies))
    assert core_flow_boards.MATCHED_TITLE.startswith(operator_home.DEFAULT_TITLES[0])
    # A home smaller than a round (the smoke on a scaled home): every board once.
    assert sorted(core_flow_boards.changed_boards(1, 40)) == list(range(40))


# ---------------------------------------------------------------------------- make and the release pre-check


def test_make_test_core_flow_runs_the_smoke_and_says_where_the_table_is() -> None:
    makefile = _read("Makefile", "the Makefile is excluded from the offline container build context")

    assert "\nCORE_FLOW_OUT ?= build/core-flow\n" in makefile
    assert "\ntest-core-flow:\n\t$(UV) run --locked python tools/core_flow.py --out \"$(CORE_FLOW_OUT)\" $(CORE_FLOW_ARGS)\n" in makefile
    assert re.search(r"^\.PHONY: .*\btest-core-flow\b", makefile, re.MULTILINE)


def test_the_release_precheck_runs_the_smoke_on_the_prebuilt_home_with_a_limit_and_it_blocks() -> None:
    workflow = _read(".github/workflows/pull_request.yaml", "workflows are excluded from the offline container build context")
    jobs = release_notes.parse_workflow_jobs(workflow)
    body = workflow.split("\n  operator-home:\n", 1)[1]
    job = body[: min(index for index in (body.find(f"\n  {name}:\n") for name in jobs) if index != -1)]
    code = "\n".join(line for line in job.splitlines() if not line.lstrip().startswith("#"))
    steps = ["      - " + step for step in ("\n" + code.split("\n    steps:\n", 1)[1]).split("\n      - ")[1:]]

    assert "    if: needs.changes.outputs.code_changed == 'true' && inputs.profile == 'full'\n" in job  # 0.1.11.1: the sweep; the pre-check runs the small-home `core-flow` job
    (smoke,) = [step for step in steps if "make test-core-flow" in step]
    assert smoke.rstrip().endswith("run: make test-core-flow CORE_FLOW_OUT=build/ui-artifacts/core-flow")
    assert HOME_BUILT in smoke and "timeout-minutes: 20" in smoke and "continue-on-error" not in code.replace("    continue-on-error: true\n", "")
    # The home built once in this job (GIGAI_OPERATOR_HOME_PREBUILT, exported by the build step) is the one it takes.
    (build,) = [step for step in steps if "tests.support.operator_home" in step]
    assert f'echo "{operator_home.PREBUILT_ENV}=${{home}}/op" >> "${{GITHUB_ENV}}"' in build and steps.index(build) < steps.index(smoke)
    assert "operator_home.take_prebuilt(" in inspect.getsource(core_flow.Flow.take_home)
    # The table is shown in the job summary and kept with every run; a failed run uploads each step's output.
    (summary,) = [step for step in steps if "core-flow/table.md >>" in step]
    assert 'run: cat build/ui-artifacts/core-flow/table.md >> "${GITHUB_STEP_SUMMARY}"' in summary and HOME_BUILT in summary
    uploads = [step for step in steps if "uses: actions/upload-artifact@v4" in step]
    assert "build/ui-artifacts/core-flow/table.md" in uploads[0] and "if: ${{ failure() }}" in uploads[1] and "path: build/ui-artifacts/\n" in uploads[1]
    assert steps.index(smoke) < steps.index(summary) < steps.index(uploads[0]) < steps.index(uploads[1])
