"""0.1.11 (orchestrator #68): the gold-key scorer, tested on SYNTHETIC data only.

A real key is private and never in this repository: the key, the posting and
the results below are invented.  These prove the scorer counts what it says
(over-asks, under-asks, the list, the either readings); they say nothing about
any real result.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tests.evals import score_against_gold as gold

POSTING = """title: Staff Engineer
company: Example Works
location: Remote

About Example Works
We build example things for example people.

Requirements
8+ years building backend systems
Strong experience with Go, Kubernetes and Terraform
Experience with AWS, GCP, or similar cloud platforms
You enjoy building developer platforms
You are a clear communicator

Nice to have
Rust is a plus

Our stack
Postgres, Kafka, Redis
"""


def build_key(lines: list[dict[str, Any]], *, cloud: str = "unclear", years_either: bool = False) -> dict[str, Any]:
    n = {line["text"]: line["n"] for line in lines}
    rows = {
        n["8+ years building backend systems"]: [("hard", None)],
        n["Strong experience with Go, Kubernetes and Terraform"]: [("askable", "Go"), ("askable", "Kubernetes"), ("askable", "Terraform")],
        n["Experience with AWS, GCP, or similar cloud platforms"]: [("askable", None)],
        n["Rust is a plus"]: [("nice_to_have", None)],
    }
    either = {n["You enjoy building developer platforms"]: ["askable"], n["Postgres, Kafka, Redis"]: ["list_item", "nice_to_have"]}
    key_lines = []
    for line in lines:
        here = rows.get(line["n"], [])
        entry: dict[str, Any] = {"n": line["n"], "text_hash": line["text_hash"], "class": here[0][0] if here else None, "alternatives": [], "soft": not here,
                                 "reason": None if here else "trait" if line["text"].startswith("You are") else "outside", "rows": [{"item": item, "class": cls, "alternatives": []} for cls, item in here]}
        if line["n"] in either:
            entry["either"] = {"classes": either[line["n"]]}
        key_lines.append(entry)
    status = {"Go": "met", "Kubernetes": "unclear", "Terraform": "met"}
    musts = [{"line": number, "item": item, "class": cls, "status": status.get(item or "", "met"), "settled_by": []} for number, here in rows.items() for cls, item in here if cls in gold.MANDATORY]
    for row in musts:
        if row["line"] == n["Experience with AWS, GCP, or similar cloud platforms"]:
            row["status"] = cloud
        if years_either and row["class"] == "hard":
            row["status_either"] = ["met", "unclear"]
    posting = {"lines": key_lines, "musts": musts, "optional": [], "verdict": "pending_user_answers", "gate": "held",
               "questions_allowed": {"max_must": 4, "max_optional": 3}, "location": {"status": "met", "ask": False}, "sponsorship": "unknown"}
    return {"schema": gold.KEY_SCHEMA, "set": "synthetic", "postings": {"01": posting}}


def answer(*, verdict: str = "pending_user_answers", drop: tuple[str, ...] = (), add: tuple[dict[str, Any], ...] = (), ask: tuple[tuple[str, str], ...] = (("tool:kubernetes", "Kubernetes"), ("cloud:aws", "req-cloud")),
           status: dict[str, str] | None = None) -> dict[str, Any]:
    rows = [
        {"id": "req-years", "requirement": "8+ years building backend systems", "class": "hard", "status": "met"},
        {"id": "req-go", "requirement": "Go", "class": "askable", "status": "met", "class_basis": "Strong experience with Go, Kubernetes and Terraform"},
        {"id": "req-k8s", "requirement": "Kubernetes", "class": "askable", "status": "unclear", "class_basis": "Strong experience with Go, Kubernetes and Terraform"},
        {"id": "req-tf", "requirement": "Terraform", "class": "askable", "status": "met", "class_basis": "Strong experience with Go, Kubernetes and Terraform"},
        {"id": "req-cloud", "requirement": "Experience with AWS, GCP, or similar cloud platforms", "class": "askable", "status": "unclear", "alternatives": ["AWS", "GCP"]},
        {"id": "req-rust", "requirement": "Rust is a plus", "class": "nice_to_have", "status": "unclear"},
    ]
    rows = [{**row, "status": (status or {}).get(row["id"], row["status"])} for row in rows if row["id"] not in drop] + list(add)
    questions = [{"question_id": question_id, "question": "?", "requirement": target} for question_id, target in ask]
    return {"schema_version": "x", "result": {"verdict": verdict, "matrix": rows, "questions": ["?"] * len(questions), "structured_questions": questions},
            "resume_gate": {"decision": {"matched_above_threshold": "suggest", "pending_user_answers": "hold_question", "not_a_match": "not_a_match"}[verdict]}}


@pytest.fixture()
def lines() -> list[dict[str, Any]]:
    return gold.number_lines(POSTING)


def score(lines: list[dict[str, Any]], result: dict[str, Any], **key_options: Any) -> dict[str, Any]:
    return gold.score_result(build_key(lines, **key_options)["postings"]["01"], result, lines)


def test_lines_are_the_non_empty_body_lines_numbered_from_one(lines: list[dict[str, Any]]) -> None:
    assert [line["n"] for line in lines] == list(range(1, 13))
    assert lines[0]["text"] == "About Example Works" and lines[-1]["text"] == "Postgres, Kafka, Redis"
    assert all(len(line["text_hash"]) == 16 for line in lines)


def test_a_result_that_agrees_with_the_key_is_fully_correct(lines: list[dict[str, Any]]) -> None:
    out = score(lines, answer())
    assert out["fully_correct"] and out["verdict"]["right"] and out["gate"] == {"key": ["held"], "got": "held", "right": True}
    assert (out["questions"]["right"], out["questions"]["over_asks"], out["questions"]["under_asks"]) == (2, 0, 0)
    assert out["list"]["exact"] and out["list"]["line_coverage"] == {"required_lines": 4, "covered": 4, "must_lines": 3, "must_covered": 3}


def test_a_question_on_a_settled_row_a_soft_line_or_sponsorship_is_an_over_ask(lines: list[dict[str, Any]]) -> None:
    trait = {"id": "req-talk", "requirement": "You are a clear communicator", "class": "askable", "status": "unclear"}
    out = score(lines, answer(add=(trait,), status={"req-go": "unclear"}, ask=(("tool:kubernetes", "req-k8s"), ("cloud:aws", "req-cloud"), ("language:go", "req-go"), ("skill:talk", "req-talk"), ("other:work_authorization", "req-years"))))
    assert out["questions"]["over_asks"] == 4  # three wrong questions and a fifth must-have question past the cap of 4
    assert [entry["call"] for entry in out["questions"]["each"]] == ["right", "right", "over", "over", "over"]
    assert out["list"]["extra"] == [{"line": 8, "class": "askable", "on": "trait"}] and not out["list"]["musts_right"]
    assert out["status"]["wrong"] == [{"line": 5, "item": "Go", "key": "met", "got": "unclear"}] and not out["fully_correct"]


def test_an_unclear_must_have_the_result_settles_and_does_not_ask_is_an_under_ask(lines: list[dict[str, Any]]) -> None:
    out = score(lines, answer(verdict="matched_above_threshold", status={"req-k8s": "met", "req-cloud": "met"}, ask=()))
    assert out["questions"]["under_asks"] == 2 and not out["verdict"]["right"] and not out["gate"]["right"]
    assert out["questions"]["key_unclear_not_asked"] == [{"line": 5, "item": "Kubernetes"}, {"line": 6, "item": None}]


def test_a_required_list_left_as_one_row_misses_its_per_tool_rows(lines: list[dict[str, Any]]) -> None:
    one = {"id": "req-stack", "requirement": "Strong experience with Go, Kubernetes and Terraform", "class": "askable", "status": "unclear"}
    out = score(lines, answer(drop=("req-go", "req-k8s", "req-tf"), add=(one,), ask=(("cloud:aws", "req-cloud"),)))
    assert len(out["list"]["missing"]) == 2 and not out["list"]["musts_right"]


def test_an_either_line_takes_a_row_or_none_and_only_a_must_have_class_there_is_wrong(lines: list[dict[str, Any]]) -> None:
    enjoy = {"id": "req-enjoy", "requirement": "You enjoy building developer platforms", "class": "askable", "status": "met"}
    stack = [{"id": f"req-{tool.lower()}", "requirement": tool, "class": "list_item", "status": "unclear", "class_basis": "listed with no requirement wording: Postgres, Kafka, Redis"} for tool in ("Postgres", "Kafka", "Redis")]
    out = score(lines, answer(add=(enjoy, *stack), ask=(("tool:kubernetes", "req-k8s"), ("cloud:aws", "req-cloud"), ("tool:kafka", "req-kafka"))))
    assert out["fully_correct"] and out["list"]["exact"] and out["questions"]["optional"] == 1 and out["questions"]["over_asks"] == 0
    held = [{**row, "class": "askable"} for row in stack]
    out = score(lines, answer(add=tuple(held), ask=(("tool:kubernetes", "req-k8s"), ("cloud:aws", "req-cloud"), ("tool:kafka", "req-kafka"))))
    assert len(out["list"]["class_wrong"]) == 3 and out["questions"]["over_asks"] == 1 and not out["fully_correct"]


def test_a_row_open_either_way_is_right_asked_or_settled_and_accepts_both_verdicts(lines: list[dict[str, Any]]) -> None:
    asked = answer(status={"req-years": "unclear", "req-k8s": "met", "req-cloud": "met"}, ask=(("years:backend", "req-years"),))
    settled = answer(verdict="matched_above_threshold", status={"req-k8s": "met", "req-cloud": "met"}, ask=())
    key = build_key(lines, cloud="met", years_either=True)["postings"]["01"]
    for row in key["musts"]:
        row["status"] = "met"
    for result in (asked, settled):
        out = gold.score_result(key, result, lines)
        assert out["fully_correct"] and out["verdict"]["key"] == ["matched_above_threshold", "pending_user_answers"]
    assert gold.score_result(key, asked, lines)["questions"]["either"] == 1


def test_a_location_question_the_key_does_not_ask_is_wrong_and_one_it_asks_is_right(lines: list[dict[str, Any]]) -> None:
    place = {"id": "elig-work-mode", "requirement": "Presence in Springfield", "class": "hard", "status": "unclear"}
    result = answer(add=(place,), ask=(("tool:kubernetes", "req-k8s"), ("cloud:aws", "req-cloud"), ("location:springfield", "elig-work-mode")))
    out = score(lines, result)
    assert not out["location"]["right"] and out["questions"]["over_asks"] == 1 and out["questions"]["each"][2]["why"] == "the key has the location met"
    key = build_key(lines)["postings"]["01"]
    key["musts"].append({"line": 0, "item": "elig-location", "class": "hard", "status": "unclear", "settled_by": []})
    key["location"] = {"status": "unclear", "ask": True}
    out = gold.score_result(key, result, lines)
    assert out["location"]["right"] and out["fully_correct"] and out["questions"]["right"] == 3


def test_a_results_folder_is_scored_per_cli_and_nothing_is_written_inside_the_repository(tmp_path: Path, lines: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "set"
    (data / "postings").mkdir(parents=True)
    (data / "gold").mkdir()
    (data / "postings" / "01-example-works-staff-engineer.md").write_text(POSTING, encoding="utf-8")
    (data / "gold" / "key.json").write_text(json.dumps(build_key(lines)), encoding="utf-8")
    for cli, result in (("claude_cli", answer()), ("codex_cli", answer(verdict="matched_above_threshold", status={"req-k8s": "met", "req-cloud": "met"}, ask=()))):
        folder = data / "results" / "arm" / "01-example-works-staff-engineer" / cli
        folder.mkdir(parents=True)
        (folder / "assessment.json").write_text(json.dumps(result), encoding="utf-8")
    assert gold.main(["--key", str(data / "gold" / "key.json"), "--results", str(data / "results" / "arm")]) == 0
    report = json.loads((data / "gold" / "scores" / "arm" / "score.json").read_text(encoding="utf-8"))
    assert report["by_cli"]["claude_cli"]["fully_correct"] == 1 and report["by_cli"]["codex_cli"]["under_asks"] == 2 and report["by_cli"]["codex_cli"]["fully_correct"] == 0
    assert "| codex_cli | 1 | 0 |" in (data / "gold" / "scores" / "arm" / "score.md").read_text(encoding="utf-8")
    assert gold.main(["--key", str(data / "gold" / "key.json"), "--results", str(data / "results" / "arm"), "--out", str(gold.REPO_ROOT / "tests" / "evals" / "scores")]) == 2
    assert "inside the repository" in capsys.readouterr().err and not (gold.REPO_ROOT / "tests" / "evals" / "scores").exists()
    (data / "postings" / "01-example-works-staff-engineer.md").write_text(POSTING.replace("8+ years", "9+ years"), encoding="utf-8")
    assert gold.main(["--key", str(data / "gold" / "key.json"), "--results", str(data / "results" / "arm")]) == 2
    assert "not the lines the key was built on" in capsys.readouterr().err
