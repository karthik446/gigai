"""Verify G08's complete offline proposal lifecycle from an installed wheel."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import uuid

from gigai.lifecycle import approve_offline, create_offline, record_feedback, revise_offline
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import validate_proposal_workpad


def _uuids():
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{value:012x}")
        for value in range(1, 48)
    )
    return lambda: next(values)


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), *args],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"installed G08 Git check {args!r} failed: {result.stderr}")
    return result.stdout


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="gigai-g08-wheel-") as temporary:
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
        uuids = _uuids()
        created = create_offline(
            home_root=home,
            requested_target=target,
            name="installed-offline-proof",
            open_editor=False,
            uuid_factory=uuids,
        )
        record_feedback(
            home_root=home,
            requested_target=target,
            proposal_id=created.proposal_id,
            feedback="Keep the installed proof offline.\n",
            uuid_factory=uuids,
        )
        revised = revise_offline(
            home_root=home,
            requested_target=target,
            proposal_id=created.proposal_id,
            change_request="Add an installed-wheel review boundary.",
            uuid_factory=uuids,
        )
        if not validate_proposal_workpad(created.workpad).valid:
            raise SystemExit("installed G08 proposal was invalid before approval")
        approved = approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=revised.proposal_id,
            uuid_factory=uuids,
        )
        if _git(created.workpad, "rev-parse", approved.tag).strip() != approved.sealed_commit:
            raise SystemExit("installed G08 approval tag did not name the sealed commit")
        pointer = (created.workpad / "manifests" / "active-gig-version.json").read_text(
            encoding="utf-8"
        )
        if approved.sealed_commit not in pointer:
            raise SystemExit("installed G08 active-version pointer missed Commit A")
        if _git(created.workpad, "rev-list", "--count", "HEAD").strip() != "6":
            raise SystemExit("installed G08 lifecycle did not produce six journal commits")
    print("verified installed GigAI G08 offline proposal lifecycle")


if __name__ == "__main__":
    main()
