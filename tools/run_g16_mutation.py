"""Run small G16 mutation probes and require the focused tests to catch them."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MUTANTS = (
    (
        "loop-transition-guard",
        "if current not in _LOOP_TRANSITIONS.get(previous, frozenset()):",
        "if False:",
        "tests/test_g16_review_loop.py::test_review_loop_rejects_skipped_state_transition",
    ),
    (
        "sealed-run-precondition",
        """    if not run_details_path.is_file() or run_details_path.is_symlink():
        raise ReviewLoopError(\"G16 requires an existing sealed Run\")
    try:
        run_details = parse_json_bytes(run_details_path.read_bytes())
    except Exception as exc:
        raise ReviewLoopError(\"G16 RunDetails are malformed\") from exc
    if not isinstance(run_details, Mapping) or run_details.get(\"status\") != \"succeeded\":
        raise ReviewLoopError(\"G16 requires a successfully sealed deterministic Run\")""",
        "    run_details = {\"status\": \"succeeded\"}",
        "tests/test_g16_review_loop.py::test_loop_requires_a_sealed_run",
    ),
)


def main() -> int:
    root = Path(__file__).parents[1]
    caught: list[str] = []
    for name, original, mutant, test in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"gigai-g16-mutant-{name}-") as directory:
            source_root = Path(directory) / "src"
            shutil.copytree(root / "src", source_root)
            path = source_root / "gigai" / ("review.py" if name == "loop-transition-guard" else "review_loop.py")
            payload = path.read_text(encoding="utf-8")
            if original not in payload:
                raise SystemExit(f"mutation anchor missing: {name}")
            path.write_text(payload.replace(original, mutant, 1), encoding="utf-8")
            result = subprocess.run(
                [str(root / ".venv/bin/python"), "-m", "pytest", "-q", test],
                cwd=root,
                env={**os.environ, "PYTHONPATH": str(source_root)},
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                raise SystemExit(f"mutation survived: {name}")
            caught.append(name)
    print("caught G16 mutations: " + ", ".join(caught))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
