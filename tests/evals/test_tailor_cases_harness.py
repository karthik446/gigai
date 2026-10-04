"""0110-10-05 C: ``run_tailor_cases_eval.py`` offline -- the fixture's premises and the runner's plumbing.

No live call: the fixture model answers (``--fake-model``).  What is pinned:
the five synthetic cases are what the eval says they are (Helm absent from
the resume and asked by the posting; the long resume prints on 3 pages; the
Skills lines wrap into one another; the control fits on one page), the plan
and the call cap (a case run is skipped when two calls no longer fit), the
report's shape, and the live gate.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout.tailor_length_store import measure_pages
from gigai.scout.tailored_resume import apply_no_loss, resume_continuations, resume_lines, validate_tailored_output

from tests.evals import run_tailor_cases_eval as runner
from tests.support.tailor_cases import copy_everything

PAYLOAD = runner.load_cases()
CASES = {case["id"]: case for case in PAYLOAD["cases"]}


def _whole(case_id: str):
    case = CASES[case_id]
    job, ctx = runner.job_and_context(case)
    return apply_no_loss(validate_tailored_output(copy_everything(case["resume"]), job, ctx), job, ctx), ctx


def test_the_cases_are_what_the_eval_says_they_are() -> None:
    assert list(CASES) == ["helm_answer", "helm_terse_answer", "over_long", "duplicate_skills", "control"]
    assert sum(case["repeat"] for case in PAYLOAD["cases"]) == 13
    for name in ("helm_answer", "helm_terse_answer"):
        case = CASES[name]
        assert "helm" not in case["resume"].lower() and "Helm" in case["posting"] and "ArgoCD" in case["posting"]
        assert [answer["question_id"] for answer in case["answers"]] == ["tool:helm", "tool:argocd"]
        assert case["answers"][1]["answer"].startswith("No")
    assert "helm" not in CASES["helm_terse_answer"]["answers"][0]["answer"].lower()  # only the question id names the skill
    # Synthetic, and no contact line: nothing for the privacy strip to withhold.
    for case in PAYLOAD["cases"]:
        _job, ctx = runner.job_and_context(case)
        assert not ctx.withheld, case["id"]
        assert "@" not in case["resume"] and "http" not in case["resume"]

    whole, ctx = _whole("over_long")
    assert runner._resume_roles(ctx) == 8
    whole_pages = measure_pages(whole)
    if whole_pages is None:
        pytest.skip("no PDF renderer here: the page premises are not checked")
    assert whole_pages == 3  # with the old-role trim applied; the resume itself is longer still
    control, _ctx = _whole("control")
    assert measure_pages(control) == 1 and control.length is None

    # The Skills lines of the duplicate case are plain ``Label: items`` lines: each runs on into the next.
    lines = resume_lines(CASES["duplicate_skills"]["resume"])
    first = lines.index("Languages: Python, Go, TypeScript") + 1
    assert resume_continuations(CASES["duplicate_skills"]["resume"])[first] == (first + 1, first + 2)


def test_the_plan_repeats_each_case_and_filters_by_name() -> None:
    runs = runner.plan(PAYLOAD)
    assert [(case["id"], run) for case, run in runs][:4] == [("helm_answer", 1), ("helm_answer", 2), ("helm_answer", 3), ("helm_terse_answer", 1)]
    assert len(runs) == 13
    assert [(case["id"], run) for case, run in runner.plan(PAYLOAD, ["control"])] == [("control", 1), ("control", 2)]
    with pytest.raises(ValueError, match="unknown case nope"):
        runner.plan(PAYLOAD, ["nope"])


def test_a_fake_run_writes_one_row_per_case_run_and_the_tables(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    report_path = tmp_path / "report.json"
    assert runner.main(["--fake-model", "--report", str(report_path), "--max-calls", "14", "--dump-dir", str(tmp_path / "dump"), "--quiet"]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["schema"] == runner.REPORT_SCHEMA and report["run"]["fake_model"] is True
    assert (report["run"]["calls_made"], report["run"]["planned_runs"], report["run"]["skipped"]) == (13, 13, [])
    assert report["run"]["instructions_digest"].startswith("sha256:")
    rows = report["rows"]
    assert [row["case"] for row in rows].count("helm_answer") == 3 and all(row["ok"] and row["model_calls"] == 1 for row in rows)
    # The fixture model adds nothing: GigAI's own rule shows Helm, and never ArgoCD.
    helm = next(row for row in rows if row["case"] == "helm_terse_answer")
    assert helm["model"]["keywords"]["Helm"] == {"by_model": [], "by_gigai": []}
    assert [hit["text"] for hit in helm["final"]["keywords"]["Helm"]["by_gigai"]] == ["Helm"]
    assert helm["final"]["keywords"]["ArgoCD"] == {"by_model": [], "by_gigai": []} and helm["final"]["added_from_answers"] == ["Helm"]
    control = next(row for row in rows if row["case"] == "control")
    assert control["final"]["added_from_answers"] == [] and control["final"]["length"] is None
    assert len(list((tmp_path / "dump").glob("*.prompt.txt"))) == 13

    assert runner.main(["--compare", str(report_path), str(report_path)]) == 0
    printed = capsys.readouterr().out
    assert printed.count("helm_terse_answer") == 6 and "BEFORE: tailor.md sha256:" in printed and "AFTER: tailor.md sha256:" in printed


def test_the_call_cap_skips_a_case_run_whose_two_calls_no_longer_fit(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    assert runner.main(["--fake-model", "--report", str(report_path), "--max-calls", "4", "--quiet"]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run"]["calls_made"] == 3 and len(report["rows"]) == 3
    assert report["run"]["skipped"][0] == "helm_terse_answer/1" and len(report["run"]["skipped"]) == 10


def test_the_live_run_is_gated_and_one_call_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    monkeypatch.delenv("GIGAI_ASSESS_EVAL_LIVE", raising=False)
    assert runner.main(["--report", str(tmp_path / "r.json")]) == 2
    assert "GIGAI_ASSESS_EVAL_LIVE=1" in capsys.readouterr().err
    assert runner.main(["--fake-model", "--report", str(tmp_path / "r.json"), "--concurrency", "2"]) == 2
    assert runner.main(["--fake-model"]) == 2  # no --report
    assert runner.main(["--dry-run"]) == 0
    assert "13 planned case runs; --max-calls 14" in capsys.readouterr().out
    assert not (tmp_path / "r.json").exists()
