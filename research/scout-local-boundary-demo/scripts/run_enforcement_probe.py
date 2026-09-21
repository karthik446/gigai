#!/usr/bin/env python3
"""Probe available macOS sandbox-exec file/network enforcement.

This is a primitive probe, not proof that the existing Ollama daemon is
sandboxed. It uses only synthetic files and a task-owned loopback receiver.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time


def _receiver(stop: threading.Event, ready: list[int], seen: list[int]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", 0))
        server.listen(4)
        server.settimeout(0.25)
        ready.append(server.getsockname()[1])
        while not stop.is_set():
            try:
                conn, _address = server.accept()
            except TimeoutError:
                continue
            with conn:
                seen.append(1)


def _run_actor(actor: Path, public: Path, private: Path, port: int, profile: str | None) -> dict[str, object]:
    command = [sys.executable, str(actor), "--public", str(public), "--private", str(private), "--port", str(port)]
    if profile is not None:
        command = ["/usr/bin/sandbox-exec", "-p", profile, *command]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = {"invalid_output": True}
    result["returncode"] = completed.returncode
    result["sandboxed"] = profile is not None
    if completed.stderr:
        # Keep diagnostics bounded and exclude process paths/fixture bytes.
        result["stderr_class"] = completed.stderr.splitlines()[-1][:160]
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="scout-boundary-", dir=args.output.parent))
    public = root / "public-selected.txt"
    private_dir = root / "private-unselected"
    private_dir.mkdir()
    private = private_dir / "sentinel.txt"
    public.write_text("SYNTHETIC-SELECTED-PUBLIC-CONTENT", encoding="utf-8")
    private.write_text("SYNTHETIC-UNSELECTED-SENTINEL-DO-NOT-EXPORT", encoding="utf-8")
    stop = threading.Event()
    ready: list[int] = []
    seen: list[int] = []
    receiver = threading.Thread(target=_receiver, args=(stop, ready, seen), daemon=True)
    receiver.start()
    deadline = time.monotonic() + 2
    while not ready and time.monotonic() < deadline:
        time.sleep(0.01)
    if not ready:
        raise SystemExit("task-owned receiver did not bind")
    port = ready[0]
    actor = Path(__file__).with_name("enforcement_actor.py")
    positive = _run_actor(actor, public, private, port, None)
    profile = (
        '(version 1) (allow default) '
        f'(deny file-read* (subpath "{private_dir}")) (deny network*)'
    )
    restricted = _run_actor(actor, public, private, port, profile)
    stop.set()
    receiver.join(timeout=1)
    result = {
        "synthetic_root": str(root),
        "receiver_connections": len(seen),
        "positive_control": positive,
        "restricted_sandbox": restricted,
        "interpretation": {
            "positive_read_and_network": positive.get("unselected_read") == "allowed" and positive.get("network_connect") == "allowed" and positive.get("child", {}).get("network_connect") == "allowed",
            "restricted_selected_read": restricted.get("selected_read") == "allowed",
            "restricted_unselected_read": str(restricted.get("unselected_read", "")).startswith("denied:"),
            "restricted_network": str(restricted.get("network_connect", "")).startswith("denied:"),
            "restricted_child_network": str(restricted.get("child", {}).get("network_connect", "")).startswith("denied:"),
        },
        "note": "This demonstrates sandbox-exec inheritance for a synthetic actor, not isolation of the existing Ollama daemon.",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "receiver_connections": len(seen), "interpretation": result["interpretation"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
