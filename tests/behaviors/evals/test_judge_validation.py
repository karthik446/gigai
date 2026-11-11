"""``gigai <gig> eval validate-judge``: a judge's agreement with labels, recorded so ``eval run`` can trust it.

The fixture gig's ``judge`` step grades ``answer`` through ``deterministic-b``. The tests write synthetic ``answer``
traces (outputs carrying the word ``good`` or ``bad``) and labels by hand, and replace ``DeterministicAdapter.invoke``
with fake judges that read the prompt: the content-following one says CORRECT when the submission is ``good`` and,
in autoevals' Battle template, Yes when the first response is the good one; the ``wrong`` one follows the content
the other way round (Yes when the bad one is first); the position-biased one always says Yes.
No socket leaves 127.0.0.1 and no judge template needs PyYAML.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gigai.adapters.deterministic import DeterministicAdapter
from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.evals import contracts, judge, records
from gigai.evals import datasets as ds
from gigai.evals import run as eval_run
from gigai.evals.list import eval_listing
from gigai.sdk import GigContext
from tests.behaviors.evals.test_run import GIG, count, gig, home, query  # noqa: F401 - fixtures
from tests.behaviors.sdk.test_ctx_model import MANIFEST

VENDOR = Path(judge.__file__).resolve().parent / "_vendor"
LABEL_TEMPLATE = {"step_id": "answer", "critique": "", "rubric": "r1", "who": "ann", "at": "2026-01-02T03:04:05Z", "round": 1}


def fake_judge(monkeypatch: pytest.MonkeyPatch, *, biased: bool = False, wrong: bool = False) -> list[str]:
    """Replace the deterministic adapter with a judge that reads the prompt; returns every prompt it saw."""

    seen: list[str] = []

    def invoke(self: DeterministicAdapter, request: object) -> InvocationResult:
        prompt: str = request.prompt  # type: ignore[attr-defined]
        seen.append(prompt)
        if "[Response 1]" in prompt:
            good_first = prompt.index("good") < prompt.index("bad")
            reply = "Yes" if biased or good_first != wrong else "No"
        else:
            reply = "GRADE: C" if "good" in prompt else "GRADE: I"
        return InvocationResult(
            status="success",
            output_text=reply,
            resolved_model=request.model,  # type: ignore[attr-defined]
            raw_usage={},
            normalized_usage=NormalizedUsage(None, None, None),
            cost_status="unavailable",
        )

    monkeypatch.setattr(DeterministicAdapter, "invoke", invoke)
    return seen


def write_answer_runs(gig: GigContext, runs: int) -> list[tuple[str, str, str]]:
    """``runs`` ci runs of the fixture's ``answer`` step, each with one trace per item: ``(run_id, item_id, output)``.
    Even-numbered runs answer well (``good``), odd ones badly (``bad``)."""

    entry = eval_run._entry(gig, "answer")
    suite = eval_run.load_suite(gig, entry, "ci")
    connection = records.open_evals_db(gig.name, gig.home)
    written: list[tuple[str, str, str]] = []
    try:
        for number in range(runs):
            run = records.RunRecord(
                run_id=f"run_{number:03d}", gig=gig.name, manifest_digest="m", dataset=suite.record, step_id="answer", suite="ci", k=1
            )
            records.write_run(connection, run)
            for item_id, row in suite.rows.items():
                output = f"the {'good' if number % 2 == 0 else 'bad'} answer {number} to {item_id}"
                trace = records.TraceRecord(f"trace_{number:03d}_{item_id}", run.run_id, "answer", eval_run._item(item_id, row), row["question"], output)
                records.write_trace(connection, trace)
                written.append((run.run_id, item_id, output))
        connection.commit()
    finally:
        connection.close()
    return written


def labels_jsonl(path: Path, traces: list[tuple[str, str, str]], flip: int = 0, unknown: int = 0) -> Path:
    """Labels for ``traces``: ``met`` for good outputs, ``not met`` for bad ones; the first ``flip`` labels are
    reversed and the next ``unknown`` are ``unknown``."""

    lines = []
    for index, (run_id, item_id, output) in enumerate(traces):
        label = "met" if "good" in output else "not met"
        if index < flip:
            label = "not met" if label == "met" else "met"
        elif index < flip + unknown:
            label = "unknown"
        lines.append(json.dumps({**LABEL_TEMPLATE, "run_id": run_id, "item_id": item_id, "label": label}))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def validations(home: Path) -> list[tuple]:
    return query(home, "SELECT scorer_id, judge_model, judge_prompt_sha256, n_items, metric, value, details FROM judge_validations ORDER BY metric")


def invoke(gig: GigContext, *args: str):
    return CliRunner().invoke(judge.validate_judge_command, list(args), obj=gig, catch_exceptions=False)


# --- pure functions ------------------------------------------------------------------------------------------------


def test_agreement_reports_kappa_its_interval_and_the_rates() -> None:
    pairs = [(1, 1)] * 40 + [(0, 0)] * 40 + [(1, 0)] * 10 + [(0, 1)] * 10
    report = judge.agreement(pairs)
    assert report["n"] == 100
    assert report["kappa"] == pytest.approx(0.6)
    assert report["ci_low"] < 0.6 < report["ci_high"]
    assert (report["tpr"], report["tnr"], report["observed_agreement"]) == (0.8, 0.8, 0.8)
    # both raters using one category: kappa is undefined, the rates still read
    degenerate = judge.agreement([(1, 1)] * 5)
    assert (degenerate["kappa"], degenerate["ci_low"], degenerate["tpr"], degenerate["tnr"]) == (None, None, 1.0, None)


def test_pass_rule_needs_126_units_kappa_0_6_and_swap_0_9() -> None:
    assert judge.passes(0.6, 126, None)
    assert judge.passes(0.6, 126, 0.9)
    assert judge.passes(0.6, 126, 0.9, 66)
    assert judge.failures(0.6, 125, None) == ["n < 126"]
    assert judge.failures(0.59, 126, 0.95) == ["kappa < 0.6"]
    assert judge.failures(0.9, 200, 0.89) == ["swap_consistency < 0.9"]
    assert judge.failures(0.9, 200, None, 0) == ["no swap pairs"]
    assert judge.failures(None, 10, 0.5) == ["n < 126", "kappa undefined", "swap_consistency < 0.9"]


def test_every_vendored_autoevals_template_parses_and_renders() -> None:
    names = sorted(path.stem for path in (VENDOR / "autoevals" / "templates").glob("*.yaml"))
    assert names == ["battle", "closed_q_a", "factuality", "humor", "possible", "security", "sql", "summary", "translation"]
    for name in names:
        template = judge.load_template(name)
        assert template.choice_scores and all(isinstance(score, float) for score in template.choice_scores.values())
        fields = dict.fromkeys(re.findall(r"\{\{\{?([a-zA-Z_]+)\}?\}\}", template.prompt), "x")
        rendered = judge.render_template(template, **fields)
        assert "{{" not in rendered and rendered.endswith(f"Answer with exactly one of: {', '.join(template.choice_scores)}.")
    battle = judge.load_template("battle")
    assert battle.choice_scores == {"Yes": 1.0, "No": 0.0}
    prompt = judge.render_template(battle, instructions="Q", output="first", expected="second")
    assert "[Response 1]\nfirst\n\n[Instruction 2]\nQ\n[Response 2]\nsecond" in prompt
    assert judge.template_choice(battle, " yes.\nbecause") == "Yes"
    assert judge.template_choice(battle, "Maybe") is None
    with pytest.raises(ValueError, match="no value for expected, output"):
        judge.render_template(battle, instructions="Q")
    with pytest.raises(ValueError, match="no vendored autoevals template"):
        judge.load_template("nope")


def test_openevals_prompts_render_their_placeholders() -> None:
    text = judge.openevals_prompt("correctness", inputs="Q", outputs="A", reference_outputs="R")
    assert "<input>\nQ\n</input>" in text and "<reference_outputs>\nR\n</reference_outputs>" in text
    hallucination = judge.openevals_prompt("hallucination", inputs="Q", outputs="A", reference_outputs="R", context="C")
    assert "<context>\nC\n</context>" in hallucination
    with pytest.raises(ValueError, match="no value for context"):
        judge.openevals_prompt("hallucination", inputs="Q", outputs="A", reference_outputs="R")


def test_swap_blocks_exchanges_the_two_responses() -> None:
    prompt = "ASK\n\nQUESTION:\nq\n\nRESPONSE A:\nfirst\nline\n\nRESPONSE B:\nsecond"
    swapped = judge.swap_blocks(prompt, ("RESPONSE A:", "RESPONSE B:"))
    assert swapped == "ASK\n\nQUESTION:\nq\n\nRESPONSE A:\nsecond\n\nRESPONSE B:\nfirst\nline"
    assert judge.swap_blocks(swapped, ("RESPONSE A:", "RESPONSE B:")) == prompt
    # a response quoting a marker in its own text does not move the split
    quoting = "ASK\n\nRESPONSE A:\nsee RESPONSE B: below\n\nRESPONSE B:\nreplying to\n\nRESPONSE A:\nabove"
    assert judge.swap_blocks(quoting, ("RESPONSE A:", "RESPONSE B:")) == "ASK\n\nRESPONSE A:\nreplying to\n\nRESPONSE A:\nabove\n\nRESPONSE B:\nsee RESPONSE B: below"
    with pytest.raises(ValueError, match="no 'RESPONSE A:'"):
        judge.swap_blocks("nothing here", ("RESPONSE A:", "RESPONSE B:"))
    with pytest.raises(ValueError, match="no 'RESPONSE A:'"):
        judge.swap_blocks("RESPONSE A: inline\n\nRESPONSE B:\nb", ("RESPONSE A:", "RESPONSE B:"))
    assert judge.json_field('sure: {"verdict": "A>B"} done', "verdict") == "A>B"
    assert judge.json_field("no json", "verdict") is None


def test_diff_md_records_the_vendored_hashes() -> None:
    for project in ("autoevals", "openevals"):
        folder = VENDOR / project
        table = re.findall(r"^\| `([^`]+)` \| [^|]+ \| [^|]+ \| `([0-9a-f]{64})` \|$", (folder / "DIFF.md").read_text(encoding="utf-8"), re.M)
        assert table, project
        listed = {name for name, _ in table}
        present = {str(path.relative_to(folder)) for path in folder.rglob("*") if path.is_file() and path.name != "DIFF.md" and "__pycache__" not in path.parts}
        assert listed == present, project
        for name, digest in table:
            assert hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest, f"{project}/{name}"


# --- against labels ------------------------------------------------------------------------------------------------


def test_kappa_over_labelled_traces_is_recorded_with_its_interval_and_rates(gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    traces = write_answer_runs(gig, 22)  # 132 traces, 66 good and 66 bad
    labels = labels_jsonl(tmp_path / "labels.jsonl", traces, flip=20)
    prompts = fake_judge(monkeypatch)

    report = judge.validate(gig, "judge", str(labels), swap=False)

    assert (report["n"], report["labels"], report["unparsed"], report["calls"]) == (132, 132, 0, 132)
    assert report["observed_agreement"] == pytest.approx(112 / 132)
    assert report["kappa"] == pytest.approx(0.6970, abs=1e-3)
    assert report["ci_low"] < report["kappa"] < report["ci_high"]
    # the 20 flipped labels cover runs 0-3: 12 good traces labelled not met, 8 bad ones labelled met
    assert (report["tpr"], report["tnr"]) == (pytest.approx(54 / 62), pytest.approx(58 / 70))
    assert (report["pass"], report["reasons"], report["swap_consistency"]) == (True, [], None)
    assert report["unlocks_scorer"] and report["label_set"] == {"name": "labels.jsonl", "sha256": "sha256:" + hashlib.sha256(labels.read_bytes()).hexdigest(), "kind": "labels"}
    # exactly the scoring prompt went to the judge, with the suite row's expected value as the reference
    assert all(prompt == contracts.judge_prompt(*_prompt_parts(gig, run_id, item_id, output)) for prompt, (run_id, item_id, output) in zip(prompts, traces, strict=True))
    # one kappa row keyed so `check_judge` finds it, no swap row
    rows = validations(home)
    assert [(row[0], row[1], row[2], row[3], row[4]) for row in rows] == [("step:judge", "deterministic-b", contracts.JUDGE_PROMPT_SHA256, 132, "kappa")]
    details = json.loads(rows[0][6])
    assert details["pass"] is True and details["ci_low"] == report["ci_low"] and details["tpr"] == report["tpr"] and details["swap_consistency"] is None
    assert details["rule"] == judge.RULE
    # the judge's calls are traced under one validation run
    assert count(home, "traces", report["run_id"]) == 132
    assert query(home, "SELECT step_id, tag, calls_made FROM runs WHERE run_id = ?", report["run_id"]) == [("judge", f"validate-judge:{labels}", 132)]


def _prompt_parts(gig: GigContext, run_id: str, item_id: str, output: str) -> tuple[str, str, str]:
    rows = eval_run.load_suite(gig, eval_run._entry(gig, "answer"), "ci").rows
    return rows[item_id]["question"], output, rows[item_id]["expected"]


def test_fewer_than_126_units_is_a_clean_fail_not_a_refusal(gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    traces = write_answer_runs(gig, 4)  # 24 traces
    labels = labels_jsonl(tmp_path / "labels.jsonl", traces, flip=2, unknown=3)
    fake_judge(monkeypatch)

    result = invoke(gig, "--step", "judge", "--against", str(labels))

    assert result.exit_code == 1, result.output
    assert "21 units from 24 labels (3 unknown, 0 contested), 0 unparsed, 21 calls" in result.output
    assert "pass: no (n < 126)" in result.output
    assert "good" not in result.output and "bad answer" not in result.output
    [(scorer, model, _sha, n, metric, value, details)] = validations(home)
    assert (scorer, n, metric) == ("step:judge", 21, "kappa") and value > 0.6
    assert json.loads(details)["pass"] is False and json.loads(details)["reasons"] == ["n < 126"]


def test_a_failed_validation_row_keeps_the_scorer_locked(gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fixture's bar is kappa >= 0.6 over 3 items; a row of 24 units passes it but records ``pass`` false, and
    ``check_judge`` honours the recorded rule. Deleting the row locks the scorer again (``latest: none``)."""

    traces = write_answer_runs(gig, 4)
    labels = labels_jsonl(tmp_path / "labels.jsonl", traces)
    fake_judge(monkeypatch)
    judge.validate(gig, "judge", str(labels), swap=False)
    spec = eval_run.ScorerSpec("step:judge", contracts.LIFTED_VERSION, judge_step="judge")
    connection = records.open_evals_db(GIG, home)
    try:
        with pytest.raises(eval_run.JudgeNotValidatedError, match=r"\(latest: kappa 1.000 over 24 items\), which fails: n < 126"):
            eval_run.check_judge(connection, gig, spec, "deterministic-b")
        connection.execute("DELETE FROM judge_validations")
        connection.commit()
        with pytest.raises(eval_run.JudgeNotValidatedError, match=r"latest: none"):
            eval_run.check_judge(connection, gig, spec, "deterministic-b")
    finally:
        connection.close()


