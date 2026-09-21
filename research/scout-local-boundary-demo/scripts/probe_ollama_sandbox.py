#!/usr/bin/env python3
"""Attempt to start Ollama under the same no-network policy, then stop it.

The expected result is a bind denial: a model server cannot be used as the
private no-network assessor's HTTP API without a separate trusted broker or
IPC design. No model is loaded and no global service is touched.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    profile = '(version 1) (allow default) (deny network*)'
    command = [
        "/usr/bin/sandbox-exec", "-p", profile,
        "/usr/bin/env", "OLLAMA_HOST=127.0.0.1:11500",
        "OLLAMA_MODELS=/Users/kar/.ollama/models", "/usr/local/bin/ollama", "serve",
    ]
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        output, _ = process.communicate(timeout=4)
    except subprocess.TimeoutExpired:
        process.kill()
        output, _ = process.communicate(timeout=2)
    elapsed = time.monotonic() - started
    result = {
        "command": command,
        "elapsed_seconds": round(elapsed, 3),
        "returncode": process.returncode,
        "network_policy": "deny network*",
        "server_started": "Listening on" in output,
        "bind_denied": "operation not permitted" in output.lower() or "permission denied" in output.lower(),
        "output_tail": "\n".join(output.splitlines()[-8:]),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
