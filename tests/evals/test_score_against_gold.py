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
    assert out["fully_correct"] and out["verdict"]["right"] and out["gate"] == {"key": ["held"], "got": "held", "right": True, "soft": False}
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


MASTER = """<!-- gigai-master:1 -->

## Summary

- Builder of example systems. <!-- id:sum-1 -->

## Experience

### ACME <!-- id:r-1 -->
**Engineer | 2020 - Present**
- Ran the example platform. <!-- id:b-1 -->
- Cut example costs. <!-- id:b-2 -->
- Led example migrations. <!-- id:b-3 -->

## Skills

- Go, Rust (async, tokio), Postgres <!-- id:s-1 -->
"""
RESUME_KEY = {"musts": [{"line": 1, "item": None, "class": "hard", "status": "met", "settled_by": ["b-1", "b-9"]},
                        {"line": 2, "item": None, "class": "askable", "status": "met", "settled_by": ["b-2"], "status_either": ["met", "unclear"]},
                        {"line": 3, "item": None, "class": "askable", "status": "unclear", "settled_by": ["b-3"]}]}
SUGGESTIONS = {"selection": {"model_pick": {"summary": "sum-1", "lines": ["b-1", "b-2"]}, "added_by_code": [], "pages": 1, "max_pages": 2}}
PRINTED = {"summary": ["sum-1"], "entries": {"r-1": ["b-1", "b-2"]}, "other": [], "skills": ["Go", "Rust (async, tokio)", "Postgres"]}
SELECTION_MD = "## Summary\n\n- Builder of example systems. <!-- R1 -->\n\n## Experience\n\n### ACME <!-- R2 -->\nEngineer | 2020 - Present\n- Ran the example platform. <!-- R3 -->\n- Cut example costs. <!-- R4 -->\n\n## Skills\n\n- Go, Rust (async, tokio), Postgres\n"


def resume(**changes: Any) -> dict[str, Any]:
    args = {"key": RESUME_KEY, "suggestions": SUGGESTIONS, "printed": PRINTED, "selection_md": SELECTION_MD, "master_text": MASTER} | changes
    return gold.resume_check(args["key"], args["suggestions"], args["printed"], args["selection_md"], args["master_text"], args.get("stored"))


def test_a_resume_that_prints_a_settling_line_of_every_met_row_verbatim_within_limits_meets_the_bar() -> None:
    assert resume()["bar"] is True


def test_a_met_row_with_none_of_its_settling_lines_printed_is_uncovered_but_an_open_or_either_row_is_not_owed() -> None:
    printed = {**PRINTED, "entries": {"r-1": ["b-2"]}}
    out = resume(printed=printed, suggestions={"selection": {**SUGGESTIONS["selection"], "model_pick": {"summary": "sum-1", "lines": ["b-2"]}}})
    assert out["uncovered"] == [{"line": 1, "item": None}] and out["covers"] is False and out["bar"] is False


def test_a_printed_line_not_in_the_master_a_cut_skill_or_too_many_pages_fails() -> None:
    assert resume(selection_md=SELECTION_MD.replace("- Cut example costs.", "- Ran the example platform at scale."))["verbatim"] is False
    assert resume(printed={**PRINTED, "skills": ["Go", "Postgres"]})["skills_kept"] is False
    assert resume(suggestions={"selection": {**SUGGESTIONS["selection"], "pages": 3}})["pages_ok"] is False
    # A selection stored since 0.1.11.5 counts no page: it is within its limit while it prints at most its cap of bullets.
    capped = {**SUGGESTIONS["selection"], "pages": None, "max_bullets": 20}
    assert resume(suggestions={"selection": capped})["pages_ok"] is True and resume(suggestions={"selection": capped})["bullets"] == len({i for ids in PRINTED["entries"].values() for i in ids})
    assert resume(suggestions={"selection": {**capped, "max_bullets": 1}})["pages_ok"] is False


