"""``gigai <gig> eval run``: call, trace and score an eval step over a suite's dataset, with call caps checked first.

The fixture gig's ``solve`` step runs on the committed 10-row tinyGSM8k ci slice through the deterministic adapter,
whose canned replies follow S-smoke's ``score_wrapped.py`` rule (§S4: ``ANSWER: <expected>`` on even source rows,
``So the total is $<expected>.00`` on odd ones, ``ANSWER: <expected + 1>`` on every 10th). The ``answer`` step
adds the ``step:judge`` scorer, whose judge (``deterministic-b``) needs a ``judge_validations`` row, inserted by
hand here. Every adapter call is counted at ``DeterministicAdapter.invoke``; no socket leaves 127.0.0.1.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import json
import logging
import re
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from gigai.adapters.deterministic import DeterministicAdapter
from gigai.evals import contracts
from gigai.evals import run as eval_run
from gigai.evals.records import evals_db_path, open_evals_db
from gigai.sdk import GigContext
from tests.behaviors.gigs.test_loader import FIXTURES, _add_fixture, _run
from tests.behaviors.sdk.test_ctx_model import MANIFEST, loopback_only, make_home

GIG = "eval-fixture"
PACKAGE = FIXTURES / GIG / "gig_eval_fixture"
SLICE = PACKAGE / "tinygsm8k.jsonl"
ROWS = [json.loads(line) for line in SLICE.read_text(encoding="utf-8").splitlines()]
#: The committed snapshot: ``match`` (numeric) accuracy of the deterministic replies on the 10-row ci slice.
SNAPSHOT_MATCH = 0.800
assert loopback_only  # the autouse socket guard, imported to apply here


def s_completion(row: int, expected: str) -> str:
    """S-smoke §S4 ``score_wrapped.py``: the fake completion for source row ``row``."""

    if row % 10 == 9:
        return f"I think ANSWER: {int(expected) + 1}"
    return f"ANSWER: {expected}" if row % 2 == 0 else f"So the total is ${expected}.00"


@pytest.fixture
def home(tmp_path: Path) -> Path:
    make_home(tmp_path / "home")
    return tmp_path / "home"


@pytest.fixture
def gig(home: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[GigContext]:
    """The fixture gig's context, its package importable and located where ``eval run`` looks for datasets."""

    monkeypatch.syspath_prepend(str(FIXTURES / GIG))
    monkeypatch.setattr(eval_run, "_package_dir", lambda name: PACKAGE if name == GIG else None)
    importlib.import_module("gig_eval_fixture")
    yield GigContext(home, GIG, MANIFEST, logging.getLogger("gigai.gig.eval-fixture"))
    for name in [name for name in sys.modules if name.split(".")[0] == "gig_eval_fixture"]:
        sys.modules.pop(name)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Every request that reaches the deterministic adapter."""

    made: list[object] = []
    invoke = DeterministicAdapter.invoke

    def counted(self: DeterministicAdapter, request: object) -> object:
        made.append(request)
        return invoke(self, request)

    monkeypatch.setattr(DeterministicAdapter, "invoke", counted)
    return made


def query(home: Path, sql: str, *args: object) -> list[tuple]:
    connection = sqlite3.connect(evals_db_path(GIG, home))
    try:
        return connection.execute(sql, args).fetchall()
    finally:
        connection.close()


def count(home: Path, table: str, run_id: str | None = None) -> int:
    if table == "scores" and run_id is not None:
        sql = "SELECT count(*) FROM scores JOIN traces USING (trace_id) WHERE traces.run_id = ?"
        return query(home, sql, run_id)[0][0]
    where = "" if run_id is None else " WHERE run_id = ?"
    return query(home, f"SELECT count(*) FROM {table}{where}", *(() if run_id is None else (run_id,)))[0][0]


def run_id(lines: list[str]) -> str:
    return lines[0].split()[1].rstrip(":")


def values(home: Path, run: str, scorer: str) -> dict[str, list[float]]:
    found: dict[str, list[float]] = {}
    for item, value in query(
        home,
        "SELECT traces.item_id, scores.value FROM scores JOIN traces USING (trace_id)"
        " WHERE traces.run_id = ? AND scores.scorer_id = ? ORDER BY traces.item_id, traces.repeat_index",
        run,
        scorer,
    ):
        found.setdefault(item, []).append(value)
    return found


def validate(home: Path, *, kappa: float, n_items: int = 30, at: str = "2026-01-01T00:00:00Z") -> None:
    """Hand-insert a kappa ``judge_validations`` row for the fixture's judge and the lifted judge prompt."""

    connection = open_evals_db(GIG, home)
    try:
        connection.execute(
            "INSERT INTO judge_validations VALUES (?, 'step:judge', ?, 'deterministic-b', ?, 'labels', 'sha256:y', ?,"
            " 'kappa', ?, NULL, ?)",
            (f"validation_{kappa}_{n_items}_{at}", contracts.LIFTED_VERSION, contracts.JUDGE_PROMPT_SHA256, n_items, kappa, at),
        )
        connection.commit()
    finally:
        connection.close()


