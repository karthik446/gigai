"""Run G26 boundary and recovery mutations in disposable source trees."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MUTANTS = (
    (
        "wall-time-guard",
        "        if remaining <= 0:\n",
        "        if False:\n",
        "tests/test_g26_model_call.py::test_bounded_call_times_out_without_accepting_a_late_result",
        "src/gigai/model_call.py",
    ),
    (
        "pre-call-cancellation-guard",
        '        raise BoundedCallError("cancelled", "builder call was cancelled before invocation")\n',
        '        raise BoundedCallError("failed", "mutated cancellation")\n',
        "tests/test_g26_model_call.py::test_bounded_call_cancels_before_invocation",
        "src/gigai/model_call.py",
    ),
    (
        "in-flight-cancellation-guard",
        '            raise BoundedCallError("cancelled", "builder call was cancelled")\n',
        '            raise BoundedCallError("failed", "mutated cancellation")\n',
        "tests/test_g26_model_call.py::test_bounded_call_cancels_while_provider_is_running",
        "src/gigai/model_call.py",
    ),
    (
        "output-token-budget-guard",
        "    if request_output_tokens > max_output_tokens:\n",
        "    if False:\n",
        "tests/test_g26_model_call.py::test_bounded_call_rejects_request_over_token_budget",
        "src/gigai/model_call.py",
    ),
    (
        "selected-reference-only-guard",
        '            if reference.decision != "selected":\n',
        "            if False:\n",
        "tests/test_g26_builder.py::test_remote_builder_receives_only_selected_reference_content",
        "src/gigai/builder.py",
    ),
    (
        "interrupted-research-recovery-guard",
        '    if state == "researching":\n',
        "    if False:\n",
        "tests/test_g26_builder.py::test_interrupted_builder_recovery_terminalizes_without_retry",
        "src/gigai/lifecycle.py",
    ),
    (
        "duplicate-build-guard",
        "        if existing_state in {\n",
        "        if False and existing_state in {\n",
        "tests/test_g26_builder.py::test_completed_builder_cannot_be_run_again",
        "src/gigai/lifecycle.py",
    ),
    (
        "unavailable-target-terminal-guard",
        "    except LifecycleError as exc:\n        base_selection = _unusable_builder_selection(model_target)\n",
        "    except LifecycleError as exc:\n        raise exc\n",
        "tests/test_g26_builder.py::test_unavailable_builder_target_writes_terminal_session",
        "src/gigai/lifecycle.py",
    ),
    (
        "existing-proposal-approval-guard",
        "    if existing_proposal_id is not None:\n",
        "    if False:\n",
        "tests/test_g26_cli_builder.py::test_create_runs_model_facilitated_build_then_explicit_approval",
        "src/gigai/lifecycle.py",
    ),
)


def main() -> int:
    root = Path(__file__).parents[1]
    python = root / ".venv/bin/python"
    caught: list[str] = []
    for name, original, mutant, test, relative_path in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"gigai-g26-mutant-{name}-") as directory:
            source_root = Path(directory) / "src"
            shutil.copytree(root / "src", source_root)
            path = source_root / relative_path.removeprefix("src/")
            payload = path.read_text(encoding="utf-8")
            if original not in payload:
                raise SystemExit(f"mutation anchor missing: {name}")
            path.write_text(payload.replace(original, mutant, 1), encoding="utf-8")
            test_path, separator, test_name = test.partition("::")
            selector = str(root / test_path) + (separator + test_name if separator else "")
            result = subprocess.run(
                [str(python), "-m", "pytest", "-q", selector],
                cwd=source_root,
                env={**os.environ, "PYTHONPATH": str(source_root)},
                capture_output=True,
                text=True,
                check=False,
                timeout=20,
            )
            if result.returncode == 0:
                raise SystemExit(f"mutation survived: {name}")
            caught.append(name)
    print("caught G26 mutations: " + ", ".join(caught))
    print(f"mutation_killed={len(caught)}/{len(MUTANTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
