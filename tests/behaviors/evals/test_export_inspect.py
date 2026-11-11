"""``gigai <gig> eval report --export inspect``: one Inspect log per run, named so ``inspect view`` lists it, with the
fields the pinned inspect-ai release requires, and no item text unless ``--include-inputs``.

``tests/fixtures/evals/inspect_sample_log.json`` is an exported log cut to two samples and validated once against
inspect-ai 0.3.288 (``EvalLog.model_validate`` and ``read_eval_log``); these tests hold the exporter to its shape
without installing the ``inspect`` extra (``importlib.util.find_spec`` is the seam).
"""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner, Result

from gigai.evals import export_inspect, records, report
from gigai.sdk import GigContext
from tests.behaviors.evals.test_compare import ACC_B, MARKER, MATCH, SCORE_B
from tests.behaviors.evals.test_report import LONG_INPUT, LONG_OUTPUT, leaks, record_run
from tests.behaviors.evals.test_run import GIG, calls, gig, home  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[3]
SAMPLE = json.loads((ROOT / "tests" / "fixtures" / "evals" / "inspect_sample_log.json").read_text(encoding="utf-8"))
#: The ``EvalSpec`` fields inspect-ai 0.3.288 requires (pydantic ``is_required``, checked in a scratch venv).
REQUIRED_EVAL = ("created", "task", "dataset", "model", "config")
#: The file name ``inspect view`` lists: ``{timestamp}_{task}_{id}.json``.
NAME = re.compile(r"^\d{4}-\d{2}-\d{2}T[\d-]+_[^_]+_[^_]+\.json$")
_real_find_spec = importlib.util.find_spec


@pytest.fixture
def with_inspect(monkeypatch: pytest.MonkeyPatch) -> None:
    """``inspect_ai`` looks installed (nothing imports it)."""

    spec = importlib.machinery.ModuleSpec("inspect_ai", None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *rest: spec if name == "inspect_ai" else _real_find_spec(name, *rest))


@pytest.fixture
def without_inspect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *rest: None if name == "inspect_ai" else _real_find_spec(name, *rest))


def invoke(gig: GigContext, *argv: str) -> Result:
    return CliRunner().invoke(report.report_command, list(argv), obj=gig)


def exported(gig: GigContext) -> list[Path]:
    folder = export_inspect.log_folder(gig)
    return sorted(folder.iterdir()) if folder.exists() else []


def shape(value: Any) -> Any:
    """The key structure of ``value``, in key order: dicts become their (key, shape) pairs, lists their first element's."""

    if isinstance(value, dict):
        return [(key, shape(item)) for key, item in value.items()]
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__


# --- the pin --------------------------------------------------------------------------------------------------------


def test_the_inspect_extra_pins_inspect_ai_and_agent_client_protocol_below_1() -> None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    extras = tomllib.loads(text)["project"]["optional-dependencies"]

    assert extras["inspect"] == ["inspect-ai==0.3.288", "agent-client-protocol<1"]
    assert extras["inspect"][0] == f"inspect-ai=={export_inspect.INSPECT_VERSION}"
    assert text.count("agent-client-protocol<1") == 1
    assert not any("inspect" in requirement for name, group in extras.items() if name != "inspect" for requirement in group)


# --- the shape ------------------------------------------------------------------------------------------------------


def test_the_sample_log_has_the_fields_the_pinned_release_requires() -> None:
    assert (SAMPLE["version"], SAMPLE["status"]) == (2, "success")
    assert set(REQUIRED_EVAL) <= set(SAMPLE["eval"])
    sample = SAMPLE["samples"][0]
    assert {"id", "epoch", "input", "target", "output"} <= set(sample)
    assert set(sample["output"]["choices"][0]["message"]) == {"role", "content"}
    assert NAME.match("2026-03-01T00-00-00_eval-fixture-solve_run-sample.json")