STORED = {"picked": [{"id": "sum-1", "code": "summary_variant"}, {"id": "b-1", "code": "supports_requirement"}, {"id": "b-2", "code": "picked_by_assessment"}],
          "cut_for_length": [{"id": "b-3", "code": "cut_lowest_value", "kind": "bullet"}, {"id": "r-9", "code": "cut_role_dropped", "kind": "role"}], "pins": []}


def stored(*extra: tuple[str, str], cut: tuple[str, ...] = ("b-3",), drop: tuple[str, ...] = ()) -> dict[str, Any]:
    return {**STORED, "picked": [item for item in STORED["picked"] if item["id"] not in drop] + [{"id": i, "code": c} for i, c in extra],
            "cut_for_length": [{"id": i, "code": "cut_lowest_value", "kind": "bullet"} for i in cut]}


def test_c1_reads_the_stored_resume_a_room_fill_line_printed_while_a_picked_line_is_cut_fails() -> None:
    assert resume()["c1"] is None and resume()["c1_checked"] is False  # no stored resume: not checkable, never a pass
    assert resume(stored=stored())["c1"] is True  # a picked line cut and nothing unpicked printed: the fit cut, not C1
    out = resume(stored=stored(("b-9", "requirement_evidence")))
    assert out["c1"] is False and out["c1_roomfill_printed"] == ["b-9"] and out["c1_picked_cut"] == ["b-3"] and out["bar"] is False
    assert resume(stored=stored(("b-9", "requirement_evidence"), cut=()))["c1"] is True  # nothing cut: no violation
    assert resume(stored={**stored(("b-9", "requirement_evidence")), "pins": ["b-9"]})["c1"] is True  # pinned lines are not room-fill


def test_c1_a_recent_role_line_fails_only_when_a_cut_line_leaves_a_met_row_with_nothing_on_the_page() -> None:
    recent = ("b-8", "recent_role_present")
    assert resume(stored=stored(recent))["c1"] is True  # b-3 settles only the open row 3: exempt
    key = {"musts": [*RESUME_KEY["musts"], {"line": 4, "item": None, "class": "askable", "status": "met", "settled_by": ["b-3", "b-4"]}]}
    out = resume(key=key, stored=stored(recent, cut=("b-3", "b-4")))
    assert out["c1"] is False and out["c1_settling_cut"] == ["b-3", "b-4"] and out["c1_recent_printed"] == ["b-8"]
    picked_b4 = {"selection": {**SUGGESTIONS["selection"], "model_pick": {"summary": "sum-1", "lines": ["b-1", "b-2", "b-4"]}}}
    assert resume(key=key, suggestions=picked_b4, stored=stored(recent, ("b-4", "supports_requirement"), cut=("b-3",)))["c1"] is True  # row 4 keeps b-4 on the page
    assert resume(key=key, stored=stored(cut=("b-3", "b-4")))["c1"] is True  # no recent-role line printed: a plain fit cut


def test_the_stored_selection_is_read_from_the_path_the_suggestions_name(tmp_path: Path) -> None:
    path = tmp_path / "resume.json"
    path.write_text(json.dumps({"selection": STORED}), encoding="utf-8")
    assert gold.stored_selection({"selection": {"resume": {"stored_path": str(path)}}}) == STORED
    assert gold.stored_selection({"selection": {"resume": {"stored_path": str(tmp_path / "gone.json")}}}) is None and gold.stored_selection({}) is None


def soft_key(lines: list[dict[str, Any]], **row: Any) -> dict[str, Any]:
    key = build_key(lines, cloud="met")["postings"]["01"]
    for must in key["musts"]:
        if must["line"] == 6:
            must.update(row)
    key["musts"] = [must for must in key["musts"] if must["item"] != "Kubernetes"]  # one open row only: the soft one
    return key


