#!/usr/bin/env python3
"""Test exact Ollama/runner endpoint policy against a distinct local port.

The profile permits only the API port 11499 and denies a separate task-owned
local listener at 11501, including for the actor's child. This does not claim
that the dynamic Ollama runner can use the policy; the compatibility probe
records that separately.
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


ALLOWED_PORT = 11499
FORBIDDEN_PORT = 11501


def _receiver(port: int, stop: threading.Event, seen: list[int], ready: list[int]) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind(("127.0.0.1", port))
        server.listen(4)
        server.settimeout(0.25)
        ready.append(port)
        while not stop.is_set():
            try:
                conn, _address = server.accept()
            except TimeoutError:
                continue
            with conn:
                seen.append(1)


def _actor(actor: Path, public: Path, private: Path, profile: str | None) -> dict[str, object]:
    command = [
        sys.executable,
        str(actor),
        "--public",
        str(public),
        "--private",
        str(private),
        "--port",
        str(ALLOWED_PORT),
        "--forbidden-port",
        str(FORBIDDEN_PORT),
        "--outward",
    ]
    if profile is not None:
        command = ["/usr/bin/sandbox-exec", "-p", profile, *command]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    try:
        result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = {"invalid_output": True}
    result["returncode"] = completed.returncode
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(tempfile.mkdtemp(prefix="scout-endpoint-", dir=args.output.parent))
    public = root / "selected-public.txt"
    private_dir = root / "unselected-private"
    private_dir.mkdir()
    private = private_dir / "sentinel.txt"
    public.write_text("SYNTHETIC-SELECTED-PUBLIC", encoding="utf-8")
    private.write_text("SYNTHETIC-UNSELECTED-SENTINEL", encoding="utf-8")
    stop = threading.Event()
    allowed_seen: list[int] = []
    forbidden_seen: list[int] = []
    allowed_ready: list[int] = []
    forbidden_ready: list[int] = []
    threads = [
        threading.Thread(target=_receiver, args=(ALLOWED_PORT, stop, allowed_seen, allowed_ready), daemon=True),
        threading.Thread(target=_receiver, args=(FORBIDDEN_PORT, stop, forbidden_seen, forbidden_ready), daemon=True),
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + 2
    while (len(allowed_ready) < 1 or len(forbidden_ready) < 1) and time.monotonic() < deadline:
        time.sleep(0.01)
    if len(allowed_ready) != 1 or len(forbidden_ready) != 1:
        raise SystemExit("task-owned receiver did not bind")
    actor = Path(__file__).with_name("enforcement_actor.py")
    positive = _actor(actor, public, private, None)
    profile = (
        '(version 1) (allow default) (deny network*) '
        '(allow network-inbound (local ip "localhost:11499")) '
        '(allow network-outbound (remote ip "localhost:11499")) '
        f'(deny file-read* (subpath "{private_dir}"))'
    )
    restricted = _actor(actor, public, private, profile)
    stop.set()
    for thread in threads:
        thread.join(timeout=1)
    result = {
        "allowed_endpoint": ALLOWED_PORT,
        "forbidden_endpoint": FORBIDDEN_PORT,
        "positive_control": positive,
        "restricted_exact_endpoint_policy": restricted,
        "receiver_connections": {
            "allowed": len(allowed_seen),
            "forbidden": len(forbidden_seen),
        },
        "profile": profile,
        "interpretation": {
            "selected_read_allowed": restricted.get("selected_read") == "allowed",
            "unselected_read_denied": str(restricted.get("unselected_read", "")).startswith("denied:"),
            "allowed_endpoint_direct": restricted.get("network_connect") == "allowed",
            "allowed_endpoint_child": restricted.get("child", {}).get("network_connect") == "allowed",
            "forbidden_endpoint_direct_denied": str(restricted.get("forbidden_network_connect", "")).startswith("denied:"),
            "forbidden_endpoint_child_denied": str(restricted.get("child", {}).get("forbidden_network_connect", "")).startswith("denied:"),
            "outward_direct_denied": str(restricted.get("outward_network_connect", "")).startswith("denied:"),
            "outward_child_denied": str(restricted.get("child", {}).get("outward_network_connect", "")).startswith("denied:"),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "interpretation": result["interpretation"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
