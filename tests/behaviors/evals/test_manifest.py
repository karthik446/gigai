"""V2: the manifest's ``[evals]`` block and generic ``[steps]`` table, and ``gigai gig check`` item 11.

The eval fixture (``tests/fixtures/gigs/eval-fixture/``) is checked as a project directory, copied to tmp so each
negative case edits its own copy; the three D1 example manifests are checked as bare files. Every row is synthetic and
no check makes a model call (item 11 imports the scorer symbols, it never calls them).
"""

from __future__ import annotations

import copy
import shutil
import tomllib
from pathlib import Path
from typing import Any

import pytest

from gigai.gigs.check import FAIL, PASS, SKIP, check_file
from gigai.gigs.eval_manifest import SPDX_ALLOWED, EvalDataset, licence_problem, resolve, step_datasets
from gigai.gigs.manifest import manifest_errors
from tests.behaviors.gigs.test_loader import FIXTURES, _run

PACKAGE = "gig_eval_fixture"
FIXTURE_MANIFEST = FIXTURES / "eval-fixture" / PACKAGE / "gig.toml"
D1_EXAMPLES = ("scout.toml", "eval-book.toml", "baseline-behavior.toml")


def _load(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text(encoding="utf-8"))


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / ".gigai"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("GIGAI_HOME", str(home))
    return home


def _copy_fixture(tmp_path: Path) -> Path:
    project = tmp_path / "eval-fixture"
    shutil.copytree(FIXTURES / "eval-fixture", project, ignore=shutil.ignore_patterns("__pycache__"))
    return project


def _check(tmp_path: Path, target: Path, home: Path) -> tuple[int, list[str]]:
    completed = _run(tmp_path, tmp_path / "site", "gig", "check", str(target), env_extra={"GIGAI_HOME": str(home)})
    return completed.returncode, (completed.stdout + completed.stderr).splitlines()


# --- schema -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", [FIXTURE_MANIFEST, *(FIXTURES / name for name in D1_EXAMPLES)], ids=lambda path: path.name)
def test_schema_accepts_the_fixture_and_the_d1_examples(path: Path) -> None:
    assert manifest_errors(_load(path)) == []


def _answer(manifest: dict[str, Any]) -> dict[str, Any]:
    return manifest["evals"]["steps"][0]


BAD_SHAPES = {
    "ci dataset without sha256": lambda m: _answer(m)["datasets"]["ci"].pop("sha256"),
    "ci dataset as a bare path": lambda m: _answer(m)["datasets"].update(ci="dataset.jsonl"),
    "dataset without a licence": lambda m: _answer(m)["datasets"]["ci"].pop("licence"),
    "unknown suite": lambda m: _answer(m)["datasets"].update(nightly={"path": "x.jsonl", "source": "s", "licence": "MIT"}),
    "short sha256": lambda m: _answer(m)["datasets"]["ci"].update(sha256="abc123"),
    "scorer that is not module:attr": lambda m: _answer(m)["scorers"].append("exact_match"),
    "prompt without a file": lambda m: _answer(m)["prompt"].pop("file"),
    "gate with an unknown suite": lambda m: _answer(m)["gates"]["ci"].update(suite="nightly"),
    "gate rule without a metric": lambda m: _answer(m)["gates"]["ci"]["primary"].pop("metric"),
    "step without model_target": lambda m: _answer(m).pop("model_target"),
    "validation without against": lambda m: m["evals"]["steps"][1]["validation"].pop("against"),
    "steps entry with an unknown key": lambda m: m.update(steps={"answer": {"prompt": "prompts/answer.txt", "temperature": 0}}),
}


@pytest.mark.parametrize("mutate", BAD_SHAPES.values(), ids=BAD_SHAPES.keys())
def test_schema_refuses_bad_evals_shapes(mutate: Any) -> None:
    manifest = copy.deepcopy(_load(FIXTURE_MANIFEST))
    mutate(manifest)
    assert manifest_errors(manifest)


def test_schema_accepts_a_generic_steps_table() -> None:
    manifest = _load(FIXTURE_MANIFEST)
    manifest["steps"] = {
        "answer": {"prompt": "prompts/answer.txt", "schema": "schemas/answer.json", "role": "planner", "target": "offline-default"},
        "jd.extract": {"prompt": "prompts/judge.txt", "description": "pull requirements from a posting"},
    }
    assert manifest_errors(manifest) == []


# --- licences -----------------------------------------------------------------------------------------------


