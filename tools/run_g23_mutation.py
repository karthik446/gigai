"""Run G23 authority and lineage mutations in disposable source trees."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile


MUTANTS = (
    (
        "sealed-pointer-comparison",
        "    if canonical_json_bytes(live) != canonical_json_bytes(parse_json_bytes(sealed_pointer)):\n",
        "    if False:\n",
        "tests/test_g23_portability.py::test_g23_pointer_substitution_is_refused",
    ),
    (
        "unpublished-pointer-refusal",
        "        if not _publication_children(root, sealed_commit):\n",
        "    if False:\n",
        "tests/test_g23_portability.py::test_g23_unpublished_pointer_is_recoverable_refusal",
    ),
    (
        "manifest-digest-revalidation",
        "    if reference.get(\"content_sha256\") != digest_imported_bytes(payload) or reference.get(\"size_bytes\") != len(payload):\n",
        "    if False:\n",
        "tests/test_g23_portability.py::test_g23_manifest_digest_is_revalidated",
    ),
    (
        "lineage-cross-gig-refusal",
        "        if proposal.get(\"gig_id\") != gig_id:\n",
        "        if False:\n",
        "tests/test_g23_portability.py::test_g23_lineage_refusals_are_closed",
    ),
    (
        "lineage-cycle-guard",
        "        if current in seen:\n",
        "        if False:\n",
        "tests/test_g23_portability.py::test_g23_lineage_refusals_are_closed",
    ),
    (
        "lineage-missing-parent-guard",
        "        if proposal is None:\n",
        "        if False:\n",
        "tests/test_g23_portability.py::test_g23_lineage_refusals_are_closed",
    ),
)


def main() -> int:
    root = Path(__file__).parents[1]
    python = root / ".venv/bin/python"
    caught: list[str] = []
    for name, original, mutant, test in MUTANTS:
        with tempfile.TemporaryDirectory(prefix=f"gigai-g23-mutant-{name}-") as directory:
            source_root = Path(directory) / "src"
            shutil.copytree(root / "src", source_root)
            path = source_root / "gigai/portability.py"
            payload = path.read_text(encoding="utf-8")
            if original not in payload:
                raise SystemExit(f"mutation anchor missing: {name}")
            path.write_text(payload.replace(original, mutant, 1), encoding="utf-8")
            test_path, separator, test_name = test.partition("::")
            selector = str(root / test_path) + (separator + test_name if separator else "")
            try:
                result = subprocess.run(
                    [str(python), "-m", "pytest", "-q", selector],
                    cwd=source_root,
                    env={**os.environ, "PYTHONPATH": str(source_root)},
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=10,
                )
            except subprocess.TimeoutExpired:
                caught.append(name)
                continue
            if result.returncode == 0:
                raise SystemExit(f"mutation survived: {name}")
            caught.append(name)
    print("caught G23 mutations: " + ", ".join(caught))
    print(f"mutation_killed={len(caught)}/{len(MUTANTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