# --- (a) the ci slice -------------------------------------------------------------------------------------------


def test_solve_on_the_ci_slice_traces_ten_calls_and_scores_each_with_every_scorer(gig: GigContext, home: Path, calls: list) -> None:
    lines = eval_run.execute(gig, "solve", "ci")
    run = run_id(lines)

    assert (count(home, "runs"), count(home, "traces"), count(home, "scores")) == (1, 10, 10 * 2)
    assert len(calls) == 10
    assert lines[0] == f"run {run}: step solve, suite ci, 10 items x 1, 10 calls"
    step, suite, k, made, dataset_sha, replay_of = query(
        home, "SELECT step_id, suite, k, calls_made, dataset_sha256, replay_of FROM runs WHERE run_id = ?", run
    )[0]
    assert (step, suite, k, made, replay_of) == ("solve", "ci", 1, 10, None)
    assert dataset_sha == eval_run.dataset_digest(hashlib.sha256(SLICE.read_bytes()).hexdigest())

    traces = {item: (raw, json.loads(parsed), status, repeat) for item, raw, parsed, status, repeat in query(
        home, "SELECT item_id, output_raw_text, output_parsed, parse_status, repeat_index FROM traces WHERE run_id = ?", run
    )}
    for row in ROWS:
        raw, parsed, status, repeat = traces[row["id"]]
        assert raw == s_completion(row["source_ids"]["row"], row["expected"])
        source_row = row["source_ids"]["row"]
        last = str(int(row["expected"]) + 1) if source_row % 10 == 9 else row["expected"] + ("" if source_row % 2 == 0 else ".00")
        assert (parsed, status, repeat) == ({"answer": last}, "ok", 0)  # final_number: the last number, commas dropped

    # S's rule: match(numeric=True) at the end is wrong exactly on every 10th source row.
    match = values(home, run, "gigai.evals.contracts:match")
    assert match == {row["id"]: [0.0 if row["source_ids"]["row"] % 10 == 9 else 1.0] for row in ROWS}
    assert sorted(item for item, (value,) in match.items() if value == 0.0) == ["tinygsm8k-059", "tinygsm8k-079"]
    assert sum(value for (value,) in match.values()) / len(ROWS) == pytest.approx(SNAPSHOT_MATCH, abs=1e-12)
    assert f"  gigai.evals.contracts:match: {SNAPSHOT_MATCH:.4f} over 10 traces" in lines
    # pattern(r"ANSWER:\s*(\d+)") needs the marker with the right digits: only the even, not-10th rows.
    marker = values(home, run, "gig_eval_fixture.scorers:answer_marker")
    assert marker == {row["id"]: [1.0 if row["source_ids"]["row"] % 2 == 0 and row["source_ids"]["row"] % 10 != 9 else 0.0] for row in ROWS}
    versions = dict(query(home, "SELECT DISTINCT scorer_id, scorer_version FROM scores"))
    assert versions == {"gigai.evals.contracts:match": contracts.LIFTED_VERSION, "gig_eval_fixture.scorers:answer_marker": contracts.LIFTED_VERSION}


