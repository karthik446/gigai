"""``gigai <gig> eval ablate``: one pre-registered ablation = one run per design cell, then the level comparisons.

The fixture gig's ``solve`` step runs on the 10-row ci slice through the deterministic adapter, made temperature-
sensitive here (``DeterministicAdapter.invoke`` monkeypatched: at temperature 1.0 every answer is wrong). The plan is
``tests/fixtures/evals/prereg.toml``: step solve, suite ci, metric match, the three temperature pairs registered.
``deterministic-b`` answers the same slice without a mistake (``responses.json``), so the ``model`` axis moves between
two offline targets; ``claude`` is the shell script of ``test_ctx_model`` when a paid target is needed. No socket
leaves 127.0.0.1; no paid call is made.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from itertools import combinations
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from gigai.adapters.deterministic import DeterministicAdapter
from gigai.canonical import digest_imported_bytes
from gigai.evals import ablate as eval_ablate
from gigai.evals import designs, prereg
from gigai.evals import run as eval_run
from gigai.evals.records import evals_db_path, open_evals_db
from gigai.sdk import GigContext
from gigai.steps import set_override
from tests.behaviors.evals.test_run import GIG, calls, gig, home, query, validate
from tests.behaviors.gigs.test_loader import _add_fixture, _run
from tests.behaviors.sdk.test_ctx_model import MANIFEST, _fake_claude

assert calls and gig and home  # the fixtures of test_run, imported to apply here

PREREG = Path(__file__).resolve().parents[2] / "fixtures" / "evals" / "prereg.toml"
ROOT = PREREG.parents[3]
WRONG = "ANSWER: -1"
ANSWER_PLAN = """\
id = "answer-judges"
registered_at = "2026-11-10"
step = "answer"
suite = "ci"
n_planned = 6
primary_metric = "exact_match"

[axes.judge_model]
levels = ["deterministic-b", "deterministic-a"]

[[comparisons]]
axis = "judge_model"
levels = ["deterministic-b", "deterministic-a"]
"""


@pytest.fixture
def hot(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The deterministic adapter answering wrongly at temperature 1.0; every request it saw."""

    seen: list[Any] = []
    invoke = DeterministicAdapter.invoke

    def sensitive(self: DeterministicAdapter, request: Any) -> Any:
        seen.append(request)
        result = invoke(self, request)
        return dataclasses.replace(result, output_text=WRONG) if request.temperature == 1.0 else result

    monkeypatch.setattr(DeterministicAdapter, "invoke", sensitive)
    return seen


def runs(home: Path) -> list[tuple[str, str, int, dict[str, Any]]]:
    if not evals_db_path(GIG, home).exists():
        return []
    rows = query(home, "SELECT run_id, tag, calls_made, prereg FROM runs ORDER BY created_at, rowid")
    return [(run, tag, made, json.loads(block)) for run, tag, made, block in rows]


def plan(tmp_path: Path, text: str, *replacements: tuple[str, str]) -> Path:
    """``tmp_path/prereg.toml`` holding ``text`` with each ``(old, new)`` applied (each must match)."""

    for old, new in replacements:
        assert old in text, old
        text = text.replace(old, new)
    path = tmp_path / "prereg.toml"
    path.write_text(text, encoding="utf-8")
    return path


def model_plan(tmp_path: Path, *replacements: tuple[str, str]) -> Path:
    """The fixture plan under id ``solve-models`` with its three temperature comparisons replaced by the model pair."""

    head, _ = PREREG.read_text(encoding="utf-8").split("[[comparisons]]", 1)
    text = head + '[[comparisons]]\naxis = "model"\nlevels = ["deterministic-a", "deterministic-b"]\n'
    return plan(tmp_path, text, ('id = "solve-sampling"', 'id = "solve-models"'), *replacements)


def invoke(gig: GigContext, *argv: str) -> Any:
    return CliRunner().invoke(eval_ablate.ablate_command, list(argv), obj=gig, catch_exceptions=False)


