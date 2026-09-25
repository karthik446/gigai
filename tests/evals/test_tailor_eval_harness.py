"""Q3 (v0.1.9): the tailor eval harness runs end to end with the fake model, its
BATCHED judge is wired, parsed and strict, the call cap stops a run, an
explicit row list is honoured, and its detector catches PLANTED fabrications
-- all offline.

Integration lane (temp home, the ``bindings.py`` model seam): every tailor
and judge call goes through ``proposal_execution.resolve_model_adapter``
(patched onto ``bindings._test_model_handler``) and the shared
``invoke_json_once`` loop with the packaged ``tailor.md`` / the eval's
``fabrication_judge.md`` -- no live model, nothing written outside
``tmp_path``.  The batched-judge cases that need a scripted answer (a planted
unsupported verdict, a verdict-count mismatch) use an in-test stub binding
with the same ``request``/``port.invoke`` surface, still through
``invoke_json_once``.

The self-test: with ``TEST_MODEL_FABRICATE_MARKER`` in the posting text the
fixture answers with three planted fabrications (an unsupported number, a
posting-only skill, a resume ref past the end) on both attempts; the
product's validator must reject the whole answer, and each planted line,
checked on its own, must be rejected for ITS reason -- so the eval's
``fabricated_claims == 0`` can never be a vacuous pass.
"""

from __future__ import annotations

from collections.abc import Sequence
import json
import os
from pathlib import Path

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.scout import proposal_execution
from gigai.scout.find_jobs import bindings
from gigai.scout.find_jobs.contracts import NotAssessedReason
from gigai.scout.tailored_resume import (
    TAILOR_INSTRUCTIONS_DIGEST,
    TailorContext,
    TailorJob,
    TailorValidationError,
    posting_terms,
    resume_lines,
    tailor_once,
    validate_tailored_output,
)

from tests.evals import run_assess_eval as assess_harness
from tests.evals import run_tailor_eval as harness

_SEAM = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"

# The operator-approved live row list: the 6 clean fits whatever their excluded
# flag, plus 4 pending rows.
_LIVE_ROWS = (
    ("cf-analytics-engineer-pl", "gh:affirm:7764109003"),
    ("cf-senior-analytics-engineer-us", "gh:coinbase:8024880"),
    ("cf-senior-backend-auth-ca", "gh:affirm:7819891003"),
    ("cf-senior-ml-fraud-ca", "gh:affirm:7806920003"),
    ("cf-analytics-intern-sf", "gh:coinbase:8175471"),
    ("cf-principal-sre-us", "gh:gitlab:8623592002"),
    ("r5-platform-sre-senior", "gh:coinbase:8179114"),
    ("r2-fullstack-staff", "gh:affirm:7872398003"),
    ("r1-ml-staff", "gh:affirm:7822387003"),
    ("r5-platform-sre-senior", "gh:cloudflare:7629805"),
)


@pytest.fixture(autouse=True)
def _isolate_seams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(proposal_execution, "resolve_model_adapter", proposal_execution.resolve_model_adapter)
    monkeypatch.delenv(_SEAM, raising=False)


def _rows_file(tmp_path: Path, rows: Sequence[tuple[str, str]] = _LIVE_ROWS) -> Path:
    path = tmp_path / "rows.txt"
    path.write_text("# approved rows\n" + "".join(f"{resume} x {posting}\n" for resume, posting in rows), encoding="utf-8")
    return path


def _run(tmp_path: Path, *extra: str) -> dict:
    report = tmp_path / "report.json"
    assert harness.main(["--fake-model", "--report", str(report), "--quiet", *extra]) == 0
    return json.loads(report.read_text(encoding="utf-8"))


# --- end to end with the fake model -----------------------------------------------------------------


