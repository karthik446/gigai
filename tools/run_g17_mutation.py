"""Run G17 mutations and require the focused tests to catch them."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MUTANTS = (
    (
        "source-digest-check",
        "if required_digest is not None and source_digest != required_digest:",
        "if False:",
        "tests/test_g17_capabilities.py::test_digest_drift_fails_before_tool_root_write",
    ),
    (
        "approval-gate",
        "if not approved:",
        "if False:",
        "tests/test_g17_capabilities.py::test_refusal_and_interruption_rollback_are_durable",
    ),
    (
        "path-containment-check",
        "if _is_symlinked_path(source_path, root) or not source_path.is_file():",
        "if False:",
        "tests/test_g17_capabilities.py::test_source_symlink_and_target_symlink_fail_closed",
    ),
    (
        "before-after-comparison",
        "if len(files) == 1 and files[0][\"path\"] == f\"tools/{capability_id}/artifact\" and files[0][\"content_sha256\"] == source_digest and len(after[\"entries\"]) == 1:",
        "if False:",
        "tests/test_g17_capabilities.py::test_install_is_idempotent_and_records_exact_snapshots",
    ),
    (
        "rollback-path",
        "if renamed and target.exists():",
        "if False:",
        "tests/test_g17_capabilities.py::test_refusal_and_interruption_rollback_are_durable",
    ),
    (
        "per-gig-provenance",
        '"installed_root": f"tools/{capability[\'capability_id\']}",',
        '"installed_root": "tools/shared",',
        "tests/test_g17_capabilities.py::test_install_is_idempotent_and_records_exact_snapshots",
    ),
)


def main() -> int:
    root = Path(__file__).parents[1]
    caught: list[str] = []
    for name, original, mutant, test in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"gigai-g17-mutant-{name}-") as directory:
            source_root = Path(directory) / "src"
            shutil.copytree(root / "src", source_root)
            path = source_root / "gigai" / "capabilities.py"
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
    print("caught G17 mutations: " + ", ".join(caught))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