def test_ofat_over_temperature_writes_three_runs_each_carrying_the_registration(gig: GigContext, home: Path, hot: list[Any]) -> None:
    result = eval_ablate.ablate(gig, ["temperature"], "ofat", PREREG)

    stored = runs(home)
    assert [block["cell"] for *_, block in stored] == [{"temperature": 0.0}, {"temperature": 0.5}, {"temperature": 1.0}]
    assert [run["run_id"] for run in result["runs"]] == [run for run, *_ in stored]
    assert [made for _, _, made, _ in stored] == [10, 10, 10] and len(hot) == 30
    assert sorted({request.temperature for request in hot}) == [0.0, 0.5, 1.0]
    for _, tag, _, block in stored:
        assert tag.startswith("ablate:solve-sampling:ofat:")
        assert block["registered_at"] == "2026-11-10"
        assert block["ablation"] == "solve-sampling"
        assert block["prereg_sha256"] == digest_imported_bytes(PREREG.read_bytes()) == result["prereg_sha256"]
        assert (block["primary_metric"], block["n_planned"], block["design"], block["alpha"]) == ("match", 10, "ofat", 0.05)
        assert block["axes"] == {"temperature": [0.0, 0.5, 1.0]}
        assert block["comparisons"] == [{"axis": "temperature", "levels": pair} for pair in ([0.0, 0.5], [0.0, 1.0], [0.5, 1.0])]
    assert result["capped"] is None and result["design"] == "ofat"


def test_the_registered_pairs_are_holm_corrected_and_every_null_shows_its_mde(gig: GigContext, home: Path, hot: list[Any]) -> None:
    result = eval_ablate.ablate(gig, ["temperature"], "ofat", PREREG)

    by_pair = {(c["a"], c["b"]): c for c in result["comparisons"]}
    assert set(by_pair) == {(0.0, 0.5), (0.0, 1.0), (0.5, 1.0)} and all(c["registered"] for c in by_pair.values())
    same, worse, worse_too = by_pair[(0.0, 0.5)], by_pair[(0.0, 1.0)], by_pair[(0.5, 1.0)]
    assert (same["mean_a"], same["mean_b"], same["difference"], same["p"], same["reject"]) == (0.8, 0.8, 0.0, 1.0, False)
    assert same["test"] == "mcnemar" and same["p_adjusted"] == 1.0
    assert (worse["mean_a"], worse["mean_b"]) == (0.8, 0.0) and worse["difference"] == pytest.approx(-0.8)
    assert worse["mcnemar"]["b"] == 8 and worse["mcnemar"]["c"] == 0
    assert worse["p"] == pytest.approx(2 * 0.5**8) and worse["p_adjusted"] == pytest.approx(3 * 2 * 0.5**8)
    assert worse["reject"] is True and worse_too["reject"] is True
    # Holm: the adjusted p-values are at least the raw ones, monotone in the raw order, and within one.
    ordered = sorted(by_pair.values(), key=lambda c: c["p"])
    assert all(c["p_adjusted"] >= c["p"] for c in ordered) and all(c["p_adjusted"] <= 1.0 for c in ordered)
    assert all(left["p_adjusted"] <= right["p_adjusted"] for left, right in zip(ordered, ordered[1:], strict=False))
    # A null result shows its MDE at the arms' n; a rejection shows it too (the MDE is per test, not per outcome).
    assert same["mde"]["n"] == 10 and same["mde"]["mde"] is None and same["mde"]["discordance"] == 0.0
    assert worse["mde"]["n"] == 10 and worse["mde"]["mde"] > 0

    lines = eval_ablate.report(result)
    assert lines[0].startswith("ablate solve-sampling: design ofat, step solve, suite ci, 10 items x 1, 3 runs, prereg sha256:")
    assert lines[0].endswith("registered 2026-11-10, alpha 0.05 two-sided")
    assert [line.split(": ")[1] for line in lines[1:4]] == ["temperature=0.0, 10 calls", "temperature=0.5, 10 calls", "temperature=1.0, 10 calls"]
    assert lines[4].startswith("  temperature 0.0 -> 0.5 (registered): match 0.8000 -> 0.8000, difference +0.0000")
    assert "McNemar p=1.0000, holm p=1.0000, no effect detected, MDE none at n=10" in lines[4]
    assert lines[5].startswith("  temperature 0.0 -> 1.0 (registered): match 0.8000 -> 0.0000, difference -0.8000")
    assert "holm p=0.0234, rejected, MDE 0." in lines[5]


