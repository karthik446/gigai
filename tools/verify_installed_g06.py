"""Verify G06 journal behavior using only an installed GigAI wheel."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

from gigai.journal import record_transition
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import provision_workpad, resolve_workpad


GIG_ID = "gig_aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
HANDOFF_ID = "handoff_11111111-1234-4abc-8def-123456789abc"


def _git(root: Path, *args: str, expected: int = 0) -> str:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != expected:
        raise SystemExit(f"installed G06 Git check {args!r} failed: {result.stderr}")
    return result.stdout


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gigai-g06-wheel-") as temporary:
        root = Path(temporary)
        home, workpads, target = root / "home", root / "workpads", root / "target"
        target.mkdir()
        run_setup(build_config(home_root=home, workpad_root=workpads, editor_argv=("/usr/bin/true",), open_with_target=False))
        binding = initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
        workpad = provision_workpad(home_root=home, project_id=binding.project_id, gig_id=GIG_ID).path
        entry = record_transition(workpad=workpad, project_id=binding.project_id, gig_id=GIG_ID, handoff_id=HANDOFF_ID, transition="creation_started", body="Installed G06 verifier.")
        if entry.sequence != 1 or not entry.path.is_file():
            raise SystemExit("installed G06 did not create the first durable handoff")
        trailers = _git(workpad, "show", "--format=%B", "--no-patch", "HEAD")
        if "GigAI-Handoff-Sequence: 000000000001" not in trailers or HANDOFF_ID not in trailers:
            raise SystemExit("installed G06 first commit lacks required trailers")
        if resolve_workpad(home_root=home, requested_target=target, gig_id=GIG_ID).path != workpad:
            raise SystemExit("installed G05 resolver rejected the G06 journal workpad")
    print("verified installed GigAI G06 journal first commit and trailer sequencing")


if __name__ == "__main__":
    main()