def test_a_passing_validation_unlocks_eval_run_and_eval_list_shows_it(gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    traces = write_answer_runs(gig, 22)
    labels = labels_jsonl(tmp_path / "labels.jsonl", traces, flip=10)
    fake_judge(monkeypatch)
    result = invoke(gig, "--step", "judge", "--against", str(labels), "--json")
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["pass"] is True

    listing = eval_listing(MANIFEST, GIG, home, None)
    judge_step = next(step for step in listing["steps"] if step["id"] == "judge")
    assert judge_step["last_validation"]["validated"] is True
    assert judge_step["last_validation"]["n_items"] == 132
    monkeypatch.setattr(DeterministicAdapter, "invoke", _real_invoke)
    lines = eval_run.execute(gig, "answer", "ci")
    assert lines[0].startswith("run run_") and "answer" in lines[0]


_real_invoke = DeterministicAdapter.invoke


def test_swap_consistency_is_order_invariance_and_catches_position_bias(gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    traces = write_answer_runs(gig, 22)
    labels = labels_jsonl(tmp_path / "labels.jsonl", traces)
    prompts = fake_judge(monkeypatch)

    report = judge.validate(gig, "judge", str(labels), swap=True)

    # 11 good and 11 bad traces per item give 11 pairs per item, 66 pairs, each asked twice
    assert (report["n"], report["swap_pairs"], report["swap_consistency"], report["swap_pair_accuracy"]) == (132, 66, 1.0, 1.0)
    assert (report["calls"], report["pass"], report["recorded"]) == (132 + 2 * 66, True, ["kappa", "swap_consistency"])
    assert sum("[Response 1]" in prompt for prompt in prompts) == 132
    rows = validations(home)
    assert [(row[4], row[5], row[3]) for row in rows] == [("kappa", 1.0, 132), ("swap_consistency", 1.0, 132)]
    assert all(json.loads(row[6])["swap_consistency"] == 1.0 for row in rows)

    # a judge that follows the content but prefers the wrong answer is consistent (the same output wins both
    # times) and inaccurate; its grades still say CORRECT for the good answers, so kappa stays 1.0
    fake_judge(monkeypatch, wrong=True)
    wrong = judge.validate(gig, "judge", str(labels), swap=True)
    assert (wrong["swap_consistency"], wrong["swap_pair_accuracy"], wrong["pass"]) == (1.0, 0.0, True)

    # a judge that always picks the first response is caught only because both orders are asked
    fake_judge(monkeypatch, biased=True)
    biased = judge.validate(gig, "judge", str(labels), swap=True)
    assert (biased["kappa"], biased["swap_consistency"], biased["swap_pair_accuracy"]) == (1.0, 0.0, 0.0)
    assert (biased["pass"], biased["reasons"]) == (False, ["swap_consistency < 0.9"])
    assert validations(home)[-1][5] == 0.0


def test_undefined_kappa_or_no_swap_pairs_is_a_clean_fail_that_records_nothing(gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One run answers every item well: every label is met, the judge agrees, so kappa has no value and no item
    has a pair to swap. The command exits 1 naming both, writes no row, and the scorer stays locked."""

    traces = write_answer_runs(gig, 1)  # 6 good traces
    labels = labels_jsonl(tmp_path / "labels.jsonl", traces)
    fake_judge(monkeypatch)

    done = invoke(gig, "--step", "judge", "--against", str(labels), "--swap")

    assert done.exit_code == 1, done.output
    assert "kappa n/a [n/a, n/a]  tpr 1.000  tnr n/a" in done.output
    assert "swap consistency n/a over 0 pairs" in done.output
    assert "recorded: nothing (no metric has a value)" in done.output
    assert "pass: no (n < 126, kappa undefined, no swap pairs)" in done.output
    assert validations(home) == []
    with pytest.raises(eval_run.RunRefused, match="\\(latest: none\\)"):
        eval_run.execute(gig, "answer", "ci")

    # without --swap the undefined kappa alone fails and still records nothing
    report = judge.validate(gig, "judge", str(labels), swap=False)
    assert (report["pass"], report["reasons"], report["recorded"], report["swap_pairs"]) == (False, ["n < 126", "kappa undefined"], [], None)
    assert validations(home) == []


def test_both_rows_are_written_in_one_transaction(gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When the second INSERT (the swap row) fails, the kappa row is rolled back with it: zero rows, and the
    connection is left outside a transaction."""

    traces = write_answer_runs(gig, 22)
    labels = labels_jsonl(tmp_path / "labels.jsonl", traces)
    fake_judge(monkeypatch)

    class SecondInsertFails(sqlite3.Connection):
        def executemany(self, sql: str, rows: object) -> sqlite3.Cursor:  # type: ignore[override]
            first, *_rest = list(rows)  # type: ignore[call-overload]
            super().executemany(sql, [first])
            assert self.in_transaction
            raise sqlite3.OperationalError("disk I/O error (simulated on the second row)")

    real_open = records.open_evals_db

    def open_failing(name: str, root: Path) -> sqlite3.Connection:
        real_open(name, root).close()
        connection = sqlite3.connect(records.evals_db_path(name, root), timeout=30, factory=SecondInsertFails)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    monkeypatch.setattr(judge.records, "open_evals_db", open_failing)
    with pytest.raises(sqlite3.OperationalError, match="simulated on the second row"):
        judge.validate(gig, "judge", str(labels), swap=True)

    assert validations(home) == []


def test_store_labels_and_contested_votes(gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``--against labels`` reads the store's own labels of the graded step; a trace whose met / not met votes tie
    is contested and dropped."""

    traces = write_answer_runs(gig, 2)  # 12 traces
    connection = records.open_evals_db(GIG, home)
    try:
        for index, (run_id, item_id, output) in enumerate(traces):
            voters = [("ann", "met" if "good" in output else "not met")]
            if index == 0:
                voters.append(("bob", "not met"))  # ann says met, bob says not met: contested
            for who, label in voters:
                connection.execute(
                    "INSERT INTO labels (label_id, dataset_id, item_id, item_version, label, source, recorded_at, run_id, step_id, critique, rubric, who, at, round, blind)"
                    " VALUES (?, 'dataset.jsonl', ?, '1', ?, 'human', '2026-01-02T03:04:05Z', ?, 'answer', '', 'r1', ?, '2026-01-02T03:04:05Z', 1, 0)",
                    (f"label_{index}_{who}", item_id, json.dumps(label), run_id, who),
                )
        connection.commit()
    finally:
        connection.close()
    fake_judge(monkeypatch)

    report = judge.validate(gig, "judge", "labels", swap=False)

    assert (report["n"], report["labels"], report["contested"], report["kappa"]) == (11, 13, 1, 1.0)
    assert report["label_set"]["name"] == "labels" and report["pass"] is False


def test_refusals_name_the_problem_before_any_call(gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = fake_judge(monkeypatch)
    with pytest.raises(eval_run.RunRefused, match="no \\[\\[evals.steps\\]\\] entry has id 'nope'"):
        judge.validate(gig, "nope", "labels", swap=False)
    with pytest.raises(eval_run.RunRefused, match="step 'answer' grades no step"):
        judge.validate(gig, "answer", "labels", swap=False)
    with pytest.raises(eval_run.RunRefused, match="--against must be a labels .jsonl file or one of labels, judgebench, mtbench_human"):
        judge.validate(gig, "judge", "mystery", swap=False)
    bad = tmp_path / "bad.jsonl"
    bad.write_text('{"run_id": "r", "item_id": "q1", "step_id": "solve", "label": "met", "critique": "", "rubric": "r", "who": "a", "at": "2026-01-02T03:04:05Z", "round": 1}\n')
    with pytest.raises(eval_run.RunRefused, match="labels bad.jsonl: line 1: step_id is not the --step"):
        judge.validate(gig, "judge", str(bad), swap=False)
    traces = write_answer_runs(gig, 1)
    stray = labels_jsonl(tmp_path / "stray.jsonl", [("run_missing", "q1", "good")])
    with pytest.raises(eval_run.RunRefused, match="label for run run_missing item q1 step answer: no succeeded trace is stored"):
        judge.validate(gig, "judge", str(stray), swap=False)
    assert prompts == [] and count(home, "judge_validations") == 0 and traces
    result = invoke(gig, "--step", "nope", "--against", "labels")
    assert result.exit_code == 2 and "no [[evals.steps]] entry has id 'nope'" in result.output


# --- against a public set -----------------------------------------------------------------------------------------


def test_judgebench_ci_slice_is_a_calibration_report_that_does_not_unlock_the_scorer(gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts = fake_judge(monkeypatch)
    items = ds.load_ci("judgebench")

    result = invoke(gig, "--step", "judge", "--against", "judgebench", "--swap")

    assert result.exit_code == 1, result.output
    assert prompts == []  # a deterministic judge answers a public set from the slice's canned completion
    assert f"{len(items)} units from {len(items)} labels" in result.output
    assert "kappa 1.000" in result.output and "tpr 1.000  tnr 1.000" in result.output
    assert f"swap consistency 0.000 over {len(items)} pairs" in result.output  # the swapped prompt has no canned answer
    assert "accuracy by source: " in result.output and "livebench-math 1.00" in result.output
    assert "pass: no (n < 126, swap_consistency < 0.9)" in result.output
    assert "validates the model, not the scoring prompt" in result.output
    assert not any(item.prompt.split("\n")[2] in result.output for item in items)  # no item text is printed
    rows = validations(home)
    assert [(row[0], row[1], row[4], row[5], row[3]) for row in rows] == [("step:judge", "deterministic-b", "kappa", 1.0, len(items)), ("step:judge", "deterministic-b", "swap_consistency", 0.0, len(items))]
    assert rows[0][2] != contracts.JUDGE_PROMPT_SHA256
    details = json.loads(rows[0][6])
    assert details["unlocks_scorer"] is False and details["label_set"]["kind"] == "public" and set(details["sources"]) >= {"livebench-math", "livebench-reasoning"}
    spec = eval_run.ScorerSpec("step:judge", contracts.LIFTED_VERSION, judge_step="judge")
    connection = records.open_evals_db(GIG, home)
    try:
        with pytest.raises(eval_run.JudgeNotValidatedError, match="latest: none"):
            eval_run.check_judge(connection, gig, spec, "deterministic-b")
    finally:
        connection.close()


def test_mtbench_human_reads_the_judge_verdict_and_swaps_the_assistants(gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A judge that is not deterministic is asked the item's prompt; here it answers from the gold label it finds
    in the prompt's own text for the original order and follows the content when swapped."""

    items = {item.prompt: item for item in ds.load_ci("mtbench_human")}
    swapped_back = {judge.swap_blocks(prompt, ("ASSISTANT A:", "ASSISTANT B:")): item for prompt, item in items.items()}
    seen: list[str] = []

    def invoke_fake(self: DeterministicAdapter, request: object) -> InvocationResult:
        prompt: str = request.prompt  # type: ignore[attr-defined]
        seen.append(prompt)
        if prompt in items:
            winner = items[prompt].expected["winner"]
        else:
            winner = {"A": "B", "B": "A"}[swapped_back[prompt].expected["winner"]]
        return InvocationResult(status="success", output_text=json.dumps({"winner": winner}), resolved_model="m", raw_usage={}, normalized_usage=NormalizedUsage(None, None, None), cost_status="unavailable")

    monkeypatch.setattr(DeterministicAdapter, "invoke", invoke_fake)
    monkeypatch.setattr(judge, "_targets", lambda gig, steps: {step: SimpleNamespace(id="deterministic-b", adapter="fake") for step in steps})

    report = judge.validate(gig, "judge", "mtbench_human", swap=True)

    assert (report["n"], report["kappa"], report["swap_consistency"], report["calls"]) == (len(items), 1.0, 1.0, 2 * len(items))
    assert report["reasons"] == ["n < 126"] and report["judge_model"] == "deterministic-b"
    assert len(seen) == 2 * len(items) and count(home, "traces", report["run_id"]) == 2 * len(items)
