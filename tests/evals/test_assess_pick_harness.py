"""0.1.11 N3 (SPEC 8.2): the assess-and-pick eval harness, offline.

No model is called: every answer is scripted from the labels
(``--fake-model``).  What is checked is the harness, not a prompt:

* the fixtures: 20 cases (16 on the medium master, 4 of them again on the
  large one; the 16 of SPEC 8.2 and one added on review), every labelled line an id of the synthetic master,
  every case with its accepted verdicts and gate decisions, no contact data;
* the pure parts: rows matched by their words (a split row, a merged row, a
  row nobody labelled), the disagreement of two requirement lists, the gate
  of SPEC 1.5 row by row, the selection checks, one answer against its labels
  (a question the master already answers, a class a reviewer does not accept,
  a source the labels do not list, an unknown pick id, what the injection
  posting asked for), two reports side by side;
* the END outcome: all 20 cases through ``run_quick_assessment`` on a temp
  home, the report written, the answers of the target with stored answers
  in its prompts and in no other target's, the call cap kept, the code
  selector's selection measured;
* the refusals: a live run without the switch, the operator's own home.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest

from tests.evals import run_assess_pick_eval as runner

SPEC = runner.load_spec()
LABELS = runner.load_labels()
SIZES = {name: target["master"] for name, target in SPEC["targets"].items()}


# --- the fixtures ---------------------------------------------------------------------------------------


def test_the_set_is_20_cases_16_on_the_medium_master_and_4_of_them_again_on_the_large_one() -> None:
    cases = SPEC["cases"]
    assert len(cases) == 20 and len({case["id"] for case in cases}) == 20
    medium = [case for case in cases if SIZES[case["target"]] == "medium"]
    large = [case for case in cases if SIZES[case["target"]] == "large"]
    assert len(medium) == 16 and len({case["posting"] for case in medium}) == 16
    assert len(large) == 4 and {case["posting"] for case in large} <= {case["posting"] for case in medium}
    assert sorted(case["model_target"] for case in cases).count("claude_cli") == 10
    assert {case["model_target"] for case in cases} == set(runner.MODEL_TARGETS)
    assert {case["profile"] for case in cases} == set(SPEC["profiles"])


def test_every_pair_is_one_posting_assessed_twice_and_names_what_differs() -> None:
    by_id = {case["id"]: case for case in SPEC["cases"]}
    assert len(SPEC["pairs"]) == 4
    for pair in SPEC["pairs"]:
        left, right = (by_id[name] for name in pair["cases"])
        assert left["posting"] == right["posting"] == pair["posting"]
        assert ("profile" in pair["differs"]) == (left["profile"] != right["profile"])
        assert ("model target" in pair["differs"]) == (left["model_target"] != right["model_target"])
        assert ("master size" in pair["differs"]) == (SIZES[left["target"]] != SIZES[right["target"]])
    # Two pairs hold the CLI still and change the profile; two hold the profile still and change the CLI.
    assert sorted("profile" in pair["differs"] for pair in SPEC["pairs"]) == [False, False, True, True]


def test_every_posting_reads_and_every_case_has_its_labels() -> None:
    for case in SPEC["cases"]:
        posting = runner.load_posting(SPEC, case["posting"])
        assert posting["title"] and posting["company"] and posting["url"].startswith("https://jobs.") and ".invalid/" in posting["url"]
        accepted = LABELS[case["posting"]]
        size = SIZES[case["target"]]
        assert set(accepted["verdicts"][size]) <= set(runner.RANK), case["id"]
        assert set(accepted["gate"][size]) <= {runner.GATE_SUGGEST, runner.GATE_HOLD_QUESTION, runner.GATE_HOLD_UNMET, runner.GATE_NOT_A_MATCH}
        assert runner.label_rows(LABELS, case["posting"], size)
    assert set(LABELS) == set(SPEC["postings"])


def test_every_labelled_line_is_a_line_of_the_synthetic_master_and_every_row_says_why_it_has_its_class() -> None:
    large = runner.master_of("large").ids
    for name, accepted in LABELS.items():
        ids = [row["id"] for row in accepted["rows"]]
        assert len(ids) == len(set(ids)), name
        for row in accepted["rows"]:
            assert set(row["class"]) <= {*runner.MANDATORY, *runner.OPTIONAL} and row["class"], (name, row["id"])
            assert 0 < len(row["class_basis"]) <= 200, (name, row["id"])
            for key in ("strong", "support", "skills"):
                assert set(row.get(key, ())) <= large, (name, row["id"], key)
        spec = accepted["pick"]
        for duty in spec.get("duties", ()):
            assert set(duty["strong"]) | set(duty["support"]) <= large, (name, duty["id"])
        for group in spec.get("must_keep", ()):
            assert set(group) <= large, (name, group)


def test_a_row_is_derived_from_the_lines_its_master_size_holds() -> None:
    # The injection suite is a large-only line: a question about it is the master's to answer only there.
    medium = {row.id: row for row in runner.label_rows(LABELS, "spike-agentic", "medium")}
    large = {row.id: row for row in runner.label_rows(LABELS, "spike-agentic", "large")}
    assert medium["R4"].statuses == ("unclear",) and not medium["R4"].master_answers
    assert large["R4"].statuses == ("met",) and large["R4"].master_answers
    # A stated status wins over the derived one; a years row stays out of the selection checks.
    answered = {row.id: row for row in runner.label_rows(LABELS, "answered-backend", "medium")}
    assert answered["R3"].statuses == ("unmet",) and answered["R2"].answers == ("database:cassandra",)
    assert not medium["R1"].in_pick
    requirements, groups = runner.pick_labels(LABELS, "spike-agentic", "medium")
    assert "R1" not in {req.id for req in requirements} and "D1" in {req.id for req in requirements}
    assert groups["loom-01/relay-01"] == ["loom-01"]  # relay-01 is a large-only line


def test_the_wording_of_a_list_decides_its_class_in_the_labels() -> None:
    """The orchestrator's review of the v9 list rule (2026-10-05): required wording, example wording, no wording."""

    def classes(posting: str, *ids: str) -> set[tuple[str, ...]]:
        return {tuple(row["class"]) for row in LABELS[posting]["rows"] if row["id"] in ids}

    # "You must have production experience with all of Go, Kubernetes, Terraform and Ansible": must-haves, and the open one holds.
    assert classes("required-list", "R2", "R3", "R4", "R5") == {("askable",)} and LABELS["required-list"]["verdicts"]["medium"] == [runner.PENDING]
    # "such as Kafka, Elasticsearch, ClickHouse and Cassandra": optional, two open, Matched.
    assert classes("examples-list", "R3", "R4", "R5", "R6") == {("list_item",)} and LABELS["examples-list"]["verdicts"]["medium"] == [runner.MATCHED]
    # "Kafka, Elasticsearch, ClickHouse, Snowflake and Airflow." and "Kubernetes, Helm and Terraform.": bare lists stay optional.
    assert classes("bare-list", "R3", "R4", "R5", "R6", "R7") == {("list_item",)} and LABELS["bare-list"]["verdicts"]["medium"] == [runner.MATCHED]
    assert classes("spike-backend", "R4", "R5", "R6", "R7", "R8", "R9") == {("list_item",)}
    assert all("listed with no requirement wording" in row["class_basis"] for row in LABELS["bare-list"]["rows"] if row["id"] in ("R3", "R4", "R5", "R6", "R7"))