def test_a_question_on_a_soft_ask_row_is_a_soft_over_ask_not_a_failure(lines: list[dict[str, Any]]) -> None:
    key = soft_key(lines, soft_ask=True)
    key["questions_allowed"]["soft"] = [{"line": 6, "item": None}]
    asked = answer(verdict="pending_user_answers", drop=("req-k8s",), status={"req-cloud": "unclear"}, ask=(("cloud:aws", "req-cloud"),))
    out = gold.score_result(key, asked, lines)
    assert out["fully_correct"] and out["questions"]["over_asks"] == 0 and out["questions"]["soft_asks"] == 1 and out["questions"]["each"][0]["call"] == "soft"
    assert out["status"]["wrong"] == [] and out["verdict"]["right"] and out["verdict"]["soft"] is True and out["gate"]["soft"] is True
    settled = answer(verdict="matched_above_threshold", drop=("req-k8s",), status={"req-cloud": "met"}, ask=())
    out = gold.score_result(key, settled, lines)
    assert out["fully_correct"] and out["questions"]["soft_asks"] == 0 and out["verdict"]["soft"] is False
    plain = soft_key(lines)  # without the marker the same question is an over-ask, a wrong status and a wrong verdict
    out = gold.score_result(plain, asked, lines)
    assert (out["questions"]["over_asks"], len(out["status"]["wrong"]), out["verdict"]["right"]) == (1, 1, False) and not out["fully_correct"]


def test_a_soft_ask_does_not_hide_a_firm_open_row_and_a_third_set_either_question_stays_a_soft_over_ask(lines: list[dict[str, Any]]) -> None:
    key = build_key(lines, cloud="met")["postings"]["01"]
    key["musts"][-1].update(soft_ask=True)  # the cloud row (soft) next to the firm open Kubernetes row
    out = gold.score_result(key, answer(verdict="matched_above_threshold", status={"req-cloud": "met", "req-k8s": "met"}, ask=()), lines)
    assert not out["verdict"]["right"] and out["verdict"]["key"] == ["pending_user_answers"] and out["questions"]["under_asks"] == 1
    held = gold.score_result(key, answer(status={"req-cloud": "unclear"}, ask=(("tool:kubernetes", "req-k8s"), ("cloud:aws", "req-cloud"))), lines)
    assert held["fully_correct"] and held["questions"]["soft_asks"] == 1 and held["questions"]["over_asks"] == 0
    either = build_key(lines, cloud="met", years_either=True)["postings"]["01"]
    either["musts"][0]["status_either"] = ["met", "unclear"]
    out = gold.score_result(either, answer(status={"req-years": "unclear", "req-cloud": "met"}, ask=(("tool:kubernetes", "req-k8s"), ("years:backend", "req-years"))), lines)
    assert out["questions"]["soft_asks"] == 1 and out["questions"]["over_asks"] == 0 and out["questions"]["each"][1]["call"] == "either"


def test_a_row_the_key_calls_matched_or_pending_accepts_both_verdicts_with_a_hard_row_open_either_way(lines: list[dict[str, Any]]) -> None:
    key = build_key(lines, cloud="met", years_either=True)["postings"]["01"]
    for must in key["musts"]:
        must["status"] = "met"
    for result in (answer(verdict="matched_above_threshold", status={"req-k8s": "met"}, ask=()), answer(status={"req-k8s": "met", "req-years": "unclear"}, ask=(("years:backend", "req-years"),))):
        assert gold.score_result(key, result, lines)["verdict"]["right"]
    assert gold.key_view(key)["verdicts"] == ["matched_above_threshold", "pending_user_answers"]


