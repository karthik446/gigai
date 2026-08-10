"""Run G19 guard mutations in disposable source trees."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MUTANTS = (
    (
        "target-head-revalidation",
        '    if target["git_head"] != expected["git_head"]:\n'
        '        raise TargetEffectRefusedError("Git HEAD changed", code="target_head_changed")',
        "    if False:\n"
        '        raise TargetEffectRefusedError("Git HEAD changed", code="target_head_changed")',
        "tests/test_g19_target_effect.py::test_g19_refuses_changed_head_before_preparation",
    ),
    (
        "target-path-containment",
        'def _safe_target_file(root: Path, relative: str) -> Path:\n'
        '    return _safe_file(root, relative, "target")',
        'def _safe_target_file(root: Path, relative: str) -> Path:\n'
        '    return root / relative',
        "tests/test_g19_target_effect.py::test_g19_rejects_path_traversal_and_symlink_targets",
    ),
    (
        "source-digest-revalidation",
        '        if digest_imported_bytes(source_bytes) != record["expected_after_sha256"]:\n'
        '            raise TargetEffectRefusedError("source artifact digest changed before preparation", code="source_digest_mismatch")',
        "        if False:\n"
        '            raise TargetEffectRefusedError("source artifact digest changed before preparation", code="source_digest_mismatch")',
        "tests/test_g19_target_effect.py::test_g19_refuses_tampered_replacement_source_before_preparation",
    ),
    (
        "dirty-target-refusal",
        '    if target["status_bytes"]:\n'
        '        raise TargetEffectRefusedError("target worktree/index is not clean", code="target_dirty")',
        "    if False:\n"
        '        raise TargetEffectRefusedError("target worktree/index is not clean", code="target_dirty")',
        "tests/test_g19_target_effect.py::test_g19_refuses_dirty_target_before_preparation",
    ),
    (
        "after-digest-verification",
        '    if digest_imported_bytes(target_bytes) != record["expected_after_sha256"]:\n'
        '        raise TargetEffectRefusedError("target after digest mismatch", code="after_digest_mismatch")',
        "    if False:\n"
        '        raise TargetEffectRefusedError("target after digest mismatch", code="after_digest_mismatch")',
        "tests/test_g19_target_effect.py::test_g19_after_exposure_digest_drift_blocks_recovery",
    ),
    (
        "atomic-exposure",
        "        os.replace(temporary, target_path)\n",
        "        target_path.write_bytes(data)\n",
        "tests/test_g19_target_effect.py::test_g19_runtime_has_no_effectful_imports_and_uses_atomic_exposure",
    ),
    (
        "exposed-recovery-decision",
        "        if _is_after_state(record, target, target_path, current):\n",
        "        if False:\n",
        "tests/test_g19_target_effect.py::test_g19_exposed_record_recovers_to_applied_after_interruption",
    ),
)


def main() -> int:
    root = Path(__file__).parents[1]
    python = root / ".venv/bin/python"
    caught: list[str] = []
    for name, original, mutant, test in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"gigai-g19-mutant-{name}-") as directory:
            source_root = Path(directory) / "src"
            shutil.copytree(root / "src", source_root)
            path = source_root / "gigai/target_effect.py"
            payload = path.read_text(encoding="utf-8")
            if original not in payload:
                raise SystemExit(f"mutation anchor missing: {name}")
            path.write_text(payload.replace(original, mutant, 1), encoding="utf-8")
            probe = subprocess.run(
                [str(python), "-c", "import gigai.target_effect as module; print(module.__file__)"],
                cwd=source_root,
                env={**os.environ, "PYTHONPATH": str(source_root)},
                capture_output=True,
                text=True,
                check=True,
            )
            if Path(probe.stdout.strip()).resolve() != path.resolve():
                raise SystemExit(f"mutation source was not imported: {probe.stdout.strip()}")
            test_path, separator, test_name = test.partition("::")
            test_selector = str(root / test_path) + (separator + test_name if separator else "")
            result = subprocess.run(
                [str(python), "-m", "pytest", "-q", test_selector],
                cwd=source_root,
                env={**os.environ, "PYTHONPATH": str(source_root)},
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode == 0:
                raise SystemExit(f"mutation survived: {name}")
            caught.append(name)
    print("caught G19 mutations: " + ", ".join(caught))
    print(f"mutation_killed={len(caught)}/{len(MUTANTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