def test_an_exported_log_has_the_sample_logs_shape(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B, MARKER: SCORE_B})
    (entry,) = report.build(gig, run)["runs"]

    log = export_inspect.build_log(GIG, entry)

    assert shape(log) == shape(SAMPLE)
    assert set(REQUIRED_EVAL) <= set(log["eval"])
    assert (log["eval"]["task"], log["eval"]["task_id"], log["eval"]["run_id"]) == ("eval-fixture-solve", run.replace("_", "-"), run)
    assert log["eval"]["dataset"] == {"name": "release.jsonl", "samples": 12}
    assert (log["eval"]["model"], log["eval"]["config"], log["eval"]["created"]) == ("deterministic-a", {"epochs": 1}, entry["created_at"][:19] + "+00:00")
    assert [(s["id"], s["epoch"]) for s in log["samples"][:2]] == [("r01", 1), ("r02", 1)]
    assert log["samples"][0]["scores"] == {"answer_marker": {"value": 0.5}, "match": {"value": 0.0}}
    assert log["results"]["scores"] == [
        {"name": m["metric"], "scorer": m["scorer"], "metrics": {"mean": {"name": "mean", "value": m["mean"]}}} for m in entry["metrics"]
    ]
    assert log["results"]["scores"][1]["metrics"]["mean"]["value"] == 0.75


def test_without_include_inputs_the_log_holds_hashes_not_text(gig: GigContext, home: Path) -> None:
    (entry,) = report.build(gig, record_run(home, {MATCH: ACC_B}))["runs"]

    sample = export_inspect.build_log(GIG, entry)["samples"][0]

    assert sample["input"] == records._sha256(f"{LONG_INPUT}r01")
    assert sample["output"]["choices"][0]["message"] == {"role": "assistant", "content": records._sha256(f"{LONG_OUTPUT}r01")}
    assert sample["target"] == ""
    assert not leaks(json.dumps(export_inspect.build_log(GIG, entry)))


def test_with_include_inputs_the_log_holds_the_prompt_target_and_output(gig: GigContext, home: Path) -> None:
    (entry,) = report.build(gig, record_run(home, {MATCH: ACC_B}), include_inputs=True)["runs"]

    sample = export_inspect.build_log(GIG, entry, include_inputs=True)["samples"][0]

    assert (sample["input"], sample["target"]) == (f"{LONG_INPUT}r01", "2")
    assert sample["output"]["choices"][0]["message"]["content"] == f"{LONG_OUTPUT}r01"


def test_a_repeat_is_an_epoch_counted_from_1(gig: GigContext, home: Path) -> None:
    (entry,) = report.build(gig, record_run(home, {MATCH: {item: [1.0, 0.0] for item in ACC_B}}))["runs"]

    log = export_inspect.build_log(GIG, entry)

    assert [(s["id"], s["epoch"]) for s in log["samples"][:3]] == [("r01", 1), ("r01", 2), ("r02", 1)]
    assert log["eval"]["config"] == {"epochs": 2}


# --- the file name --------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("created", "stamp"),
    [
        ("2026-03-01T00:00:07.000000Z", "2026-03-01T00-00-07"),
        ("2026-03-01T01:30:00+02:00", "2026-02-28T23-30-00"),  # converted to UTC
        ("2026-03-01T00:00:00", "2026-03-01T00-00-00"),  # no offset: taken as UTC
    ],
)
def test_the_file_name_is_utc_time_task_and_id_with_no_underscore_inside(created: str, stamp: str) -> None:
    name = export_inspect.file_name("my_gig", {"created_at": created, "step": "solve_it", "run": "run_a_b"})

    assert name == f"{stamp}_my-gig-solve-it_run-a-b.json"
    assert NAME.match(name) and export_inspect.FILE_NAME.pattern == NAME.pattern


