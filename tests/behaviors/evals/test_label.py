"""``gigai <gig> eval label``: the blind met / not met / unknown page, and the labels JSONL format it exports and imports.

Runs are recorded by hand (two arms of the fixture gig's ``solve`` step over five items) with run ids, tags and model
call fields that are each unique strings, so a test can assert none of them reaches the page. The page reads its
answers from stdin (``CliRunner(input=...)``); no pty, no model call.
"""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import stat
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest
from click.testing import CliRunner, Result

from gigai.evals import label as eval_label
from gigai.evals import records
from gigai.evals.label import LABEL_KEYS, PAGE_RUBRIC, label_command, selected_rows, validate_batch
from gigai.evals.verbs import EVAL_VERBS
from gigai.sdk import GigContext
from tests.behaviors.evals.test_run import GIG, home  # noqa: F401 - fixture
from tests.behaviors.gigs.test_loader import _add_fixture, _run
from tests.behaviors.sdk.test_ctx_model import MANIFEST

STEP = "solve"
FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "evals" / "labels_10.jsonl"
FORMAT_DOC = Path(eval_label.__file__).with_name("labels_format.md")
ARM_A, ARM_B = "run_label-a", "run_label-b"
ITEMS = [f"item-0{n}" for n in range(1, 6)]
SECRET = "SECRET-TEXT"
WHO = "page-tester"


def model_call(run_id: str) -> records.ModelCall:
    arm = run_id.removeprefix("run_label-")
    return records.ModelCall(
        f"target-{arm}-x", f"provider-{arm}-x", f"https://endpoint-{arm}.invalid/v1", f"Adapter{arm}X",
        f"modelname-{arm}-x", f"resolved-{arm}-x", None, (), records.Settings(), {}, None, None, None,
    )  # fmt: skip


def identities(run_id: str) -> list[str]:
    """Every string that would tell a labeller which run, arm, model or target gave a row."""

    call = model_call(run_id)
    return [run_id, f"tag-{run_id}", call.model_id, call.model_provider, call.model_endpoint, call.model_adapter,
            call.model_name, call.resolved_model]  # fmt: skip


def question(item: str) -> str:
    return f"Question {item}: how many apples are left?"


def reply(run_id: str, item: str) -> str:
    return f"The total is {'1' if run_id == ARM_A else '2'}{item[-1]} apples."


def seed(
    home: Path,
    runs: Sequence[str] = (ARM_A, ARM_B),
    items: Mapping[str, Sequence[str]] | None = None,
    scores: Mapping[tuple[str, str], Mapping[str, Sequence[float]]] | None = None,
) -> None:
    """Record ``runs`` of step ``solve`` on ``items`` (default: all five each); ``scores[(run, item)][scorer]`` is the
    scorer's values on the row's trace, written in order (the last is the latest)."""

    connection = records.open_evals_db(GIG, home)
    try:
        for run_id in runs:
            dataset = records.Dataset("tinygsm8k.jsonl", "1", "sha256:" + "0" * 64, {"count": 5})
            run = records.RunRecord(run_id, GIG, "sha256:m", dataset, None, STEP, "ci", f"tag-{run_id}", 1)
            records.write_run(connection, run)
            for item in (items or {}).get(run_id, ITEMS):
                trace = records.TraceRecord(
                    f"trace_{run_id}_{item}", run_id, STEP, records.ItemRef(item, "2"), question(item),
                    reply(run_id, item), model=model_call(run_id),
                )  # fmt: skip
                records.write_trace(connection, trace)
                for scorer, values in (scores or {}).get((run_id, item), {}).items():
                    for number, value in enumerate(values):
                        score = records.ScoreRecord(
                            f"score_{run_id}_{item}_{scorer}_{number}", trace.trace_id, scorer, "1", value,
                            recorded_at=f"2026-02-01T00:00:0{number}.000Z",
                        )  # fmt: skip
                        records.write_score(connection, score)
    finally:
        connection.close()