def test_a_second_run_of_the_same_plan_is_fine_but_a_changed_file_is_refused(gig: GigContext, home: Path, hot: list[Any], tmp_path: Path) -> None:
    plan = tmp_path / "prereg.toml"
    shutil.copy(PREREG, plan)
    eval_ablate.ablate(gig, ["temperature"], "ofat", plan)
    eval_ablate.ablate(gig, ["temperature"], "ofat", plan)
    assert len(runs(home)) == 6

    plan.write_bytes(plan.read_bytes() + b"\n# looked at the data first\n")
    with pytest.raises(eval_run.RunRefused, match=r"^pre-registration changed: ablation 'solve-sampling' was registered as sha256:[0-9a-f]{64}, .* now hashes sha256:[0-9a-f]{64}; register a changed plan under a new id$"):
        eval_ablate.ablate(gig, ["temperature"], "ofat", plan)
    assert len(runs(home)) == 6 and len(hot) == 60

    # A changed plan under a new id is a new study.
    plan.write_bytes(plan.read_bytes().replace(b'id = "solve-sampling"', b'id = "solve-sampling-2"'))
    eval_ablate.ablate(gig, ["temperature"], "ofat", plan)
    assert len(runs(home)) == 9


def test_l9_over_four_axes_writes_nine_orthogonal_runs(gig: GigContext, home: Path, hot: list[Any]) -> None:
    result = eval_ablate.ablate(gig, ["temperature", "top_p", "output_mode", "prompt"], "l9", PREREG)

    stored = runs(home)
    assert len(stored) == 9 and all(made == 10 for _, _, made, _ in stored) and len(hot) == 90
    cells = [block["cell"] for *_, block in stored]
    assert all(set(cell) == {"temperature", "top_p", "output_mode", "prompt"} for cell in cells)
    for left, right in combinations(("temperature", "top_p", "output_mode", "prompt"), 2):
        pairs = [(cell[left], cell[right]) for cell in cells]
        assert len(set(pairs)) == 9, (left, right, pairs)  # every level pair of two columns exactly once
    assert all(block["design"] == "l9" for *_, block in stored)
    # Each level's arm is its three runs; the registered temperature pairs are corrected, the other pairs exploratory.
    for c in result["comparisons"]:
        assert len(c["runs_a"]) == 3 and len(c["runs_b"]) == 3 and c["items"] == 10
        assert c["registered"] == (c["axis"] == "temperature")
        assert (c["p_adjusted"] is not None) == c["registered"]
    assert len(result["comparisons"]) == 12
    # The prompt and output_mode levels reached the adapter: three templates, the schema sent with its level.
    assert {request.prompt.split("\n")[0] for request in hot} >= {"Be brief.", "Think step by step."}
    assert {request.output_mode for request in hot} == {"text", "json", "schema"}
    assert all(request.schema is not None for request in hot if request.output_mode == "schema")


def test_l9_refuses_axes_that_do_not_fit() -> None:
    three = {"a": (1, 2, 3), "b": (1, 2, 3)}
    assert len(designs.design("l9", three).cells) == 9
    with pytest.raises(designs.DesignError, match="two to four axes, not 1"):
        designs.design("l9", {"a": (1, 2, 3)})
    with pytest.raises(designs.DesignError, match="exactly three levels per axis; axis b has 2"):
        designs.design("l9", {"a": (1, 2, 3), "b": (1, 2)})
    with pytest.raises(designs.DesignError, match="at least two"):
        designs.design("ofat", {"a": (1,)})
    ofat = designs.design("ofat", three)
    assert ofat.cells == [{"a": 1, "b": 1}, {"a": 2, "b": 1}, {"a": 3, "b": 1}, {"a": 1, "b": 2}, {"a": 1, "b": 3}]
    assert ofat.arm("a", 1) == [0] and ofat.arm("a", 2) == [1] and ofat.arm("b", 3) == [4]
    l9 = designs.design("l9", {"a": (1, 2, 3), "b": (1, 2, 3), "c": (1, 2, 3), "d": (1, 2, 3)})
    assert all(len(l9.arm(axis, level)) == 3 for axis in "abcd" for level in (1, 2, 3))


