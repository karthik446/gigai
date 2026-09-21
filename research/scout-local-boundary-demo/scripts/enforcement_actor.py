#!/usr/bin/env python3
"""Synthetic actor used by the macOS sandbox boundary probe.

It records only access outcomes and never transmits the sentinel or fixture
contents. A child process repeats the network attempt to test inheritance.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys


def _read(path: Path) -> str:
    try:
        path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 - probe records only safe class
        return f"denied:{type(exc).__name__}"
    return "allowed"


def _connect(host: str, port: int) -> str:
    try:
        with socket.create_connection((host, port), timeout=1.0):
            return "allowed"
    except Exception as exc:  # noqa: BLE001 - probe records only safe class
        return f"denied:{type(exc).__name__}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--public", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--forbidden-port", type=int)
    parser.add_argument("--outward", action="store_true")
    args = parser.parse_args()
    child_code = """
import json, socket, sys
from pathlib import Path
private = Path(sys.argv[1])
try:
    private.read_text(encoding='utf-8')
    private_result = 'allowed'
except Exception as exc:
    private_result = 'denied:' + type(exc).__name__
try:
    with socket.create_connection(('127.0.0.1', int(sys.argv[2])), timeout=1.0):
        network_result = 'allowed'
except Exception as exc:
    network_result = 'denied:' + type(exc).__name__
forbidden_result = None
if len(sys.argv) > 3 and sys.argv[3] != '--outward':
    try:
        with socket.create_connection(('127.0.0.1', int(sys.argv[3])), timeout=1.0):
            forbidden_result = 'allowed'
    except Exception as exc:
        forbidden_result = 'denied:' + type(exc).__name__
outward_result = None
if '--outward' in sys.argv[3:]:
    try:
        with socket.create_connection(('198.51.100.1', 80), timeout=1.0):
            outward_result = 'allowed'
    except Exception as exc:
        outward_result = 'denied:' + type(exc).__name__
print(json.dumps({'private_read': private_result, 'network_connect': network_result, 'forbidden_network_connect': forbidden_result, 'outward_network_connect': outward_result}, sort_keys=True))
"""
    child_args = [str(args.private), str(args.port)]
    if args.forbidden_port is not None:
        child_args.append(str(args.forbidden_port))
    if args.outward:
        child_args.append("--outward")
    child = subprocess.run(
        [
            sys.executable,
            "-c",
            child_code,
            *child_args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    try:
        child_result = json.loads(child.stdout)
    except json.JSONDecodeError:
        child_result = {"private_read": "invalid-child-result", "network_connect": "invalid-child-result"}
    result = {
        "selected_read": _read(args.public),
        "unselected_read": _read(args.private),
        "network_connect": _connect("127.0.0.1", args.port),
        "child": child_result,
    }
    if args.forbidden_port is not None:
        result["forbidden_network_connect"] = _connect("127.0.0.1", args.forbidden_port)
    if args.outward:
        result["outward_network_connect"] = _connect("198.51.100.1", 80)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