def test_a_created_at_that_is_not_an_iso_time_is_refused() -> None:
    with pytest.raises(report.RunRefused, match=r"^cannot export: created_at 'yesterday' is not an ISO time$"):
        export_inspect.file_name("g", {"created_at": "yesterday", "step": "s", "run": "r"})


# --- the CLI --------------------------------------------------------------------------------------------------------


@pytest.mark.usefixtures("with_inspect")
def test_export_writes_one_private_log_per_run_in_the_gig_folder(gig: GigContext, home: Path) -> None:
    first = record_run(home, {MATCH: ACC_B})
    second = record_run(home, {MATCH: ACC_B})

    done = invoke(gig, "--export", "inspect")

    assert done.exit_code == 0, done.stderr
    folder = home / "gigs" / GIG / "inspect"
    paths = exported(gig)
    assert [path.parent for path in paths] == [folder, folder] and folder.stat().st_mode & 0o777 == 0o700
    assert all(NAME.match(path.name) and path.stat().st_mode & 0o777 == 0o600 for path in paths)
    assert [json.loads(path.read_text(encoding="utf-8"))["eval"]["run_id"] for path in paths] == [first, second]
    assert done.stdout.splitlines()[:2] == [f"wrote {paths[0]}", f"wrote {paths[1]}"] and done.stderr == ""


@pytest.mark.usefixtures("with_inspect")
def test_export_with_json_keeps_stdout_pure_json(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B})

    done = invoke(gig, "--json", "--export", "inspect", "--run", run)

    assert [entry["run"] for entry in json.loads(done.stdout)["runs"]] == [run]
    assert done.stderr.splitlines() == [f"wrote {exported(gig)[0]}"]


@pytest.mark.usefixtures("with_inspect")
def test_a_refused_report_writes_no_log(gig: GigContext, home: Path) -> None:
    record_run(home, {MATCH: ACC_B})

    done = invoke(gig, "--export", "inspect", "--run", "run_nope")

    assert done.exit_code == 2 and exported(gig) == []


@pytest.mark.usefixtures("with_inspect")
def test_a_symlinked_log_folder_is_refused(gig: GigContext, home: Path, tmp_path: Path) -> None:
    record_run(home, {MATCH: ACC_B})
    (tmp_path / "elsewhere").mkdir()
    (home / "gigs" / GIG / "inspect").symlink_to(tmp_path / "elsewhere")

    done = invoke(gig, "--export", "inspect")

    assert (done.exit_code, done.stdout) == (2, "")
    assert done.stderr.splitlines() == [f"gigai {GIG} eval: {home / 'gigs' / GIG / 'inspect'} is a symlink: remove it and export again"]
    assert list((tmp_path / "elsewhere").iterdir()) == []


@pytest.mark.usefixtures("without_inspect")
def test_export_without_the_extra_exits_2_with_the_install_hint_before_reading(gig: GigContext, home: Path) -> None:
    record_run(home, {MATCH: ACC_B})

    done = invoke(gig, "--export", "inspect")

    assert (done.exit_code, done.stdout) == (2, "")
    assert done.stderr.splitlines() == [
        f"gigai {GIG} eval: --export inspect needs the inspect extra (inspect-ai 0.3.288): install it with pip install 'gigai[inspect]'"
    ]
    assert exported(gig) == []


@pytest.mark.usefixtures("without_inspect")
def test_the_missing_extra_is_reported_even_without_a_store(gig: GigContext) -> None:
    done = invoke(gig, "--export", "inspect")

    assert done.exit_code == 2 and "pip install 'gigai[inspect]'" in done.stderr


def test_only_inspect_is_a_choice_for_export(gig: GigContext) -> None:
    done = invoke(gig, "--export", "csv")

    assert done.exit_code == 2 and "csv" in done.stderr


# --- lazy imports ---------------------------------------------------------------------------------------------------