@pytest.fixture(autouse=True)
def labeller(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USER", WHO)
    monkeypatch.setenv("LOGNAME", "someone-else")


@pytest.fixture
def ctx(home: Path) -> GigContext:  # noqa: F811 - pytest fixture argument
    return GigContext(home, GIG, MANIFEST, logging.getLogger("gigai.gig.eval-fixture"))


def label(ctx: GigContext, *argv: str, step: str | None = STEP, input: str | None = None) -> Result:
    args = (["--step", step] if step is not None else []) + list(argv)
    return CliRunner().invoke(label_command, args, obj=ctx, input=input)


def answers(*pairs: tuple[str, str]) -> str:
    return "".join(f"{answer}\n{critique}\n" for answer, critique in pairs)


def labels(home: Path) -> list[dict[str, object]]:
    connection = sqlite3.connect(records.evals_db_path(GIG, home))
    connection.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in connection.execute("SELECT * FROM labels ORDER BY rowid")]
    finally:
        connection.close()


def fixture_rows() -> list[dict[str, object]]:
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


def batch(tmp_path: Path, lines: Sequence[str], name: str = "batch.jsonl") -> Path:
    path = tmp_path / name
    path.write_text("".join(line + "\n" for line in lines), encoding="utf-8")
    return path


def as_format(row: Mapping[str, object]) -> dict[str, object]:
    """A stored label as a labels JSONL row."""

    found = {key: row[key] for key in LABEL_KEYS}
    found["label"] = json.loads(str(row["label"]))
    return found


# --- --from and --export -----------------------------------------------------------------------------------------


def test_from_imports_the_ten_rows_blind_with_their_rubric(ctx: GigContext, home: Path) -> None:
    seed(home)

    done = label(ctx, "--from", str(FIXTURE))

    assert done.exit_code == 0, done.output
    assert done.stdout == "imported 10 labels of step solve (0 already stored)\n"
    stored = labels(home)
    assert len(stored) == 10
    assert sorted((as_format(row) for row in stored), key=lambda r: (r["run_id"], r["item_id"])) == sorted(
        fixture_rows(), key=lambda r: (r["run_id"], r["item_id"])
    )
    for row in stored:
        assert row["blind"] == 1 and row["rubric"] == "gigai-met-1" and row["source"] == "human"
        assert row["dataset_id"] == "tinygsm8k.jsonl" and row["item_version"] == "2"
        assert row["relabel_of"] is None and str(row["label_id"]).startswith("label_")
        assert str(row["recorded_at"]).endswith("Z")
    assert {json.loads(str(row["label"])) for row in stored} == {"met", "not met", "unknown"}


def test_export_writes_sorted_rows_of_the_format_and_reimporting_them_is_a_no_op(
    ctx: GigContext, home: Path, tmp_path: Path
) -> None:
    seed(home)
    assert label(ctx, "--from", str(FIXTURE)).exit_code == 0
    before = labels(home)
    out = tmp_path / "out.jsonl"

    done = label(ctx, "--export", str(out))

    assert done.exit_code == 0, done.output
    assert done.stdout == f"exported 10 labels of step solve to {out}\n"
    expected = sorted(fixture_rows(), key=lambda r: (r["run_id"], r["item_id"], r["step_id"], r["who"], r["at"], r["round"]))
    assert out.read_text(encoding="utf-8") == "".join(json.dumps(r, sort_keys=True, ensure_ascii=False) + "\n" for r in expected)
    assert "naïve" in out.read_text(encoding="utf-8")
    assert stat.S_IMODE(out.stat().st_mode) == 0o600
    assert [list(json.loads(line)) for line in out.read_text().splitlines()] == [sorted(LABEL_KEYS)] * 10

    again = label(ctx, "--from", str(out))
    twice = label(ctx, "--from", str(FIXTURE))

    assert again.exit_code == 0 and again.stdout == "imported 0 labels of step solve (10 already stored)\n"
    assert twice.exit_code == 0 and twice.stdout == "imported 0 labels of step solve (10 already stored)\n"
    assert labels(home) == before


def test_an_input_key_refuses_the_whole_batch_and_is_named(ctx: GigContext, home: Path, tmp_path: Path) -> None:
    seed(home)
    lines = FIXTURE.read_text(encoding="utf-8").splitlines()
    poisoned = json.loads(lines[6]) | {"input": f"{SECRET} Question item-05: how many apples are left?"}
    path = batch(tmp_path, [*lines[:6], json.dumps(poisoned), *lines[7:]])

    done = label(ctx, "--from", str(path))

    assert done.exit_code == 2 and done.stdout == ""
    assert done.stderr.splitlines() == [
        "gigai eval-fixture eval label: line 7: key 'input' is not in the labels format"
        " (keys: run_id, item_id, step_id, label, critique, rubric, who, at, round)"
    ]
    assert SECRET not in done.stderr and "apples" not in done.stderr
    assert labels(home) == []