# Copied by hand from B G1, research/gigai-2/evals-respike/B-capability-map-criteria.md:105; not from the constant.
B_G1_ALLOW_LIST = ("CC0-1.0", "CC-BY-4.0", "CC-BY-SA-4.0", "MIT", "Apache-2.0", "BSD-3-Clause", "ODC-By-1.0", "CDLA-Permissive-2.0")


def test_the_allow_list_is_b_g1s() -> None:
    assert set(SPDX_ALLOWED) == set(B_G1_ALLOW_LIST)


@pytest.mark.parametrize("licence", B_G1_ALLOW_LIST)
def test_every_allow_listed_licence_passes_on_a_ci_dataset(licence: str) -> None:
    assert licence_problem(EvalDataset("ci", "dataset.jsonl", licence=licence)) is None
    assert licence_problem(EvalDataset("ci", "dataset.jsonl", licence=licence.lower())) is None


@pytest.mark.parametrize("licence", ["CC-BY-NC-4.0", "cc-by-nc-4.0", "private", "research-only", None])
def test_a_ci_dataset_refuses_any_other_licence(licence: str | None) -> None:
    problem = licence_problem(EvalDataset("ci", "dataset.jsonl", licence=licence))
    assert problem is not None and "ci dataset" in problem


def test_non_allow_listed_licences_are_allowed_only_for_private_data_under_data_outside_ci() -> None:
    assert licence_problem(EvalDataset("release", "{data}/release.jsonl", licence="CC-BY-NC-4.0")) is None
    assert licence_problem(EvalDataset("release", "{data}/release.jsonl")) is None
    assert licence_problem(EvalDataset("upgrade", "{data}/rows.jsonl", licence="private")) is None
    assert "shipped dataset" in (licence_problem(EvalDataset("upgrade", "rows.jsonl", licence="private")) or "")
    assert "shipped dataset" in (licence_problem(EvalDataset("release", "rows.jsonl", licence="CC-BY-NC-4.0")) or "")
    assert "shipped dataset" in (licence_problem(EvalDataset("release", "rows.jsonl")) or "")
    assert licence_problem(EvalDataset("release", "rows.jsonl", licence="MIT")) is None
    assert licence_problem(EvalDataset("ci", "{data}/ci.jsonl", licence="CC-BY-NC-4.0")) is not None


def test_the_old_bare_dataset_stands_for_release() -> None:
    (dataset,) = step_datasets({"id": "a", "model_target": "t", "dataset": "{data}/gold.jsonl"})
    assert (dataset.suite, dataset.path, dataset.private) == ("release", "{data}/gold.jsonl", True)


def test_a_dataset_path_cannot_leave_its_root(tmp_path: Path) -> None:
    package, data = tmp_path / "pkg", tmp_path / "data"
    assert resolve("rows.jsonl", package, data) == (package / "rows.jsonl").resolve()
    assert resolve("{data}/rows.jsonl", package, data) == (data / "rows.jsonl").resolve()
    assert resolve("../rows.jsonl", package, data) is None
    assert resolve("{data}/../pkg/rows.jsonl", package, data) is None
    assert resolve("{data}/rows.jsonl", package, None) is None


# --- item 11 ------------------------------------------------------------------------------------------------


def test_gig_check_passes_the_eval_fixture_project(tmp_path: Path, home: Path) -> None:
    code, lines = _check(tmp_path, _copy_fixture(tmp_path), home)

    assert code == 0, lines
    item_11 = [line for line in lines if line.split()[1:2] == ["11"]]
    assert any("evals.steps.answer.prompt.file: prompts/answer.txt sha256 " in line for line in item_11)
    assert any("evals.steps.answer.datasets.ci: dataset.jsonl sha256 1900380c" in line for line in item_11)
    assert any(line.startswith("SKIP") and "datasets.release" in line and "absent (private)" in line for line in item_11)
    assert any(line.startswith("PASS") and "2 scorer/postprocess symbol(s) import" in line for line in item_11)


NEGATIVE = {
    "scorer does not import": (
        '"gig_eval_fixture.scorers:exact_match"',
        '"gig_eval_fixture.scorers:no_such_scorer"',
        "evals.steps.answer.scorers: 'gig_eval_fixture.scorers:no_such_scorer' does not import",
    ),
    "ci dataset missing": ('path = "dataset.jsonl"', 'path = "missing.jsonl"', "evals.steps.answer.datasets.ci.path"),
    "wrong ci sha256": ("1900380cb28eab58", "0000000000000000", "evals.steps.answer.datasets.ci.sha256: declared 0000000000000000"),
    "non-commercial ci licence": ('licence = "CC0-1.0"', 'licence = "cc-by-nc-4.0"', "evals.steps.answer.datasets.ci.licence"),
    "private licence on a shipped release": (
        'path = "{data}/datasets/answer-release.jsonl"',
        'path = "dataset.jsonl"',
        "evals.steps.answer.datasets.release.licence: licence 'private' on a shipped dataset",
    ),
    "release path leaves {data}": (
        'path = "{data}/datasets/answer-release.jsonl"',
        'path = "{data}/../../../outside.jsonl"',
        "evals.steps.answer.datasets.release.path: '{data}/../../../outside.jsonl' leaves {data}",
    ),
    "ci path leaves the package": ('path = "dataset.jsonl"', 'path = "../dataset.jsonl"', "evals.steps.answer.datasets.ci.path: '../dataset.jsonl' leaves the gig's package"),
}


