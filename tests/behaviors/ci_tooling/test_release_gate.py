"""release-gate: exact-tag CI is skipped only when a gate passed on the same tree.

Fixture values are the real v0.1.9 ones: the squash merge 80865ec and the
tested PR head 9728387 share tree 12e7cfa. Pure: no network, no git process.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools import release_gate

TREE = "12e7cfa25b681d93939d00fffc885a3a1f0d4393"
SQUASH = "80865ec"
PR_HEAD = "9728387"
TREES = {SQUASH: TREE, PR_HEAD: TREE, "aaaaaaa": "f" * 40, "bbbbbbb": "e" * 40}
TITLE = release_gate.GATE_DISPLAY_TITLE


def _run(sha: str, title: str = TITLE, conclusion: str = "success") -> dict[str, str]:
    return {"head_sha": sha, "display_title": title, "conclusion": conclusion}


def test_squash_sha_and_pr_head_resolve_to_the_same_gate_run() -> None:
    runs = [_run("aaaaaaa"), _run(PR_HEAD), _run("bbbbbbb")]
    found_for_pr_head = release_gate.find_gate_commit(TREES[PR_HEAD], runs, TREES.get)
    found_for_squash = release_gate.find_gate_commit(TREES[SQUASH], runs, TREES.get)
    assert found_for_pr_head == found_for_squash == PR_HEAD


def test_no_matching_tree_falls_back_to_running_ci() -> None:
    runs = [_run("aaaaaaa"), _run("bbbbbbb")]
    assert release_gate.find_gate_commit(TREE, runs, TREES.get) is None
    assert release_gate.find_gate_commit(TREE, [], TREES.get) is None


def test_only_successful_release_gate_runs_count() -> None:
    runs = [
        _run(PR_HEAD, title="Pull request (pr)"),
        _run(PR_HEAD, title="Pull request (full)"),
        _run(PR_HEAD, conclusion="failure"),
    ]
    assert release_gate.find_gate_commit(TREE, runs, TREES.get) is None


def test_unknown_commit_tree_is_not_a_match() -> None:
    assert release_gate.find_gate_commit(TREE, [_run("ccccccc")], TREES.get) is None


def test_release_workflow_uses_the_lookup_and_ci_skips_on_a_green_gate() -> None:
    path = Path(__file__).resolve().parents[3] / ".github/workflows/release.yml"
    if not path.is_file():
        pytest.skip("release workflow is excluded from the offline container build context")
    text = path.read_text(encoding="utf-8")
    assert 'python tools/release_gate.py --commit "${COMMIT}"' in text
    assert "gate_green: ${{ steps.gate.outputs.gate_green }}" in text
    assert "if: needs.preflight.outputs.gate_green != 'true'" in text