def test_k_repeats_every_item_with_repeat_indexes_0_to_k_minus_1(gig: GigContext, home: Path, calls: list) -> None:
    run = run_id(eval_run.execute(gig, "solve", "ci", k=3))

    assert (len(calls), count(home, "traces", run), count(home, "scores", run)) == (30, 30, 60)
    repeats: dict[str, list[int]] = {}
    for item, repeat in query(home, "SELECT item_id, repeat_index FROM traces WHERE run_id = ? ORDER BY item_id, repeat_index", run):
        repeats.setdefault(item, []).append(repeat)
    assert repeats == {row["id"]: [0, 1, 2] for row in ROWS}
    assert query(home, "SELECT k FROM runs WHERE run_id = ?", run) == [(3,)]


# --- (b) caps are checked before the call -------------------------------------------------------------------------


def test_max_calls_stops_before_the_call_that_would_pass_it(gig: GigContext, home: Path, calls: list) -> None:
    with pytest.raises(eval_run.CapReachedError, match=r"^cap 3 reached before call 4$"):
        eval_run.execute(gig, "solve", "ci", max_calls=3)

    assert len(calls) == 3  # call 4 never reached the adapter
    assert count(home, "traces") == 3
    assert query(home, "SELECT calls_made FROM runs") == [(3,)]
    assert count(home, "scores") == 0


def test_max_calls_counts_judge_calls_too(gig: GigContext, home: Path, calls: list) -> None:
    validate(home, kappa=0.8)
    with pytest.raises(eval_run.CapReachedError, match=r"^cap 8 reached before call 9$"):
        eval_run.execute(gig, "answer", "ci", max_calls=8)

    assert len(calls) == 8
    assert dict(query(home, "SELECT step_id, count(*) FROM traces GROUP BY step_id")) == {"answer": 6, "judge": 2}


def test_a_budget_on_the_context_caps_the_run(gig: GigContext, home: Path, calls: list) -> None:
    @dataclass(frozen=True)
    class Budgeted(GigContext):
        budget: object = None

    budgeted = Budgeted(gig.home, gig.name, gig.manifest, gig.log, SimpleNamespace(max_calls=2))
    with pytest.raises(eval_run.CapReachedError, match=r"^cap 2 reached before call 3$"):
        eval_run.execute(budgeted, "solve", "ci", max_calls=5)
    assert len(calls) == count(home, "traces") == 2


def test_the_gate_cap_bounds_only_non_deterministic_calls() -> None:
    made: list[str] = []
    model = SimpleNamespace(model=lambda step, prompt, **fields: made.append(step))

    paid = eval_run.CallBudget(None, 0, {"paid": False})
    with pytest.raises(eval_run.CapReachedError, match=r"^cap 0 reached before call 1$"):
        paid.call(model, "paid", "p")  # type: ignore[arg-type]
    assert (made, paid.made) == ([], 0)

    free = eval_run.CallBudget(None, 0, {"free": True})
    free.call(model, "free", "p")  # type: ignore[arg-type]
    free.call(model, "free", "p")  # type: ignore[arg-type]
    assert (made, free.made, free.paid) == (["free", "free"], 2, 0)


# --- judges --------------------------------------------------------------------------------------------------------


def test_a_judge_scorer_without_a_passing_validation_is_refused_before_any_call(gig: GigContext, home: Path, calls: list) -> None:
    with pytest.raises(eval_run.JudgeNotValidatedError, match=r"latest: none"):
        eval_run.execute(gig, "answer", "ci")
    validate(home, kappa=0.5, at="2026-01-01T00:00:00Z")
    with pytest.raises(eval_run.JudgeNotValidatedError, match=r"kappa >= 0.6 over >= 3 items \(latest: kappa 0.500 over 30 items\)"):
        eval_run.execute(gig, "answer", "ci")
    validate(home, kappa=0.9, n_items=2, at="2026-01-02T00:00:00Z")
    with pytest.raises(eval_run.JudgeNotValidatedError, match=r"latest: kappa 0.900 over 2 items"):
        eval_run.execute(gig, "answer", "ci")
    assert (calls, count(home, "runs")) == ([], 0)


