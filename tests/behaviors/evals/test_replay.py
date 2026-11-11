"""``eval run --from-traces RUN`` and ``eval rescore RUN``: score stored traces again with no adapter call.

The adapter counter (``calls``) wraps ``DeterministicAdapter.invoke``; a replay or rescore that reached any model
would move it off 0.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

import pytest

from gigai.evals import contracts
from gigai.evals import run as eval_run
from gigai.sdk import GigContext
from tests.behaviors.evals import test_run as base

home, gig, calls = base.home, base.gig, base.calls
query, count, run_id, values, validate = base.query, base.count, base.run_id, base.values, base.validate
loopback_only = base.loopback_only  # the autouse socket guard, applied here too
SCORERS = ("gigai.evals.contracts:match", "gig_eval_fixture.scorers:answer_marker")


def _means(home: Path, run: str) -> dict[str, float]:
    return {
        scorer: sum(found := [v for vs in values(home, run, scorer).values() for v in vs]) / len(found)
        for scorer in SCORERS
    }


def test_from_traces_makes_a_new_run_from_stored_traces_without_calling(gig: GigContext, home: Path, calls: list) -> None:
    source = run_id(eval_run.execute(gig, "solve", "ci"))
    calls.clear()

    lines = eval_run.replay(gig, "solve", source)
    replayed = run_id(lines)

    assert calls == []  # no adapter call
    assert lines[0] == f"run {replayed}: step solve, suite ci, 10 items x 1, 0 calls, replay of {source}"
    assert query(home, "SELECT replay_of, calls_made, suite, k, dataset_sha256 FROM runs WHERE run_id = ?", replayed) == query(
        home, "SELECT ?, 0, suite, k, dataset_sha256 FROM runs WHERE run_id = ?", source, source
    )
    assert (count(home, "traces", replayed), count(home, "scores", replayed)) == (10, 20)
    before, after = _means(home, source), _means(home, replayed)
    assert before["gigai.evals.contracts:match"] == pytest.approx(base.SNAPSHOT_MATCH, abs=1e-12)
    for scorer in SCORERS:
        assert abs(after[scorer] - before[scorer]) < 0.0005
    assert values(home, replayed, SCORERS[0]) == values(home, source, SCORERS[0])

    copied = {item: (raw, parsed) for item, raw, parsed in query(home, "SELECT item_id, output_raw_text, output_parsed FROM traces WHERE run_id = ?", replayed)}
    original = {item: (raw, parsed) for item, raw, parsed in query(home, "SELECT item_id, output_raw_text, output_parsed FROM traces WHERE run_id = ?", source)}
    assert copied == original
    assert not set(query(home, "SELECT trace_id FROM traces WHERE run_id = ?", replayed)) & set(query(home, "SELECT trace_id FROM traces WHERE run_id = ?", source))


def test_the_postprocess_is_pure_on_every_stored_output(gig: GigContext, home: Path) -> None:
    from gig_eval_fixture.scorers import final_number

    source = run_id(eval_run.execute(gig, "solve", "ci"))
    for raw, parsed, status in query(home, "SELECT output_raw_text, output_parsed, parse_status FROM traces WHERE run_id = ?", source):
        first, second = final_number(raw), final_number(raw)
        assert first == second == (json.loads(parsed), status)


def test_a_replayed_judge_reads_its_stored_replies(gig: GigContext, home: Path, calls: list) -> None:
    validate(home, kappa=0.8)
    source = run_id(eval_run.execute(gig, "answer", "ci"))
    calls.clear()

    replayed = run_id(eval_run.replay(gig, "answer", source))

    assert calls == []
    assert dict(query(home, "SELECT step_id, count(*) FROM traces WHERE run_id = ? GROUP BY step_id", replayed)) == {"answer": 6, "judge": 6}
    assert values(home, replayed, "step:judge") == values(home, source, "step:judge")

    validate(home, kappa=0.1, at="2026-03-01T00:00:00Z")  # the judge as resolved now must still be validated
    with pytest.raises(eval_run.JudgeNotValidatedError):
        eval_run.replay(gig, "answer", source)
    assert calls == []


def test_replay_refuses_another_steps_run_and_an_unknown_run(gig: GigContext, home: Path, calls: list) -> None:
    source = run_id(eval_run.execute(gig, "solve", "ci"))
    with pytest.raises(eval_run.RunRefused, match=r"ran step 'solve', not 'answer'"):
        eval_run.replay(gig, "answer", source)
    with pytest.raises(eval_run.RunRefused, match=r"no run run_nope in this gig's eval store"):
        eval_run.replay(gig, "solve", "run_nope")


def test_replay_and_rescore_refuse_a_dataset_changed_since_the_source_run(
    gig: GigContext, home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, calls: list
) -> None:
    source = run_id(eval_run.execute(gig, "solve", "ci"))
    changed = base.SLICE.read_bytes() + b"\n"
    package = tmp_path / "package"
    package.mkdir()
    (package / "tinygsm8k.jsonl").write_bytes(changed)
    monkeypatch.setattr(eval_run, "_package_dir", lambda name: package)
    manifest = copy.deepcopy(dict(gig.manifest))  # the manifest pins the new file, so only the run's sha differs
    new_sha = hashlib.sha256(changed).hexdigest()
    next(step for step in manifest["evals"]["steps"] if step["id"] == "solve")["datasets"]["ci"]["sha256"] = new_sha
    edited = GigContext(gig.home, gig.name, manifest, gig.log)
    runs = count(home, "runs")

    for again in (lambda: eval_run.replay(edited, "solve", source), lambda: eval_run.rescore(edited, source)):
        with pytest.raises(eval_run.RunRefused, match=rf"^the ci dataset changed since the run being replayed \(sha256:[0-9a-f]{{64}}, now sha256:{new_sha}\)$"):
            again()
    assert count(home, "runs") == runs


def test_rescore_writes_new_score_rows_on_the_same_traces(gig: GigContext, home: Path, calls: list) -> None:
    source = run_id(eval_run.execute(gig, "solve", "ci"))
    traces, scores, runs = count(home, "traces"), count(home, "scores"), count(home, "runs")
    calls.clear()

    lines = eval_run.rescore(gig, source, ["includes"])

    assert calls == []
    assert lines == [f"rescored run {source}: step solve, suite ci, 0 calls", "  includes: 0.8000 over 10 traces"]
    assert (count(home, "traces"), count(home, "runs"), count(home, "scores")) == (traces, runs, scores + 10)
    rows = query(
        home,
        "SELECT traces.output_raw_text, scores.value, scores.scorer_version FROM scores JOIN traces USING (trace_id)"
        " WHERE scores.scorer_id = 'includes' AND traces.run_id = ?",
        source,
    )
    expected = {row["id"]: row["expected"] for row in base.ROWS}
    assert len(rows) == 10
    outputs = dict(query(home, "SELECT output_raw_text, item_id FROM traces WHERE run_id = ?", source))
    for raw, value, version in rows:
        assert (value, version) == (float(expected[outputs[raw]] in raw), contracts.LIFTED_VERSION)


def test_rescore_with_no_names_reruns_the_steps_scorers_and_refuses_an_unvalidated_judge(gig: GigContext, home: Path, calls: list) -> None:
    source = run_id(eval_run.execute(gig, "solve", "ci"))
    assert eval_run.rescore(gig, source)[1:] == [
        f"  gigai.evals.contracts:match: {base.SNAPSHOT_MATCH:.4f} over 10 traces",
        "  gig_eval_fixture.scorers:answer_marker: 0.3000 over 10 traces",
    ]
    validate(home, kappa=0.8)
    judged = run_id(eval_run.execute(gig, "answer", "ci"))
    validate(home, kappa=0.2, at="2026-04-01T00:00:00Z")
    with pytest.raises(eval_run.JudgeNotValidatedError):
        eval_run.rescore(gig, judged, ["step:judge"])