def test_fake_model_run_goes_end_to_end_through_the_shipped_path(tmp_path: Path) -> None:
    dump = tmp_path / "raw"
    report = _run(tmp_path, "--max-calls", "6", "--dump-dir", str(dump))
    assert report["schema"] == harness.REPORT_SCHEMA
    run = report["run"]
    assert run["fake_model"] is True and run["judge"] is True and run["model_target"] == "ollama_local"
    assert run["instructions_digest"] == TAILOR_INSTRUCTIONS_DIGEST
    assert run["row_selection"]["mode"] == "planned" and run["row_selection"]["excluded_rows_included"] == []
    # Rule E in the planned mode: excluded label rows are listed, never planned.
    excluded = [label for label in assess_harness.load_labels(include_excluded=True) if label.excluded]
    assert run["excluded_rows"] == [{"resume_id": label.resume_id, "posting_id": label.posting_id} for label in excluded]
    assert all(not row["excluded"] for row in report["rows"])
    # The cap counts every model call: 6 calls = 3 rows x (1 tailor + 1 judge); the 4th row's tailor call is refused.
    planned = assess_harness.plan_rows(assess_harness.load_labels())
    assert run["max_calls"] == 6 and run["calls_made"] == 6 and run["stopped_at_cap"] is True
    assert run["rows_not_done"][0] == {"resume_id": planned[3].resume_id, "posting_id": planned[3].posting_id, "stage": "tailor", "reason": "call cap 6 reached before a tailor call"}
    assert len(run["rows_not_done"]) == len(planned) - 3 and all(item["stage"] == "not started" for item in run["rows_not_done"][1:])

    rows = report["rows"]
    assert len(rows) == 3 and all(row["clean_fit"] for row in rows)
    for row in rows:
        assert row["ok"] is True and row["attempts"] == 1 and row["retried"] is False
        assert row["attempt_errors"] == [None] and row["attempt_guards"] == [None]
        # The fixture: header copy R1, summary = R1 rewritten, skills copy R1.
        assert row["sections"] == ["summary", "skills"]
        assert row["copy_lines"] == 2 and row["rewritten_lines"] == 1
        header, summary, skills = row["lines"]
        assert header["where"] == "header[1]" and header["kind"] == "copy" and header["verbatim"] is True and "judge" not in header
        assert summary["where"] == "summary line 1" and summary["kind"] == "rewritten" and summary["claim"] == 1
        assert summary["sources"] == [{"label": "R1", "text": header["text"]}]  # every accepted line lists its cited source text
        assert summary["guard_hit"] is False and summary["judge"] == {"supported": True, "unsupported_span": None}
        assert skills["kind"] == "copy" and skills["verbatim"] is True
        assert row["fabricated_lines"] == [] and row["judge_calls"] == 1 and row["judge_attempts"] == 1 and row["judge_ok"] is True
        assert row["judge_stopped_at_cap"] is False and row["unjudged_lines"] == 0
        assert row["markdown"].startswith("# ") and "<!-- R1 -->" in row["markdown"]
        assert row["usage"] == {"input_tokens": 10, "output_tokens": 18, "total_tokens": 28}
        assert row["judge_usage"] == {"input_tokens": 10, "output_tokens": 18, "total_tokens": 28}

    calls = report["calls"]
    assert [call["role"] for call in calls] == ["tailor", "judge"] * 3
    assert all(call["attempt"] == 1 and call["error"] is None for call in calls)
    assert calls[1]["row"] == [rows[0]["resume_id"], rows[0]["posting_id"]]

    metrics = report["metrics"]
    assert metrics["calls"] == {"max_calls": 6, "made": 6, "tailor_calls": 3, "judge_calls": 3, "stopped_at_cap": True, "rows_planned": len(planned), "rows_done": 3, "rows_not_done": run["rows_not_done"]}
    assert metrics["lines"] == {"total": 9, "copy": 6, "rewritten": 3, "answer_refs": 0, "expanded_refs": 0}
    fab = metrics["fabrication"]
    assert fab["fabricated_claims"] == 0 and fab["fabrication_rate"] == 0.0 and fab["lines"] == []
    assert fab["judge_calls"] == 3 and fab["judge_attempts"] == 3 and fab["judge_retries"] == 0
    assert fab["judge_unsupported"] == 0 and fab["judge_failures"] == 0 and fab["unjudged_rewritten_lines"] == 0
    assert metrics["guard_rejections"] == {guard: {"retried": 0, "invalid_after_retry": 0} for guard in harness.GUARDS} | {"total_retried": 0, "total_invalid_after_retry": 0}
    assert metrics["bars"] == {"fabricated_claims_bar": 0, "fabricated_claims_bar_met": True, "invalid_after_retry_bar_met": True}
    rel = metrics["reliability"]
    assert rel["invalid_after_retry"] == 0 and rel["valid_output_rate"] == 1.0
    assert rel["latency_seconds"]["per_call"]["all"]["count"] == 6 and rel["latency_seconds"]["per_call"]["judge"]["count"] == 3
    assert set(rel["latency_seconds"]["per_call"]["tailor"]) == {"count", "mean", "median", "p90", "p95", "max"}
    assert rel["tokens"] == {"all": {"input": 60, "output": 108, "total": 168}, "tailor": {"input": 30, "output": 54, "total": 84}, "judge": {"input": 30, "output": 54, "total": 84}}
    # Two samples with every line's sources: the planned rows are all clean fits here.
    sample = metrics["samples"]["clean_fit"]
    assert sample["resume_id"] == rows[0]["resume_id"] and metrics["samples"]["pending"] is None
    rendered = sample["markdown_with_sources"]
    assert rendered.count("> R1: ") == 3 and "> judge: supported" in rendered and "> check: copy line, verbatim=True" in rendered
    assert "WARNING" not in rendered
    # The raw dump: one prompt + one output per attempt, plus the index.
    names = sorted(path.name for path in dump.iterdir())
    assert len(names) == 13 and names[0].startswith("01-") and names[0].endswith("-tailor-attempt1-output.txt")
    assert any(name.endswith("-judge-attempt1-prompt.md") for name in names) and "calls.json" in names
    assert (dump / names[1]).read_text(encoding="utf-8").startswith(bindings.TEST_MODEL_TAILOR_MARKER)
    assert _SEAM not in os.environ


def test_no_judge_run_and_answers_reach_the_row(tmp_path: Path) -> None:
    # A planned (non-excluded) resume with fixed answers: the answers are rendered
    # into the prompt as A <question_id> sources and listed on the row. The fixture
    # model cites cloud:gcp only, which no eval resume has, so no answer ref is
    # expected here (the api-e2e journey proves the answer-ref path).
    answers = harness.load_answers()
    planned = assess_harness.plan_rows(assess_harness.load_labels())
    label = next(item for item in planned if answers.get(item.resume_id))
    report = _run(tmp_path, "--resume", label.resume_id, "--posting", label.posting_id, "--max-calls", "1", "--no-judge")
    assert report["run"]["judge"] is False and report["run"]["stopped_at_cap"] is False
    row = report["rows"][0]
    assert row["resume_id"] == label.resume_id
    from gigai.scout.question_ids import normalize_question_id

    assert row["answers"] == sorted({normalize_question_id(item.question_id) for item in answers[label.resume_id]})
    assert row["judge_calls"] == 0 and row["lines"][1]["judge"] is None and row["unjudged_lines"] == 1
    assert report["metrics"]["fabrication"]["judge_enabled"] is False
    assert report["metrics"]["calls"]["made"] == 1 and report["metrics"]["bars"]["fabricated_claims_bar_met"] is True


# --- row selection -----------------------------------------------------------------------------------