def _with(**changes: object) -> str:
    row = fixture_rows()[1] | {"at": "2026-01-31T10:00:00Z"}
    return json.dumps(row | changes)


def _without(key: str) -> str:
    row = fixture_rows()[1]
    del row[key]
    return json.dumps(row)


MALFORMED = {
    "missing key": (_without("critique"), "line 11: missing key critique"),
    "label one letter off": (_with(label="mett"), "line 11: label must be one of met, not met, unknown"),
    "label capitalised": (_with(label="Met"), "line 11: label must be one of"),
    "label trailing space": (_with(label="met "), "line 11: label must be one of"),
    "label null": (_with(label=None), "line 11: label must be one of"),
    "round zero": (_with(round=0), "line 11: round must be an integer from 1 to 1000"),
    "round one over": (_with(round=1001), "line 11: round must be an integer from 1 to 1000"),
    "round past sqlite": (_with(round=2**70), "line 11: round must be an integer from 1 to 1000"),
    "round float": (_with(round=1.0), "line 11: round must be"),
    "round bool": (_with(round=True), "line 11: round must be"),
    "round text": (_with(round="1"), "line 11: round must be"),
    "at without zone": (_with(at="2026-01-31T09:30:00"), "line 11: at must be a UTC time"),
    "at with offset": (_with(at="2026-01-31T09:30:00+00:00"), "line 11: at must be"),
    "at impossible day": (_with(at="2026-02-30T09:30:00Z"), "line 11: at must be"),
    "at with a space": (_with(at="2026-01-31 09:30:00Z"), "line 11: at must be"),
    "critique 201 chars": (_with(critique=SECRET + "x" * (201 - len(SECRET))), "line 11: critique must be a one-line"),
    "critique two lines": (_with(critique=f"{SECRET}\nsecond line"), "line 11: critique must be"),
    "critique line separator": (_with(critique=f"{SECRET}\u2028second"), "line 11: critique must be"),
    "critique not text": (_with(critique=[SECRET]), "line 11: critique must be"),
    "who empty": (_with(who=""), "line 11: who must be a non-empty"),
    "who blank": (_with(who="   "), "line 11: who must be"),
    "rubric a number": (_with(rubric=1), "line 11: rubric must be"),
    "run id null": (_with(run_id=None), "line 11: run_id must be"),
    "item id 201 chars": (_with(item_id=SECRET + "i" * (201 - len(SECRET))), "line 11: item_id must be"),
    "NaN": (_with(round=1).replace('"round": 1', '"round": NaN'), "line 11: NaN and Infinity are not JSON"),
    "Infinity": (_with(round=1).replace('"round": 1', '"round": Infinity'), "line 11: NaN and Infinity"),
    "-Infinity": (_with(round=1).replace('"round": 1', '"round": -Infinity'), "line 11: NaN and Infinity"),
    "duplicate key": (_with()[:-1] + f', "critique": "{SECRET}"}}', "line 11: key 'critique' appears twice"),
    "an array": (json.dumps([SECRET]), "line 11: not a JSON object"),
    "a string": (json.dumps(SECRET), "line 11: not a JSON object"),
    "cut short": (_with(critique=SECRET)[:-5], "line 11: not a JSON object"),
    "another step": (_with(step_id="answer"), "line 11: step_id is not the --step"),
    "no such run": (_with(run_id=f"run_{SECRET}"), "line 11 names a run, step and item with no trace in evals.sqlite"),
    "no such item": (_with(item_id=f"item-{SECRET}"), "line 11 names a run, step and item with no trace"),
    "same key, other label": (_with(at="2026-01-31T09:30:00Z", label="not met"), "line 11 has the key of line 2 with a different label"),
}  # fmt: skip


