"""Run G27 guard mutations in disposable source trees."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
PYTHON = sys.executable
MUTANTS = (
    (
        "question-ceiling-before-persistence",
        "    if len(payload[\"questions\"]) > max_questions:\n",
        "    if False:\n",
        "tests/test_g27_runtime.py::test_g27_rejects_six_questions_before_session_persistence",
        "question_generation.py",
    ),
    (
        "manifest-question-ceiling",
        "    if len(direction_questions) > 5:\n",
        "    if False:\n",
        "tests/test_g27_runtime.py::test_g27_refuses_a_sixth_direction_question",
        "discovery.py",
    ),
    (
        "improve-context-required",
        "    if session.request_kind == \"improve\" and (\n        improve_context is None or improve_summary_bytes is None\n    ):\n",
        "    if False:\n",
        "tests/test_g27_runtime.py::test_g27_refuses_improve_manifest_without_context_summary",
        "discovery.py",
    ),
    (
        "reference-digest-boundary",
        "        if content is None or reference is None or digest_imported_bytes(content) != reference.content_sha256:\n",
        "        if False:\n",
        "tests/test_g27_runtime.py::test_g27_refuses_selected_reference_digest_drift",
        "discovery.py",
    ),
    (
        "discovery-journal-transition",
        '        transition="gig_discovery_manifest_written",\n',
        '        transition="proposal_interview_updated",\n',
        "tests/test_g26_cli_builder.py::test_create_runs_model_facilitated_build_then_explicit_approval",
        "lifecycle.py",
    ),
)


def main() -> int:
    caught: list[str] = []
    for name, original, mutant, test, module_name in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"gigai-g27-mutant-{name}-") as directory:
            source_root = Path(directory) / "src"
            shutil.copytree(ROOT / "src", source_root)
            path = source_root / f"gigai/{module_name}"
            payload = path.read_text(encoding="utf-8")
            if original not in payload:
                raise SystemExit(f"mutation anchor missing: {name}")
            path.write_text(payload.replace(original, mutant, 1), encoding="utf-8")
            test_path, separator, test_name = test.partition("::")
            selector = str(ROOT / test_path) + (separator + test_name if separator else "")
            result = subprocess.run(
                [PYTHON, "-m", "pytest", "-q", selector],
                cwd=source_root,
                env={**os.environ, "PYTHONPATH": str(source_root)},
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                raise SystemExit(f"mutation survived: {name}\n{result.stdout}")
            caught.append(name)
    print("caught G27 mutations: " + ", ".join(caught))
    print(f"mutation_killed={len(caught)}/{len(MUTANTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
