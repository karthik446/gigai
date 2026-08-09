from __future__ import annotations

import json
from pathlib import Path
import os
import re
import subprocess
import sys
from urllib.request import Request, urlopen

from gigai.canonical import parse_json_bytes
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _setup(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    target = tmp_path / "target"
    target.mkdir()
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: __import__("uuid").UUID("12345678-1234-4234-9234-123456789abc"),
    )
    return home, target


def test_cli_create_launches_and_completes_local_interview(tmp_path: Path) -> None:
    home, target = _setup(tmp_path)
    reference = tmp_path / "source.txt"
    reference.write_bytes(b"CLI exact reference bytes\n")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    process = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "from gigai.cli import cli; cli()",
            "create",
            "cli-proof",
            "--home",
            str(home),
            "--target",
            str(target),
            "--reference",
            str(reference),
            "--request",
            "Create a bounded CLI proposal.",
            "--no-open",
            "--json",
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        line = process.stderr.readline().strip()
        match = re.fullmatch(r"GigAI local interview: (http://127\.0\.0\.1:\d+/session/[A-Za-z0-9_-]+)", line)
        assert match is not None, line
        url = match.group(1)
        snapshot_path = next((tmp_path / "workpads").rglob("manifests/proposal-interview.json"))
        snapshot = parse_json_bytes(snapshot_path.read_bytes())
        reference_id = snapshot["references"][0]["reference_id"]
        endpoint = f"{url}/events"

        def send(question_id: str, value: object) -> None:
            request = Request(
                endpoint,
                data=json.dumps({"event": "answer", "question_id": question_id, "value": value}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(request, timeout=4) as response:
                assert response.status == 200

        send("scope", "Create a bounded CLI proposal.")
        send("references", [reference_id])
        send("effect", "write_workpad")
        send("privacy", "local_only")
        send("capability", "none")
        send("operator-confirmation", True)
        with urlopen(
            Request(endpoint, data=b'{"event":"approve"}', headers={"Content-Type": "application/json"}, method="POST"),
            timeout=4,
        ) as response:
            assert json.loads(response.read())["state"] == "approved"
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, stderr
        payload = json.loads(stdout)
        assert payload["status"] == "approved"
        assert payload["session_id"] == snapshot["session_id"]
        assert payload["proposal_id"].startswith("gp_")
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
