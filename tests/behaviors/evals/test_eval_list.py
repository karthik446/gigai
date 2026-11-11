"""V2: ``gigai <gig> eval list [--json]`` lists the steps a gig's ``[evals]`` table declares, with dataset provenance.

The eval fixture is "installed" the way test_loader.py does it; its ``answer`` verb traces through the deterministic
adapter (no provider, no network). Every row is synthetic.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from gigai.evals.records import open_evals_db
from tests.behaviors.gigs.test_loader import FIXTURES, _add_fixture, _run

GIG = "eval-fixture"
PACKAGE = FIXTURES / GIG / "gig_eval_fixture"


@pytest.fixture
def site(tmp_path: Path) -> Path:
    site = tmp_path / "site"
    _add_fixture(site, GIG)
    return site


@pytest.fixture
def home(tmp_path: Path) -> Path:
    return tmp_path / ".gigai"


def _gigai(tmp_path: Path, site: Path, home: Path, *argv: str):
    return _run(tmp_path, site, *argv, env_extra={"GIGAI_HOME": str(home)})


def _listing(tmp_path: Path, site: Path, home: Path) -> dict:
    completed = _gigai(tmp_path, site, home, GIG, "eval", "list", "--json")
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def _snapshot(root: Path) -> dict[str, tuple[bytes | None, int]]:
    """Every path under ``root`` with its bytes (``None`` for a folder) and mtime."""

    return {
        str(path.relative_to(root)): (path.read_bytes() if path.is_file() else None, path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
    }


def test_list_json_has_every_step_and_dataset_provenance(tmp_path: Path, site: Path, home: Path) -> None:
    listing = _listing(tmp_path, site, home)

    assert (listing["gig"], listing["cluster"], listing["store"]) == (GIG, "fixture", None)
    assert [step["id"] for step in listing["steps"]] == ["answer", "judge", "solve"]
    answer, judge, _solve = listing["steps"]
    for dataset in answer["datasets"].values():
        assert all(dataset[key] for key in ("sha256", "licence", "source")), dataset
    ci, release = answer["datasets"]["ci"], answer["datasets"]["release"]
    assert (ci["path"], ci["licence"], ci["status"]) == ("dataset.jsonl", "CC0-1.0", "present")
    assert ci["sha256"] == hashlib.sha256((PACKAGE / "dataset.jsonl").read_bytes()).hexdigest()
    assert release["status"] == "absent (private)"
    assert answer["prompt"] == {
        "id": "answer",
        "version": "1",
        "file": "prompts/answer.txt",
        "sha256": hashlib.sha256((PACKAGE / "prompts" / "answer.txt").read_bytes()).hexdigest(),
        "status": "present",
    }
    assert answer["scorers"] == ["gig_eval_fixture.scorers:exact_match", "step:judge"]
    assert answer["postprocess"] == "gig_eval_fixture:postprocess"
    assert answer["gates"]["ci"]["max_calls"] == 0
    assert (judge["grades"], judge["dimension"], judge["validation"]["min_kappa"]) == ("answer", "correctness", 0.6)
    assert answer["last_run"] is None and judge["last_validation"] is None


def test_list_reports_the_last_run_and_the_latest_judge_validation(tmp_path: Path, site: Path, home: Path) -> None:
    for run_id in ("run_a", "run_b"):
        traced = _gigai(tmp_path, site, home, GIG, "answer", "--item", "q1", "--item", "q2", "--run-id", run_id)
        assert traced.returncode == 0, traced.stderr
    connection = open_evals_db(GIG, home)
    try:
        for metric, value in (("kappa", 0.7), ("tpr", 0.9)):
            connection.execute(
                "INSERT INTO judge_validations VALUES (?, 'step:judge', '1', 'offline-default', 'sha256:x', 'labels', 'sha256:y', 30, ?, ?, NULL, '2026-01-01T00:00:00Z')",
                (f"validation_{metric}", metric, value),
            )
        connection.commit()
    finally:
        connection.close()

    before = _snapshot(home)
    answer, judge, _solve = _listing(tmp_path, site, home)["steps"]
    assert _snapshot(home) == before  # eval list writes nothing, the store included

    assert answer["last_run"]["run_id"] == "run_b"
    assert (answer["last_run"]["traces"], answer["last_run"]["failed"]) == (2, 0)
    assert answer["last_run"]["dataset_id"]
    assert judge["last_run"] is None
    assert judge["last_validation"]["metrics"] == {"kappa": 0.7, "tpr": 0.9}
    assert judge["last_validation"]["n_items"] == 30


def test_list_text_names_private_data_and_never_imports_scorers(tmp_path: Path, site: Path, home: Path) -> None:
    (site / "gig_eval_fixture" / "scorers.py").write_text("raise RuntimeError('imported')\n", encoding="utf-8")

    completed = _gigai(tmp_path, site, home, GIG, "eval", "list")

    assert completed.returncode == 0, completed.stderr
    assert "eval-fixture: 3 eval step(s), cluster fixture" in completed.stdout
    assert "dataset release  {data}/datasets/answer-release.jsonl  absent (private)" in completed.stdout
    assert "last run: none" in completed.stdout


def test_a_private_dataset_present_under_the_gig_folder_lists_present(tmp_path: Path, site: Path, home: Path) -> None:
    rows = home / "gigs" / GIG / "datasets" / "answer-release.jsonl"
    rows.parent.mkdir(parents=True)
    rows.write_text('{"id": "p1"}\n', encoding="utf-8")

    assert _listing(tmp_path, site, home)["steps"][0]["datasets"]["release"]["status"] == "present"


@pytest.mark.parametrize("escape", ["dotdot", "symlink"])
def test_a_private_dataset_outside_the_gig_folder_exits_1(tmp_path: Path, site: Path, home: Path, escape: str) -> None:
    if escape == "dotdot":
        manifest = site / "gig_eval_fixture" / "gig.toml"
        text = manifest.read_text(encoding="utf-8")
        manifest.write_text(text.replace("{data}/datasets/answer-release.jsonl", "{data}/../../outside.jsonl", 1), encoding="utf-8")
    else:
        outside = tmp_path / "outside.jsonl"
        outside.write_text('{"id": "p1"}\n', encoding="utf-8")
        rows = home / "gigs" / GIG / "datasets" / "answer-release.jsonl"
        rows.parent.mkdir(parents=True)
        rows.symlink_to(outside)

    completed = _gigai(tmp_path, site, home, GIG, "eval", "list", "--json")

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["steps"][0]["datasets"]["release"]["status"] == "outside its root"
    assert "dataset not where the manifest says: answer.release: {data}/" in completed.stderr


def test_a_missing_ci_dataset_exits_1(tmp_path: Path, site: Path, home: Path) -> None:
    (site / "gig_eval_fixture" / "dataset.jsonl").unlink()

    completed = _gigai(tmp_path, site, home, GIG, "eval", "list", "--json")

    assert completed.returncode == 1
    assert json.loads(completed.stdout)["steps"][0]["datasets"]["ci"]["status"] == "missing"
    assert "dataset not where the manifest says: answer.ci: dataset.jsonl (missing)" in completed.stderr


def test_an_unreadable_store_is_reported_not_raised(tmp_path: Path, site: Path, home: Path) -> None:
    connection = open_evals_db(GIG, home)
    connection.close()
    with sqlite3.connect(home / "gigs" / GIG / "evals.sqlite") as db:
        db.execute("PRAGMA user_version = 999")

    listing = _listing(tmp_path, site, home)

    assert "newer than version" in listing["store"]
    assert listing["steps"][0]["last_run"] is None


def test_eval_is_mounted_only_on_a_gig_with_an_evals_table(tmp_path: Path, site: Path, home: Path) -> None:
    _add_fixture(site, "storegig")

    evals_help = _gigai(tmp_path, site, home, GIG, "eval", "--help")
    assert evals_help.returncode == 0
    assert "list            List this gig's eval steps" in evals_help.stdout
    assert "eval " in _gigai(tmp_path, site, home, GIG, "--help").stdout
    assert "eval " not in _gigai(tmp_path, site, home, "storegig", "--help").stdout
    assert _gigai(tmp_path, site, home, "storegig", "eval", "list").returncode == 2