def test_a_met_row_citing_none_of_the_keys_settling_lines_is_a_wrong_citation_and_advisory_only(lines: list[dict[str, Any]]) -> None:
    key = build_key(lines, cloud="met")["postings"]["01"]
    for must in key["musts"]:
        must["status"] = "met"
        must["settled_by"] = ["b-aaa"] if must["item"] != "Go" else []  # a row with no settled_by is skipped
    key["musts"][0]["status_either"] = ["met", "unclear"]  # an open-either row is skipped too
    rows = {"req-years": ["b-zzz"], "req-go": ["b-zzz"], "req-k8s": ["b-zzz"], "req-tf": ["b-aaa", "b-zzz"], "req-cloud": ["b-zzz"]}
    result = answer(verdict="matched_above_threshold", status={"req-k8s": "met", "req-cloud": "met"}, ask=())
    for row in result["result"]["matrix"]:
        row["sources"] = rows.get(row["id"], [])
    out = gold.score_result(key, result, lines)
    assert [(item["group"], item["item"], item["cited"]) for item in out["wrong_citation"]] == [("must", "Kubernetes", ["b-zzz"]), ("must", None, ["b-zzz"])]
    assert out["wrong_citation"][1]["line"] == 6 and out["fully_correct"]  # advisory: the verdict still stands
    unclear = gold.score_result(key, answer(status={"req-k8s": "unclear"}, ask=()), lines)  # an unclear row is not a met row with a wrong citation
    assert all(item["item"] != "Kubernetes" for item in unclear["wrong_citation"])


def test_a_met_nice_to_have_row_is_a_wrong_citation_only_against_key_lines_and_counted_apart_when_the_key_has_none(lines: list[dict[str, Any]]) -> None:
    key = build_key(lines, cloud="met")["postings"]["01"]

    def rust(sources: list[str], status: str = "met") -> dict[str, Any]:
        result = answer(status={"req-rust": status}, ask=())
        for row in result["result"]["matrix"]:
            row["sources"] = sources if row["id"] == "req-rust" else []
        return result

    out = gold.score_result(key, rust(["b-zzz"]), lines)  # the key records no lines for the row
    assert out["wrong_citation"] == [] and [item["cited"] for item in out["no_key_lines"]] == [["b-zzz"]]
    assert gold.score_result(key, rust([]), lines)["no_key_lines"] == []  # no source cited: nothing to judge
    assert gold.score_result(key, rust(["b-zzz"], "unclear"), lines)["no_key_lines"] == []  # not met
    for line in key["lines"]:
        for entry in line["rows"]:
            if entry["class"] == "nice_to_have":
                entry["supporting"] = ["b-aaa"]  # the key records lines for the row
    wrong = gold.score_result(key, rust(["b-zzz"]), lines)
    assert [(item["group"], item["item"], item["cited"]) for item in wrong["wrong_citation"]] == [("nice", None, ["b-zzz"])] and wrong["no_key_lines"] == []
    right = gold.score_result(key, rust(["b-aaa", "b-zzz"]), lines)
    assert right["wrong_citation"] == [] and right["fully_correct"] == wrong["fully_correct"]  # advisory only


# --- strict keys (F6a, 0.1.11.12): evidence tiers, scored citations, the fit cap and the release bar ---------


NAMES = {(4, None): "years", (5, "Go"): "go", (5, "Kubernetes"): "k8s", (5, "Terraform"): "tf", (6, None): "cloud"}
#: Two nice-to-have rows on no posting line: they widen the fit's denominator (weight 1 each) and nothing else.
EXTRA_NICE = tuple({"id": f"req-x{i}", "requirement": f"Extra nicety {i}", "class": "nice_to_have", "status": "unclear"} for i in (1, 2))


def strict_key(lines: list[dict[str, Any]], outcome: str = "rejected", **tiers: str) -> dict[str, Any]:
    """The synthetic key as a strict key: every must row carries a tier (``clear`` unless named), met only when clear."""

    key = build_key(lines, cloud="met")["postings"]["01"]
    for row in key["musts"]:
        name = NAMES[(row["line"], row["item"])]
        tier = tiers.get(name, "clear")
        ids = [] if tier == "missing" else [f"b-{name}"]
        row.update(evidence_tier=tier, evidence_ids=ids, in_submitted=tier == "clear", status="met" if tier == "clear" else "unclear", settled_by=ids if tier == "clear" else [])
    key["outcome"] = outcome
    return key


