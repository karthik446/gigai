"""Verify G19 through a freshly installed GigAI distribution."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile
import uuid

from gigai.lifecycle import approve_offline, create_offline
from gigai.review_loop import run_review_loop
from gigai.run import launch_run
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.target_effect import apply_target_effect, authorize_target_effect, prepare_target_effect
from gigai.workpad import resolve_workpad


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)
    return result


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gigai-g19-installed-") as raw_root:
        root = Path(raw_root)
        home = root / "home"
        target = root / "target"
        target.mkdir()
        _run(["git", "-C", str(target), "init", "--initial-branch=main"])
        _run(["git", "-C", str(target), "config", "user.name", "G19 Installed Fixture"])
        _run(["git", "-C", str(target), "config", "user.email", "g19-installed@example.invalid"])
        (target / "README.md").write_text("before\n", encoding="utf-8")
        _run(["git", "-C", str(target), "add", "README.md"])
        _run(["git", "-C", str(target), "commit", "-m", "installed baseline"])
        run_setup(
            build_config(
                home_root=home,
                workpad_root=root / "workpads",
                editor_argv=("/usr/bin/true",),
                open_with_target=False,
            )
        )
        initialize_target(home_root=home, requested_target=target)
        values = iter(
            uuid.UUID(f"00000000-0000-4000-8000-{index:012x}")
            for index in range(1, 200)
        )
        created = create_offline(
            home_root=home,
            requested_target=target,
            name="installed-g19",
            open_editor=False,
            uuid_factory=lambda: next(values),
        )
        approved = approve_offline(
            home_root=home,
            requested_target=target,
            proposal_id=created.proposal_id,
            uuid_factory=lambda: next(values),
        )
        resolved = resolve_workpad(
            home_root=home,
            requested_target=target,
            gig_id=created.gig_id,
            allow_semantic_state=True,
        )
        run_id = launch_run(
            home_root=home,
            requested_target=target,
            gig_id=created.gig_id,
            wait=True,
            uuid_factory=lambda: next(values),
        ).run_id
        review = run_review_loop(
            workpad=resolved.path,
            project_id=resolved.project_id,
            gig_id=created.gig_id,
            run_id=run_id,
            profile="repository",
        )
        if review.state != "complete" or review.addressed_artifact_id is None:
            raise SystemExit("installed G19 prerequisite Review Loop did not complete")
        before_head = _run(["git", "-C", str(target), "rev-parse", "HEAD"]).stdout.strip()
        source_path = f"addressed/{review.addressed_artifact_id}.json"
        authorized = authorize_target_effect(
            resolved=resolved,
            proposal_id=approved.proposal_id,
            relative_target_path="README.md",
            source_artifact_path=source_path,
            operator={"kind": "operator", "id": "installed-user"},
        )
        prepare_target_effect(
            resolved=resolved,
            effect_id=str(authorized.record["effect_id"]),
        )
        applied = apply_target_effect(
            resolved=resolved,
            effect_id=str(authorized.record["effect_id"]),
        )
        if applied.record["state"] != "applied":
            raise SystemExit(f"installed G19 effect did not apply: {applied.record}")
        if (target / "README.md").read_bytes() != (resolved.path / source_path).read_bytes():
            raise SystemExit("installed G19 target bytes do not match the authorized source")
        if _run(["git", "-C", str(target), "rev-parse", "HEAD"]).stdout.strip() != before_head:
            raise SystemExit("installed G19 created an unauthorized target commit")
        status = _run(["git", "-C", str(target), "status", "--porcelain=v1"]).stdout.splitlines()
        if status != [" M README.md"]:
            raise SystemExit(f"installed G19 target delta is not exact: {status}")
    print("verified installed GigAI G19 target effect")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
