"""Run one G28 verification tier and write a truthful machine-readable report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "docs/development/evidence/phase-5/G28/tier-reports"
MANIFEST = ROOT / "research/evals/g28/g26-g27-manifest.json"
OBSERVATIONS = ROOT / "research/evals/g28/g26-g27-deterministic-observations.json"


def _repo_argument(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def _commands(tier: str, report_dir: Path) -> list[list[str]]:
    uv = "uv"
    if tier == "unit":
        return [[
            uv,
            "run",
            "--locked",
            "pytest",
            "-q",
            "tests/test_g28_evaluation.py",
            "tests/test_g28_roles.py",
            "tests/test_g28_setup_create.py",
        ]]
    if tier == "integration":
        return [[
            uv,
            "run",
            "--locked",
            "pytest",
            "-q",
            "tests/test_g22_cli_create.py",
            "tests/test_g22_http_approval.py",
            "tests/test_g26_cli_builder.py",
            "tests/test_g27_discovery_contract.py",
        ]]
    if tier == "installed":
        return [[uv, "run", "python", "tools/verify_installed_g28.py"]]
    if tier == "behavior":
        report_dir.mkdir(parents=True, exist_ok=True)
        report_paths = {
            split: report_dir / f"behavior-{split}.json"
            for split in ("development", "calibration", "final_held_out_acceptance")
        }
        return [
            [
                uv,
                "run",
                "gigai",
                "eval",
                "behavior",
                "--manifest",
                _repo_argument(MANIFEST),
                "--observations",
                _repo_argument(OBSERVATIONS),
                "--split",
                split,
                "--output",
                _repo_argument(output),
            ]
            for split, output in report_paths.items()
        ]
    raise ValueError(f"unknown G28 tier: {tier}")


def run(tier: str, report_path: Path) -> int:
    commands = _commands(tier, report_path.parent)
    started = time.monotonic()
    results = []
    for command in commands:
        completed = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            shell=False,
        )
        results.append(
            {
                "command": [str(item) for item in command],
                "returncode": completed.returncode,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
            }
        )
        if completed.returncode != 0:
            break
    duration_seconds = round(time.monotonic() - started, 3)
    returncode = next(
        (result["returncode"] for result in results if result["returncode"] != 0),
        0,
    )
    report = {
        "schema_version": "1.0",
        "kind": "g28_tier_report",
        "tier": tier,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "duration_seconds": duration_seconds,
        "commands": [result["command"] for result in results],
        "returncode": returncode,
        "status": "pass" if returncode == 0 else "fail",
        "results": results,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"tier": tier, "status": report["status"], "report": str(report_path)}))
    return returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "tier",
        choices=("unit", "integration", "installed", "behavior"),
        help="one independently runnable G28 verification tier",
    )
    parser.add_argument(
        "--report",
        type=Path,
        help="JSON report path (default: the G28 tier-reports directory)",
    )
    args = parser.parse_args()
    report_path = args.report or DEFAULT_REPORT_DIR / f"{args.tier}.json"
    return run(args.tier, report_path)


if __name__ == "__main__":
    raise SystemExit(main())
