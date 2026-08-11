"""Run G21 mutation probes in disposable source trees."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MUTANTS = (
    (
        "occurrence-slot-uniqueness",
        "    if existing is not None:\n",
        "    if False:\n",
        "tests/test_g21_occurrence.py::test_manual_occurrence_runs_once_and_preserves_active_pointer",
        "src/gigai/occurrence.py",
    ),
    (
        "snapshot-identity-revalidation",
        "            if verified_snapshot != record[\"snapshot\"]:\n",
        "            if False:\n",
        "tests/test_g21_occurrence.py::test_valid_replacement_snapshot_is_blocked_by_identity_digest",
        "src/gigai/occurrence.py",
    ),
    (
        "terminal-replay-idempotency",
        "    if record[\"state\"] in _TERMINAL_STATES:\n        return _result(resolved, record)\n",
        "    if False:\n        return _result(resolved, record)\n",
        "tests/test_g21_occurrence.py::test_explicit_terminal_states_are_idempotent_and_never_run",
        "src/gigai/occurrence.py",
    ),
    (
        "run-linkage-binding",
        '    record["run_id"] = run.run_id\n',
        '    record["run_id"] = None\n',
        "tests/test_g21_occurrence.py::test_manual_occurrence_runs_once_and_preserves_active_pointer",
        "src/gigai/occurrence.py",
    ),
    (
        "missing-output-block",
        "    elif current_output is None or prior_output is None:\n",
        "    elif False:\n",
        "tests/test_g21_comparison.py::test_missing_output_is_blocked_without_fabricated_delta",
        "src/gigai/comparison.py",
    ),
    (
        "comparison-winner-null",
        '"selected_winner": {"type": "null"}',
        '"selected_winner": {"type": ["null", "string"]}',
        "tests/test_g21_contract.py::test_comparison_schema_enforces_null_winner",
        "src/gigai/schemas/gig-comparison.schema.json",
    ),
    (
        "prepared-run-terminalization-guard",
        '    if record["state"] == "run_prepared":\n',
        "    if False:\n",
        "tests/test_g21_occurrence.py::test_mark_refuses_inflight_prepared_run_until_reconciled",
        "src/gigai/occurrence.py",
    ),
    (
        "refusal-outcome-actor-schema-guard",
        '          "outcome_actor": {"$ref": "urn:gigai:schema:common:1#/$defs/actor"}\n',
        '          "outcome_actor": {"type": ["object", "null"]}\n',
        "tests/test_g21_contract.py::test_occurrence_schema_requires_refusal_reason_outcome_and_actor",
        "src/gigai/schemas/gig-occurrence.schema.json",
    ),
    (
        "comparison-version-mismatch-guard",
        '    elif current_run.get("gig_version") != prior_run.get("gig_version"):\n',
        "    elif False:\n",
        "tests/test_g21_comparison.py::test_different_run_versions_are_explicitly_incomparable",
        "src/gigai/comparison.py",
    ),
)


def main() -> int:
    root = Path(__file__).parents[1]
    python = root / ".venv/bin/python"
    caught: list[str] = []
    for name, original, mutant, test, relative_path in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"gigai-g21-mutant-{name}-") as directory:
            source_root = Path(directory) / "src"
            shutil.copytree(root / "src", source_root)
            path = source_root / relative_path.removeprefix("src/")
            payload = path.read_text(encoding="utf-8")
            if original not in payload:
                raise SystemExit(f"mutation anchor missing: {name}")
            if name == "terminal-replay-idempotency":
                position = payload.rfind(original)
                if position < 0:
                    raise SystemExit(f"mutation anchor missing: {name}")
                payload = payload[:position] + mutant + payload[position + len(original):]
            else:
                payload = payload.replace(original, mutant, 1)
            path.write_text(payload, encoding="utf-8")
            test_path, separator, test_name = test.partition("::")
            selector = str(root / test_path) + (separator + test_name if separator else "")
            result = subprocess.run(
                [str(python), "-m", "pytest", "-q", selector],
                cwd=source_root,
                env={**os.environ, "PYTHONPATH": str(source_root)},
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                raise SystemExit(f"mutation survived: {name}")
            caught.append(name)
    print("caught G21 mutations: " + ", ".join(caught))
    print(f"mutation_killed={len(caught)}/{len(MUTANTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
