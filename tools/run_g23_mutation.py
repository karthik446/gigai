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
        "tests/test_g23_portability.py::test_g23_lineage_refusals_walk_real_git_history",
    ),
    (
        "lineage-cycle-guard",
        "        if current in seen:\n",
        "        if False:\n",
        "tests/test_g23_portability.py::test_g23_lineage_refusals_walk_real_git_history",
    ),
    (
        "lineage-missing-parent-guard",
        "        if proposal is None:\n",
        "        if False:\n",
        "tests/test_g23_portability.py::test_g23_lineage_refusals_walk_real_git_history",
    ),
    (
        "ambiguous-publication-identity",
        "                    or pointer.get(\"approved_proposal_id\") != expected_proposal_id\n",
        "                    or False\n",
        "tests/test_g23_portability.py::test_g23_forged_publication_child_is_ambiguous",
    ),
    (
        "ambiguous-publication-count",
        "    if len(publication_commits) != 1:\n",
        "    if len(publication_commits) < 1:\n",
        "tests/test_g23_portability.py::test_g23_two_genuine_publication_children_are_ambiguous",
    ),
    (
        "publication-child-sealed-commit",
        "                    or pointer.get(\"journal_commit\") != sealed_commit\n",
        "                    or False\n",
        "tests/test_g23_portability.py::test_g23_publication_child_sealed_commit_mismatch_is_ambiguous",
    ),
    (
        "publication-child-tag-resolution",
        "                if resolved_tag.returncode != 0 or resolved_tag.stdout.strip() != sealed_commit:\n",
        "                if False:\n",
        "tests/test_g23_portability.py::test_g23_publication_child_tag_mismatch_is_ambiguous",
    ),
    (
        "tag-resolution-guard",
        "def _require_approval_tag(root: Path, tag: str, sealed_commit: str) -> None:\n"
        "    resolved_tag = _git(root, \"rev-parse\", \"--verify\", tag, check=False)\n"
        "    if resolved_tag.returncode != 0 or resolved_tag.stdout.strip() != sealed_commit:\n",
        "def _require_approval_tag(root: Path, tag: str, sealed_commit: str) -> None:\n"
        "    resolved_tag = _git(root, \"rev-parse\", \"--verify\", tag, check=False)\n"
        "    if False:\n",
        "tests/test_g23_portability.py::test_g23_tag_resolution_is_independently_refused",
    ),
    (
        "lineage-create-terminus",
        "            if proposal.get(\"kind\") != \"create\":\n",
        "            if False:\n",
        "tests/test_g23_portability.py::test_g23_lineage_refusals_are_closed",
    ),
    (
        "historical-proposal-schema-guard",
        "        _require_schema(\"gig-proposal.schema.json\", canonical_json_bytes(payload), \"refused_lineage_authority\")\n",
        "        if False:\n",
        "tests/test_g23_portability.py::test_g23_historical_proposal_schema_is_real_git_guarded",
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
