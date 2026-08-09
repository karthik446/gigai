"""Disposable fake CLI used only by S18-02 process-boundary tests."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--model", default="fixture-cli-model")
    args, remainder = parser.parse_known_args()
    if args.scenario == "sleep":
        time.sleep(30)
    if args.scenario == "exit":
        print("synthetic cli failure", file=sys.stderr)
        return 17
    if args.scenario == "malformed":
        print("not-json")
        return 0
    if args.scenario == "credential-check":
        payload = {
            "credential_present": "S18_02_SYNTHETIC_TOKEN" in os.environ,
            "cwd": os.getcwd(),
            "argv": remainder,
        }
    else:
        payload = {
            "model": args.model,
            "output": "fake cli response",
            "finish": "stop",
            "usage": {"input_tokens": 2, "output_tokens": 3, "total_tokens": 5},
            "cwd": os.getcwd(),
            "argv": remainder,
        }
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