def met_result(*, add: tuple[dict[str, Any], ...] = (), unclear: tuple[str, ...] = (), **sources: list[str]) -> dict[str, Any]:
    """A matched result with every row met (``unclear`` names the rows left open), each must row citing its own master line unless ``sources`` says otherwise."""

    status = {row: "unclear" if row in unclear else "met" for row in ("req-years", "req-go", "req-k8s", "req-tf", "req-cloud", "req-rust")}
    result = answer(verdict="matched_above_threshold", status=status, add=add, ask=())
    for row in result["result"]["matrix"]:
        name = str(row["id"]).removeprefix("req-")
        row["sources"] = sources.get(name, [f"b-{name}"])
    return result


def test_a_met_result_on_a_buried_row_is_a_lenient_met_and_a_row_disagreement(lines: list[dict[str, Any]]) -> None:
    key = strict_key(lines, k8s="buried")
    out = gold.score_result(key, met_result(), lines)
    assert out["lenient_met"] == [{"line": 5, "item": "Kubernetes", "tier": "buried"}]
    assert (out["row_agreement"]["key_rows"], out["row_agreement"]["agree"], out["row_agreement"]["value"]) == (5, 4, 0.8)
    assert [(row["item"], row["agree"]) for row in out["row_agreement"]["each"] if not row["agree"]] == [("Kubernetes", False)]
    honest = gold.score_result(key, met_result(unclear=("req-k8s",)), lines)  # the result leaves the buried row open: agreement, no lenient met
    assert honest["lenient_met"] == [] and honest["row_agreement"]["value"] == 1.0
    missing = gold.score_result(key, answer(verdict="matched_above_threshold", drop=("req-k8s",), status={"req-cloud": "met"}, ask=()), lines)  # no row for the buried row: a hidden gap, a disagreement
    assert (missing["row_agreement"]["key_rows"], missing["row_agreement"]["agree"], missing["row_agreement"]["absent_clear"]) == (5, 4, 0)
    assert [row["got"] for row in missing["row_agreement"]["each"] if not row["agree"]] == ["absent"]
    short = gold.score_result(key, answer(verdict="matched_above_threshold", drop=("req-cloud",), status={"req-k8s": "unclear"}, ask=()), lines)  # no row for a clear row: list completeness, out of the agreement
    assert (short["row_agreement"]["key_rows"], short["row_agreement"]["agree"], short["row_agreement"]["value"], short["row_agreement"]["absent_clear"]) == (4, 4, 1.0, 1)
    assert [row["item"] for row in short["row_agreement"]["each"]] == [None, "Go", "Kubernetes", "Terraform"] and len(short["list"]["missing"]) == 1


def test_an_answered_result_row_agrees_with_an_unclear_key_row_disagrees_with_a_met_one_and_is_never_lenient_or_an_over_ask(lines: list[dict[str, Any]]) -> None:
    result = met_result(unclear=("req-k8s",))
    for row in result["result"]["matrix"]:
        if row["id"] in ("req-k8s", "req-go"):  # the buried row (key unclear) and a clear row (key met), both answer-only
            row["status"] = "answered"
    out = gold.score_result(strict_key(lines, k8s="buried"), result, lines)
    assert [(row["item"], row["got"], row["agree"]) for row in out["row_agreement"]["each"] if row["item"] in ("Go", "Kubernetes")] == [("Go", "answered", False), ("Kubernetes", "answered", True)]
    assert (out["row_agreement"]["key_rows"], out["row_agreement"]["agree"], out["lenient_met"]) == (5, 4, [])
    assert out["questions"]["over_asks"] == 0 and out["questions"]["each"] == []
    assert out["fit"] < gold.FIT_CAP and not out["fit_violation"]  # answered is held like unclear by the product's fit too
    assert out["status"]["wrong"] == [{"line": 5, "item": "Go", "key": "met", "got": "answered"}]  # read as unclear: wrong against met, right against unclear