def test_an_axis_the_steps_adapter_does_not_carry_is_refused_before_any_run(gig: GigContext, home: Path, hot: list[Any]) -> None:
    set_override(home, GIG, MANIFEST, "solve", "claude-cli")
    with pytest.raises(eval_run.RunRefused, match=r"^axis unsupported by adapter claude_cli: temperature \(target carries output_mode, schema, system\)$"):
        eval_ablate.ablate(gig, ["temperature"], "ofat", PREREG)
    assert runs(home) == [] and hot == []
    # output_mode is carried by every adapter, so it is not what refuses here: a CLI target is refused for not running.
    assert eval_ablate._request_fields("claude_cli") == {"output_mode", "schema", "system"}


@pytest.mark.parametrize(
    ("axis", "reason"),
    [
        ("reasoning_effort", "axis reasoning_effort cannot move per run yet: reasoning_effort is not a request setting"),
        ("max_output_tokens", "axis max_output_tokens cannot move per run yet: max_output_tokens is not a request setting"),
        ("timeout", "axis timeout cannot move per run yet: timeout_seconds is not a request setting"),
        ("colour", "unknown axis 'colour': one of model, prompt, temperature"),
    ],
)
def test_a_knob_a_run_cannot_move_yet_is_refused_up_front(gig: GigContext, home: Path, hot: list[Any], axis: str, reason: str) -> None:
    with pytest.raises(eval_run.RunRefused, match=reason):
        eval_ablate.ablate(gig, [axis], "ofat", PREREG)
    assert runs(home) == [] and hot == []
    text = " ".join(invoke(gig, "--help").output.split())
    assert "model, prompt, temperature, output_mode, judge_model, top_p, few_shot, context, retry_policy." in text
    assert "Not yet (they need core request fields): reasoning_effort, max_output_tokens, timeout." in text


def test_the_model_axis_binds_the_step_to_each_registered_target_per_run(gig: GigContext, home: Path, hot: list[Any], tmp_path: Path) -> None:
    result = eval_ablate.ablate(gig, ["model"], "ofat", model_plan(tmp_path))

    stored = runs(home)
    assert [block["cell"] for *_, block in stored] == [{"model": "deterministic-a"}, {"model": "deterministic-b"}]
    assert [request.target_name for request in hot] == ["deterministic-a"] * 10 + ["deterministic-b"] * 10
    assert query(home, "SELECT DISTINCT model_id FROM traces ORDER BY model_id") == [("deterministic-a",), ("deterministic-b",)]
    [c] = result["comparisons"]
    assert (c["axis"], c["a"], c["b"], c["registered"], c["mean_a"], c["mean_b"]) == ("model", "deterministic-a", "deterministic-b", True, 0.8, 1.0)
    assert not (home / "gigs" / GIG / "steps.toml").exists()  # the binding moved per run, nothing was written

    # A target config.toml lacks is refused before any run row; a model level and a setting the target cannot carry too.
    unknown = model_plan(tmp_path, ('"deterministic-b"', '"deterministic-z"'), ('id = "solve-models"', 'id = "s2"'))
    with pytest.raises(eval_run.RunRefused, match="deterministic-z"):
        eval_ablate.ablate(gig, ["model"], "ofat", unknown)
    cli = model_plan(tmp_path, ('"deterministic-b"', '"claude-cli"'), ('id = "solve-models"', 'id = "s3"'))
    with pytest.raises(eval_run.RunRefused, match=r"^axis unsupported by adapter claude_cli: temperature"):
        eval_ablate.ablate(gig, ["model", "temperature"], "ofat", cli)
    assert len(runs(home)) == 2 and len(hot) == 20