@pytest.mark.parametrize(("old", "new", "field"), NEGATIVE.values(), ids=NEGATIVE.keys())
def test_gig_check_fails_and_names_the_field(tmp_path: Path, home: Path, old: str, new: str, field: str) -> None:
    project = _copy_fixture(tmp_path)
    manifest = project / PACKAGE / "gig.toml"
    text = manifest.read_text(encoding="utf-8")
    assert old in text
    manifest.write_text(text.replace(old, new, 1), encoding="utf-8")

    code, lines = _check(tmp_path, project, home)

    assert code == 1, lines
    assert any(line.startswith("FAIL") and line.split()[1] == "11" and field in line for line in lines), lines


def test_a_private_dataset_present_under_data_is_hashed(tmp_path: Path, home: Path) -> None:
    rows = home / "gigs" / "eval-fixture" / "datasets" / "answer-release.jsonl"
    rows.parent.mkdir(parents=True)
    rows.write_text('{"id": "p1"}\n', encoding="utf-8")

    code, lines = _check(tmp_path, _copy_fixture(tmp_path), home)

    assert code == 0, lines  # a non-ci digest mismatch is a WARN
    assert any(line.startswith("WARN") and "evals.steps.answer.datasets.release.sha256: declared" in line for line in lines)


def test_a_private_dataset_symlinked_out_of_data_fails(tmp_path: Path, home: Path) -> None:
    outside = tmp_path / "outside.jsonl"
    outside.write_text('{"id": "p1"}\n', encoding="utf-8")
    rows = home / "gigs" / "eval-fixture" / "datasets" / "answer-release.jsonl"
    rows.parent.mkdir(parents=True)
    rows.symlink_to(outside)

    code, lines = _check(tmp_path, _copy_fixture(tmp_path), home)

    assert code == 1, lines
    assert any(
        line.startswith("FAIL") and "evals.steps.answer.datasets.release.path: '{data}/datasets/answer-release.jsonl' leaves {data}" in line
        for line in lines
    ), lines


def test_static_references_are_checked_without_a_package(home: Path) -> None:
    manifest = copy.deepcopy(_load(FIXTURE_MANIFEST))
    manifest["evals"]["steps"][0]["scorers"] = ["step:answer"]
    manifest["evals"]["steps"][1]["grades"] = "no-such-step"
    manifest["evals"]["steps"].append(copy.deepcopy(manifest["evals"]["steps"][1]))

    results = [result for result in check_file(manifest, installed="0.2.0") if result.item == "11"]

    failed = [result.message for result in results if result.outcome == FAIL]
    assert any(message.startswith("evals.steps: step id 'judge' is declared more than once") for message in failed)
    assert any(message.startswith("evals.steps.answer.scorers: 'step:answer' names no judge step") for message in failed)
    assert any(message.startswith("evals.steps.judge.grades: 'no-such-step' names no step") for message in failed)
    assert [result.outcome for result in results if "files and symbols" in result.message] == [SKIP]


def test_a_manifest_without_evals_has_no_item_11(home: Path) -> None:
    manifest = _load(FIXTURE_MANIFEST)
    del manifest["evals"]

    assert [result for result in check_file(manifest, installed="0.2.0") if result.item == "11"] == []


@pytest.mark.parametrize("name", D1_EXAMPLES)
def test_the_d1_examples_pass_the_static_half_of_item_11(name: str, home: Path) -> None:
    results = [result for result in check_file(_load(FIXTURES / name), installed="0.2.0") if result.item == "11"]

    assert [result.outcome for result in results] == [PASS, SKIP]


def test_a_directory_with_two_manifests_is_refused(tmp_path: Path, home: Path) -> None:
    project = _copy_fixture(tmp_path)
    shutil.copytree(project / PACKAGE, project / "src" / PACKAGE)

    code, lines = _check(tmp_path, project, home)

    assert code != 0
    assert any("2 gig.toml files" in line for line in lines), lines
