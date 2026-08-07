"""Verify the installed G14 sequential Goal scheduler."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from gigai.lifecycle import approve_offline, create_offline
from gigai.run import read_run_details
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def main() -> None:
    executable = Path(os.sys.executable).parent / "gigai"
    with tempfile.TemporaryDirectory(prefix="gigai-g14-wheel-") as temporary:
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
        values = iter(
            uuid.UUID(f"00000000-0000-4000-8000-{index:012x}")
            for index in range(1, 40)
        )
        created = create_offline(
            home_root=home,
            requested_target=target,
            name="installed-g14-proof",
            open_editor=False,
            uuid_factory=lambda: next(values),
        )
        approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=created.proposal_id,
            uuid_factory=lambda: next(values),
        )
        result = subprocess.run(
            [
                os.fspath(executable), "run", created.gig_id,
                "--target", os.fspath(target), "--home", os.fspath(home),
                "--wait", "--json",
            ],
            capture_output=True, text=True, check=False, shell=False,
        )
        if result.returncode != 0:
            raise SystemExit(f"installed G14 run failed: {result.stderr}")
        summary = json.loads(result.stdout)
        details = read_run_details(
            home_root=home,
            requested_target=target,
            gig_id=created.gig_id,
            run_id=summary["run_id"],
        )
        if details["status"] != "succeeded":
            raise SystemExit("installed G14 scheduler did not succeed")
        if not details["goals"] or not all(
            goal["status"] == "complete" for goal in details["goals"]
        ):
            raise SystemExit("installed G14 scheduler did not complete every Goal")
        if details["realized_max_parallel_goals"] != 1:
            raise SystemExit("installed G14 scheduler violated sequential execution")
    print("verified installed GigAI G14 sequential Goal scheduler")


if __name__ == "__main__":
    main()