def test_an_unclear_must_have_the_result_holds_as_answered_is_not_owed_a_question(lines: list[dict[str, Any]]) -> None:
    out = score(lines, answer(status={"req-k8s": "answered", "req-cloud": "answered"}, ask=()))  # both answers on file, nothing asked
    assert (out["questions"]["under_asks"], out["questions"]["key_unclear_not_asked"], out["status"]["wrong"]) == (0, [], [])
    one = score(lines, answer(status={"req-k8s": "answered"}, ask=(("cloud:aws", "req-cloud"),)))  # one on file, the other asked
    assert (one["questions"]["right"], one["questions"]["under_asks"], one["questions"]["over_asks"]) == (1, 0, 0)


def test_a_wrong_citation_on_a_strict_must_row_fails_fully_correct(lines: list[dict[str, Any]]) -> None:
    out = gold.score_result(strict_key(lines), met_result(go=["b-zzz"]), lines)
    assert out["wrong_citation"] == [{"group": "must", "line": 5, "item": "Go", "cited": ["b-zzz"], "scored": True}] and not out["fully_correct"]
    assert gold.score_result(strict_key(lines), met_result(), lines)["fully_correct"]
    key = build_key(lines, cloud="met")["postings"]["01"]  # a v2 key (no tier fields): the same wrong citation stays advisory
    for must in key["musts"]:
        must.update(status="met", settled_by=[f"b-{NAMES[(must['line'], must['item'])]}"])
    before = gold.score_result(key, met_result(go=["b-zzz"]), lines)
    assert [item["scored"] for item in before["wrong_citation"]] == [False] and before["fully_correct"]


def test_fit_100_with_a_buried_hard_row_is_a_fit_violation_and_fit_69_is_not(lines: list[dict[str, Any]]) -> None:
    key = strict_key(lines, years="buried")
    out = gold.score_result(key, met_result(), lines)  # 5 must rows x2 + 1 nice, all met
    assert (out["fit"], out["fit_violation"], out["outcome"]) == (100, True, "rejected")
    assert out["key_below_met_rows"] == [{"line": 4, "item": None, "tier": "buried"}]
    low = gold.score_result(key, met_result(add=EXTRA_NICE, unclear=("req-cloud",)), lines)  # 9 of 13: fit 69
    assert (low["fit"], low["fit_violation"], low["key_below_met_rows"]) == (69, False, out["key_below_met_rows"])
    assert gold.score_result(strict_key(lines), met_result(), lines)["fit_violation"] is False  # every key row clear: nothing to cap


def test_an_elig_row_without_a_tier_is_not_counted_and_a_v2_key_has_no_agreement(lines: list[dict[str, Any]]) -> None:
    key = strict_key(lines)
    key["musts"].append({"line": 0, "item": None, "class": "hard", "status": "met", "settled_by": []})
    place = {"id": "elig-location", "requirement": "Remote", "class": "hard", "status": "met"}
    out = gold.score_result(key, met_result(add=(place,)), lines)
    assert out["row_agreement"]["key_rows"] == 5 and all(row["line"] != 0 for row in out["row_agreement"]["each"])
    v2 = score(lines, answer())
    assert v2["row_agreement"] == {"key_rows": 0, "agree": 0, "value": None, "absent_clear": 0, "each": []} and v2["lenient_met"] == []
    assert (v2["fit"], v2["fit_violation"], v2["key_below_met_rows"], v2["outcome"]) == (55, False, [], None)  # 6 of 11


