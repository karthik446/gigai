"""Contract-first evaluation manifests, observations, and reports."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping

from .canonical import canonical_json_digest


class EvaluationError(ValueError):
    """A manifest, observation set, or report contract is invalid."""


SPLITS = {"development", "calibration", "final_held_out_acceptance"}


def _canonical_digest(value: object) -> str:
    return canonical_json_digest(value)


def _object(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise EvaluationError(f"{label} must be an object")
    return value


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise EvaluationError(f"{label} must be a non-empty string")
    return value


def _string_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise EvaluationError(f"{label} must be a list of non-empty strings")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise EvaluationError(f"{label} must not contain duplicates")
    return result


@dataclass(frozen=True)
class EvaluationManifest:
    payload: dict[str, Any]
    digest: str
    cases: tuple[dict[str, Any], ...]

    @property
    def corpus_id(self) -> str:
        return str(self.payload["corpus_id"])


@dataclass(frozen=True)
class CaseScore:
    case_id: str
    split: str
    true_positive: int
    false_positive: int
    false_negative: int
    forbidden: tuple[str, ...]
    status: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "split": self.split,
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
            "forbidden": list(self.forbidden),
            "status": self.status,
        }


def validate_manifest(payload: object) -> EvaluationManifest:
    root = _object(payload, "manifest")
    if root.get("schema_version") != "1.0":
        raise EvaluationError("manifest.schema_version must be '1.0'")
    if root.get("kind") != "behavior_manifest":
        raise EvaluationError("manifest.kind must be 'behavior_manifest'")
    _string(root.get("corpus_id"), "manifest.corpus_id")
    _string(root.get("corpus_version"), "manifest.corpus_version")
    cases_value = root.get("cases")
    if not isinstance(cases_value, list) or not cases_value:
        raise EvaluationError("manifest.cases must be a non-empty list")
    cases: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, value in enumerate(cases_value):
        case = _object(value, f"manifest.cases[{index}]")
        case_id = _string(case.get("case_id"), f"manifest.cases[{index}].case_id")
        if case_id in seen:
            raise EvaluationError(f"duplicate case_id: {case_id}")
        seen.add(case_id)
        _string(case.get("case_version"), f"manifest.cases[{index}].case_version")
        split = _string(case.get("split"), f"manifest.cases[{index}].split")
        if split not in SPLITS:
            raise EvaluationError(f"unsupported split: {split}")
        expected = _string_list(case.get("expected"), f"manifest.cases[{index}].expected")
        forbidden = _string_list(case.get("forbidden"), f"manifest.cases[{index}].forbidden")
        if set(expected) & set(forbidden):
            raise EvaluationError(f"case {case_id} has expected and forbidden overlap")
        labels = _string_list(case.get("labels", []), f"manifest.cases[{index}].labels")
        if not labels:
            raise EvaluationError(f"case {case_id} must have at least one label")
        cases.append({**case, "expected": list(expected), "forbidden": list(forbidden), "labels": list(labels)})
    contamination = _object(root.get("contamination"), "manifest.contamination")
    if contamination.get("rule") != "tuning_never_certifies_final":
        raise EvaluationError("manifest.contamination.rule is unsupported")
    return EvaluationManifest(root, _canonical_digest(root), tuple(cases))


def load_manifest(path: Path) -> EvaluationManifest:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EvaluationError(f"cannot read manifest {path}: {exc}") from exc
    return validate_manifest(payload)


def _validate_observations(value: object, manifest: EvaluationManifest) -> tuple[dict[str, Any], dict[str, list[str]]]:
    root = _object(value, "observations")
    if root.get("schema_version") != "1.0":
        raise EvaluationError("observations.schema_version must be '1.0'")
    if root.get("manifest_digest") != manifest.digest:
        raise EvaluationError("observations.manifest_digest does not match manifest")
    solver = _object(root.get("solver"), "observations.solver")
    for key in ("kind", "id", "version"):
        _string(solver.get(key), f"observations.solver.{key}")
    if root.get("contamination_check") != "clean":
        raise EvaluationError("observations.contamination_check must be 'clean'")
    _string(root.get("tuning_cutoff"), "observations.tuning_cutoff")
    rows = root.get("cases")
    if not isinstance(rows, list):
        raise EvaluationError("observations.cases must be a list")
    known = {case["case_id"] for case in manifest.cases}
    observed: dict[str, list[str]] = {}
    for index, row_value in enumerate(rows):
        row = _object(row_value, f"observations.cases[{index}]")
        case_id = _string(row.get("case_id"), f"observations.cases[{index}].case_id")
        if case_id not in known:
            raise EvaluationError(f"observations references unknown case: {case_id}")
        if case_id in observed:
            raise EvaluationError(f"duplicate observation case_id: {case_id}")
        observed[case_id] = list(_string_list(row.get("observed"), f"observations.cases[{index}].observed"))
    return root, observed


def score_behavior(manifest: EvaluationManifest, observations: object, split: str) -> dict[str, Any]:
    if split not in SPLITS:
        raise EvaluationError(f"unsupported split: {split}")
    observation_root, observed = _validate_observations(observations, manifest)
    selected = [case for case in manifest.cases if case["split"] == split]
    if not selected:
        raise EvaluationError(f"manifest has no cases in split {split}")
    scores: list[CaseScore] = []
    for case in selected:
        expected = set(case["expected"])
        actual = set(observed.get(case["case_id"], []))
        forbidden = tuple(sorted(actual & set(case["forbidden"])))
        tp = len(expected & actual)
        fp = len(actual - expected)
        fn = len(expected - actual)
        scores.append(CaseScore(case["case_id"], split, tp, fp, fn, forbidden, "pass" if not forbidden and not fn else "fail"))
    tp = sum(item.true_positive for item in scores)
    fp = sum(item.false_positive for item in scores)
    fn = sum(item.false_negative for item in scores)
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    passed = all(item.status == "pass" for item in scores)
    return {
        "schema_version": "1.0",
        "kind": "behavior_report",
        "manifest_digest": manifest.digest,
        "corpus_id": manifest.corpus_id,
        "split": split,
        "solver": observation_root["solver"],
        "verifier": {"id": "gigai.behavior-set-verifier", "version": "1", "kind": "deterministic"},
        "methodology_plumbing": "pass",
        "behavior_scored": "pass" if passed else "fail",
        "candidate_judge_scored": False,
        "metrics": {"precision": precision, "recall": recall, "false_positive_count": fp, "false_negative_count": fn},
        "cases": [item.as_dict() for item in scores],
        "contamination": {"check": observation_root["contamination_check"], "tuning_cutoff": observation_root["tuning_cutoff"]},
        "status": "pass" if passed else "fail",
    }


def write_report(report: Mapping[str, Any], output: Path | None) -> None:
    encoded = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if output is None:
        print(encoded, end="")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding="utf-8")


__all__ = ["EvaluationError", "EvaluationManifest", "load_manifest", "score_behavior", "validate_manifest", "write_report"]