def test_a_validated_judge_scores_through_its_own_calls(gig: GigContext, home: Path, calls: list) -> None:
    validate(home, kappa=0.8)
    run = run_id(eval_run.execute(gig, "answer", "ci"))

    assert len(calls) == 12 and count(home, "traces", run) == 12  # 6 answers, 6 judge calls
    assert values(home, run, "step:judge") == {"q1": [1.0], "q2": [1.0], "q3": [1.0], "q4": [1.0], "q5": [0.0], "q6": [0.0]}
    assert values(home, run, "gig_eval_fixture.scorers:exact_match") == values(home, run, "step:judge")
    judged = query(home, "SELECT DISTINCT judge_model, judge_prompt_sha256, judge_invocation_id IS NOT NULL FROM scores WHERE scorer_id = 'step:judge'")
    assert judged == [("deterministic-b", contracts.JUDGE_PROMPT_SHA256, 1)]

    validate(home, kappa=0.4, at="2026-02-01T00:00:00Z")  # a newer, failing validation wins
    with pytest.raises(eval_run.JudgeNotValidatedError):
        eval_run.execute(gig, "answer", "ci")


# --- datasets, refusals, privacy -----------------------------------------------------------------------------------


def test_an_absent_private_suite_says_where_to_put_it(gig: GigContext, home: Path, calls: list) -> None:
    with pytest.raises(eval_run.RunRefused, match=r"the release dataset is not there: put it at .*eval-fixture.*\(sha256 [0-9a-f]{64}\)"):
        eval_run.execute(gig, "answer", "release")
    assert calls == []


def test_a_dataset_that_differs_from_its_pinned_sha256_is_refused(gig: GigContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: list) -> None:
    copy = tmp_path / "package"
    copy.mkdir()
    (copy / "tinygsm8k.jsonl").write_bytes(SLICE.read_bytes() + b"\n")
    monkeypatch.setattr(eval_run, "_package_dir", lambda name: copy)

    with pytest.raises(eval_run.RunRefused, match=r"tinygsm8k.jsonl has sha256 [0-9a-f]{64}, not the manifest's d75f87e7"):
        eval_run.execute(gig, "solve", "ci")
    assert calls == []


def test_dataset_digest_prefixes_the_bare_hex() -> None:
    assert eval_run.dataset_digest("ab12") == "sha256:ab12"


def shares_a_fragment(message: str, texts: Iterator[str] | list[str], n: int = 8) -> bool:
    """Whether ``message`` holds any ``n`` consecutive characters of one of ``texts``."""

    return any(text[at : at + n] in message for text in texts for at in range(len(text) - n + 1))


def leaky(trace: contracts.ScoredTrace, reference: object) -> contracts.Score:
    """A scorer that puts the prompt's repr in its error."""

    raise ValueError(f"cannot read {trace.input!r}")


def leaky_input(trace: contracts.ScoredTrace, reference: object) -> contracts.Score:
    raise ValueError(f"bad {trace.input[:30]}")


def leaky_output(trace: contracts.ScoredTrace, reference: object) -> contracts.Score:
    raise ValueError(f"bad output {trace.output}")


def leaky_reference(trace: contracts.ScoredTrace, reference: object) -> contracts.Score:
    raise ValueError(f"ref {reference} for {trace.input[5:30]}")


def bad_value(trace: contracts.ScoredTrace, reference: object) -> contracts.Score:
    """A scorer whose value is the answer itself, which ``score_value`` quotes in its error."""

    return contracts.Score(trace.output)


_RAISED = "failed on input sha256:[0-9a-f]{64}: ValueError: "


