"""Verify G22's local interview through a freshly installed console script."""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen


def _run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(result.stderr or result.stdout)


def main() -> int:
    executable = Path(sys.prefix) / "bin" / "gigai"
    if not executable.is_file():
        raise SystemExit(f"installed gigai console script is missing: {executable}")
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    with tempfile.TemporaryDirectory(prefix="gigai-g22-installed-") as raw_root:
        root = Path(raw_root)
        target = root / "target"
        home = root / "home"
        workpads = root / "workpads"
        target.mkdir()
        workpads.mkdir()
        reference = root / "reference.txt"
        reference.write_bytes(b"installed G22 exact reference bytes\n")
        _run(
            [
                str(executable),
                "setup",
                "--non-interactive",
                "--home",
                str(home),
                "--workpad-root",
                str(workpads),
                "--editor",
                "/usr/bin/true",
                "--no-open-with-target",
                "--json",
            ]
        )
        _run([str(executable), "init", "--home", str(home), "--target", str(target), "--json"])
        process = subprocess.Popen(
            [
                str(executable),
                "create",
                "installed-proof",
                "--home",
                str(home),
                "--target",
                str(target),
                "--reference",
                str(reference),
                "--request",
                "Create an installed-wheel proposal.",
                "--no-open",
                "--json",
            ],
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert process.stderr is not None
        line = process.stderr.readline().strip()
        match = re.fullmatch(
            r"GigAI local interview: (http://127\.0\.0\.1:\d+/session/[A-Za-z0-9_-]+)",
            line,
        )
        if match is None:
            raise SystemExit(f"installed G22 interview did not launch: {line!r}")
        url = match.group(1)
        snapshot_path = next(workpads.rglob("manifests/proposal-interview.json"))
        endpoint = f"{url}/events"

        def send(question_id: str, value: object) -> None:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            payload = {
                "event": "answer",
                "question_id": question_id,
                "value": value,
                "revision": snapshot["revision"],
                "sequence": len(snapshot["events"]) + 1,
            }
            with urlopen(
                Request(
                    endpoint,
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=4,
            ) as response:
                if response.status != 200:
                    raise SystemExit(f"answer failed with HTTP {response.status}")

        reference_id = json.loads(snapshot_path.read_text(encoding="utf-8"))["references"][0]["reference_id"]
        send("scope", "Create an installed-wheel proposal.")
        send("references", [reference_id])
        send("effect", "write_workpad")
        send("privacy", "local_only")
        send("capability", "none")
        send("operator-confirmation", True)
        snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        approval = {
            "event": "approve",
            "revision": snapshot["revision"],
            "sequence": len(snapshot["events"]) + 1,
        }
        with urlopen(
            Request(
                endpoint,
                data=json.dumps(approval).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            ),
            timeout=4,
        ) as response:
            if json.loads(response.read())["state"] != "approved":
                raise SystemExit("installed G22 approval did not reach approved")
        stdout, stderr = process.communicate(timeout=10)
        if process.returncode != 0:
            raise SystemExit(stderr)
        summary = json.loads(stdout)
        if summary["status"] != "approved" or not summary["proposal_id"].startswith("gp_"):
            raise SystemExit(f"unexpected installed G22 summary: {summary}")
        if (workpads / "runs").exists():
            raise SystemExit("installed G22 unexpectedly created a Run")
    print("verified installed GigAI G22 create interview")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
