"""0.1.11 N3P (orchestrator #29): the real-data assess eval harness, self-tested on SYNTHETIC data.

CI cannot hold real data: this builds a data directory out of the synthetic
fixtures (the pick eval's invented master and postings) and runs the harness
over it with the offline model.  It proves the harness runs and writes what it
says; it is not evidence of quality and no report may cite it as such.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from tests.evals import run_assess_pick_eval as pe
from tests.evals import run_assess_real_eval as real


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("synthetic-data")
    (root / "postings").mkdir()
    (root / "master.md").write_text(pe.master_of("medium").markdown, encoding="utf-8")
    spec = pe.load_spec()
    for index, name in enumerate(("agentic", "backend"), 1):
        posting = pe.load_posting(spec, name)
        (root / "postings" / f"{index:02d}-{name}.md").write_text(
            f"title: {posting['title']}\ncompany: {posting['company']}\nlocation: {posting['location']}\nurl: {posting['url']}\n\n{posting['text']}", encoding="utf-8")
    (root / "answers.json").write_text(json.dumps({"answers": [{"question_id": "database:cassandra", "question": "Cassandra?", "answer": "No Cassandra; MongoDB at two employers."}]}), encoding="utf-8")
    (root / "setup.json").write_text(json.dumps({"prefs": {"countries": ["US"], "visa_sponsorship_required": True, "city": None, "work_mode": "remote"}}), encoding="utf-8")
    (root / "profiles.json").write_text(json.dumps({"profiles": [
        {"profile_id": "p1", "label": "Staff Software Engineer", "titles": ["Staff Software Engineer"], "is_default": True},
        {"profile_id": "p2", "label": "Staff AI Engineer", "titles": ["Staff AI Engineer"], "is_default": False}]}), encoding="utf-8")
    (root / "ten.json").write_text(json.dumps([{"slug": "02-backend", "current_verdict": "matched_above_threshold", "profile_id": "p2"}]), encoding="utf-8")
    return root


def test_the_data_directory_is_read_and_each_posting_goes_to_its_profile_on_each_cli(data_dir: Path) -> None:
    data = real.load_data(data_dir)
    assert [p["slug"] for p in data["postings"]] == ["01-agentic", "02-backend"] and data["postings"][0]["url"].startswith("https://")
    planned = real.plan(data, list(real.MODEL_TARGETS))
    assert [(i["slug"], i["model_target"], i["profile"]) for i in planned] == [
        ("01-agentic", "claude_cli", 0), ("02-backend", "claude_cli", 1), ("01-agentic", "codex_cli", 0), ("02-backend", "codex_cli", 1)]
    assert [i["slug"] for i in real.plan(data, ["codex_cli"], ["02"])] == ["02-backend"]
    with pytest.raises(ValueError, match="unknown posting"):
        real.plan(data, ["codex_cli"], ["nope"])


def test_a_directory_without_its_files_is_refused_by_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="missing master.md"):
        real.load_data(tmp_path)


def test_a_run_writes_every_file_under_out_and_never_under_the_repository(data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "results"
    assert real.main(["--data-dir", str(data_dir), "--fake-model", "--label", "selftest", "--out", str(out), "--quiet", "--max-calls", "6"]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["schema"] == real.REPORT_SCHEMA and report["run"]["calls_made"] == 4 and report["run"]["skipped"] == []
    for slug in ("01-agentic", "02-backend"):
        for cli in real.MODEL_TARGETS:
            folder = out / slug / cli
            assert {p.name for p in folder.iterdir()} >= {"attempt1.prompt.txt", "attempt1.output.txt", "assessment.json", "case.json", "code-selection.json"}
    row = next(r for r in report["rows"] if r["slug"] == "02-backend" and r["model_target"] == "codex_cli")
    assert row["ok"] and row["stored_verdict_before"] == "matched_above_threshold" and row["verdict"] == "pending_user_answers"
    assert row["facts"]["questions_on_must_have"] == 1 and row["model_selection"] is None and row["code_selection"]["page_fit"]
    assert row["extras"] is not None and row["calls"] == 1 and row["prompt_chars"][0] > 10000
    # The prompt of the second profile is its own, and the stored answer reached it.
    assert "No Cassandra; MongoDB at two employers." in (out / "02-backend" / "codex_cli" / "attempt1.prompt.txt").read_text(encoding="utf-8")
    text = (out / "report.md").read_text(encoding="utf-8")
    assert "| 02-backend | codex_cli |" in text and "Requirement lists of one posting on the two CLIs" in text
    assert len(report["agreement"]) == 2 and report["agreement"][0]["rate"] == 0.0


def test_the_cap_the_switch_the_scratch_home_and_the_repository_are_refused(data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    base = ["--data-dir", str(data_dir)]
    monkeypatch.delenv(real.LIVE_ENV, raising=False)
    assert real.main(base) == 2 and f"set {real.LIVE_ENV}=1" in capsys.readouterr().err
    monkeypatch.setenv(real.LIVE_ENV, "1")
    assert real.main(base) == 2 and "--home is required" in capsys.readouterr().err
    assert real.main([*base, "--home", str(tmp_path / "none")]) == 2 and "holds no config.toml" in capsys.readouterr().err
    assert real.main([*base, "--fake-model", "--out", str(real.harness.REPO_ROOT / "results")]) == 2 and "inside the repository" in capsys.readouterr().err
    out = tmp_path / "capped"
    assert real.main([*base, "--fake-model", "--out", str(out), "--quiet", "--max-calls", "3"]) == 0
    skipped = json.loads((out / "report.json").read_text(encoding="utf-8"))["run"]["skipped"]
    # After two calls a third and its retry no longer fit under 3: the rest is skipped, not half run.
    assert skipped == ["01-agentic/codex_cli", "02-backend/codex_cli"]


def test_coverage_reads_met_must_have_rows_off_the_answers_own_sources() -> None:
    rows = [
        {"requirement": "Kubernetes", "class": "askable", "status": "met", "sources": ["a-1", "a-2"], "traced": []},
        {"requirement": "Go", "class": "askable", "status": "met", "sources": ["b-1", "A tooling:go"], "traced": []},
        {"requirement": "v8 row", "class": "hard", "status": "met", "traced": ["c-1"]},
        {"requirement": "optional", "class": "list_item", "status": "met", "sources": ["d-1"], "traced": []},
    ]
    found = real.coverage(rows, frozenset({"a-2", "c-1"}))
    assert found["cited_rows"] == 3 and found["lost"] == ["Go"] and found["strongest_shown"] == 0


def test_the_replay_re_classes_an_only_preferred_matrix_as_a_first_assessment_does(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 0.1.11.12 F2c: the replay reads the posting the run read and applies the first assessment's class guard, before the strict check.
    from gigai.scout import requirements_list as rl
    from gigai.scout.assessment_core import row_question_id

    monkeypatch.setattr("gigai.scout.assess_strict.strict_rows", lambda *_args, **_kwargs: [])  # the class guard alone
    items = ("5+ years building production backend systems", "Experience with Python and Go", "Kubernetes in production",
             "Distributed data processing (Spark or Ray)", "Prior work at an ML infrastructure company")
    text = "Senior Software Engineer\n\nPreferred Qualifications\n" + "".join(f"- {item}\n" for item in items) + "\nCompensation\nThe base salary range is $180,000 - $250,000.\n"
    data = tmp_path / "data"
    (data / "postings").mkdir(parents=True)
    (data / "master.md").write_text(pe.master_of("medium").markdown, encoding="utf-8")
    (data / "postings" / "01-onlypref.md").write_text(f"title: Senior Software Engineer\ncompany: Example\nlocation: Remote\nurl: https://example.com/1\n\n{text}", encoding="utf-8")
    ids = rl.assign_ids("sha256:" + "4" * 64, items)
    rows = [{"id": ids[place], "requirement": item, "class": "nice_to_have", "class_basis": f"Preferred Qualifications: {item}",
             "status": "unclear" if place == 2 else "met", "resume_evidence": [], "sources": []} for place, item in enumerate(items)]
    stored = {"result": {"verdict": "matched_above_threshold", "matrix": rows, "questions": [], "structured_questions": []}, "resume_gate": {"decision": "suggest", "reasons": []}}
    source = data / "results" / "before"
    for slug in ("01-onlypref", "02-unposted"):  # the second has no posting file: nothing is re-classed, and the summary says why
        (source / slug / "claude_cli").mkdir(parents=True)
        (source / slug / "claude_cli" / "assessment.json").write_text(json.dumps(stored), encoding="utf-8")
    summary = real.replay_postcheck(data, source, tmp_path / "after", today=date(2026, 10, 28))
    first, second = summary["cases"]
    assert [(item["step"], item["row"], item["before"], item["after"]) for item in first["changes"]] == [("class_guard", ids[place], "nice_to_have", "askable") for place in range(5)]
    assert first["verdict"] == {"before": "matched_above_threshold", "after": "pending_user_answers"} and first["gate"] == {"before": "suggest", "after": "hold_question"}
    replayed = json.loads((tmp_path / "after" / "01-onlypref" / "claude_cli" / "assessment.json").read_text(encoding="utf-8"))
    assert [(row["class"], row["class_basis"]) for row in replayed["result"]["matrix"]] == [("askable", f"only list: Preferred Qualifications: {item}") for item in items]
    assert [item["question_id"] for item in replayed["result"]["structured_questions"]] == [row_question_id(ids[2])]
    assert first["posting"] == "01-onlypref.md" and replayed["postcheck"]["posting"] == "01-onlypref.md"
    assert second["posting"] is None and second["changes"] == [] and (second["verdict"]["after"], second["gate"]["after"]) == ("matched_above_threshold", "suggest")
