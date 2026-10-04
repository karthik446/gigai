"""0.1.10.9 master P7: the master assess eval's runner (``run_master_assess_eval.py``), offline.

The fixture model stands in for the live one (``--fake-model``): these tests prove the runner plans, caps,
measures and compares, never that an assessment is good.  The live numbers are in the worker's report.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import assess_master
from gigai.scout.master_selection import EVIDENCE_CAP
from tests.evals import run_master_assess_eval as runner


def test_the_cases_are_the_spikes_six_and_the_matrix_postings_for_the_infrastructure_profile() -> None:
    profiles, postings, matrix = runner.load_profiles(), runner.load_postings(), runner.load_matrix_postings()
    found = runner.cases(profiles, postings, matrix)
    assert len(found) == 11 and [posting["id"] for _profile, posting in found[6:]] == [
        "mq-list-helm-gap", "mq-list-three-gaps", "mq-stated-rows", "mq-must-have-tool-gap", "mq-control-all-met",
    ]
    assert {profile["profile_id"] for profile, _posting in found[6:]} == {runner.MATRIX_PROFILE}
    assert runner.load_candidate() == {"location": "Denver, CO", "countries": ["US"], "visa_sponsorship_required": False, "work_mode": ""}
    planned = runner.plan(["view", "evidence"], found, 2)
    assert [variant for variant, _rep, _profile, _posting in planned] == ["view"] * 22 + ["evidence"] * 22
    assert [rep for _variant, rep, _profile, _posting in planned[:22]] == [1] * 11 + [2] * 11
    with pytest.raises(ValueError, match="unknown variant whole"):
        runner.plan(["whole"], found, 1)


def test_the_view_is_the_profiles_two_pages_and_the_evidence_view_is_what_the_product_builds() -> None:
    master = runner.load_master()
    for profile in runner.load_profiles():
        view = runner.profile_view(master, profile)
        assert view.pages == 2 and view.fits
        for posting in runner.load_postings():
            text, holds = runner.resume_text(runner.VARIANT_VIEW, master, profile, posting, view)
            assert text == view.markdown and holds["pages"] == 2
            text, holds = runner.resume_text(runner.VARIANT_EVIDENCE, master, profile, posting, view)
            # The product's own builder, with the prior a profile on that view has.
            built = assess_master.evidence_text(
                master, assess_master.profile_prior(titles=tuple(profile["titles"]), item_ids=tuple(view.item_ids()), profile_id=profile["profile_id"], label=profile["label"]),
                title=posting["title"], posting_text=posting["text"], company=posting["company"], location=posting["location"], today=runner.TODAY,
            )
            assert text == built.markdown and len(text) <= EVIDENCE_CAP and holds["within_cap"]
            assert holds["bullets"] > 3 * view.markdown.count("\n- ") // 2


def test_evidence_support_tells_a_quote_a_paraphrase_and_a_string_to_read() -> None:
    facts = "- Architected the agent runtime that executes 2.1 million tool-calling LLM tasks per day across 140 internal teams.\nDenver, CO"
    assert runner.evidence_support("executes 2.1 million tool-calling LLM tasks per day", facts) == "verbatim"
    assert runner.evidence_support("Architected the agent runtime ... across 140 internal teams", facts) == "verbatim"
    assert runner.evidence_support("Agent runtime: 2.1 million LLM tasks per day for 140 teams", facts) == "paraphrase"
    # Another number, or mostly other words: a person reads it.
    assert runner.evidence_support("executes 3 million LLM tasks per day", facts) == "check"
    assert runner.evidence_support("Managed a Kubernetes fleet on AWS", facts) == "check"
    assert runner.evidence_support("Denver, CO", facts) == "verbatim"


def test_a_fake_run_of_both_variants_and_the_rule(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    view, evidence = tmp_path / "view.json", tmp_path / "evidence.json"
    assert runner.main(["--fake-model", "--variant", "view", "--repeat", "1", "--limit", "2", "--report", str(view), "--max-calls", "3", "--quiet"]) == 0
    assert runner.main([
        "--fake-model", "--variant", "evidence", "--repeat", "1", "--limit", "2", "--report", str(evidence), "--max-calls", "3", "--quiet",
        "--dump-dir", str(tmp_path / "dump"),
    ]) == 0
    before, after = (json.loads(path.read_text(encoding="utf-8")) for path in (view, evidence))
    assert before["schema"] == after["schema"] == runner.REPORT_SCHEMA and before["run"]["fake_model"] is True
    assert (before["run"]["calls_made"], before["run"]["skipped"], after["run"]["calls_made"]) == (2, [], 2)
    assert before["run"]["instructions_digest"] == after["run"]["instructions_digest"] == after["run"]["instructions_digest_at_import"]
    row = after["rows"][0]
    assert (row["variant"], row["ok"], row["calls"], row["rep"]) == ("evidence", True, 1, 1)
    # The whole evidence view reached the prompt, and it is about three times the 2-page view.
    assert row["resume_whole_in_prompt"] and before["rows"][0]["resume_whole_in_prompt"]
    assert row["resume"]["chars"] > 2 * before["rows"][0]["resume"]["chars"]
    assert row["verdict"] == "pending_user_answers" and [item["question_id"] for item in row["questions"]] == ["cloud:gcp"]
    assert len(list((tmp_path / "dump").glob("*.prompt.txt"))) == 2

    assert runner.main(["--compare", str(view), str(evidence)]) == 0
    printed = capsys.readouterr().out
    assert "the rule: not decided" in printed and "2. invalid answers: view 0, evidence 0: holds" in printed
    assert runner.main(["--compare", str(view), str(evidence), "--invented", "0"]) == 0
    assert "the rule: NO REGRESSION" in capsys.readouterr().out


def _report(variant: str, verdicts: dict[str, list[str | None]], questions: int = 0, check: int = 0) -> dict:
    rows = []
    for case, found in verdicts.items():
        for rep, verdict in enumerate(found, 1):
            row: dict = {"variant": variant, "case": case, "rep": rep, "ok": verdict is not None}
            if verdict is not None:
                row.update({
                    "verdict": verdict, "questions": [{"question_id": f"q:{n}", "question": ""} for n in range(questions)], "met": 1, "unclear": 0,
                    "evidence_check": [{"requirement": "r", "status": "met", "text": "t"}] * check,
                })
            rows.append(row)
    return {"rows": rows}


def test_the_rule_names_each_way_the_evidence_view_can_be_a_regression() -> None:
    matched, pending, no = "matched_above_threshold", "pending_user_answers", "not_a_match"
    view = _report("view", {"a": [pending, pending], "b": [matched, matched]}, questions=2)

    better = runner.compare(view, _report("evidence", {"a": [matched, pending], "b": [matched, matched]}, questions=1, check=1), invented=0)
    assert better["no_regression"] is True and better["rule"]["invented_evidence"]["to_check"] == 4
    assert better["cases"][0]["view"] == ["pending", "pending"] and better["cases"][0]["evidence"] == ["matched", "pending"]
    # Not decided until a person has read the strings to check.
    assert runner.compare(view, _report("evidence", {"a": [matched, pending], "b": [matched, matched]}, questions=1, check=1))["no_regression"] is None

    worse = runner.compare(view, _report("evidence", {"a": [matched, matched], "b": [matched, no]}, questions=1), invented=0)
    assert worse["no_regression"] is False and worse["rule"]["verdicts_not_worse"] == {"worse_cases": ["b"], "holds": False}
    invented = runner.compare(view, _report("evidence", {"a": [matched, matched], "b": [matched, matched]}), invented=1)
    assert invented["no_regression"] is False and invented["rule"]["invented_evidence"]["holds"] is False
    invalid = runner.compare(view, _report("evidence", {"a": [matched, None], "b": [matched, matched]}), invented=0)
    assert invalid["no_regression"] is False and invalid["rule"]["invalid_answers"] == {"view": 0, "evidence": 1, "holds": False}
    asks_more = runner.compare(view, _report("evidence", {"a": [pending, pending], "b": [matched, matched]}, questions=3), invented=0)
    assert asks_more["no_regression"] is False and asks_more["rule"]["open_questions"] == {"view": 8, "evidence": 12, "holds": False}


def test_the_call_cap_skips_a_case_and_the_live_run_is_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    report = tmp_path / "report.json"
    assert runner.main(["--fake-model", "--variant", "view", "--repeat", "1", "--report", str(report), "--max-calls", "3", "--quiet"]) == 0
    capped = json.loads(report.read_text(encoding="utf-8"))
    assert capped["run"]["calls_made"] == 2 and len(capped["rows"]) == 2 and len(capped["run"]["skipped"]) == 9

    monkeypatch.delenv("GIGAI_ASSESS_EVAL_LIVE", raising=False)
    assert runner.main(["--report", str(report)]) == 2
    assert "GIGAI_ASSESS_EVAL_LIVE=1" in capsys.readouterr().err
    assert runner.main(["--fake-model", "--report", str(report), "--concurrency", "2"]) == 2
    assert runner.main(["--fake-model"]) == 2  # no --report
    assert runner.main(["--dry-run", "--variant", "view", "--variant", "evidence"]) == 0
    assert "44 planned calls" in capsys.readouterr().out
