#!/usr/bin/env python3
"""Reproduce the exact-port Ollama compatibility probe.

The server and request client inherit an experimental macOS profile that
permits only localhost:11499 and denies a synthetic sentinel directory.
Ollama's model runner chooses a dynamic loopback port; the probe records the
resulting runner bind failure instead of widening policy to all loopback.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import signal
import subprocess
import time


PORT = 11499


def _profile(private_dir: Path) -> str:
    return (
        '(version 1) (allow default) (deny network*) '
        f'(allow network-inbound (local ip "localhost:{PORT}")) '
        f'(allow network-outbound (remote ip "localhost:{PORT}")) '
        f'(deny file-read* (subpath "{private_dir}"))'
    )


def _clean_env() -> dict[str, str]:
    env = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/Users/kar",
        "OLLAMA_HOST": f"127.0.0.1:{PORT}",
        "OLLAMA_MODELS": "/Users/kar/.ollama/models",
        "OLLAMA_NO_CLOUD": "1",
        "NO_PROXY": "127.0.0.1",
    }
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        env[key] = ""
    return env


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    args = parser.parse_args()
    private_dir = args.output.parent / "native-policy-private"
    private_dir.mkdir(parents=True, exist_ok=True)
    private = private_dir / "sentinel.txt"
    private.write_text("SYNTHETIC-UNSELECTED-SENTINEL", encoding="utf-8")
    log = args.output.parent / "native-policy-server.log"
    profile = _profile(private_dir)
    env_args = [f"{key}={value}" for key, value in _clean_env().items()]
    server_command = [
        "/usr/bin/sandbox-exec",
        "-p",
        profile,
        "/usr/bin/env",
        *env_args,
        "/usr/local/bin/ollama",
        "serve",
    ]
    started = time.monotonic()
    with log.open("w", encoding="utf-8") as stream:
        server = subprocess.Popen(server_command, stdout=stream, stderr=subprocess.STDOUT)
    try:
        ready = False
        for _ in range(30):
            if server.poll() is not None:
                break
            if "Listening on" in log.read_text(encoding="utf-8", errors="replace"):
                ready = True
                break
            time.sleep(1)
        client_command = [
            "/usr/bin/sandbox-exec",
            "-p",
            profile,
            "/usr/bin/env",
            *env_args,
            "/Users/kar/orca/workspaces/gigai/gigai-v0.1.7/.venv/bin/python",
            "research/scout-local-boundary-demo/scripts/run_local_proposal.py",
            "--host",
            f"http://127.0.0.1:{PORT}",
            "--model",
            "qwen3.8:latest",
            "--fixture",
            str(args.fixture),
            "--output",
            str(args.output.parent / "native-policy-exact.json"),
            "--timeout",
            "150",
        ]
        completed = subprocess.run(client_command, capture_output=True, text=True, check=False, timeout=165)
        server_log = log.read_text(encoding="utf-8", errors="replace")
        ports = re.findall(r'--port (\d+)', server_log)
        result = {
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "server_ready": ready,
            "client_returncode": completed.returncode,
            "client_stdout": completed.stdout[-2000:],
            "client_stderr": completed.stderr[-4000:],
            "profile": profile,
            "cloud_disabled": True,
            "proxy_environment_scrubbed": True,
            "model_requested": "qwen3.8:latest",
            "runner_observed_ports": [int(port) for port in ports],
            "runner_failure": "llama-server could not bind its dynamic loopback port under exact-port policy" if ports else None,
            "result_file": str(args.output.parent / "native-policy-exact.json"),
            "server_log": str(log),
        }
    finally:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=2)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "client_returncode": result["client_returncode"], "elapsed_seconds": result["elapsed_seconds"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