def test_explicit_row_list_is_run_in_order_whatever_the_excluded_flag(tmp_path: Path) -> None:
    rows_file = _rows_file(tmp_path, _LIVE_ROWS[:4])
    report = _run(tmp_path, "--rows-file", str(rows_file), "--row", "r1-ml-staff x gh:affirm:7822387003", "--max-calls", "10")
    selection = report["run"]["row_selection"]
    assert selection["mode"] == "explicit"
    assert selection["rows"] == [f"{resume} x {posting}" for resume, posting in _LIVE_ROWS[:4]] + ["r1-ml-staff x gh:affirm:7822387003"]
    # Three of the four clean fits carry the assess eval's excluded flag; they run and are scored here.
    labels = {label.key: label for label in assess_harness.load_labels(include_excluded=True)}
    expected_excluded = [f"{resume} x {posting}" for resume, posting in _LIVE_ROWS[:4] if labels[(resume, posting)].excluded]
    assert len(expected_excluded) == 3 and selection["excluded_rows_included"] == expected_excluded
    assert "does not depend on the posting's location" in selection["note"]
    assert [(row["resume_id"], row["posting_id"]) for row in report["rows"]] == [*_LIVE_ROWS[:4], ("r1-ml-staff", "gh:affirm:7822387003")]
    assert sum(1 for row in report["rows"] if row["excluded"]) == 3
    assert report["metrics"]["calls"]["rows_done"] == 5 and report["metrics"]["reliability"]["valid"] == 5, "an explicitly selected excluded row IS scored"
    assert report["metrics"]["samples"]["clean_fit"]["resume_id"] == "cf-analytics-engineer-pl"
    assert report["metrics"]["samples"]["pending"]["resume_id"] == "r1-ml-staff" and report["metrics"]["samples"]["pending"]["expected_verdict"] == "pending_user_answers"
    assert report["run"]["excluded_rows"], "the sheet's excluded rows are still listed for the record"


