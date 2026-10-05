"""0.1.10.9 master P4: the master tailor eval's runner (``run_master_tailor_eval.py``), offline.

The fixture model stands in for the live one (``--fake-model``): these tests prove the runner plans, caps,
measures and reports, never that a tailoring is good.  The live numbers are in the worker's report.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.evals import run_master_tailor_eval as runner


def test_the_fixtures_are_the_spikes_six_cases_and_the_labels_are_ids_of_the_master() -> None:
    master = runner.load_master()
    profiles, postings = runner.load_profiles(), runner.load_postings()
    labels = runner.load_labels()
    assert [profile["profile_id"] for profile in profiles] == ["profile-ai", "profile-swe"] and len(postings) == 3
    assert set(labels) == {(profile["profile_id"], posting["id"]) for profile in profiles for posting in postings}
    assert all(ids and ids <= set(master.items) for ids in labels.values())
    payload = json.loads(runner.LABELS_PATH.read_text(encoding="utf-8"))
    assert "SYNTHETIC" in payload["note"] and "not the operator's" in payload["note"]
    # Each profile's own resume gives the prior a migrated profile has: its lines and its entries, by master id.
    for profile in profiles:
        prior = runner.base_ids(master, runner.legacy_resume(profile["profile_id"]))
        assert len(prior) > 25 and set(prior) <= set(master.items) | set(master.entries)
        assert any(item in master.entries for item in prior) and any(item.startswith("b-") for item in prior)


def test_the_plan_runs_every_case_of_one_variant_before_the_next() -> None:
    profiles, postings = runner.load_profiles(), runner.load_postings()
    cases = runner.plan(["today", "shortlist"], profiles, postings)
    assert [variant for variant, _profile, _posting in cases] == ["today"] * 6 + ["shortlist"] * 6
    assert len(runner.plan(["view"], profiles, postings, 2)) == 2
    with pytest.raises(ValueError, match="unknown variant whole"):
        runner.plan(["whole"], profiles, postings)


def test_a_fake_run_measures_today_and_the_master_path_and_the_comparison_prints_both(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    today, master = tmp_path / "today.json", tmp_path / "master.json"
    assert runner.main(["--fake-model", "--no-judge", "--variant", "today", "--limit", "2", "--report", str(today), "--max-calls", "5", "--quiet"]) == 0
    assert runner.main([
        "--fake-model", "--no-judge", "--variant", "shortlist", "--limit", "2", "--report", str(master), "--max-calls", "5", "--quiet",
        "--out-dir", str(tmp_path / "out"), "--dump-dir", str(tmp_path / "dump"),
    ]) == 0
    before, after = (json.loads(path.read_text(encoding="utf-8")) for path in (today, master))
    assert before["schema"] == after["schema"] == runner.REPORT_SCHEMA and before["run"]["fake_model"] is True
    assert (before["run"]["calls_made"], before["run"]["skipped"], after["run"]["calls_made"]) == (2, [], 2)
    assert before["run"]["instructions_digest"] == after["run"]["instructions_digest"]
    row = after["rows"][0]
    assert (row["variant"], row["ok"], row["picked_by"], row["calls"]) == ("shortlist", True, "model", 1)
    assert 40 <= row["candidates"]["bullets"] <= 60 and row["resume_lines_in_prompt"] == row["resume_lines"] and not row["prompt_truncated"]
    # The fixture model answers with a summary and nothing else of the set: the code's Skills line is shown all the same.
    assert row["key_skills"].split("/")[0] == row["key_skills"].split("/")[1] and row["item_id_mismatches"] == 0
    assert row["invented"] is None, "a result that shows a rewrite and was not judged has no invented count"
    assert "candidates" not in before["rows"][0] and "picked_by" not in before["rows"][0]
    assert len(list((tmp_path / "out").glob("shortlist-*.md"))) == 2 and len(list((tmp_path / "dump").glob("*.prompt.txt"))) == 2

    assert runner.main(["--compare", str(today), str(master)]) == 0
    printed = capsys.readouterr().out
    assert "today " in printed and "shortlist " in printed and "key skills" in printed


def test_the_call_cap_skips_a_case_and_the_live_run_is_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "report.json"
    assert runner.main(["--fake-model", "--no-judge", "--variant", "today", "--report", str(report), "--max-calls", "3", "--quiet"]) == 0
    capped = json.loads(report.read_text(encoding="utf-8"))
    assert capped["run"]["calls_made"] == 2 and len(capped["rows"]) == 2 and len(capped["run"]["skipped"]) == 4

    monkeypatch.delenv("GIGAI_ASSESS_EVAL_LIVE", raising=False)
    assert runner.main(["--report", str(report)]) == 2
    assert "GIGAI_ASSESS_EVAL_LIVE=1" in capsys.readouterr().err
    assert runner.main(["--fake-model", "--report", str(report), "--concurrency", "2"]) == 2
    assert runner.main(["--fake-model"]) == 2  # no --report
    assert runner.main(["--dry-run", "--variant", "view"]) == 0
    assert "6 planned cases" in capsys.readouterr().out
