"""0110-10-08: the undefined-name check (`make lint`: ruff's pyflakes rules over src/gigai) and the CI job that runs it.

0.1.10.9 shipped ``time.monotonic()`` in a module with no ``import time``, on a
line only a large store reaches. Nothing read the code for names that are
never defined: there was no lint step at all. These tests keep the step: the
make target, its pinned ruff, and a ``lint`` job that every PR and every
release pre-check runs whatever changed.

0110-10-hf2: the step selects every pyflakes rule (``F``): undefined names
(F821, F823) and also unused imports and variables (F401, F841) and
redefinitions (F811). A narrower selection is a failure here, and so is a
``# noqa: F...`` in the package that does not say why.
"""

from __future__ import annotations

from pathlib import Path
import re

import pytest

from tools import release_notes

ROOT = Path(__file__).resolve().parents[3]


def _read(relative: str, why: str) -> str:
    path = ROOT / relative
    if not path.is_file():
        pytest.skip(why)
    return path.read_text(encoding="utf-8")


def test_make_lint_is_ruff_pyflakes_over_the_package_with_a_pinned_version() -> None:
    makefile = _read("Makefile", "the Makefile is excluded from the offline container build context")

    assert re.search(r"^RUFF_VERSION \?= \d+\.\d+\.\d+$", makefile, re.MULTILINE), "ruff's version is pinned in the Makefile"
    # Every F rule, never fixed in place: F821 and F823 (the 0.1.10.9 crash) are two of them.
    assert "\nlint:\n\t$(UV) tool run ruff@$(RUFF_VERSION) check --select F --no-fix src/gigai\n" in makefile


def test_every_pyflakes_exception_in_the_package_says_why() -> None:
    """``# noqa: F401`` alone hides the next real unused import on that line: each one says why it is there, on that line."""

    bare = []
    for path in sorted((ROOT / "src" / "gigai").rglob("*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            found = re.search(r"#\s*noqa:\s*F\d+(?:\s*,\s*[A-Z]+\d+)*(.*)$", line)
            if found and not re.search(r"[A-Za-z]{3,}", found.group(1)):
                bare.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()}")
    assert not bare, "\n".join(bare)


def test_the_lint_job_runs_make_lint_on_every_pr_and_profile_whatever_changed() -> None:
    workflow = _read(".github/workflows/pull_request.yaml", "workflows are excluded from the offline container build context")
    jobs = release_notes.parse_workflow_jobs(workflow)

    assert jobs["lint"].needs == []  # not behind `changes`: a docs-only PR is linted too
    body = workflow.split("\n  lint:\n", 1)[1]
    job = body[: min(index for index in (body.find(f"\n  {name}:\n") for name in jobs) if index != -1)]
    assert "\n    if:" not in job  # no profile condition: pr, release and full
    assert "      - run: make lint\n" in job
    assert "continue-on-error" not in job
