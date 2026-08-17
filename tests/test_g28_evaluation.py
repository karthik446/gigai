from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.evaluation import EvaluationError, score_behavior, validate_manifest


def _manifest() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "kind": "behavior_manifest",
        "corpus_id": "g28-fixture",
        "corpus_version": "1",
        "contamination": {"rule": "tuning_never_certifies_final"},
        "cases": [
            {
                "case_id": "case-pass",
                "case_version": "1",
                "split": "development",
                "labels": ["positive"],
                "expected": ["definition_captured"],
                "forbidden": ["run_created"],
            },
            {
                "case_id": "case-negative",
                "case_version": "1",
                "split": "development",
                "labels": ["negative"],
                "expected": ["approval_refused"],
                "forbidden": ["target_mutated"],
            },
        ],
    }


def _observations(manifest_digest: str, values: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "manifest_digest": manifest_digest,
        "solver": {"kind": "deterministic_fixture", "id": "g28-fixture", "version": "1"},
        "contamination_check": "clean",
        "tuning_cutoff": "commit:test",
        "cases": values,
    }


def test_behavior_report_scores_separately_supplied_observations() -> None:
    manifest = validate_manifest(_manifest())
    observations = _observations(
        manifest.digest,
        [
            {"case_id": "case-pass", "observed": ["definition_captured"]},
            {"case_id": "case-negative", "observed": ["approval_refused"]},
        ],
    )
    report = score_behavior(manifest, observations, "development")
    assert report["status"] == "pass"
    assert report["methodology_plumbing"] == "pass"
    assert report["behavior_scored"] == "pass"
    assert report["candidate_judge_scored"] is False
    assert report["metrics"]["precision"] == 1.0


def test_behavior_report_rejects_forbidden_observation() -> None:
    manifest = validate_manifest(_manifest())
    observations = _observations(
        manifest.digest,
        [
            {"case_id": "case-pass", "observed": ["definition_captured", "run_created"]},
            {"case_id": "case-negative", "observed": ["approval_refused"]},
        ],
    )
    report = score_behavior(manifest, observations, "development")
    assert report["status"] == "fail"
    assert report["behavior_scored"] == "fail"
    assert report["cases"][0]["forbidden"] == ["run_created"]


def test_behavior_report_refuses_contaminated_or_mismatched_observations() -> None:
    manifest = validate_manifest(_manifest())
    observations = _observations("sha256:wrong", [])
    try:
        score_behavior(manifest, observations, "development")
    except EvaluationError as exc:
        assert "manifest_digest" in str(exc)
    else:
        raise AssertionError("mismatched observations were accepted")

    observations = _observations(manifest.digest, [])
    observations["contamination_check"] = "unknown"
    try:
        score_behavior(manifest, observations, "development")
    except EvaluationError as exc:
        assert "contamination_check" in str(exc)
    else:
        raise AssertionError("contaminated observations were accepted")


def test_cli_eval_contract_and_behavior_write_report(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")
    loaded = validate_manifest(_manifest())
    observations_path = tmp_path / "observations.json"
    observations_path.write_text(
        json.dumps(
            _observations(
                loaded.digest,
                [
                    {"case_id": "case-pass", "observed": ["definition_captured"]},
                    {"case_id": "case-negative", "observed": ["approval_refused"]},
                ],
            )
        ),
        encoding="utf-8",
    )
    runner = CliRunner()
    contract = runner.invoke(cli, ["eval", "contract", "--manifest", str(manifest_path)])
    assert contract.exit_code == 0, contract.output
    assert json.loads(contract.output)["status"] == "pass"
    output_path = tmp_path / "report.json"
    behavior = runner.invoke(
        cli,
        [
            "eval",
            "behavior",
            "--manifest",
            str(manifest_path),
            "--observations",
            str(observations_path),
            "--split",
            "development",
            "--output",
            str(output_path),
        ],
    )
    assert behavior.exit_code == 0, behavior.output
    assert json.loads(output_path.read_text(encoding="utf-8"))["candidate_judge_scored"] is False