def test_importing_the_cli_or_the_report_loads_neither_the_exporter_nor_inspect_ai() -> None:
    code = (
        "import sys, gigai.cli\n"
        "print(sorted(m for m in sys.modules if m.startswith(('gigai.evals.report', 'gigai.evals.export_inspect', 'inspect_ai'))))\n"
        "import gigai.evals.report\n"
        "print(sorted(m for m in sys.modules if m.startswith(('gigai.evals.export_inspect', 'inspect_ai'))))\n"
    )

    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True, timeout=60)

    assert done.stdout.splitlines() == ["[]", "[]"]


# --- what Inspect's header-only read needs ---------------------------------------------------------------------------

#: The top-level keys in written order: Inspect's header scan stops at ``samples`` and needs ``version`` before it.
HEADER = ["version", "status", "eval", "plan", "results", "stats", "samples"]
SAMPLE_PATH = ROOT / "tests" / "fixtures" / "evals" / "inspect_sample_log.json"


def test_a_written_log_starts_with_version_and_has_its_whole_header_before_samples(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B, MARKER: SCORE_B})

    (path,) = export_inspect.write_logs(gig, report.build(gig, run))

    written = path.read_text(encoding="utf-8")
    log = json.loads(written)
    assert list(log) == HEADER and list(SAMPLE) == HEADER
    assert written.startswith('{\n  "version": 2,\n  "status": "success",\n  "eval": {')
    assert (log["plan"], log["stats"]) == ({"name": "plan", "steps": []}, {"started_at": log["eval"]["created"]})
    assert list(log["eval"])[:2] == ["run_id", "created"] and list(log["samples"][0])[:2] == ["id", "epoch"]


def test_inspect_reads_the_header_of_an_exported_log_and_of_the_sample_log(gig: GigContext, home: Path) -> None:
    inspect_log = pytest.importorskip("inspect_ai.log")
    run = record_run(home, {MATCH: ACC_B, MARKER: SCORE_B})
    (path,) = export_inspect.write_logs(gig, report.build(gig, run))

    header = inspect_log.read_eval_log(str(path), header_only=True)
    sample = inspect_log.read_eval_log(str(SAMPLE_PATH), header_only=True)

    assert (header.status, header.eval.task, header.eval.run_id, header.samples) == ("success", "eval-fixture-solve", run, None)
    assert [(score.name, score.metrics["mean"].value) for score in header.results.scores] == [("answer_marker", pytest.approx(0.575)), ("match", 0.75)]
    assert (sample.status, sample.eval.run_id, sample.samples) == ("success", "run_sample", None)
    full = inspect_log.read_eval_log(str(path))
    assert [(s.id, s.epoch) for s in full.samples[:2]] == [("r01", 1), ("r02", 1)]


def test_a_run_with_no_traces_exports_a_log_with_no_samples(gig: GigContext, home: Path) -> None:
    run = record_run(home, {}, calls_made=0)

    (path,) = export_inspect.write_logs(gig, report.build(gig, run))

    log = json.loads(path.read_text(encoding="utf-8"))
    assert list(log) == HEADER and log["samples"] == []
    assert log["results"] == {"total_samples": 0, "completed_samples": 0, "scores": []}
    assert (log["eval"]["dataset"]["samples"], log["eval"]["model"], log["eval"]["run_id"]) == (0, "none", run)


# --- scorers sharing a name ------------------------------------------------------------------------------------------

OTHER_MATCH = "other.mod:match"


def test_two_scorers_sharing_a_name_keep_their_own_scores_under_their_scorer_ids(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B, OTHER_MATCH: {item: [0.25] for item in ACC_B}, MARKER: SCORE_B})
    (entry,) = report.build(gig, run)["runs"]

    log = export_inspect.build_log(GIG, entry)

    assert log["samples"][0]["scores"] == {"answer_marker": {"value": 0.5}, MATCH: {"value": 0.0}, OTHER_MATCH: {"value": 0.25}}
    assert [(s["name"], s["scorer"], s["metrics"]["mean"]["value"]) for s in log["results"]["scores"]] == [
        ("answer_marker", MARKER, pytest.approx(0.575)),
        (MATCH, MATCH, 0.75),
        (OTHER_MATCH, OTHER_MATCH, 0.25),
    ]


