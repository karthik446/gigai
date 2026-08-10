"""Run G20 gate mutations in disposable source trees."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MUTANTS = (
    (
        "evidence-sufficiency-gate",
        "    if not evidence_sufficient:\n",
        "    if False:\n",
        "tests/test_g20_learning_runtime.py::test_improvement_has_independent_evidence_and_quality_gates",
    ),
    (
        "quality-no-regression-gate",
        "    if quality_gate[\"final_holdout_pass\"] is not True or quality_gate[\"no_regression\"] is not True:\n",
        "    if False:\n",
        "tests/test_g20_learning_runtime.py::test_improvement_has_independent_evidence_and_quality_gates",
    ),
)


def main() -> int:
    root = Path(__file__).parents[1]
    python = root / ".venv/bin/python"
    caught: list[str] = []
    for name, original, mutant, test in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"gigai-g20-mutant-{name}-") as directory:
            source_root = Path(directory) / "src"
            shutil.copytree(root / "src", source_root)
            path = source_root / "gigai/improvement.py"
            payload = path.read_text(encoding="utf-8")
            if original not in payload:
                raise SystemExit(f"mutation anchor missing: {name}")
            path.write_text(payload.replace(original, mutant, 1), encoding="utf-8")
            test_path, separator, test_name = test.partition("::")
            result = subprocess.run(
                [str(python), "-m", "pytest", "-q", str(root / test_path) + separator + test_name],
                cwd=source_root,
                env={**os.environ, "PYTHONPATH": str(source_root)},
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                raise SystemExit(f"mutation survived: {name}")
            caught.append(name)
    print("caught G20 mutations: " + ", ".join(caught))
    print(f"mutation_killed={len(caught)}/{len(MUTANTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