def test_the_judge_model_axis_needs_every_judge_level_validated_and_moves_the_judge(gig: GigContext, home: Path, hot: list[Any], tmp_path: Path) -> None:
    answer = plan(tmp_path, ANSWER_PLAN)
    validate(home, kappa=0.9)  # deterministic-b, the manifest's judge
    with pytest.raises(eval_run.JudgeNotValidatedError, match=r"^scorer step:judge refused: judge deterministic-a with prompt sha256:[0-9a-f]{64} needs a validation"):
        eval_ablate.ablate(gig, ["judge_model"], "ofat", answer)
    assert runs(home) == [] and hot == []

    connection = open_evals_db(GIG, home)
    try:
        connection.execute(
            "INSERT INTO judge_validations SELECT 'validation_a', scorer_id, scorer_version, 'deterministic-a', judge_prompt_sha256, dataset_id,"
            " dataset_sha256, n_items, metric, value, details, recorded_at FROM judge_validations WHERE judge_model = 'deterministic-b'"
        )
        connection.commit()
    finally:
        connection.close()
    result = eval_ablate.ablate(gig, ["judge_model"], "ofat", answer)

    stored = runs(home)
    assert [block["cell"] for *_, block in stored] == [{"judge_model": "deterministic-b"}, {"judge_model": "deterministic-a"}]
    judged = [(request.target_name) for request in hot if request.prompt.startswith("\nYou are assessing")]
    assert judged == ["deterministic-b"] * 6 + ["deterministic-a"] * 6
    assert {request.target_name for request in hot if not request.prompt.startswith("\nYou are assessing")} == {"deterministic-a"}
    [c] = result["comparisons"]
    assert (c["axis"], c["a"], c["b"], c["registered"], c["p"]) == ("judge_model", "deterministic-b", "deterministic-a", True, 1.0)
    # The solve step has no judge scorer: judge_model has nothing to move there.
    with pytest.raises(eval_run.RunRefused, match="^axis judge_model: step solve has no judge scorer; it moves one step:<judge> scorer's target$"):
        eval_ablate.ablate(gig, ["judge_model"], "ofat", PREREG)


def test_a_one_sided_registration_halves_p_on_its_side_and_mirrors_it_on_the_other(gig: GigContext, home: Path, hot: list[Any], tmp_path: Path) -> None:
    text = PREREG.read_text(encoding="utf-8")
    less = eval_ablate.ablate(gig, ["temperature"], "ofat", plan(tmp_path, text, ('direction = "two-sided"', 'direction = "less"')))
    greater = eval_ablate.ablate(gig, ["temperature"], "ofat", plan(tmp_path, text, ('direction = "two-sided"', 'direction = "greater"'), ('id = "solve-sampling"', 'id = "s2"')))
    two_sided = eval_ablate.ablate(gig, ["temperature"], "ofat", plan(tmp_path, text, ('id = "solve-sampling"', 'id = "s3"')))

    drop = {(c["a"], c["b"]): c["p"] for c in two_sided["comparisons"]}[(0.0, 1.0)]
    assert 0 < drop < 0.05
    assert {(c["a"], c["b"]): c["p"] for c in less["comparisons"]}[(0.0, 1.0)] == pytest.approx(drop / 2)
    assert {(c["a"], c["b"]): c["p"] for c in greater["comparisons"]}[(0.0, 1.0)] == pytest.approx(1 - drop / 2)
    assert (less["direction"], greater["direction"]) == ("less", "greater")
    assert eval_ablate.report(less)[0].endswith("alpha 0.05 less")