# --- file names that clash -------------------------------------------------------------------------------------------


def test_two_runs_that_map_to_one_file_name_are_refused_before_anything_is_written(gig: GigContext, home: Path) -> None:
    (entry,) = report.build(gig, record_run(home, {MATCH: ACC_B}))["runs"]
    twins = {"gig": GIG, "include_inputs": False, "runs": [entry, {**entry, "run": "run_x y"}, {**entry, "run": "run-x-y"}]}
    name = export_inspect.file_name(GIG, twins["runs"][1])

    with pytest.raises(report.RunRefused) as refused:
        export_inspect.write_logs(gig, twins)

    assert str(refused.value) == f"cannot export: runs run_x y and run-x-y map to the same file name {name}"
    assert exported(gig) == []


def test_run_ids_that_differ_in_a_kept_character_get_their_own_files(gig: GigContext, home: Path) -> None:
    (entry,) = report.build(gig, record_run(home, {MATCH: ACC_B}))["runs"]

    paths = export_inspect.write_logs(gig, {"runs": [{**entry, "run": "run-x-y"}, {**entry, "run": "run-x-z"}]})

    assert [json.loads(path.read_text(encoding="utf-8"))["eval"]["run_id"] for path in paths] == ["run-x-y", "run-x-z"]
    assert exported(gig) == sorted(paths)


@pytest.mark.usefixtures("with_inspect")
@pytest.mark.parametrize("content", ['{"eval": {"run_id": "run_other"}}', '{"eval": "x"}', "not json", "[]"])
def test_a_file_that_is_not_a_log_of_the_run_is_never_replaced(gig: GigContext, home: Path, content: str) -> None:
    run = record_run(home, {MATCH: ACC_B})
    (entry,) = report.build(gig, run)["runs"]
    folder = export_inspect.log_folder(gig)
    folder.mkdir(mode=0o700)
    path = folder / export_inspect.file_name(GIG, entry)
    path.write_text(content, encoding="utf-8")

    done = invoke(gig, "--export", "inspect")

    assert (done.exit_code, done.stdout) == (2, "")
    assert done.stderr.splitlines() == [f"gigai {GIG} eval: cannot export run {run}: {path} holds something else; move it away and export again"]
    assert path.read_text(encoding="utf-8") == content and exported(gig) == [path]


@pytest.mark.usefixtures("with_inspect")
def test_a_symlink_where_a_log_goes_is_refused(gig: GigContext, home: Path, tmp_path: Path) -> None:
    run = record_run(home, {MATCH: ACC_B})
    (entry,) = report.build(gig, run)["runs"]
    folder = export_inspect.log_folder(gig)
    folder.mkdir(mode=0o700)
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"eval": {"run_id": run}}), encoding="utf-8")
    (folder / export_inspect.file_name(GIG, entry)).symlink_to(target)

    done = invoke(gig, "--export", "inspect")

    assert done.exit_code == 2 and "holds something else" in done.stderr
    assert json.loads(target.read_text(encoding="utf-8")) == {"eval": {"run_id": run}}


@pytest.mark.usefixtures("with_inspect")
def test_exporting_a_run_again_replaces_its_own_file(gig: GigContext, home: Path) -> None:
    run = record_run(home, {MATCH: ACC_B})
    assert invoke(gig, "--export", "inspect", "--include-inputs").exit_code == 0
    (path,) = exported(gig)
    assert LONG_INPUT in path.read_text(encoding="utf-8")

    done = invoke(gig, "--export", "inspect")

    assert done.exit_code == 0 and exported(gig) == [path]
    assert not leaks(path.read_text(encoding="utf-8")) and json.loads(path.read_text(encoding="utf-8"))["eval"]["run_id"] == run