@pytest.mark.parametrize("bad, message", MALFORMED.values(), ids=MALFORMED.keys())
def test_a_malformed_batch_imports_nothing_and_never_echoes_a_value(
    ctx: GigContext, home: Path, tmp_path: Path, bad: str, message: str
) -> None:
    seed(home)
    path = batch(tmp_path, [*FIXTURE.read_text(encoding="utf-8").splitlines(), bad])

    done = label(ctx, "--from", str(path))

    assert done.exit_code == 2, done.output
    assert done.stdout == "" and len(done.stderr.splitlines()) == 1
    assert done.stderr.startswith("gigai eval-fixture eval label: ") and message in done.stderr
    assert SECRET not in done.stderr
    assert labels(home) == []


def test_the_boundaries_of_the_format_are_accepted(ctx: GigContext, home: Path, tmp_path: Path) -> None:
    seed(home)
    first = fixture_rows()[1]
    edge = first | {"critique": "c" * 200, "at": "2026-01-31T09:31:00.123456Z", "round": 2, "label": "not met"}
    path = batch(tmp_path, ["", FIXTURE.read_text(encoding="utf-8").splitlines()[1], "   ", json.dumps(edge), json.dumps(edge)])

    done = label(ctx, "--from", str(path))

    assert done.exit_code == 0, done.output
    assert done.stdout == "imported 2 labels of step solve (0 already stored)\n"
    original, relabel = labels(home)
    assert original["round"] == 1 and original["relabel_of"] is None
    assert relabel["round"] == 2 and relabel["relabel_of"] == original["label_id"] and relabel["critique"] == "c" * 200


def test_a_stored_label_with_the_same_key_and_another_answer_refuses_the_batch(
    ctx: GigContext, home: Path, tmp_path: Path
) -> None:
    seed(home)
    assert label(ctx, "--from", str(FIXTURE)).exit_code == 0
    before = labels(home)
    fresh = fixture_rows()[0] | {"item_id": "item-04", "at": "2026-01-31T11:00:00Z"}
    clash = fixture_rows()[1] | {"critique": "changed my mind"}
    path = batch(tmp_path, [json.dumps(fresh), json.dumps(clash)])

    done = label(ctx, "--from", str(path))

    assert done.exit_code == 2 and done.stdout == ""
    assert "line 2 has the key of a stored label with a different label" in done.stderr
    assert labels(home) == before


@pytest.mark.parametrize("text", ["", "\n   \n\n"], ids=["empty", "blank lines"])
def test_a_batch_with_no_rows_is_refused(ctx: GigContext, home: Path, tmp_path: Path, text: str) -> None:
    seed(home)
    path = tmp_path / "empty.jsonl"
    path.write_text(text, encoding="utf-8")

    done = label(ctx, "--from", str(path))

    assert done.exit_code == 2 and done.stdout == ""
    assert done.stderr == f"gigai eval-fixture eval label: no rows in {path}\n"
    assert labels(home) == []


def test_the_highest_round_is_accepted() -> None:
    row = fixture_rows()[0]

    assert validate_batch(json.dumps(row | {"round": 1000}))[0].round == 1000


