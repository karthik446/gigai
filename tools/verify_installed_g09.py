"""Verify G09's read-only command surface from an installed wheel."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from gigai.lifecycle import create_offline
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _uuids():
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{value:012x}") for value in range(1, 32)
    )
    return lambda: next(values)


def _run(*args: str) -> str:
    executable = Path(sys.executable).parent / "gigai"
    result = subprocess.run(
        [os.fspath(executable), *args],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"installed G09 command {args!r} failed: {result.stderr}")
    return result.stdout


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gigai-g09-wheel-") as temporary:
        root = Path(temporary)
        home, workpads, target = root / "home", root / "workpads", root / "target"
        target.mkdir()
        run_setup(
            build_config(
                home_root=home,
                workpad_root=workpads,
                editor_argv=("/usr/bin/true",),
                open_with_target=False,
            )
        )
        initialize_target(
            home_root=home,
            requested_target=target,
            uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
        )
        created = create_offline(
            home_root=home,
            requested_target=target,
            name="installed-read-proof",
            open_editor=False,
            uuid_factory=_uuids(),
        )
        common = (
            created.gig_id,
            "--target",
            os.fspath(target),
            "--home",
            os.fspath(home),
            "--json",
        )
        payloads = {
            command: json.loads(_run(command, *common))
            for command in ("proposals", "status", "show", "history", "plan")
        }
        if (
            payloads["status"]["gig_id"] != created.gig_id
            or payloads["plan"]["authority"] != "proposed"
        ):
            raise SystemExit(
                "installed G09 projection did not report proposed authority"
            )
        if (
            json.loads(_run("gigs", "--home", os.fspath(home), "--json"))[0]["gig_id"]
            != created.gig_id
        ):
            raise SystemExit("installed G09 gigs omitted the created Gig")
    print("verified installed GigAI G09 rebuildable index and read commands")


if __name__ == "__main__":
    main()