def test_the_bar_block_scores_fit_violations_lenient_met_agreement_and_over_asks_for_the_set_and_per_outcome(tmp_path: Path, lines: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]) -> None:
    data = tmp_path / "set"
    (data / "postings").mkdir(parents=True)
    (data / "gold").mkdir()
    (data / "postings" / "01-example-works-staff-engineer.md").write_text(POSTING, encoding="utf-8")
    (data / "gold" / "key.json").write_text(json.dumps({"schema": gold.KEY_SCHEMA, "set": "synthetic", "postings": {"01": strict_key(lines, k8s="buried")}}), encoding="utf-8")
    results = (("claude_cli", met_result()), ("codex_cli", met_result(add=EXTRA_NICE, unclear=("req-k8s", "req-rust"))),  # the honest result: 8 of 13, fit 62
               ("gemini_cli", met_result(add=EXTRA_NICE, unclear=("req-tf", "req-rust"))))  # lenient on Kubernetes (and Terraform left open) but fit 62: no violation, the lenient-met item fails it
    for cli, result in results:
        folder = data / "results" / "arm" / "01-example-works-staff-engineer" / cli
        folder.mkdir(parents=True)
        (folder / "assessment.json").write_text(json.dumps(result), encoding="utf-8")
    assert gold.main(["--key", str(data / "gold" / "key.json"), "--results", str(data / "results" / "arm")]) == 0
    report = json.loads((data / "gold" / "scores" / "arm" / "score.json").read_text(encoding="utf-8"))
    lenient, honest, capped = report["bar"]["claude_cli"], report["bar"]["codex_cli"], report["bar"]["gemini_cli"]
    assert sorted(lenient) == ["all", "rejected"] and lenient["all"] == lenient["rejected"]
    assert lenient["all"]["row_agreement"] == {"value": 0.8, "pass": False} and lenient["all"]["fit_violations"] == {"value": 1, "pass": False} and lenient["all"]["pass"] is False
    assert lenient["all"]["lenient_met"] == {"value": 1, "pass": False} and list(lenient["all"])[5:] == ["fit_violations", "lenient_met", "row_agreement", "max_over_asks_per_job", "pass"]
    assert honest["all"]["row_agreement"] == {"value": 1.0, "pass": True} and honest["all"]["fit_violations"] == {"value": 0, "pass": True} and honest["all"]["lenient_met"] == {"value": 0, "pass": True}
    assert honest["all"]["max_over_asks_per_job"] == {"value": 0, "pass": True} and honest["all"]["pass"] is True
    assert capped["all"]["fit_violations"] == {"value": 0, "pass": True} and capped["all"]["lenient_met"] == {"value": 1, "pass": False} and capped["all"]["pass"] is False
    assert (lenient["all"]["results"], lenient["all"]["key_rows"], lenient["all"]["agree"], lenient["all"]["absent_clear"]) == (1, 5, 4, 0)
    totals = report["by_cli"]["claude_cli"]
    assert (totals["strict_rows"], totals["rows_agree"], totals["lenient_met"], totals["fit_violations"]) == (5, 4, 1, 1)
    rendered = (data / "gold" / "scores" / "arm" / "score.md").read_text(encoding="utf-8")
    assert "| claude_cli | all | 1 | 0 | 1 FAIL | 1 FAIL | 0.800 (4/5) FAIL | 0 ok | FAIL |" in rendered and "| codex_cli | all | 1 | 0 | 0 ok | 0 ok | 1.000 (5/5) ok | 0 ok | ok |" in rendered
    assert "| gemini_cli | all | 1 | 0 | 0 ok | 1 FAIL | 0.600 (3/5) FAIL | 0 ok | FAIL |" in rendered
    assert "gemini_cli: bar FAIL: fit violations 0, lenient met 1, row agreement 0.6 (3/5, absent clear 0), max over-asks per job 0\n" in capsys.readouterr().out
    assert "| 01 | claude_cli | 100 | rejected | 5 | 4 | 0 | 1 | yes | 0 |" in rendered and "Other disagreements (the result not met on a clear row, or no row for a buried or missing one):\n- 01 gemini_cli: L5 Terraform (clear) key met, got unclear\n" in rendered