def test_an_insert_that_fails_midway_leaves_nothing_imported(
    ctx: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(home)
    insert = eval_label._insert
    calls = []

    def failing_last(connection: sqlite3.Connection, *args: object, **kwargs: object) -> str:
        calls.append(1)
        if len(calls) == 10:
            raise sqlite3.OperationalError("disk I/O error")
        return insert(connection, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(eval_label, "_insert", failing_last)

    done = label(ctx, "--from", str(FIXTURE))

    assert len(calls) == 10
    assert done.exit_code == 1 and done.stdout == ""
    assert done.stderr == "gigai eval-fixture eval label: disk I/O error\n"
    assert labels(home) == []


def test_validate_batch_names_every_extra_key_and_cuts_a_long_one_short() -> None:
    row = fixture_rows()[0] | {"zeta": 1, "input": 2, SECRET * 10: 3}

    with pytest.raises(eval_label.LabelRefused) as caught:
        validate_batch(json.dumps(row))

    message = str(caught.value)
    assert message.startswith("line 1: key 'SECRET-TEXTSECRET-TEXTSECRET-TEXTSECRET-...', 'input', 'zeta' is not")
    assert SECRET * 4 not in message
    assert validate_batch(FIXTURE.read_text(encoding="utf-8")) == validate_batch(FIXTURE.read_text() + "\n\n")


@pytest.mark.parametrize(
    "setup, message",
    [
        ("text", "is not UTF-8 text"),
        ("missing", "no labels file at"),
    ],
)
def test_an_unreadable_batch_exits_2(ctx: GigContext, home: Path, tmp_path: Path, setup: str, message: str) -> None:
    seed(home)
    path = tmp_path / "batch.jsonl"
    if setup == "text":
        path.write_bytes(b'{"run_id": "\xff"}\n')

    done = label(ctx, "--from", str(path))

    assert done.exit_code == 2 and message in done.stderr and labels(home) == []


def test_export_refuses_an_existing_file_and_a_step_with_no_labels(ctx: GigContext, home: Path, tmp_path: Path) -> None:
    seed(home)
    empty = label(ctx, "--export", str(tmp_path / "none.jsonl"))
    assert empty.exit_code == 2 and "no labels of step solve to export" in empty.stderr
    assert not (tmp_path / "none.jsonl").exists()

    assert label(ctx, "--from", str(FIXTURE)).exit_code == 0
    taken = tmp_path / "taken.jsonl"
    taken.write_text("keep me\n")
    done = label(ctx, "--export", str(taken))

    assert done.exit_code == 2 and "exists; export writes a new file" in done.stderr
    assert taken.read_text() == "keep me\n"
    other = label(ctx, "--export", str(tmp_path / "answer.jsonl"), step="answer")
    assert other.exit_code == 2 and "no labels of step answer to export" in other.stderr


def test_the_format_doc_lists_exactly_the_validator_keys_in_order() -> None:
    table = [line.split("|")[1].strip().strip("`") for line in FORMAT_DOC.read_text().splitlines() if line.startswith("| `")]
    assert tuple(table) == LABEL_KEYS


# --- the page ----------------------------------------------------------------------------------------------------


def test_the_page_shows_input_and_output_but_never_the_run_arm_model_or_target(ctx: GigContext, home: Path) -> None:
    seed(home)

    done = label(ctx, input=answers(*[("m", f"critique {n}") for n in range(10)]))

    assert done.exit_code == 0, done.output
    for run_id in (ARM_A, ARM_B):
        for secret in identities(run_id):
            assert secret not in done.output
        for item in ITEMS:
            assert reply(run_id, item) in done.stdout
    assert "label-a" not in done.output and "label-b" not in done.output and "tag-" not in done.output
    assert all(question(item) in done.stdout for item in ITEMS)
    assert done.stdout.startswith("step solve: 10 rows, 0 labelled by page-tester, 10 to go.\n")
    assert done.stdout.count("--- row ") == 10 and done.stdout.endswith("10 labelled, 0 left.\n")
    stored = labels(home)
    assert len(stored) == 10 and len({(row["run_id"], row["item_id"]) for row in stored}) == 10
    for number, row in enumerate(stored):
        assert row["who"] == WHO and row["source"] == "human" and row["blind"] == 1 and row["rubric"] == PAGE_RUBRIC
        assert row["label"] == '"met"' and row["critique"] == f"critique {number}" and row["round"] == 1
        assert row["step_id"] == STEP and row["relabel_of"] is None and row["item_version"] == "2"
        assert validate_batch(json.dumps(as_format(row)), STEP)


def test_the_page_re_asks_a_bad_answer_or_an_overlong_critique(ctx: GigContext, home: Path) -> None:
    seed(home, runs=(ARM_A,), items={ARM_A: ["item-01", "item-02"]})
    script = "maybe\nmett\nn\n" + "x" * 201 + "\n" + "y" * 200 + "\n" + "U\n\nq\n"

    done = label(ctx, input=script)

    assert done.exit_code == 0, done.output
    assert done.stdout.count("answer met (m), not met (n), unknown (u) or q to stop") == 2
    assert done.stdout.count("a critique is one line of at most 200 characters") == 1
    assert [(row["label"], row["critique"]) for row in labels(home)] == [('"not met"', "y" * 200), ('"unknown"', "")]
    assert done.stdout.endswith("2 labelled, 0 left.\n")


def test_the_page_resumes_at_the_rows_this_labeller_has_not_labelled(
    ctx: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(home)

    first = label(ctx, input=answers(("m", "a"), ("n", "b"), ("u", "c")) + "q\n")
    second = label(ctx, input=answers(("m", "d"), ("m", "e")))  # input ends: the page stops, keeping both

    assert first.exit_code == 0 and first.stdout.endswith("3 labelled, 7 left.\n")
    assert second.exit_code == 0 and second.stdout.startswith("step solve: 10 rows, 3 labelled by page-tester, 7 to go.\n")
    assert second.stdout.endswith("2 labelled, 5 left.\n")
    third = label(ctx, input=answers(*[("m", "f")] * 5))
    assert third.stdout.startswith("step solve: 10 rows, 5 labelled by page-tester, 5 to go.\n")
    stored = labels(home)
    assert len(stored) == 10 and len({(row["run_id"], row["item_id"]) for row in stored}) == 10
    order = [(row.run_id, row.item_id) for row in _rows(home, None)]
    assert [(row["run_id"], row["item_id"]) for row in stored] == order

    monkeypatch.setenv("USER", "second-labeller")
    other = label(ctx, input="q\n")
    assert other.stdout.startswith("step solve: 10 rows, 0 labelled by second-labeller, 10 to go.\n")
    done = label(ctx, input="q\n")
    assert done.exit_code == 0 and done.stdout.endswith("0 labelled, 10 left.\n")


def test_q_stops_the_page_even_with_answers_after_it(ctx: GigContext, home: Path) -> None:
    seed(home)

    done = label(ctx, input=answers(("m", "a")) + "q\n" + answers(("m", "b"), ("n", "c")))

    assert done.exit_code == 0 and done.stdout.endswith("1 labelled, 9 left.\n")
    assert [(row["label"], row["critique"]) for row in labels(home)] == [('"met"', "a")]


def _rows(home: Path, select: str | None) -> list[eval_label.Row]:
    connection = sqlite3.connect(records.evals_db_path(GIG, home))
    try:
        return selected_rows(connection, GIG, STEP, select)
    finally:
        connection.close()


def test_random_n_is_a_seeded_deterministic_prefix_of_the_page_order(
    ctx: GigContext, home: Path, tmp_path: Path
) -> None:
    seed(home)
    other_home = tmp_path / "other-home"
    seed(other_home)

    everything = _rows(home, None)
    assert [(r.run_id, r.item_id) for r in everything] != sorted((r.run_id, r.item_id) for r in everything)
    assert _rows(home, None) == everything == _rows(other_home, None)
    assert _rows(home, "random:4") == everything[:4] == _rows(other_home, "random:4")
    assert _rows(home, "random:1") == everything[:1] and _rows(home, "random:99") == everything

    done = label(ctx, "--select", "random:3", input=answers(*[("u", "")] * 3))

    assert done.exit_code == 0, done.output
    assert done.stdout.startswith("step solve: 3 rows, 0 labelled by page-tester, 3 to go.\n")
    assert [(row["run_id"], row["item_id"]) for row in labels(home)] == [(r.run_id, r.item_id) for r in everything[:3]]


SCORES = {
    (ARM_A, "item-01"): {"s1": [1.0], "s2": [1.0]},  # met everywhere, exactly at the bar
    (ARM_B, "item-01"): {"s1": [1.0], "s2": [1.0]},
    (ARM_A, "item-02"): {"s1": [1.0], "s2": [0.9999999]},  # just below met: scorers disagree here
    (ARM_B, "item-02"): {"s1": [1.0], "s2": [1.0]},  # met here, but s2 splits item-02 across the arms
    (ARM_A, "item-03"): {"s1": [0.0], "s2": [0.0]},  # below met, but everyone agrees
    (ARM_B, "item-03"): {"s1": [0.0], "s2": [0.0]},
    (ARM_A, "item-04"): {"s1": [0.0, 1.0], "s2": [1.0]},  # rescored: the latest s1 (1.0) counts
    (ARM_B, "item-04"): {"s1": [1.0], "s2": [1.0]},
    (ARM_A, "item-05"): {"s1": [math.inf], "s2": [1.0]},
    (ARM_B, "item-05"): {"s1": [-math.inf], "s2": [1.0]},
}


def test_select_below_met_and_contested_follow_the_scorers(ctx: GigContext, home: Path) -> None:
    seed(home, items={ARM_A: [*ITEMS, "item-06"], ARM_B: ITEMS}, scores=SCORES)  # item-06 of arm A has no score

    below = {(r.run_id, r.item_id) for r in _rows(home, "below:met")}
    split = {(r.run_id, r.item_id) for r in _rows(home, "contested")}

    assert below == {(ARM_A, "item-02"), (ARM_A, "item-03"), (ARM_B, "item-03"), (ARM_B, "item-05")}
    assert split == {(ARM_A, "item-02"), (ARM_B, "item-02"), (ARM_A, "item-05"), (ARM_B, "item-05")}
    order = [(r.run_id, r.item_id) for r in _rows(home, None)]
    assert [(r.run_id, r.item_id) for r in _rows(home, "contested")] == [key for key in order if key in split]

    done = label(ctx, "--select", "contested", input=answers(*[("n", "")] * 4))

    assert done.exit_code == 0 and done.stdout.startswith("step solve: 4 rows, 0 labelled by page-tester, 4 to go.\n")
    assert {(row["run_id"], row["item_id"]) for row in labels(home)} == split


def test_relabel_re_asks_a_seeded_share_with_relabel_of_and_the_next_round(
    ctx: GigContext, home: Path, tmp_path: Path
) -> None:
    seed(home)
    assert label(ctx, input=answers(*[("m", f"first-pass {n}") for n in range(10)])).exit_code == 0
    originals = {(row["run_id"], row["item_id"]): row for row in labels(home)}

    done = label(ctx, "--relabel", "0.2", input=answers(("n", "second look"), ("u", "")))

    assert done.exit_code == 0, done.output
    assert done.stdout.startswith("step solve: 2 of your labels to give again.\n")
    assert "first-pass" not in done.stdout and '"met"' not in done.stdout
    relabels = labels(home)[10:]
    assert len(relabels) == 2
    for row in relabels:
        original = originals[(row["run_id"], row["item_id"])]
        assert row["relabel_of"] == original["label_id"] and row["round"] == 2 and row["who"] == WHO
        assert row["blind"] == 1 and row["rubric"] == PAGE_RUBRIC
    assert [row["label"] for row in relabels] == ['"not met"', '"unknown"']

    again = label(ctx, "--relabel", "0.2", input="")
    assert again.stdout.startswith("step solve: 0 of your labels to give again.\n") and len(labels(home)) == 12

    other_home = tmp_path / "other-home"
    seed(other_home)
    other = GigContext(other_home, GIG, MANIFEST, logging.getLogger("gigai.gig.eval-fixture"))
    assert label(other, input=answers(*[("m", "")] * 10)).exit_code == 0
    assert label(other, "--relabel", "0.2", input=answers(("n", ""), ("n", ""))).exit_code == 0
    assert [(r["run_id"], r["item_id"]) for r in labels(other_home)[10:]] == [(r["run_id"], r["item_id"]) for r in relabels]


@pytest.mark.parametrize("fraction, count", [("0.25", 3), ("0.24", 2), ("0.04", 1), ("1", 10), ("1.0", 10)])
def test_relabel_rounds_the_share_and_asks_at_least_one(ctx: GigContext, home: Path, fraction: str, count: int) -> None:
    seed(home)
    assert label(ctx, input=answers(*[("m", "")] * 10)).exit_code == 0

    done = label(ctx, "--relabel", fraction, input="q\n")

    assert done.exit_code == 0 and done.stdout.startswith(f"step solve: {count} of your labels to give again.\n")
    assert len(labels(home)) == 10


@pytest.mark.parametrize("fraction", ["nan", "NaN", "inf", "-inf", "0", "-0.1", "1.0000001", "abc", ""])
def test_relabel_refuses_a_fraction_outside_zero_to_one(ctx: GigContext, home: Path, fraction: str) -> None:
    seed(home)

    done = label(ctx, "--relabel", fraction, input="m\n\n")

    assert done.exit_code == 2 and "--relabel" in done.stderr and labels(home) == []


@pytest.mark.parametrize("select", ["random:0", "random:", "random:x", "random:-1", "random:1.5", "below", "contested:1", "below:unmet"])
def test_select_refuses_an_unknown_mode(ctx: GigContext, home: Path, select: str) -> None:
    seed(home)

    done = label(ctx, "--select", select, input="m\n\n")

    assert done.exit_code == 2 and "use contested, below:met or random:N" in done.stderr and labels(home) == []


# --- refusals ----------------------------------------------------------------------------------------------------


def test_an_unknown_step_exits_2(ctx: GigContext, home: Path) -> None:
    seed(home)

    done = label(ctx, step="nope", input="m\n\n")

    assert done.exit_code == 2 and done.stdout == ""
    assert done.stderr == "gigai eval-fixture eval label: no [[evals.steps]] entry has id 'nope'\n"
    assert label(ctx, step=None).exit_code == 2


@pytest.mark.parametrize(
    "argv",
    [
        ["--export", "a.jsonl", "--from", "b.jsonl"],
        ["--from", "b.jsonl", "--select", "contested"],
        ["--export", "a.jsonl", "--relabel", "0.5"],
    ],
)
def test_options_that_do_not_combine_exit_2(ctx: GigContext, home: Path, tmp_path: Path, argv: list[str]) -> None:
    seed(home)
    argv = [str(tmp_path / a) if a.endswith(".jsonl") else a for a in argv]

    done = label(ctx, *argv)

    assert done.exit_code == 2 and labels(home) == [] and not (tmp_path / "a.jsonl").exists()


@pytest.mark.parametrize("argv", [[], ["--from", str(FIXTURE)], ["--export", "out.jsonl"]])
def test_a_gig_with_no_store_exits_2_and_creates_none(ctx: GigContext, home: Path, argv: list[str]) -> None:
    done = label(ctx, *argv, input="m\n\n")

    assert done.exit_code == 2 and "has no evals.sqlite yet" in done.stderr
    assert not records.evals_db_path(GIG, home).exists()


def test_a_newer_store_exits_2(ctx: GigContext, home: Path) -> None:
    seed(home)
    connection = sqlite3.connect(records.evals_db_path(GIG, home))
    connection.execute("PRAGMA user_version = 2")
    connection.close()

    done = label(ctx, "--from", str(FIXTURE))

    assert done.exit_code == 2 and done.stdout == ""


@pytest.mark.parametrize("user", ["", "two\nlines"])
def test_a_labeller_with_no_usable_login_exits_2(
    ctx: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch, user: str
) -> None:
    seed(home)
    monkeypatch.setenv("USER", user)

    def no_login() -> str:
        raise OSError("no login")

    monkeypatch.setattr(eval_label.getpass, "getuser", no_login if user == "" else lambda: "fallback")

    done = label(ctx, input="m\n\n")

    if user == "":
        assert done.exit_code == 2 and "cannot tell who is labelling; set USER" in done.stderr
        assert labels(home) == []
    else:
        assert done.exit_code == 2 and labels(home) == []


def test_the_login_falls_back_to_the_os_when_user_is_unset(
    ctx: GigContext, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(home, runs=(ARM_A,), items={ARM_A: ["item-01"]})
    monkeypatch.delenv("USER")
    monkeypatch.setattr(eval_label.getpass, "getuser", lambda: "os-login")

    assert label(ctx, input="m\n\n").exit_code == 0
    assert [row["who"] for row in labels(home)] == ["os-login"]


# --- wiring ------------------------------------------------------------------------------------------------------


def test_the_gig_cli_mounts_label_and_exits_2_on_a_refused_batch(tmp_path: Path, home: Path) -> None:
    seed(home)
    site = tmp_path / "site"
    _add_fixture(site, GIG)
    env = {"GIGAI_HOME": str(home)}
    bad = batch(tmp_path, [_with(input=SECRET)])

    imported = _run(tmp_path, site, GIG, "eval", "label", "--step", STEP, "--from", str(FIXTURE), env_extra=env)
    refused = _run(tmp_path, site, GIG, "eval", "label", "--step", STEP, "--from", str(bad), env_extra=env)

    assert imported.returncode == 0, imported.stderr
    assert imported.stdout == "imported 10 labels of step solve (0 already stored)\n"
    assert refused.returncode == 2 and refused.stdout == "" and SECRET not in refused.stderr
    assert refused.stderr.startswith("gigai eval-fixture eval label: line 1: key 'input' is not in the labels format")
    assert len(labels(home)) == 10


def test_importing_the_cli_does_not_load_the_label_module() -> None:
    assert EVAL_VERBS["label"] == "gigai.evals.label:label_command"
    probe = "import sys, gigai.cli; print('gigai.evals.label' in sys.modules)"
    done = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "False"