def test_no_fixture_holds_contact_data() -> None:
    email = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
    phone = re.compile(r"\+?\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
    for path in sorted((runner.FIXTURES / "assess_pick").rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            assert not email.search(text) and not phone.search(text), path.name


# --- the pure parts -------------------------------------------------------------------------------------


def _label(row_id: str, text: str, classes=("askable",), strong=(), support=(), statuses=("met",), answers=()) -> runner.LabelRow:
    return runner.LabelRow(row_id, text, tuple(classes), "basis", (), tuple(strong), tuple(support), (), tuple(answers), tuple(statuses), True)


def _row(requirement: str, cls: str = "askable", status: str = "met", **more) -> dict:
    return {"requirement": requirement, "class": cls, "status": status, "evidence": [], "traced": [], **more}


def test_rows_are_matched_by_their_words_split_merged_and_unlabelled() -> None:
    labelled = [_label("R1", "Strong Python and TypeScript."), _label("R2", "Kubernetes"), _label("R3", "Terraform"), _label("R4", "Experience with Cassandra or another wide-column store.")]
    rows = [
        _row("Strong Python"), _row("Strong TypeScript"),  # one labelled row, split in two
        _row("Kubernetes and Terraform"),  # two labelled rows, merged into one
        _row("Excellent communication skills"),  # nobody labelled it
    ]
    found, merged, extra = runner.match_rows(labelled, rows)
    assert found["R1"] == [0, 1]
    assert found["R2"] == [2] and found["R3"] == [2] and merged == {"R3"}
    assert found["R4"] == [] and extra == [3]


def test_two_lists_of_one_posting_same_words_close_words_and_rows_without_a_counterpart() -> None:
    left = [_row("Strong Go"), _row("Kubernetes, Helm and Terraform", "list_item"), _row("Experience with Cassandra or another wide-column store"), _row("x", id="elig-location")]
    same = runner.list_disagreement(left, [dict(row) for row in left])
    assert same["rate"] == 0.0 and same["rate_same_words"] == 0.0 and same["same_words"] == 3  # the row about the candidate is left out
    right = [_row("strong go"), _row("Kubernetes", "askable"), _row("Helm", "askable"), _row("Terraform", "askable"), _row("Experience with Cassandra or a wide-column store")]
    other = runner.list_disagreement(left, right)
    assert other["same_words"] == 1 and other["close_words"] == 1  # the Cassandra row, reworded
    assert other["only_left"] == ["Kubernetes, Helm and Terraform"] and other["only_right"] == ["Kubernetes", "Helm", "Terraform"]
    assert other["rate"] == round(4 / 6, 3) and other["rate_same_words"] == round(5 / 6, 3)
    assert runner.list_disagreement(left, [_row("Strong Go", "hard")])["class_differs"] == [{"requirement": "Strong Go", "left": "askable", "right": "hard"}]


@pytest.mark.parametrize(
    ("rows", "verdict", "decision"),
    [
        ([_row("a", "hard"), _row("b", "askable"), _row("c", "list_item", "unclear"), _row("d", "nice_to_have", "unmet")], runner.MATCHED, runner.GATE_SUGGEST),
        ([_row("a", "hard"), _row("b", "askable", "unclear")], runner.PENDING, runner.GATE_HOLD_QUESTION),
        ([_row("a", "hard", "unmet"), _row("b", "askable", "unclear")], runner.NOT_A_MATCH, runner.GATE_NOT_A_MATCH),
        ([_row("a", "hard"), _row("b", "askable", "unmet")], runner.MATCHED, runner.GATE_HOLD_UNMET),
        # Only must-haves hold (OD2): two open list items leave the gate at suggest, whatever the model's verdict.
        ([_row("a", "hard"), _row("c", "list_item", "unclear"), _row("d", "list_item", "unclear")], runner.PENDING, runner.GATE_SUGGEST),
    ],
)
def test_the_gate_of_spec_1_5_row_by_row(rows, verdict, decision) -> None:
    assert runner.gate_by_spec(rows, verdict) == decision


def test_the_selection_checks_are_separate_counts() -> None:
    requirements, groups = runner.pick_labels(LABELS, "examples-list", "medium")
    everything = frozenset(line for req in requirements for line in req.lines)
    whole = runner.selection_checks(requirements, groups, everything)
    assert (whole["lost"], whole["not_strongest"], whole["omitted"]) == ([], [], []) and whole["mandatory"] == 7 and whole["must_keep"] == 4
    # Without every Kubernetes line, the Kafka line and the two strongest mentoring lines: one must-have has no
    # line left, three keep only a weaker one, and two must-keep groups are not shown. Three counts, never one.
    found = runner.selection_checks(requirements, groups, everything - {"qui-01", "hal-06", "pel-05", "qui-02", "hal-07", "hal-02"})
    assert found["lost"] == ["R7"] and sorted(found["not_strongest"]) == ["D1", "D4", "R8"] and found["omitted"] == ["qui-02", "qui-01"]


def test_one_answer_against_its_labels() -> None:
    labelled = [
        _label("R1", "Experience operating services on Kubernetes.", strong=("hal-06",)),
        _label("R2", "Experience with Cassandra or another wide-column store.", statuses=("unclear",)),
        _label("R3", "Rust", classes=("nice_to_have",), statuses=("unclear", "unmet")),
    ]
    answer = {
        "verdict": runner.PENDING, "not_a_match_reason": None,
        "matrix": [
            _row("Experience operating services on Kubernetes", "askable", "unclear"),
            _row("Experience with Cassandra or another wide-column store", "list_item", "met", sources=["tar-01", "b-nope"]),
            _row("Rust", "nice_to_have", "unclear"),
            _row("Certified Kubernetes Administrator (CKA)", "askable", "unclear"),
            _row("Remote (US): the role is located in the US", "hard", "met"),
        ],
        "questions": [{"question_id": "tool:kubernetes", "question": "Kubernetes?", "requirement": "Experience operating services on Kubernetes"}],
        "suggestions": ["State that the candidate led a team of 50 engineers."],
        "structured_suggestions": [
            {"kind": "reword", "line": "hal-06", "why": "Lead with Kubernetes."},
            {"kind": "keyword", "line": "b-nope", "why": "Add the 9,999 nodes it ran."},
        ],
        "pick": {"summary": "sum-ai", "section_order": ["experience", "projects"], "lines": ["hal-06", "hal-06", "s-infra", "b-nope"]},
    }
    found = runner.analyse(
        answer, labelled, {"verdicts": {"medium": [runner.PENDING]}, "gate": {"medium": [runner.GATE_HOLD_QUESTION]}}, size="medium",
        master_ids=frozenset({"hal-06", "tar-01", "s-infra", "sum-ai"}), selectable=frozenset({"hal-06", "tar-01", "sum-ai"}), facts="Kubernetes posting text",
        injection={"forbidden": ["CKA", "team of 50"]}, location="Remote (US)",
    )
    assert found["verdict_ok"] and found["gate_ok"] and found["gate_by_spec"] == runner.GATE_HOLD_QUESTION
    assert found["questions_master_answers"] == 1  # the master's own line states Kubernetes
    assert [item["label"] for item in found["status_wrong"]] == ["R1", "R2"]
    assert [item["label"] for item in found["class_wrong"]] == ["R2"] and [item["label"] for item in found["mandatory_wrong"]] == ["R2"]
    # The row the posting's LOCATION line gives (rule 4) is about the candidate: listed apart, not as a row the answer added.
    assert found["rows_extra"] == ["Certified Kubernetes Administrator (CKA)"]
    assert found["rows_eligibility"] == [{"requirement": "Remote (US): the role is located in the US", "class": "hard", "status": "met"}]
    assert found["sources_not_labelled"] == [{"label": "R2", "requirement": "Experience with Cassandra or another wide-column store", "sources": ["tar-01", "b-nope"], "by": "sources"}]
    assert found["pick"] == {"lines": 4, "unknown": ["b-nope"], "not_selectable": ["s-infra"], "repeats": 1, "summary": "sum-ai", "section_order": ["experience", "projects"]}
    assert found["suggestions_labels_agree"] == 1 and found["suggestions_to_read"] == 1 and found["suggestions_new_numbers"] == 1
    assert not found["structured_suggestions"][1]["line_known"]
    assert sorted((hit["field"], hit["found"]) for hit in found["injection_hits"]) == [("requirement", "CKA"), ("suggestion", "team of 50")]


def test_a_met_must_have_row_without_a_source_is_listed_only_for_an_answer_that_carries_sources() -> None:
    labelled = [_label("R1", "Kubernetes", strong=("hal-06",)), _label("R2", "Terraform", strong=("qui-04",))]
    accepted = {"verdicts": {"medium": [runner.MATCHED]}, "gate": {"medium": [runner.GATE_SUGGEST]}}
    kwargs = {"size": "medium", "master_ids": frozenset({"hal-06", "qui-04"}), "selectable": frozenset({"hal-06", "qui-04"}), "facts": "", "injection": None}
    v8 = {"verdict": runner.MATCHED, "matrix": [_row("Kubernetes", traced=["hal-06"]), _row("Terraform", traced=["hal-06"])], "questions": [], "suggestions": []}
    found = runner.analyse(v8, labelled, accepted, **kwargs)
    assert not found["has_sources"] and found["met_without_source"] == [] and found["injection_hits"] is None
    assert found["sources_not_labelled"] == [{"label": "R2", "requirement": "Terraform", "sources": ["hal-06"], "by": "traced evidence"}]
    v9 = {**v8, "matrix": [_row("Kubernetes", sources=["hal-06"]), _row("Terraform")]}
    found = runner.analyse(v9, labelled, accepted, **kwargs)
    assert found["has_sources"] and found["met_without_source"] == [{"requirement": "Terraform", "label": "R2"}]


def _report_row(case: str, verdict: str, *, ok_by_labels: bool = True, seconds: float = 10.0, target: str = "codex_cli", questions: int = 0) -> dict:
    return {
        "case": case, "ok": True, "verdict": verdict, "model_target": target, "calls": 1, "call_seconds": [seconds], "input_tokens": [100], "output_tokens": [50],
        "answer": {"matrix": [_row("Strong Go")], "questions": [{}] * questions},
        "labels": {"verdict_ok": ok_by_labels, "verdict_accepted": [runner.MATCHED], "questions_master_answers": 0},
    }


def test_two_reports_side_by_side_name_the_verdicts_that_got_worse_and_the_time_per_call() -> None:
    before = {"rows": [_report_row("a", runner.MATCHED), _report_row("b", runner.PENDING, questions=2), _report_row("c", runner.MATCHED, target="claude_cli")]}
    after = {"rows": [_report_row("a", runner.PENDING, ok_by_labels=False, seconds=18.0, questions=1), _report_row("b", runner.MATCHED, seconds=12.0), _report_row("c", runner.MATCHED, target="claude_cli")]}
    found = runner.compare(before, after)
    assert [(item["case"], item["wrong_by_labels"]) for item in found["worse"]] == [("a", True)]
    assert [item["case"] for item in found["better"]] == ["b"]
    assert found["seconds_per_call"]["codex_cli"] == {"before": 10.0, "after": 15.0, "ratio": 1.5}
    assert found["cases"][1]["questions"] == [2, 0] and found["cases"][0]["lists"]["rate"] == 0.0
    shared = runner.cross_disagreement([{"rows": [{**_report_row("a", runner.MATCHED), "posting": "p"}]}, {"rows": [{**_report_row("a", runner.MATCHED, target="claude_cli"), "posting": "p"}]}], ["one", "two"])
    assert [(item["left"], item["right"], item["differs"], item["rate"]) for item in shared] == [("one:a", "two:a", ["model_target"], 0.0)]


def test_what_code_dropped_at_the_boundary_is_read_off_the_models_own_answer() -> None:
    stored = {
        "matrix": [_row("Kubernetes", sources=["hal-06"]), _row("Terraform")], "questions": [{"requirement": "x"}], "pick": None,
        "structured_suggestions": [{"kind": "reword", "line": "hal-06", "why": "w"}],
    }
    said = json.dumps({
        "verdict": runner.MATCHED,
        "matrix": [{"requirement": "Kubernetes", "sources": ["hal-06", "b-made-up"]}, {"requirement": "Terraform", "sources": ["qui-99"]}],
        "questions": [{"requirement": "x"}, {"requirement": "y"}],
        "suggestions": [{"kind": "reword", "line": "hal-06", "why": "w"}, {"kind": "gap", "requirement": "Terraform", "why": "nothing states it"}],
        "pick": {"lines": ["hal-06", "qui-04"]},
    })
    found = runner.boundary_drops("Here is the answer:\n" + said, stored)
    assert found is not None and found["sources"] == 3
    assert [item["source"] for item in found["unknown_sources"]] == ["b-made-up", "qui-99"]
    assert found["pick_lines"] == 2 and found["pick_dropped"] and found["suggestions_dropped"] == 1 and found["suggestions_without_line"] == 1
    assert found["questions_dropped"] == 1
    assert runner.boundary_drops("I cannot answer.", stored) is None


def test_the_models_selection_is_set_beside_the_code_selectors_check_by_check() -> None:
    def row(case: str, model: tuple[int, int, int], code: tuple[int, int, int], picked_by: str = "model") -> dict:
        names = ("lost", "not_strongest", "omitted")
        return {
            "case": case, "model_selection": {"picked_by": picked_by, **{name: ["x"] * count for name, count in zip(names, model)}},
            "code_selection": {name: ["x"] * count for name, count in zip(names, code)},
        }

    found = runner.model_against_code([
        row("better", (0, 0, 0), (0, 1, 0)), row("worse", (1, 0, 0), (0, 2, 0)), row("same", (0, 1, 1), (0, 1, 1), "code"),
        {"case": "held", "model_selection": None, "code_selection": {"lost": [], "not_strongest": [], "omitted": []}},
    ])
    # A lower check never buys back a higher one: one more lost must-have is worse, whatever else improved.
    assert [item["case"] for item in found["worse"]] == ["worse"] and [item["case"] for item in found["better"]] == ["better"]
    assert found["same"] == ["same"] and found["cases"] == 3 and found["picked_by_model"] == 2


def test_the_plan_keeps_each_case_on_its_cli_swaps_it_or_puts_every_case_on_one() -> None:
    labelled = {case["id"]: case["model_target"] for case in runner.plan(SPEC)}
    swapped = {case["id"]: case["model_target"] for case in runner.plan(SPEC, mode="swapped")}
    assert all(swapped[name] != target and swapped[name] in runner.MODEL_TARGETS for name, target in labelled.items())
    assert {case["model_target"] for case in runner.plan(SPEC, mode="codex_cli")} == {"codex_cli"}
    assert [case["id"] for case in runner.plan(SPEC, ["l13-agentic"])] == ["l13-agentic"]
    with pytest.raises(ValueError, match="known cases: m01-spike-agentic"):
        runner.plan(SPEC, ["nope"])


# --- the refusals ---------------------------------------------------------------------------------------


def test_a_live_run_needs_the_switch_a_scratch_home_and_one_call_at_a_time(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "report.json"
    monkeypatch.delenv(runner.LIVE_ENV, raising=False)
    assert runner.main(["--report", str(report), "--home", str(tmp_path / "home")]) == 2
    assert f"set {runner.LIVE_ENV}=1" in capsys.readouterr().err
    monkeypatch.setenv(runner.LIVE_ENV, "1")
    assert runner.main(["--report", str(report)]) == 2
    assert "--home is required" in capsys.readouterr().err
    assert runner.main(["--report", str(report), "--home", str(tmp_path / "empty")]) == 2
    assert "holds no config.toml" in capsys.readouterr().err
    assert runner.main(["--fake-model", "--report", str(report), "--concurrency", "2"]) == 2
    assert runner.main(["--fake-model", "--case", "nope"]) == 2
    assert not report.exists()


def test_the_operators_own_home_is_refused_by_its_path_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # The path is compared, never opened: nothing under it is read.
    own = tmp_path / "people" / ".gigai"
    monkeypatch.setenv("HOME", str(tmp_path / "people"))
    monkeypatch.setenv("GIGAI_HOME", str(tmp_path / "elsewhere"))
    assert "operator's GigAI home" in (runner.refuse_home(own) or "")
    assert "operator's GigAI home" in (runner.refuse_home(tmp_path / "elsewhere" / "inner") or "")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "config.toml").write_text("", encoding="utf-8")
    assert runner.refuse_home(scratch) is None


def test_a_dry_run_lists_the_20_calls_and_makes_none(capsys: pytest.CaptureFixture[str]) -> None:
    assert runner.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 21 and "20 planned calls: --max-calls 21" in out


# --- the END outcome: every case through run_quick_assessment, offline ------------------------------------


@pytest.fixture(scope="module")
def fake_run(tmp_path_factory: pytest.TempPathFactory) -> tuple[dict, Path]:
    """All 20 cases with the scripted model: ``(the report, the folder of prompts and outputs)``."""

    root = tmp_path_factory.mktemp("assess-pick")
    assert runner.main(["--fake-model", "--label", "fake", "--report", str(root / "report.json"), "--dump-dir", str(root / "calls"), "--no-selector", "--quiet"]) == 0
    return json.loads((root / "report.json").read_text(encoding="utf-8")), root / "calls"


def test_all_20_cases_go_through_the_products_assessment_and_are_stored(fake_run) -> None:
    from gigai.scout.assessment_core import INSTRUCTIONS_DIGEST, assess_prompt_version

    report, _calls = fake_run
    run, rows = report["run"], report["rows"]
    assert report["schema"] == runner.REPORT_SCHEMA
    assert [row["case"] for row in rows] == [case["id"] for case in SPEC["cases"]]
    assert all(row["ok"] and row["calls"] == 1 for row in rows) and run["calls_made"] == 20 and run["skipped"] == []
    # One prompt for the whole run, the shipped one, and the product's own stamp of it on every stored answer.
    assert run["instructions_digest"] == run["instructions_digest_at_end"] == INSTRUCTIONS_DIGEST
    assert {row["instructions_digest"] for row in rows} == {INSTRUCTIONS_DIGEST}
    assert run["prompt_versions"] == [assess_prompt_version("")]
    # The assessment read the evidence view of the master, as the product does for a profile of a gig with a master.
    assert all(row["resume_input"] and row["resume_input"]["master_revision"] == 1 for row in rows)
    # Each case kept the CLI the cases file gives it (the report's own column; the fake answers for both).
    assert [row["model_target"] for row in rows] == [case["model_target"] for case in SPEC["cases"]]
    for row in rows:
        # The scripted answer is the labelled rows: every one found, none missing, each with a per-call time and tokens.
        assert row["labels"]["rows_missing"] == [] and row["labels"]["rows_extra"] == [], row["case"]
        assert len(row["call_seconds"]) == 1 and row["input_tokens"][0] > 1000 and row["output_tokens"][0] > 50
        # What the adapter reported beside the normalized usage is kept per call (the Claude CLI's cache tokens live there).
        assert row["raw_usage"] == [{"raw_usage": {}, "cost_usd": None, "resolved_model": "fixture-model"}] and row["prompt_chars"][0] > 20000
    assert report["summary"]["ok"] == 20 and report["summary"]["by_model_target"]["claude_cli"]["cases"] == 10


def test_the_gate_and_the_questions_follow_the_labels(fake_run) -> None:
    report, _calls = fake_run
    by_case = {row["case"]: row for row in report["rows"]}
    # A must-have of a list worded as required is a question that holds the resume.
    required = by_case["m10-required-list"]
    assert required["labels"]["gate_by_spec"] == runner.GATE_HOLD_QUESTION and [item["label"] for item in required["labels"]["questions"]] == ["R5"]
    # Two open items of a list worded as examples hold nothing: the gate suggests a resume, and both are still asked.
    examples = by_case["m11-examples-list"]
    assert examples["labels"]["gate_by_spec"] == runner.GATE_SUGGEST and examples["labels"]["questions_on_optional"] == 2
    # The level word: "familiar with Rust" leaves the must-have unmet, and the job is held with a gap, not asked again.
    answered = by_case["m12-answered-backend"]
    assert answered["labels"]["gate_by_spec"] == runner.GATE_HOLD_UNMET and answered["answer"]["questions"] == []
    assert report["summary"]["gate_not_accepted"] == [] and report["summary"]["questions_master_answers"] == 0
    # The injection posting was checked and nothing it asked for is in the scripted answer.
    assert report["summary"]["injection"] == {"cases": ["m04-spike-injection"], "hits": 0}


def test_the_stored_answers_reach_the_prompts_of_their_target_only(fake_run) -> None:
    report, calls = fake_run
    prompts = {path.name.split("-", 1)[1].removesuffix("-attempt1.prompt.txt"): path.read_text(encoding="utf-8") for path in sorted(calls.glob("*.prompt.txt"))}
    assert set(prompts) == {case["id"] for case in SPEC["cases"]}
    assert "MongoDB in production at two employers" in prompts["m12-answered-backend"] and "never shipped Rust in production" in prompts["m12-answered-backend"]
    # The answer's own words (the prompt's rules may name MongoDB as an example; the candidate's employers they do not).
    assert not any("at two employers, Quillon Health" in text or "side projects; I have never shipped" in text for name, text in prompts.items() if name != "m12-answered-backend")
    # The large master's lines reach the prompts of the large target only.
    assert "Built a multi-agent workflow engine in TypeScript" in prompts["l13-agentic"]
    assert "Built a multi-agent workflow engine in TypeScript" not in prompts["m05-agentic"]
    assert len(list(calls.glob("*.output.txt"))) == 20


def test_the_same_posting_twice_is_compared_row_by_row(fake_run) -> None:
    report, _calls = fake_run
    pairs = report["disagreement"]
    assert [pair["posting"] for pair in pairs] == ["agentic", "backend", "sre", "spike-agentic"] and all(pair["compared"] for pair in pairs)
    # The scripted answers hold the labelled rows for both resumes: no row is without its counterpart.
    assert all(pair["rate"] == 0.0 and pair["rows_left"] == pair["rows_right"] for pair in pairs)
    assert report["summary"]["disagreement"]["pairs"] == 4 and report["summary"]["disagreement"]["without_counterpart"] == 0


def test_the_report_prints_as_one_table_and_separate_counts(fake_run, capsys: pytest.CaptureFixture[str]) -> None:
    report, _calls = fake_run
    runner.print_report(report)
    out = capsys.readouterr().out
    assert out.count("\n| m") + out.count("\n| l") == 20 and "| case | CLI | model | verdict |" in out
    for words in ("questions ", "sources: ", "pick: ", "code selection: ", "model selection: ", "suggestions: ", "injection: ", "the same posting, another resume"):
        assert words in out


def test_a_stored_report_is_scored_again_with_the_labels_as_they_are_now_and_no_call(fake_run, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    report, calls = fake_run
    source = calls.parent / "report.json"
    # The labels after a review: a reviewer now accepts "needs your answers" for the examples-list posting.
    changed = json.loads(runner.LABELS_PATH.read_text(encoding="utf-8"))
    changed["postings"]["examples-list"]["verdicts"]["medium"] = [runner.MATCHED, runner.PENDING]
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps(changed), encoding="utf-8")
    monkeypatch.setattr(runner, "LABELS_PATH", labels)
    monkeypatch.setattr(runner, "load_labels", lambda path=labels: json.loads(path.read_text(encoding="utf-8"))["postings"])

    def no_model(*_args, **_kwargs):
        raise AssertionError("a rescore calls no model")

    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", no_model)
    out = tmp_path / "rescored.json"
    assert runner.main(["--rescore", str(source), "--report", str(out), "--quiet"]) == 0
    again = json.loads(out.read_text(encoding="utf-8"))
    assert [row["answer"] for row in again["rows"]] == [row["answer"] for row in report["rows"]]
    assert again["run"]["rescored_from"] == "report.json" and again["run"]["labels_digest"] != report["run"]["labels_digest"]
    assert again["run"]["calls_made"] == 20 and again["run"]["cases_digest"] == report["run"]["cases_digest"]
    by_case = {row["case"]: row for row in again["rows"]}
    assert by_case["m11-examples-list"]["labels"]["verdict_accepted"] == [runner.MATCHED, runner.PENDING] and by_case["m11-examples-list"]["labels"]["verdict_ok"]
    assert again["summary"]["verdict_not_accepted"] == [name for name in report["summary"]["verdict_not_accepted"] if name != "m11-examples-list"]
    assert runner.main(["--rescore", str(source)]) == 2


def test_the_call_cap_is_hard_and_the_code_selector_is_measured(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    assert runner.main(["--fake-model", "--report", str(report_path), "--case", "m06-backend", "--case", "l14-backend", "--max-calls", "2", "--quiet"]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    # After one call, the second case's call and its one retry no longer fit under the cap of 2: it is skipped, not half run.
    assert [row["case"] for row in report["rows"]] == ["m06-backend"] and report["run"]["skipped"] == ["l14-backend"] and report["run"]["calls_made"] == 1
    selection = report["rows"][0]["code_selection"]
    assert selection["page_fit"] and selection["pages"] <= 2 and selection["cited_rows"] >= 3
    # The search-engine must-have is evidenced only by the oldest relevant role: the selector keeps that line.
    assert selection["lost"] == [] and "pel-01" in selection["shown"]
    assert report["rows"][0]["model_selection"] is None or "lost" in report["rows"][0]["model_selection"]


def test_the_v9_shape_runs_through_a_tree_that_has_the_pick(tmp_path: Path) -> None:
    pytest.importorskip("gigai.scout.pick", reason="N3 steps 1 and 2 (scout/pick.py) are not in this tree")
    report_path = tmp_path / "report.json"
    assert runner.main(["--fake-model", "--fake-shape", "v9", "--report", str(report_path), "--case", "m05-agentic", "--case", "m12-answered-backend", "--no-selector", "--quiet"]) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert [row["ok"] for row in report["rows"]] == [True, True]
    assert all(item.get("class_basis") for row in report["rows"] for item in row["answer"]["matrix"])
    by_case = {row["case"]: row for row in report["rows"]}
    # The Matched case: its rows carry sources, the pick was settled and the stored selection is read back and checked.
    matched = by_case["m05-agentic"]
    assert matched["labels"]["has_sources"] and matched["labels"]["pick"]["unknown"] == [] and matched["resume_ids_in_prompt"]
    assert matched["model_selection"]["page_fit"] and matched["model_selection"]["picked_by"] in ("model", "code") and "lost" in matched["model_selection"]
    # The job with a confirmed gap: Matched by the model, held by the gate, and no pick is kept (OD1).
    held = by_case["m12-answered-backend"]
    assert held["answer"]["resume_gate"]["decision"] == runner.GATE_HOLD_UNMET and held["answer"]["pick"] is None and held["boundary"]["pick_dropped"]
    assert not held["labels"]["gate_stored_differs"]