@pytest.mark.parametrize(
    ("scorer", "head"),
    [*((name, _RAISED) for name in ("leaky", "leaky_input", "leaky_output", "leaky_reference")), ("bad_value", "on input sha256:[0-9a-f]{64}: score value ")],
)
def test_a_failing_scorer_names_the_input_by_its_sha256_and_prints_no_fragment_of_the_item(gig: GigContext, home: Path, scorer: str, head: str) -> None:
    run = run_id(eval_run.execute(gig, "solve", "ci"))
    with pytest.raises(eval_run.RunFailed) as failure:
        eval_run.rescore(gig, run, [f"tests.behaviors.evals.test_run:{scorer}"])
    found = re.fullmatch(rf"scorer tests\.behaviors\.evals\.test_run:{scorer} {head}(.*<redacted>.*)", str(failure.value), re.S)
    assert found, str(failure.value)
    detail = found.group(1)
    outputs = [s_completion(row["source_ids"]["row"], row["expected"]) for row in ROWS]
    assert not shares_a_fragment(detail, [row["prompt"] for row in ROWS] + outputs)
    assert not any(re.search(rf"(?<!\w){row['expected']}(?!\w)", detail) for row in ROWS)


def test_redaction_keeps_the_message_text_the_item_does_not_hold() -> None:
    private = ("Solve the following math problem: 3 apples", {"expected": "70", "id": "q1"})
    assert eval_run._redact("ValueError at row 70: bad Solve the following math probl", *private) == "ValueError at row <redacted>: bad <redacted>"
    assert eval_run._redact("ref 700 for 'math problem: 3' and 70.", *private) == "ref 700 for '<redacted>' and <redacted>."
    assert eval_run._redact("nothing private here", *private) == "nothing private here"


def test_redaction_reaches_the_repr_and_json_forms_from_8_characters() -> None:
    text = 'Zoë said "no"\nand left the room'
    # Each 8-character quote occurs in one form only: plain, repr, JSON, JSON without ASCII escapes.
    for quoted in ('d "no"\na', ' "no"\\na', "Zo\\u00eb", 'ë said \\'):
        assert eval_run._redact(f"bad <{quoted}>", text) == "bad <<redacted>>"
    assert eval_run._redact("bad <Zoë sai>", text) == "bad <Zoë sai>"


@pytest.mark.parametrize(
    ("error", "refusal", "head"),
    [(RuntimeError, eval_run.RunFailed, r"call 1 \(input sha256:[0-9a-f]{64}\) failed: RuntimeError: "), (KeyError, eval_run.RunRefused, "")],
)
def test_an_adapter_error_quoting_the_prompt_is_redacted(
    gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch, error: type[Exception], refusal: type[Exception], head: str
) -> None:
    def failing(self: DeterministicAdapter, request: Any) -> object:
        raise error(f"upstream refused {request.prompt[3:60]!r}")

    monkeypatch.setattr(DeterministicAdapter, "invoke", failing)
    with pytest.raises(refusal) as failure:
        eval_run.execute(gig, "solve", "ci")
    assert re.fullmatch(rf"{head}upstream refused '<redacted>'", str(failure.value))


