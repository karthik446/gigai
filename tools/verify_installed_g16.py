"""Verify the installed G16 deterministic Review Loop implementation."""

from __future__ import annotations

from pathlib import Path
import tempfile
import uuid

from gigai.lifecycle import approve_offline, create_offline
from gigai.review_loop import run_review_loop
from gigai.run import launch_run
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.workpad import resolve_workpad


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="gigai-g16-wheel-") as directory:
        root = Path(directory)
        home = root / "home"
        target = root / "target"
        target.mkdir()
        run_setup(build_config(home_root=home, workpad_root=root / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
        initialize_target(home_root=home, requested_target=target, uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"))
        values = iter(uuid.UUID(f"00000000-0000-4000-8000-{index:012x}") for index in range(1, 100))
        created = create_offline(home_root=home, requested_target=target, name="g16-installed", open_editor=False, uuid_factory=lambda: next(values))
        approve_offline(home_root=home, requested_target=target, proposal_id=created.proposal_id, uuid_factory=lambda: next(values))
        resolved = resolve_workpad(home_root=home, requested_target=target, gig_id=created.gig_id, allow_semantic_state=True)
        profiles = ("research", "climate", "pull-request", "repository", "spreadsheet")
        for index, profile in enumerate(profiles, start=1):
            values = iter(uuid.UUID(f"00000000-0000-4000-8000-{(index * 30 + offset):012x}") for offset in range(20))
            sealed = launch_run(home_root=home, requested_target=target, gig_id=created.gig_id, wait=True, uuid_factory=lambda: next(values))
            if sealed.status != "succeeded":
                raise SystemExit(f"installed G16 prerequisite Run did not complete: {profile}")
            run_id = sealed.run_id
            result = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=created.gig_id, run_id=run_id, profile=profile)
            if result.state != "complete":
                raise SystemExit(f"installed G16 profile did not complete: {profile}")
        values = iter(uuid.UUID(f"00000000-0000-4000-8000-{(200 + offset):012x}") for offset in range(20))
        sealed = launch_run(home_root=home, requested_target=target, gig_id=created.gig_id, wait=True, uuid_factory=lambda: next(values))
        blocked = run_review_loop(workpad=resolved.path, project_id=resolved.project_id, gig_id=created.gig_id, run_id=sealed.run_id, profile="research", cycle_limit_case=True)
        if blocked.state != "blocked":
            raise SystemExit("installed G16 cycle-limit fixture did not block")
    print("verified installed GigAI G16 deterministic Review Loop")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
