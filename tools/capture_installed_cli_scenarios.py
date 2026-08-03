"""Capture normalized G02 scenario artifacts from an installed executable."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import tempfile

from tests.scenarios import (
    InstalledGigAI,
    ScenarioHarness,
    ScenarioRoots,
    ScenarioSpec,
    copy_fixture_repository,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    arguments = parser.parse_args()

    executable = arguments.executable.resolve(strict=True)
    os.environ["GIGAI_TEST_EXECUTABLE"] = os.fspath(executable)
    installed = InstalledGigAI.current()
    arguments.output_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="gigai-g02-scenarios-") as temporary:
        roots = ScenarioRoots.create(Path(temporary) / "installed-cli")
        copy_fixture_repository(
            "non-python", roots.target, fixture_root=roots.fixtures
        )
        harness = ScenarioHarness(installed.command, roots)
        for name, argv in (("help", ("--help",)), ("version", ("--version",))):
            result = harness.run(ScenarioSpec(name=name, argv=argv))
            shutil.copy2(
                result.artifact,
                arguments.output_dir / f"scenario-{result.artifact.name}",
            )

    print(f"captured installed GigAI scenarios in {arguments.output_dir}")


if __name__ == "__main__":
    main()