def test_a_judge_call_error_quoting_the_answer_and_reference_is_redacted(gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    validate(home, kappa=0.8)
    invoke = DeterministicAdapter.invoke

    def judge_fails(self: DeterministicAdapter, request: Any) -> object:
        fields = dict(re.findall(r"\[BEGIN (\w+) \(\w+\)\]\n(.*?)\n\[END", request.prompt, re.S))
        if fields:  # the judge's prompt: quote its reference and the graded answer
            raise RuntimeError(f"judge refused: expected {fields['CRITERION']} in {fields['SUBMISSION']}")
        return invoke(self, request)

    monkeypatch.setattr(DeterministicAdapter, "invoke", judge_fails)
    with pytest.raises(eval_run.RunFailed) as failure:
        eval_run.execute(gig, "answer", "ci")
    message = str(failure.value)
    # The first judged item is q1, whose reference is "4": too short to be a fragment, redacted as a value.
    assert re.match(r"call 7 \(input sha256:[0-9a-f]{64}\) failed: RuntimeError: judge refused: expected <redacted> in", message)
    assert not re.search(r"(?<!\w)4(?!\w)", message.split("judge refused: ", 1)[1])
    stored = [text for pair in query(home, "SELECT input, output_raw_text FROM traces WHERE step_id = 'answer'") for text in pair]
    assert len(stored) == 12 and not shares_a_fragment(message, stored)


def test_a_failing_postprocess_is_redacted_against_the_output(gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = run_id(eval_run.execute(gig, "solve", "ci"))

    def parse(raw_text: str) -> tuple[object, str]:
        raise ValueError(f"cannot parse {raw_text}")

    monkeypatch.setattr(importlib.import_module("gig_eval_fixture.scorers"), "final_number", parse)
    with pytest.raises(eval_run.RunFailed) as failure:
        eval_run.execute(gig, "solve", "ci")
    assert re.fullmatch(
        r"call 1 \(input sha256:[0-9a-f]{64}\) failed: postprocess failed: ValueError: cannot parse <redacted>", str(failure.value)
    )
    with pytest.raises(eval_run.RunFailed) as replayed:
        eval_run.replay(gig, "solve", run)
    assert re.fullmatch(r"postprocess failed: ValueError: cannot parse <redacted>", str(replayed.value))


@pytest.mark.parametrize("template", ["Solve: {prompt} }", "Solve: {0}", "Solve: {prompt!z}"])
def test_a_prompt_template_that_is_not_a_format_string_is_refused(
    gig: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch, calls: list, template: str
) -> None:
    monkeypatch.setattr(eval_run, "_template", lambda gig, entry: template)
    with pytest.raises(eval_run.RunRefused, match=r"^the prompt template of step solve is not a valid format string: \S"):
        eval_run.execute(gig, "solve", "ci")
    assert calls == []
    assert not evals_db_path(GIG, home).exists() or count(home, "runs") == 0


def retargeted(gig: GigContext, step_id: str, target: str | None) -> GigContext:
    """``gig`` with ``[steps].<step_id>`` bound to ``target`` (``None``: the entry removed)."""

    manifest = copy.deepcopy(dict(gig.manifest))
    if target is None:
        del manifest["steps"][step_id]
    else:
        manifest["steps"][step_id]["target"] = target
    return GigContext(gig.home, gig.name, manifest, gig.log)


def test_a_step_without_a_steps_entry_or_with_an_unknown_target_is_refused(gig: GigContext, home: Path, calls: list) -> None:
    with pytest.raises(eval_run.RunRefused, match=r"step 'solve' is not declared in the gig manifest's \[steps\] table"):
        eval_run.execute(retargeted(gig, "solve", None), "solve", "ci")
    with pytest.raises(eval_run.RunRefused, match=r"no-such-target"):
        eval_run.execute(retargeted(gig, "solve", "no-such-target"), "solve", "ci")
    validate(home, kappa=0.8)
    run = run_id(eval_run.execute(gig, "answer", "ci"))
    unjudged = retargeted(gig, "judge", None)
    with pytest.raises(eval_run.RunRefused, match=r"step 'judge' is not declared"):
        eval_run.replay(unjudged, "answer", run)
    with pytest.raises(eval_run.RunRefused, match=r"step 'judge' is not declared"):
        eval_run.rescore(unjudged, run)
    assert count(home, "runs") == 1
    (home / "config.toml").write_text("[[model_targets]\n", encoding="utf-8")
    with pytest.raises(eval_run.RunRefused, match=r"is not valid UTF-8 TOML"):
        eval_run.execute(gig, "solve", "ci")


def test_a_refused_request_setting_or_schema_mode_leaves_no_run_row(gig: GigContext, home: Path, calls: list) -> None:
    eval_run.execute(gig, "solve", "ci")
    del calls[:]
    with pytest.raises(eval_run.RunRefused, match=r"asks for schema output but its item has no expected\.schema"):
        eval_run.execute(gig, "solve", "ci", settings={"output_mode": "schema"})
    cli = retargeted(gig, "solve", "claude-cli")
    for name in ("temperature", "seed"):
        with pytest.raises(eval_run.RunRefused, match=rf"step 'solve' sets '{name}', which the claude_cli target 'claude-cli' cannot carry"):
            eval_run.execute(cli, "solve", "ci", settings={name: 1})
    assert (calls, count(home, "runs")) == ([], 1)


def test_a_release_run_on_a_non_deterministic_target_needs_a_call_cap(gig: GigContext, home: Path, calls: list) -> None:
    cli = retargeted(gig, "answer", "claude-cli")
    for suite in ("release", "upgrade"):
        with pytest.raises(eval_run.RunRefused, match=rf"^the {suite} suite has no call cap for step\(s\) answer on a non-deterministic target: pass --max-calls N"):
            eval_run.execute(cli, "answer", suite)
    # With a cap, or with deterministic targets only, the run gets as far as the (absent) dataset.
    for capped, limit in ((cli, 5), (gig, None)):
        with pytest.raises(eval_run.RunRefused, match=r"the release dataset is not there"):
            eval_run.execute(capped, "answer", "release", max_calls=limit)
    # The judge a scorer calls counts too.
    with pytest.raises(eval_run.RunRefused, match=r"^the release suite has no call cap for step\(s\) judge on"):
        eval_run.execute(retargeted(gig, "judge", "claude-cli"), "answer", "release")
    assert calls == []


def test_the_cli_prints_counts_and_means_but_no_dataset_text(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    def gigai(*argv: str):
        return _run(tmp_path, site, GIG, "eval", *argv, env_extra={"GIGAI_HOME": str(home)})

    done = gigai("run", "--step", "solve", "--suite", "ci", "--tag", "smoke")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines()[1:] == [
        f"  gigai.evals.contracts:match: {SNAPSHOT_MATCH:.4f} over 10 traces",
        "  gig_eval_fixture.scorers:answer_marker: 0.3000 over 10 traces",
    ]
    capped = gigai("run", "--step", "solve", "--suite", "ci", "--max-calls", "3")
    assert (capped.returncode, capped.stderr) == (2, f"gigai {GIG} eval: cap 3 reached before call 4\n")
    rescored = gigai("rescore", run_id(done.stdout.splitlines()), "--scorer", "includes")
    assert rescored.returncode == 0, rescored.stderr
    assert rescored.stdout.splitlines()[1] == "  includes: 0.8000 over 10 traces"
    mixed = gigai("run", "--step", "solve", "--from-traces", "run_x", "--suite", "ci")
    assert mixed.returncode == 2 and "--from-traces makes no calls; drop --suite" in mixed.stderr
    missing = gigai("run", "--step", "solve")
    assert missing.returncode == 2 and "--suite is required (or --from-traces RUN)" in missing.stderr

    printed = "".join(result.stdout + result.stderr for result in (done, capped, rescored, mixed, missing))
    for row in ROWS:
        assert row["prompt"][:40] not in printed
        assert s_completion(row["source_ids"]["row"], row["expected"]) not in printed
    assert query(home, "SELECT tag FROM runs WHERE run_id = ?", run_id(done.stdout.splitlines())) == [("smoke",)]


def test_the_cli_refuses_a_step_whose_target_the_config_no_longer_has_in_one_line(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)
    config = home / "config.toml"
    config.write_text(config.read_text(encoding="utf-8").replace('"deterministic-a"', '"deterministic-z"'), encoding="utf-8")

    refused = _run(tmp_path, site, GIG, "eval", "run", "--step", "solve", "--suite", "ci", env_extra={"GIGAI_HOME": str(home)})
    assert refused.returncode == 2
    assert re.fullmatch(rf"gigai {GIG} eval: [^\n]*'deterministic-a'[^\n]*\n", refused.stderr)
    assert not evals_db_path(GIG, home).exists() or count(home, "runs") == 0
