"""0110-038: the rules A/B mode of the assess eval -- its fixture and its runner, offline.

The fixture is synthetic and bounded (at most 30 postings, at most 60 model
calls planned); the runner goes end to end through the shipped path with the
fake model, refuses a live run that is not asked for, and never spends more
than its call budget.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from gigai.scout import proposal_execution
from gigai.scout.assessment_core import INSTRUCTIONS_DIGEST

from tests.evals import run_assess_rules_eval as rules


@pytest.fixture(autouse=True)
def _isolate_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proposal_execution, "resolve_model_adapter", proposal_execution.resolve_model_adapter)
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", raising=False)
    monkeypatch.delenv("GIGAI_ASSESS_EVAL_LIVE", raising=False)


def test_the_fixture_is_synthetic_and_bounded() -> None:
    payload = rules.load_cases()
    cases = payload["cases"]

    assert 1 <= len(cases) <= rules.MAX_CASES == 30
    assert len(rules.plan(payload)) <= rules.DEFAULT_MAX_CALLS == 60
    text = json.dumps(payload).lower()
    # The operator's UAT postings are named as SHAPES only; no real company, no contact value.
    for real in ("hypr", "duolingo", "opswat"):
        assert not re.search(rf"\"company\": \"[^\"]*{real}", text), real
    assert "@" not in text and "linkedin.com" not in text and "github.com" not in text and "http" not in text
    for case in cases:
        assert case["expected"] and set(case["expected"]) <= set(rules.harness.VERDICTS)
        # Short on purpose: the incomplete-posting guard never withholds a Matched on these.
        assert len(rules.posting_text(payload, case)) < 1_200
    rules_covered = {case["rule"].split(":")[0].split(" + ")[0] for case in cases}
    assert {"rule 4", "rule 5", "work mode"} <= rules_covered
    shapes = {case.get("shape") for case in cases}
    assert {"the Hypr shape", "the Duolingo shape", "the Opswat shape"} <= shapes


def test_the_pre_change_column_is_the_same_candidate_with_no_work_mode() -> None:
    payload = rules.load_cases()
    for case in payload["cases"]:
        after = rules.render_prompt(payload, case, rules.SHIPPED)
        before = rules.render_prompt(payload, case, rules.BEFORE)
        assert "CANDIDATE WORK MODE" not in before
        if payload["candidates"][case["candidate"]]["work_mode"]:
            paragraph = next(block for block in after.split("\n\n") if block.startswith("CANDIDATE WORK MODE"))
            assert after.replace("\n\n" + paragraph, "") == before
        else:
            assert after == before, "a candidate with no work mode has one prompt for both versions"
    planned = rules.plan(payload)
    with_mode = sum(1 for case in payload["cases"] if payload["candidates"][case["candidate"]]["work_mode"])
    assert len(planned) == len(payload["cases"]) + with_mode


def test_a_live_run_must_be_asked_for(tmp_path: Path) -> None:
    assert rules.main(["--report", str(tmp_path / "report.json"), "--quiet"]) == 2
    assert not (tmp_path / "report.json").exists()


def test_fake_model_run_goes_end_to_end_and_keeps_to_its_call_budget(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    assert rules.main(["--fake-model", "--report", str(report_path), "--quiet", "--max-calls", "9"]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))

    run, summary = report["run"], report["summary"]
    assert report["schema"] == "gigai-assess-rules-eval-report:1"
    assert run["fake_model"] is True and run["model_target"] == "ollama_local"
    assert run["instructions_digest"] == INSTRUCTIONS_DIGEST and run["prompt_version"] == "assess-prompt-v5"
    # Two calls are held back for each case (the product's own retry), so a budget of 9 runs 8 one-call cases.
    assert summary["calls"] == 8 == len(report["rows"]) and summary["calls"] <= run["max_calls"]
    assert len(run["skipped"]) == run["planned_calls"] - 8
    # bindings._test_model_handler answers every prompt the same way: pending on cloud:gcp.
    assert {row["verdict"] for row in report["rows"]} == {"pending_user_answers"}
    first = summary["cases"][0]
    assert first["same_prompt"] is True and first["v5"] == first["v4"] == "pending_user_answers" and first["v5_pass"] is False


def test_the_summary_scores_each_case_against_its_expected_verdicts() -> None:
    payload = rules.load_cases()

    def row(case: str, version: str, verdict: str) -> dict:
        return {"case": case, "version": version, "ok": True, "attempts": 1, "verdict": verdict, "question_ids": [], "usage": None, "not_assessed_reason": None}

    rows = [
        row("visa-no-sponsorship", "v5", "not_a_match"),
        row("remote-only-hybrid-own-city", "v5", "not_a_match"),
        row("remote-only-hybrid-own-city", "v4", "matched_above_threshold"),
        row("remote-only-city-mode-not-stated", "v5", "matched_above_threshold"),
        row("remote-only-city-mode-not-stated", "v4", "matched_above_threshold"),
    ]
    summary = rules.summarize(payload, rows)
    by_case = {item["case"]: item for item in summary["cases"]}

    assert by_case["visa-no-sponsorship"]["v5_pass"] is True and by_case["visa-no-sponsorship"]["v4"] == "not_a_match"
    own_city = by_case["remote-only-hybrid-own-city"]
    assert own_city["v5_pass"] is True and own_city["v4_pass"] is True and own_city["v4_meets_v5_expectation"] is False
    assert summary["wrong_v5"] == ["remote-only-city-mode-not-stated"]
    assert summary["v5"] == {"passed": 2, "of": 3, "rate": 0.6667}
    assert summary["calls"] == 5


def test_case_flag_selects_only_the_named_cases(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = rules.load_cases()
    with_mode = next(case["id"] for case in payload["cases"] if payload["candidates"][case["candidate"]]["work_mode"])
    without_mode = next(case["id"] for case in payload["cases"] if not payload["candidates"][case["candidate"]]["work_mode"])

    assert rules.main(["--dry-run", "--case", with_mode, "--case", without_mode]) == 0
    listed = [line.split()[1:3] for line in capsys.readouterr().out.splitlines()]
    assert sorted(listed) == sorted([["v5", with_mode], ["v4", with_mode], ["v5", without_mode]])

    assert rules.main(["--dry-run", "--case", with_mode, "--only-version", "v5"]) == 0
    assert [line.split()[1:3] for line in capsys.readouterr().out.splitlines()] == [["v5", with_mode]]

    report_path = tmp_path / "report.json"
    assert rules.main(["--fake-model", "--case", without_mode, "--report", str(report_path), "--quiet"]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [row["case"] for row in report["rows"]] == [without_mode]
    assert [item["case"] for item in report["summary"]["cases"]] == [without_mode]
    assert report["run"]["planned_calls"] == 1


def test_an_unknown_case_name_is_a_clear_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert rules.main(["--dry-run", "--case", "no-such-case"]) == 2
    err = capsys.readouterr().err
    assert "unknown case 'no-such-case'" in err and "known cases:" in err
    assert rules.main(["--fake-model", "--case", "no-such-case", "--report", str(tmp_path / "r.json")]) == 2
    assert not (tmp_path / "r.json").exists()