def test_a_holdout_key_with_outcome_unknown_loads_and_groups_and_a_misspelt_tier_or_outcome_is_refused(tmp_path: Path, lines: list[dict[str, Any]]) -> None:
    data = tmp_path / "set"
    (data / "postings").mkdir(parents=True)
    (data / "gold").mkdir()
    (data / "postings" / "01-example-works-staff-engineer.md").write_text(POSTING, encoding="utf-8")
    key_path = data / "gold" / "key.json"
    key_path.write_text(json.dumps({"schema": gold.KEY_SCHEMA, "set": "synthetic", "postings": {"01": strict_key(lines, outcome="unknown", k8s="buried")}}), encoding="utf-8")
    folder = data / "results" / "arm" / "01-example-works-staff-engineer" / "claude_cli"
    folder.mkdir(parents=True)
    result = met_result()  # the clear cloud row is dropped (absent clear, out of the agreement) and so is the buried Kubernetes row (a hidden gap: a disagreement that is not lenient)
    result["result"]["matrix"] = [row for row in result["result"]["matrix"] if row["id"] not in ("req-cloud", "req-k8s")]
    (folder / "assessment.json").write_text(json.dumps(result), encoding="utf-8")
    assert gold.load_key(key_path)["postings"]["01"]["outcome"] == "unknown"
    assert gold.main(["--key", str(key_path), "--results", str(data / "results" / "arm")]) == 0
    report = json.loads((data / "gold" / "scores" / "arm" / "score.json").read_text(encoding="utf-8"))
    assert sorted(report["bar"]["claude_cli"]) == ["all", "unknown"] and report["results"][0]["outcome"] == "unknown"
    assert report["results"][0]["row_agreement"]["absent_clear"] == 1 and report["bar"]["claude_cli"]["unknown"]["absent_clear"] == 1 and report["by_cli"]["claude_cli"]["absent_clear"] == 1
    rendered = (data / "gold" / "scores" / "arm" / "score.md").read_text(encoding="utf-8")
    assert "| claude_cli | unknown | 1 | 0 | 1 FAIL | 0 ok | 0.750 (3/4) FAIL |" in rendered and "| 01 | claude_cli | 100 | unknown | 4 | 3 | 1 | 0 | yes | 0 |" in rendered
    assert "\n- 01 claude_cli: L5 Kubernetes (buried) key unclear, got absent\n" in rendered and "L6 (clear)" not in rendered
    for field, value in (("outcome", "ghosted"), ("evidence_tier", "burried")):
        key = {"schema": gold.KEY_SCHEMA, "set": "synthetic", "postings": {"01": strict_key(lines)}}
        (key["postings"]["01"] if field == "outcome" else key["postings"]["01"]["musts"][0])[field] = value
        key_path.write_text(json.dumps(key), encoding="utf-8")
        with pytest.raises(ValueError, match=value):
            gold.load_key(key_path)


def test_the_fit_is_the_products_own_number_so_a_changed_fit_rule_changes_the_violation(lines: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch) -> None:
    key = strict_key(lines, years="buried")
    seen: list[list[str]] = []

    def capped(matrix: Any) -> int:
        rows = list(matrix)
        seen.append([f"{row.requirement_class.value}:{row.status.value}" for row in rows])
        return 49

    monkeypatch.setattr(gold, "fit_percent", capped)
    out = gold.score_result(key, met_result(), lines)
    assert (out["fit"], out["fit_violation"]) == (49, False) and out["key_below_met_rows"] == [{"line": 4, "item": None, "tier": "buried"}]
    assert seen == [["hard:met", "askable:met", "askable:met", "askable:met", "askable:met", "nice_to_have:met"]]  # the product's typed rows, from the stored matrix
