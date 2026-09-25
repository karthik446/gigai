"""Shared assertions for tests that write into a real managed workpad.

regression-001: no test checked that a managed workpad was left clean by a
run, so a write under an unexcluded path (U26's raw/, B4's progress/) could
leave `git status` dirty without any test noticing. Any test that exercises
a code path writing directly into a real (git-initialized) workpad should
call `assert_managed_workpad_clean` afterward.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess


def assert_managed_workpad_clean(workpad_path: Path) -> None:
    """Assert a real managed workpad's working tree has no divergence.

    Mirrors exactly what `gigai.index._require_clean_authority` checks
    (`git status --porcelain --untracked-files=all`), so a pass here means
    `read_index`/`gigai doctor`'s `journal.index` check will not raise
    "authoritative workpad has uncommitted divergence" on this workpad.
    """

    result = subprocess.run(
        ["git", "-C", os.fspath(workpad_path), "status", "--porcelain", "--untracked-files=all"],
        env={
            **os.environ,
            "GIT_CONFIG_GLOBAL": "/dev/null",
            "GIT_CONFIG_NOSYSTEM": "1",
        },
        capture_output=True,
        text=True,
        check=True,
        shell=False,
    )
    assert result.stdout == "", (
        f"managed workpad {workpad_path} has uncommitted divergence:\n{result.stdout}"
    )


__all__ = ["assert_managed_workpad_clean"]
