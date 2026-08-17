from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from gigai.canonical import parse_json_bytes
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def test_create_runs_model_facilitated_build_then_explicit_approval(tmp_path: Path) -> None:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=tmp_path / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(home_root=home, requested_target=target)
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from gigai.cli import cli; cli()",
            "create",
            "builder-proof",
            "--home",
            str(home),
            "--target",
            str(target),
            "--no-open",
            "--json",
        ],
        env={**__import__("os").environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = process.stderr.readline().strip()
        match = re.fullmatch(r"GigAI local interview: (http://127\.0\.0\.1:\d+/session/[A-Za-z0-9_-]+)", line)
        assert match is not None, line
        session_url = match.group(1)
        endpoint = f"{session_url}/events"
        snapshot_path = next((tmp_path / "workpads").rglob("manifests/proposal-interview.json"))

        def send(event: str, **values: object) -> dict[str, object]:
            current = parse_json_bytes(snapshot_path.read_bytes())
            payload = {
                "event": event,
                "revision": current["revision"],
                "sequence": len(current["events"]) + 1,
                **values,
            }
            try:
                with urlopen(
                    Request(
                        endpoint,
                        data=json.dumps(payload).encode(),
                        headers={"Content-Type": "application/json"},
                        method="POST",
                    ),
                    timeout=5,
                ) as response:
                    return json.loads(response.read())
            except HTTPError as error:
                raise AssertionError(error.read().decode()) from error

        send("answer", question_id="scope", value="Review this repository")
        snapshot = parse_json_bytes(snapshot_path.read_bytes())
        desired_outputs = next(
            item for item in snapshot["questions"] if item["question_id"] == "desired-outputs"
        )
        assert desired_outputs["provenance"].startswith("model://")
        send("answer", question_id="desired-outputs", value=["comparison"])
        snapshot = parse_json_bytes(snapshot_path.read_bytes())
        changing_context = next(
            item for item in snapshot["questions"] if item["question_id"] == "changing-context"
        )
        assert changing_context["provenance"].startswith("model://")
        send("answer", question_id="changing-context", value="The repository changes between Runs")
        send("build")
        with urlopen(session_url, timeout=5) as response:
            review_html = response.read().decode()
        assert "A local Gig proposal assembled" in review_html
        assert "The operator will review" in review_html
        workpad = next((tmp_path / "workpads").rglob("manifests/gig-proposal.json")).parent.parent
        assert (workpad / "manifests/proposal-draft-manifest.json").is_file()
        discovery_manifest = parse_json_bytes(
            (workpad / "manifests/gig-discovery-manifest.json").read_bytes()
        )
        assert discovery_manifest["request_kind"] == "create"
        assert len(discovery_manifest["question_rounds"][0]["questions"]) == 3
        builder_snapshot = parse_json_bytes((workpad / "manifests/gig-builder-session.json").read_bytes())
        assert builder_snapshot["state"] == "operator_review"
        send("approve")
        stdout, stderr = process.communicate(timeout=20)
        assert process.returncode == 0, stderr
        result = json.loads(stdout)
        assert result["status"] == "approved"
        assert (workpad / "manifests/active-gig-version.json").is_file()
        proposal_commits = subprocess.check_output(
            ["git", "-C", str(workpad), "log", "--format=%H", "--", "manifests/gig-proposal.json"],
            text=True,
        ).splitlines()
        proposal_ids = [
            json.loads(
                subprocess.check_output(
                    ["git", "-C", str(workpad), "show", f"{commit}:manifests/gig-proposal.json"],
                    text=True,
                )
            )["proposal_id"]
            for commit in proposal_commits
        ]
        assert len(proposal_ids) >= 1
        assert len(set(proposal_ids)) == 1
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate()