def test_the_approved_live_row_list_is_ten_labelled_pairs_and_dry_run_prints_them(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    labels = assess_harness.load_labels(include_excluded=True)
    selected = harness.select_rows(labels, _LIVE_ROWS)
    assert len(selected) == 10 and sum(1 for label in selected if label.clean_fit) == 6
    assert all(label.expected_verdict == "pending_user_answers" for label in selected if not label.clean_fit)
    assert harness.main(["--dry-run", "--rows-file", str(_rows_file(tmp_path)), "--max-calls", "25"]) == 0
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == 10 and lines[0].endswith("cf-analytics-engineer-pl x gh:affirm:7764109003 answers - excluded-flag")
    assert lines[6].split()[1] == "confident" and "r5-platform-sre-senior x gh:coinbase:8179114" in lines[6]
    assert "10 rows; at least 20 calls without retries; cap 25" in captured.err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--row", "nobody x gh:coinbase:8024880"], "nobody x gh:coinbase:8024880 is not a labelled pair"),
        (["--row", "r1-ml-staff x gh:affirm:7822387003", "--row", "r1-ml-staff x gh:affirm:7822387003"], "is listed twice"),
        (["--row", "r1-ml-staff"], "is not '<resume_id> x <posting_id>'"),
    ],
)
def test_a_bad_row_spec_is_refused_before_any_call(argv: list[str], message: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.main(["--fake-model", "--dry-run", *argv]) == 2
    assert message in capsys.readouterr().err


def test_row_spec_parsing_accepts_x_and_comma() -> None:
    assert harness.parse_row_spec("  r1-ml-staff x gh:affirm:7822387003 ") == ("r1-ml-staff", "gh:affirm:7822387003")
    assert harness.parse_row_spec("r1-ml-staff,gh:affirm:7822387003") == ("r1-ml-staff", "gh:affirm:7822387003")
    with pytest.raises(harness.RowSelectionError):
        harness.parse_row_spec(" x gh:affirm:7822387003")


def test_dry_run_plans_rows_without_any_call(capsys: pytest.CaptureFixture[str]) -> None:
    assert harness.main(["--dry-run", "--max-calls", "4"]) == 0
    captured = capsys.readouterr()
    lines = [line for line in captured.out.splitlines() if line.strip()]
    assert len(lines) == len(assess_harness.load_labels())
    assert lines[0].split()[1] == "clean"
    assert "rule E: not planned, not scored" in captured.err


def test_answers_fixture_covers_every_expected_question_id_in_the_labels() -> None:
    answers = harness.load_answers()
    for label in assess_harness.load_labels():
        have = {item.question_id for item in answers.get(label.resume_id, ())}
        missing = set(label.expected_question_ids) - have
        assert not missing, f"{label.resume_id} lacks a fixed answer for {sorted(missing)}"
    for items in answers.values():
        assert all(item.answer.strip() for item in items)


# --- the call cap -----------------------------------------------------------------------------------


def test_the_cap_stops_the_run_before_a_judge_call_and_lists_the_rows_not_done(tmp_path: Path) -> None:
    # 3 calls: row 1 tailor + judge, row 2 tailor; the judge for row 2 would be the 4th -> refused.
    report = _run(tmp_path, "--rows-file", str(_rows_file(tmp_path, _LIVE_ROWS[:4])), "--max-calls", "3")
    run = report["run"]
    assert run["calls_made"] == 3 and run["stopped_at_cap"] is True
    assert run["rows_not_done"] == [
        {"resume_id": "cf-senior-analytics-engineer-us", "posting_id": "gh:coinbase:8024880", "stage": "judge", "reason": "call cap 3 reached before the judge call"},
        {"resume_id": "cf-senior-backend-auth-ca", "posting_id": "gh:affirm:7819891003", "stage": "not started", "reason": "call cap 3 reached"},
        {"resume_id": "cf-senior-ml-fraud-ca", "posting_id": "gh:affirm:7806920003", "stage": "not started", "reason": "call cap 3 reached"},
    ]
    first, second = report["rows"]
    assert first["judge_ok"] is True and second["judge_ok"] is None
    assert second["ok"] is True and second["judge_stopped_at_cap"] is True and second["judge_calls"] == 0 and second["unjudged_lines"] == 1
    assert second["lines"][1]["judge"] is None and second["fabricated_lines"] == []
    metrics = report["metrics"]
    assert metrics["calls"]["made"] == 3 and metrics["calls"]["rows_done"] == 2 and metrics["calls"]["stopped_at_cap"] is True
    assert metrics["fabrication"]["judge_stopped_at_cap"] == 1 and metrics["fabrication"]["unjudged_rewritten_lines"] == 1
    assert metrics["bars"]["fabricated_claims_bar_met"] is False, "an unjudged rewritten line never passes the bar"
    assert len(report["calls"]) == 3


def test_capped_binding_refuses_the_call_that_would_exceed_the_cap_without_invoking() -> None:
    class Port:
        name = "stub"
        invoked = 0

        def invoke(self, request):
            self.invoked += 1
            return _result("{}")

    class Binding:
        port = Port()

        def request(self, *, role, prompt):
            return _Request(role, prompt)

    budget = harness.CallBudget(max_calls=1)
    capped = harness.CappedBinding(Binding(), budget)
    budget.begin(harness.ROLE_TAILOR, ("r", "p"))
    capped.port.invoke(capped.request(role="reviewer", prompt="one"))
    with pytest.raises(harness.CallCapReached) as info:
        capped.port.invoke(capped.request(role="reviewer", prompt="two"))
    assert str(info.value) == "call cap 1 reached before a tailor call" and not hasattr(info.value, "code")
    assert capped.port.name == "stub" and budget.made == 1 and Binding.port.invoked == 1
    assert budget.calls[0].prompt == "one" and budget.calls[0].role == "tailor" and budget.calls[0].row_key == ("r", "p")


# --- the batched judge --------------------------------------------------------------------------------


class _Request:
    def __init__(self, role: str, prompt: str) -> None:
        self.role = role
        self.prompt = prompt


def _result(text: str) -> InvocationResult:
    return InvocationResult(status="success", output_text=text, resolved_model="stub", raw_usage={}, normalized_usage=NormalizedUsage(5, 7, 12), cost_status="unavailable")


class _ScriptedBinding:
    """A binding with the product's surface whose port answers from a script, in order."""

    def __init__(self, replies: Sequence[object]) -> None:
        self.prompts: list[str] = []
        self._replies = list(replies)
        binding = self

        class Port:
            name = "scripted"

            def invoke(self, request):
                binding.prompts.append(request.prompt)
                reply = binding._replies.pop(0)
                return _result(reply if isinstance(reply, str) else json.dumps(reply))

        self.port = Port()

    def request(self, *, role: str, prompt: str) -> _Request:
        return _Request(role, prompt)

    def close(self) -> None:
        pass


_CLAIMS = (
    harness.JudgeClaim(1, "Built Python services.", (("R1", "Built Python services at Acme."),)),
    harness.JudgeClaim(2, "Used GCP daily.", (("R2", "Ran services."), ("A cloud:gcp", "cloud gcp Yes."))),
    harness.JudgeClaim(3, "Led a team of 8.", (("R3", "Worked on a team."),)),
)


def test_batched_judge_happy_path_is_one_call_with_one_verdict_per_numbered_claim() -> None:
    binding = _ScriptedBinding([{"verdicts": [{"line": 3, "supported": True, "unsupported_span": None}, {"line": 1, "supported": True, "unsupported_span": "ignored when supported"}, {"line": 2, "supported": True, "unsupported_span": None}]}])
    verdict = harness.judge_resume(binding, _CLAIMS)
    assert len(binding.prompts) == 1, "one judge call per tailored resume"
    prompt = binding.prompts[0]
    assert prompt.startswith(bindings.TEST_MODEL_JUDGE_MARKER + "\n")
    assert "exactly one verdict for each of the 3 claims" in prompt
    assert "CLAIM 1:\nBuilt Python services.\nSOURCES FOR CLAIM 1:\nR1: Built Python services at Acme.\n\nCLAIM 2:\nUsed GCP daily.\nSOURCES FOR CLAIM 2:\nR2: Ran services.\nA cloud:gcp: cloud gcp Yes.\n\nCLAIM 3:" in prompt
    assert "{{" not in prompt and "previous answer was rejected" not in prompt
    assert verdict["judge_ok"] is True and verdict["judge_attempts"] == 1 and verdict["judge_error"] is None
    assert verdict["verdicts"] == {1: {"supported": True, "unsupported_span": None}, 2: {"supported": True, "unsupported_span": None}, 3: {"supported": True, "unsupported_span": None}}
    assert verdict["usage"] == {"input_tokens": 5, "output_tokens": 7, "total_tokens": 12}


def test_the_fake_judge_answers_one_verdict_per_claim_block() -> None:
    prompt = harness.render_judge_prompt(_CLAIMS)
    assert bindings._test_model_judge_reply(prompt) == {"verdicts": [{"line": n, "supported": True, "unsupported_span": None} for n in (1, 2, 3)]}
    assert harness.parse_judge_answer(bindings._test_model_judge_reply(prompt), 3)[2] == {"supported": True, "unsupported_span": None}


def _tailor_reply(*lines: str) -> dict:
    return {"header": [{"copy": 1}], "sections": [{"heading": "summary", "lines": [{"text": text, "refs": [{"kind": "resume", "line": 1}]} for text in lines]}]}


_LABEL = assess_harness.Label("r", "p", "pending_user_answers", (), False, False, False, "test", "")
_POSTING = assess_harness.Posting("p", "Software Engineer", "Acme", "Denver, CO", "https://example.test/p", "Acme is hiring a Software Engineer to build services. Requirements: Python.")
_RESUME_FIXTURE = assess_harness.Resume("r", "Software engineer with Python service experience.\nRan a small team.\n", ("US",), (), False, "synthetic", None, "test")


def test_a_planted_unsupported_line_is_flagged_through_the_batch() -> None:
    binding = _ScriptedBinding(
        [
            _tailor_reply("Python service engineer.", "Ran a large platform team."),
            {"verdicts": [{"line": 1, "supported": True, "unsupported_span": None}, {"line": 2, "supported": False, "unsupported_span": "large platform team"}]},
        ]
    )
    budget = harness.CallBudget(max_calls=25)
    row = harness.tailor_row(harness.CappedBinding(binding, budget), _LABEL, _POSTING, _RESUME_FIXTURE, (), budget=budget)
    assert row["ok"] is True and row["judge_ok"] is True and row["judge_calls"] == 1 and budget.made == 2
    header, first, second = row["lines"]
    assert header["kind"] == "copy" and header["verbatim"] is True
    assert first["claim"] == 1 and first["judge"] == {"supported": True, "unsupported_span": None} and first["fabricated"] is False
    assert second["claim"] == 2 and second["judge"] == {"supported": False, "unsupported_span": "large platform team"} and second["fabricated"] is True
    assert second["guard_hit"] is False, "the deterministic guards pass this line; only the judge catches it"
    assert row["fabricated_lines"] == [second]
    metrics = harness.summarize([row], planned=1, max_calls=25, judge=True, calls=budget.calls)
    fab = metrics["fabrication"]
    assert fab["fabricated_claims"] == 1 and fab["judge_unsupported"] == 1 and fab["judge_failures"] == 0
    assert fab["lines"][0]["where"] == "summary line 2" and fab["lines"][0]["sources"] == [{"label": "R1", "text": "Software engineer with Python service experience."}]
    assert metrics["bars"]["fabricated_claims_bar_met"] is False
    assert "> judge: UNSUPPORTED span: 'large platform team'" in metrics["samples"]["pending"]["markdown_with_sources"]


def test_an_answer_ref_is_offered_and_accepted_under_its_canonical_id() -> None:
    # The live run of 2026-09-25 rejected every citation of ``language:java_cpp_go``
    # because the harness keyed the answers raw while the validator looks the id up
    # canonically (``language:cpp_go_java``); the prompt must show the canonical id
    # and a citation of it must be accepted, exactly as in the product.
    answers = (harness.FixedAnswer("language:java_cpp_go", "Go: yes, daily. Java and C++: no."),)
    binding = _ScriptedBinding(
        [
            {"header": [{"copy": 1}], "sections": [{"heading": "summary", "lines": [{"text": "Go daily; no Java or C++.", "refs": [{"kind": "answer", "question_id": "language:cpp_go_java"}]}]}]},
            {"verdicts": [{"line": 1, "supported": True, "unsupported_span": None}]},
        ]
    )
    budget = harness.CallBudget(max_calls=25)
    row = harness.tailor_row(harness.CappedBinding(binding, budget), _LABEL, _POSTING, _RESUME_FIXTURE, answers, budget=budget)
    assert "A language:cpp_go_java: Go: yes, daily. Java and C++: no." in binding.prompts[0]
    assert "java_cpp_go" not in binding.prompts[0]
    assert row["ok"] is True and row["attempts"] == 1 and row["answers"] == ["language:cpp_go_java"]
    assert row["lines"][1]["sources"] == [{"label": "A language:cpp_go_java", "text": "language cpp go java Go: yes, daily. Java and C++: no."}]
    assert "A language:cpp_go_java: language cpp go java Go: yes, daily. Java and C++: no." in binding.prompts[1]
    metrics = harness.summarize([row], planned=1, max_calls=25, judge=True, calls=budget.calls)
    assert metrics["lines"]["answer_refs"] == 1 and metrics["fabrication"]["fabricated_claims"] == 0


_WRAPPED_RESUME_FIXTURE = assess_harness.Resume(
    "r", "Software engineer with Python service experience and\nGo services in production for 12 years.\n", ("US",), (), False, "synthetic", None, "test"
)
_GO_POSTING = assess_harness.Posting(
    "p", "Software Engineer", "Acme", "Denver, CO", "https://example.test/p", "Acme is hiring a Software Engineer to build services. Requirements: Python; Go."
)


def test_a_cited_wrapped_resume_line_reaches_the_guards_the_row_and_the_judge_as_its_whole_span() -> None:
    # tailor-r2: R1 ends mid-sentence and R2 finishes it; the model cites R1 only
    # (the r5 x cloudflare miss of the r1 live run). The harness builds the
    # product's context, so the number and the term that live on R2 pass the
    # guards, the row lists the joined span with its continuation, and the
    # judge's SOURCES carry the joined span, not the cited line alone.
    binding = _ScriptedBinding(
        [
            _tailor_reply("Python and Go services for 12 years."),
            {"verdicts": [{"line": 1, "supported": True, "unsupported_span": None}]},
        ]
    )
    budget = harness.CallBudget(max_calls=25)
    row = harness.tailor_row(harness.CappedBinding(binding, budget), _LABEL, _GO_POSTING, _WRAPPED_RESUME_FIXTURE, (), budget=budget)
    joined = "Software engineer with Python service experience and Go services in production for 12 years."
    # The prompt's numbering is unchanged: two lines, R1 and R2.
    assert "R1: Software engineer with Python service experience and\nR2: Go services in production for 12 years." in binding.prompts[0]
    assert row["ok"] is True and row["attempts"] == 1 and "go" in row["guard_terms"]
    header, summary = row["lines"]
    assert header["sources"] == [{"label": "R1", "text": "Software engineer with Python service experience and"}]  # a copy line never expands
    assert header["verbatim"] is True
    assert summary["sources"] == [{"label": "R1", "text": joined, "continued_lines": [2]}]
    assert summary["guard_hit"] is False and summary["numeric_hits"] == [] and summary["term_hits"] == []
    assert "CLAIM 1:\nPython and Go services for 12 years.\nSOURCES FOR CLAIM 1:\nR1: " + joined + "\n" in binding.prompts[1] + "\n"
    metrics = harness.summarize([row], planned=1, max_calls=25, judge=True, calls=budget.calls)
    assert metrics["lines"]["expanded_refs"] == 1 and metrics["fabrication"]["fabricated_claims"] == 0
    assert "  > R1+R2: " + joined in metrics["samples"]["pending"]["markdown_with_sources"]
    assert "# Software engineer with Python service experience and <!-- R1 -->" in row["markdown"]


def test_a_verdict_count_mismatch_is_a_judge_failure_after_one_retry() -> None:
    short = {"verdicts": [{"line": 1, "supported": True, "unsupported_span": None}]}
    binding = _ScriptedBinding([_tailor_reply("Python service engineer.", "Ran a small team."), short, short])
    budget = harness.CallBudget(max_calls=25)
    row = harness.tailor_row(harness.CappedBinding(binding, budget), _LABEL, _POSTING, _RESUME_FIXTURE, (), budget=budget)
    assert row["ok"] is True and budget.made == 3
    assert row["judge_ok"] is False and row["judge_attempts"] == 2 and row["judge_error"] == "1 verdicts for 2 claims; missing claim(s) [2]"
    assert "Your previous answer was rejected: 1 verdicts for 2 claims; missing claim(s) [2]." in binding.prompts[2]
    assert "previous answer was rejected" not in binding.prompts[1]
    assert row["unjudged_lines"] == 2 and all(entry["judge"] is None for entry in row["lines"][1:]) and row["fabricated_lines"] == []
    metrics = harness.summarize([row], planned=1, max_calls=25, judge=True, calls=budget.calls)
    assert metrics["fabrication"]["judge_failures"] == 1 and metrics["fabrication"]["judge_retries"] == 1 and metrics["fabrication"]["judge_attempts"] == 2
    assert metrics["fabrication"]["unjudged_rewritten_lines"] == 2 and metrics["bars"]["fabricated_claims_bar_met"] is False
    assert metrics["calls"] == {"max_calls": 25, "made": 3, "tailor_calls": 1, "judge_calls": 2, "stopped_at_cap": False, "rows_planned": 1, "rows_done": 1, "rows_not_done": []}


@pytest.mark.parametrize(
    ("decoded", "message"),
    [
        ({"supported": True, "unsupported_span": None}, "judge answer must carry a verdicts list"),
        ({"verdicts": [{"line": 1, "supported": True}, {"line": 1, "supported": False, "unsupported_span": "x"}]}, "claim 1 has more than one verdict"),
        ({"verdicts": [{"line": 1, "supported": True}, {"line": 3, "supported": True}]}, "verdicts[2] names claim 3; the claims are 1 to 2"),
        ({"verdicts": [{"line": 1, "supported": True}, {"line": 2, "supported": "yes"}]}, "the verdict for claim 2 must carry a boolean supported"),
        ({"verdicts": [{"line": 1, "supported": True}, {"line": 2, "supported": False, "unsupported_span": 3}]}, "the verdict for claim 2 has an unsupported_span that is not a string or null"),
        ({"verdicts": [{"line": "1", "supported": True}, {"line": 2, "supported": True}]}, "verdicts[1] has no integer line"),
        ({"verdicts": [1, {"line": 2, "supported": True}]}, "verdicts[1] is not an object"),
    ],
)
def test_parse_judge_answer_rejects_every_malformed_batch(decoded: dict, message: str) -> None:
    with pytest.raises(ValueError) as info:
        harness.parse_judge_answer(decoded, 2)
    assert str(info.value) == message


def test_judge_prompt_states_the_verbless_bullet_convention_and_stays_strict_on_role_framing() -> None:
    prompt = harness.render_judge_prompt(_CLAIMS)
    assert "a verbless resume bullet under an employment or project entry means the candidate did that work, so adding the plain verb is supported" in prompt
    assert '"NLP pipelines for document classification (spaCy, scikit-learn)" supports "Built NLP pipelines for document classification using spaCy"' in prompt
    assert "role framing stays strict: upgrading participation into initiation or ownership is unsupported" in prompt
    assert '"with SLOs" -> "introducing SLOs", "worked on" -> "led", "part of" -> "owned"' in prompt
    assert "the upgraded words are the unsupported span" in prompt
    # Both conventions live in the rules paragraph, before the retry paragraph and the claims.
    rules = prompt.split("\n\n")[0]
    assert "verbless resume bullet" in rules and "role framing stays strict" in rules
    assert "{{" not in prompt


def test_judge_prompt_template_has_only_known_placeholders_and_needs_a_claim() -> None:
    template = harness.load_judge_template()
    assert template.startswith(bindings.TEST_MODEL_JUDGE_MARKER + "\n")
    assert set(harness._PLACEHOLDER.findall(template)) == {"count", "claims", "validation_error"}
    with pytest.raises(ValueError):
        harness.render_judge_prompt(())
    with pytest.raises(ValueError, match="unknown placeholder"):
        harness.render_judge_prompt(_CLAIMS[:1], template="{{nope}}")


# --- per-guard rejection counts from the attempt errors ---------------------------------------------


def test_attempt_errors_are_re_derived_per_attempt_and_classified_per_guard() -> None:
    binding = _ScriptedBinding(
        [
            _tailor_reply("Led a team of 8 engineers."),  # numeric guard
            _tailor_reply("Deep Kubernetes experience."),  # posting-term guard (Kubernetes is in the job below)
        ]
    )
    posting = assess_harness.Posting("p", "Software Engineer", "Acme", "Denver, CO", "https://example.test/p", "Build Python services on Kubernetes. Requirements: Python; Kubernetes.")
    budget = harness.CallBudget(max_calls=25)
    row = harness.tailor_row(harness.CappedBinding(binding, budget), _LABEL, posting, _RESUME_FIXTURE, (), budget=budget)
    assert row["ok"] is False and row["attempts"] == 2 and row["not_assessed_reason"] == "model_output_invalid"
    assert row["attempt_errors"] == [
        'summary line 1 contains the number "8" that appears in none of its cited sources (R1)',
        'summary line 1 contains the posting term "kubernetes" that appears in none of its cited sources (R1)',
    ]
    assert row["attempt_guards"] == ["numeric", "posting_term"]
    assert row["validation_error"] == row["attempt_errors"][1], "the product keeps only the last error; the per-attempt list is re-derived"
    metrics = harness.summarize([row], planned=1, max_calls=25, judge=True, calls=budget.calls)
    assert metrics["guard_rejections"] == {
        "numeric": {"retried": 1, "invalid_after_retry": 0},
        "posting_term": {"retried": 0, "invalid_after_retry": 1},
        "provenance": {"retried": 0, "invalid_after_retry": 0},
        "copy_line_shape": {"retried": 0, "invalid_after_retry": 0},
        "total_retried": 1,
        "total_invalid_after_retry": 1,
    }
    assert metrics["reliability"]["invalid_after_retry"] == 1 and metrics["reliability"]["rejections"][0]["attempt_guards"] == ["numeric", "posting_term"]
    assert metrics["calls"]["judge_calls"] == 0, "an invalid resume is never judged"


def test_a_recovered_retry_counts_the_first_attempts_guard_as_retried() -> None:
    binding = _ScriptedBinding(
        [
            {"header": [{"text": "x", "refs": []}], "sections": []},  # copy-line/shape
            _tailor_reply("Python service engineer."),
            {"verdicts": [{"line": 1, "supported": True, "unsupported_span": None}]},
        ]
    )
    budget = harness.CallBudget(max_calls=25)
    row = harness.tailor_row(harness.CappedBinding(binding, budget), _LABEL, _POSTING, _RESUME_FIXTURE, (), budget=budget)
    assert row["ok"] is True and row["retried"] is True and row["attempt_guards"] == ["copy_line_shape", None]
    metrics = harness.summarize([row], planned=1, max_calls=25, judge=True, calls=budget.calls)
    assert metrics["guard_rejections"]["copy_line_shape"] == {"retried": 1, "invalid_after_retry": 0}
    assert metrics["reliability"]["retries"] == 1 and metrics["reliability"]["recovered_on_retry"] == 1
    assert metrics["reliability"]["invalid_after_retry_bar_met"] is True


@pytest.mark.parametrize(
    ("message", "guard"),
    [
        ('summary line 1 contains the number "8" that appears in none of its cited sources (R1)', "numeric"),
        ('summary line 1 contains the posting term "kubernetes" that appears in none of its cited sources (R1)', "posting_term"),
        ("summary line 1 cites resume line 999; the resume has 2 lines", "provenance"),
        ('summary line 1 cites "cloud:aws", which is not an answered question', "provenance"),
        ('experience entry 1 heading[1] must be a copy line ({"copy": <resume line number>})', "copy_line_shape"),
        ("header has 9 copy lines; at most 8 allowed", "copy_line_shape"),
        ("the answer is not a JSON object", "copy_line_shape"),
        (None, None),
    ],
)
def test_classify_validation_error(message: str | None, guard: str | None) -> None:
    assert harness.classify_validation_error(message) == guard


def test_latency_stats_carry_p95() -> None:
    assert harness.latency_stats([]) == {"count": 0, "mean": None, "median": None, "p90": None, "p95": None, "max": None}
    stats = harness.latency_stats([float(n) for n in range(1, 21)])
    assert stats == {"count": 20, "mean": 10.5, "median": 10.5, "p90": 18.0, "p95": 19.0, "max": 20.0}


# --- the detector self-test: planted fabrications are rejected ----------------------------------

_POSTING_TEXT = (
    "Acme is hiring a Software Engineer to build Python services on Kubernetes and Terraform. "
    "Requirements: Python; Kubernetes; Terraform. " + bindings.TEST_MODEL_FABRICATE_MARKER
)
_RESUME = "Software engineer with Python service experience.\nRan a small team.\n"
_JOB = TailorJob(title="Software Engineer", company="Acme", location="Denver, CO", posting_text=_POSTING_TEXT)
_CTX = TailorContext(resume_lines=resume_lines(_RESUME))


def _fixture_binding(tmp_path: Path):
    home = tmp_path / "home"
    home.mkdir()
    config = assess_harness.build_fake_config(home)
    return assess_harness.resolve_binding(config, "ollama_local", home_root=home)


def test_planted_fabrications_are_rejected_by_the_product_on_both_attempts(tmp_path: Path) -> None:
    with assess_harness.seam_env(**{_SEAM: "1"}):
        binding = _fixture_binding(tmp_path)
        try:
            attempt = tailor_once(binding, _JOB, _CTX)
        finally:
            binding.close()
    assert not attempt.ok and attempt.not_assessed_reason is NotAssessedReason.MODEL_OUTPUT_INVALID
    assert attempt.attempts == 2
    assert attempt.validation_error == 'summary line 1 contains the number "8" that appears in none of its cited sources (R1)'


def test_planted_fabrications_through_the_harness_row_count_as_numeric_rejections(tmp_path: Path) -> None:
    posting = assess_harness.Posting("p", "Software Engineer", "Acme", "Denver, CO", "https://example.test/p", _POSTING_TEXT)
    budget = harness.CallBudget(max_calls=25)
    with assess_harness.seam_env(**{_SEAM: "1"}):
        binding = harness.CappedBinding(_fixture_binding(tmp_path), budget)
        try:
            row = harness.tailor_row(binding, _LABEL, posting, _RESUME_FIXTURE, (), budget=budget)
        finally:
            binding.close()
    assert row["ok"] is False and row["attempt_guards"] == ["numeric", "numeric"] and budget.made == 2
    metrics = harness.summarize([row], planned=1, max_calls=25, judge=True, calls=budget.calls)
    assert metrics["guard_rejections"]["numeric"] == {"retried": 1, "invalid_after_retry": 1}
    assert metrics["bars"]["fabricated_claims_bar_met"] is False, "no valid resume: the bar cannot be met vacuously"


@pytest.mark.parametrize(
    ("line", "message"),
    [
        (
            {"text": bindings.TEST_MODEL_FABRICATED_NUMBER_LINE, "refs": [{"kind": "resume", "line": 1}]},
            'summary line 1 contains the number "8" that appears in none of its cited sources (R1)',
        ),
        (
            {"text": bindings.TEST_MODEL_FABRICATED_TERM_LINE, "refs": [{"kind": "resume", "line": 1}]},
            'summary line 1 contains the posting term "kubernetes" that appears in none of its cited sources (R1)',
        ),
        (
            {"text": "Software engineer with Python service experience.", "refs": [{"kind": "resume", "line": 999}]},
            "summary line 1 cites resume line 999; the resume has 2 lines",
        ),
    ],
)
def test_each_planted_fabrication_is_rejected_for_its_own_reason(line: dict, message: str) -> None:
    with pytest.raises(TailorValidationError) as info:
        validate_tailored_output({"header": [{"copy": 1}], "sections": [{"heading": "summary", "lines": [line]}]}, _JOB, _CTX)
    assert str(info.value) == message


def test_the_evals_own_detector_flags_a_planted_line_and_passes_a_supported_one() -> None:
    terms = posting_terms(_POSTING_TEXT, exclude=("Software Engineer", "Acme", "Denver, CO"))
    source = "Software engineer with Python service experience."
    planted = harness.detect_line(bindings.TEST_MODEL_FABRICATED_NUMBER_LINE + " " + bindings.TEST_MODEL_FABRICATED_TERM_LINE, [source], terms)
    assert planted["guard_hit"] is True
    assert planted["numeric_hits"] == ["8", "12"] and planted["term_hits"] == ["kubernetes", "terraform"]
    clean = harness.detect_line("Python service engineer.", [source], terms)
    assert clean == {"numeric_hits": [], "term_hits": [], "guard_hit": False}


def test_summarize_counts_a_judge_unsupported_line_as_fabricated_and_scores_every_row_given() -> None:
    accepted = {"where": "summary line 1", "kind": "rewritten", "text": "x", "sources": [{"label": "R1", "text": "x"}], "numeric_hits": [], "term_hits": [], "guard_hit": False, "claim": 1, "judge": {"supported": False, "unsupported_span": "x"}, "fabricated": True}
    row = {
        "resume_id": "r", "posting_id": "p", "clean_fit": True, "expected_verdict": "matched_above_threshold", "excluded": False, "ok": True, "attempts": 1, "retried": False,
        "validation_error": None, "attempt_errors": [None], "attempt_guards": [None], "not_assessed_reason": None, "elapsed_seconds": 0.1, "usage": None,
        "lines": [accepted], "copy_lines": 0, "rewritten_lines": 1, "fabricated_lines": [accepted], "judge_calls": 1, "judge_attempts": 1, "judge_ok": True,
        "judge_error": None, "judge_usage": None, "judge_elapsed_seconds": 0.1, "judge_stopped_at_cap": False, "unjudged_lines": 0, "markdown": "# x <!-- R1 -->\n",
    }
    invalid = {**row, "ok": False, "attempts": 2, "retried": True, "not_assessed_reason": "model_output_invalid", "validation_error": "bad", "attempt_errors": ["bad", "bad"], "attempt_guards": ["copy_line_shape", "copy_line_shape"], "lines": [], "rewritten_lines": 0, "fabricated_lines": [], "judge_calls": 0, "judge_attempts": 0, "judge_ok": None, "markdown": None}
    excluded_row = {**row, "resume_id": "r-excluded", "clean_fit": False, "excluded": True, "fabricated_lines": [accepted, accepted]}
    metrics = harness.summarize([row, invalid, excluded_row], planned=3, max_calls=25, judge=True)
    assert metrics["calls"]["rows_done"] == 3, "every row given is scored; explicit selection decides, not the flag"
    assert metrics["fabrication"]["fabricated_claims"] == 3 and metrics["fabrication"]["judge_unsupported"] == 2
    assert metrics["fabrication"]["lines"][0]["where"] == "summary line 1"
    assert metrics["bars"]["fabricated_claims_bar_met"] is False
    assert metrics["reliability"]["invalid_after_retry"] == 1 and metrics["bars"]["invalid_after_retry_bar_met"] is False
    assert metrics["reliability"]["rejections"][0]["validation_error"] == "bad"
    assert metrics["guard_rejections"]["copy_line_shape"] == {"retried": 1, "invalid_after_retry": 1}
    assert metrics["samples"]["clean_fit"]["resume_id"] == "r" and metrics["samples"]["pending"]["resume_id"] == "r-excluded"