def test_the_plan_command_runs_fully_under_max_calls_zero_because_offline_calls_are_free(gig: GigContext, home: Path, hot: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(ROOT)
    result = invoke(gig, "--axis", "temperature", "--design", "ofat", "--prereg", "tests/fixtures/evals/prereg.toml", "--max-calls", "0")

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    assert len(lines) == 7 and [line.split(": ")[1] for line in lines[1:4]] == ["temperature=0.0, 10 calls", "temperature=0.5, 10 calls", "temperature=1.0, 10 calls"]
    assert "holm p=1.0000, no effect detected" in lines[4] and "holm p=0.0234, rejected" in lines[5] and "holm p=0.0234, rejected" in lines[6]
    assert [made for _, _, made, _ in runs(home)] == [10, 10, 10] and len(hot) == 30


def test_the_call_cap_counts_paid_calls_only_and_stops_the_ablation_where_it_is_reached(
    gig: GigContext, home: Path, hot: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    record = _fake_claude(tmp_path / "bin")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    cli = model_plan(tmp_path, ('"deterministic-b"', '"claude-cli"'))

    # Cap 0: the offline cell runs fully, the paid cell refuses its first call before the adapter; exit 2.
    result = invoke(gig, "--axis", "model", "--prereg", str(cli), "--max-calls", "0", "--json")
    assert result.exit_code == 2 and result.stderr == f"gigai {GIG} eval: cap 0 reached before paid call 1\n"
    parsed = json.loads(result.stdout)
    assert [run["calls"] for run in parsed["runs"]] == [10, 0] and parsed["capped"] == "cap 0 reached before paid call 1"
    [c] = parsed["comparisons"]
    assert c["p"] is None and not record.exists()

    # Cap 3: the paid cell makes three calls, then stops; the row keeps them.
    result = invoke(gig, "--axis", "model", "--prereg", str(cli), "--max-calls", "3", "--json")
    assert result.exit_code == 2 and result.stderr == f"gigai {GIG} eval: cap 3 reached before paid call 4\n"
    parsed = json.loads(result.stdout)
    assert [run["calls"] for run in parsed["runs"]] == [10, 3]
    assert record.read_bytes().count(b"--output-format\0") == 3
    assert query(home, "SELECT model_id, count(*) FROM traces GROUP BY model_id ORDER BY model_id") == [("claude-cli", 3), ("deterministic-a", 20)]
    assert len(hot) == 20  # the deterministic calls, all free

    # Unlimited: a paid run through the fake completes and the comparison is measured.
    result = invoke(gig, "--axis", "model", "--prereg", str(cli), "--json")
    assert result.exit_code == 0, result.output
    assert [run["calls"] for run in json.loads(result.stdout)["runs"]] == [10, 10]
    [c] = json.loads(result.stdout)["comparisons"]
    assert c["p"] is not None


def test_the_plan_must_fit_the_step_and_the_run(gig: GigContext, home: Path, hot: list[Any], tmp_path: Path) -> None:
    text = PREREG.read_text(encoding="utf-8")
    plan = tmp_path / "prereg.toml"

    plan.write_text(text.replace("n_planned = 10", "n_planned = 12"), encoding="utf-8")
    with pytest.raises(eval_run.RunRefused, match="^pre-registration plans 12 items per run but the ci suite of step solve has 10$"):
        eval_ablate.ablate(gig, ["temperature"], "ofat", plan)
    plan.write_text(text.replace('primary_metric = "match"', 'primary_metric = "bleu"'), encoding="utf-8")
    with pytest.raises(eval_run.RunRefused, match="^primary metric 'bleu' is not a scorer of step solve$"):
        eval_ablate.ablate(gig, ["temperature"], "ofat", plan)
    plan.write_text(text.replace("levels = [0.0, 0.5]", "levels = [0.0, 0.7]"), encoding="utf-8")
    with pytest.raises(eval_run.RunRefused, match="compares temperature 0.0 with 0.7, which this ablation does not run"):
        eval_ablate.ablate(gig, ["temperature"], "ofat", plan)
    plan.write_text("hypothesis = 'free text'\n" + text, encoding="utf-8")
    with pytest.raises(eval_run.RunRefused, match="unknown key\\(s\\) hypothesis"):
        eval_ablate.ablate(gig, ["temperature"], "ofat", plan)
    with pytest.raises(eval_run.RunRefused, match="cannot read pre-registration"):
        eval_ablate.ablate(gig, ["temperature"], "ofat", tmp_path / "missing.toml")
    with pytest.raises(eval_run.RunRefused, match="axes prompt and few_shot both set the step's prompt template"):
        eval_ablate.ablate(gig, ["prompt", "few_shot"], "ofat", PREREG)
    with pytest.raises(eval_run.RunRefused, match="axis few_shot has no default levels"):
        eval_ablate.ablate(gig, ["few_shot"], "ofat", PREREG)
    assert runs(home) == [] and hot == []


def test_the_registration_is_parsed_strictly(tmp_path: Path) -> None:
    plan = tmp_path / "prereg.toml"
    plan.write_text(PREREG.read_text(encoding="utf-8"), encoding="utf-8")
    registration = prereg.load_registration(plan)
    assert (registration.id, registration.step, registration.suite, registration.k, registration.alpha) == ("solve-sampling", "solve", "ci", 1, 0.05)
    assert registration.axes["prompt"] == ("prompts/solve.txt", "prompts/solve-terse.txt", "prompts/solve-steps.txt")
    assert registration.schemas == {"output_mode": {"type": "object", "properties": {"answer": {"type": "string"}}}}
    assert [(c.axis, c.a, c.b) for c in registration.comparisons] == [("temperature", 0.0, 0.5), ("temperature", 0.0, 1.0), ("temperature", 0.5, 1.0)]
    for broken, reason in [
        ('suite = "ci"', 'suite = "nightly"'),
        ("alpha = 0.05", "alpha = 1.5"),
        ('direction = "two-sided"', 'direction = "up"'),
        ("k = 1", "k = 0"),
        ('[axes.top_p]\nlevels = [0.1, 0.5, 1.0]', '[axes.top_p]\nlevels = [0.1]'),
        ('axis = "temperature"\nlevels = [0.5, 1.0]', 'axis = "temperature"\nlevels = [0.5, 0.5]'),
    ]:
        plan.write_text(PREREG.read_text(encoding="utf-8").replace(broken, reason), encoding="utf-8")
        with pytest.raises(prereg.PreregError):
            prereg.load_registration(plan)


def test_the_cli_runs_the_plan_offline_and_prints_no_dataset_text(tmp_path: Path, home: Path) -> None:
    site = tmp_path / "site"
    _add_fixture(site, GIG)

    def gigai(*argv: str):
        return _run(tmp_path, site, GIG, "eval", "ablate", *argv, env_extra={"GIGAI_HOME": str(home)})

    # The plan's own command: every call is on a deterministic target, so a cap of 0 paid calls stops nothing.
    full = gigai("--axis", "temperature", "--design", "ofat", "--prereg", str(PREREG), "--max-calls", "0")
    assert (full.returncode, full.stderr) == (0, ""), full.stdout
    lines = full.stdout.splitlines()
    assert len(lines) == 7 and lines[0].startswith("ablate solve-sampling: design ofat, step solve, suite ci, 10 items x 1, 3 runs")
    assert [line.split(": ")[1] for line in lines[1:4]] == ["temperature=0.0, 10 calls", "temperature=0.5, 10 calls", "temperature=1.0, 10 calls"]
    assert all("holm p=1.0000, no effect detected, MDE none at n=10" in line for line in lines[4:])
    stored = runs(home)
    assert [made for _, _, made, _ in stored] == [10, 10, 10] and all(block["registered_at"] == "2026-11-10" for *_, block in stored)
    assert query(home, "SELECT count(*) FROM traces") == [(30,)]

    as_json = gigai("--axis", "model", "--prereg", str(model_plan(tmp_path)), "--json")
    assert as_json.returncode == 0, as_json.stderr
    result = json.loads(as_json.stdout)
    assert [run["calls"] for run in result["runs"]] == [10, 10] and result["capped"] is None
    assert [(c["a"], c["b"], c["mean_a"], c["mean_b"]) for c in result["comparisons"]] == [("deterministic-a", "deterministic-b", 0.8, 1.0)]

    printed = full.stdout + as_json.stdout
    for request_text in ("Carly", "cupcakes", "ANSWER:", "So the total"):
        assert request_text not in printed  # prompt text and answers never reach the terminal

    usage = gigai("--design", "ofat", "--prereg", str(PREREG))
    assert usage.returncode == 2 and "--axis" in usage.stderr


def test_importing_the_cli_does_not_import_the_ablation_modules() -> None:
    import subprocess
    import sys

    done = subprocess.run(
        [sys.executable, "-c", "import sys, gigai.cli; print(sorted(m for m in sys.modules if m.startswith('gigai.evals')))"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert not any(name in done.stdout for name in ("gigai.evals.ablate", "gigai.evals.designs", "gigai.evals.prereg"))
