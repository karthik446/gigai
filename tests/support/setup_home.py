"""0.1.10.9 CIFIX2: a configured GigAI home for a test, on a machine with no model runtime installed.

``gigai setup --non-interactive`` with no ``--create-model-target`` takes its default from the ``codex`` or
``claude`` CLI it finds on ``PATH`` and refuses when neither is installed (``setup_invalid``: "no usable model
runtime is configured"). A developer's machine has them; a CI runner does not. A test that only needs a home
names the deterministic ``ollama_local`` fixture target instead, as the Scout CLI tests do; setup calls no model.
"""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME

#: The ``gigai setup`` flags that configure the fixture model target and make it the default.
FIXTURE_MODEL_ARGS: tuple[str, ...] = (
    "--endpoint", "ollama_local=ollama_local:http://127.0.0.1:11434",
    "--model-target", f"ollama_local=ollama_local:{TEST_MODEL_NAME}@{TEST_MODEL_DIGEST}",
    "--create-model-target", "ollama_local",
)


def setup_home(home: Path, *, workpad_root: Path) -> Path:
    """Run non-interactive ``gigai setup`` for ``home`` with the fixture model target; returns ``home``."""

    result = CliRunner().invoke(
        cli,
        ["setup", "--non-interactive", "--home", str(home), "--workpad-root", str(workpad_root), "--editor", "/usr/bin/true", *FIXTURE_MODEL_ARGS, "--json"],
    )
    assert result.exit_code == 0, result.output
    return home


__all__ = ["FIXTURE_MODEL_ARGS", "setup_home"]
