"""Measure a bounded pytest selection with collection and per-phase timing.

This runner is intentionally a thin pytest API wrapper.  It does not alter
fixtures, markers, ordering or assertions; it only records collection time,
setup/call/teardown durations, outcomes and the final exit code.  Keep the
selection explicit and offline when using it for S11 receipts.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]


class MeasurementPlugin:
    def __init__(self) -> None:
        self.started = time.perf_counter()
        self.collection_started = self.started
        self.collection_finished: float | None = None
        self.session_finished: float | None = None
        self.items_collected = 0
        self.phase_reports: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.collection_errors: list[dict[str, str]] = []

    @pytest.hookimpl
    def pytest_sessionstart(self, session: pytest.Session) -> None:
        self.started = time.perf_counter()
        self.collection_started = self.started

    @pytest.hookimpl
    def pytest_collection_finish(self, session: pytest.Session) -> None:
        self.collection_finished = time.perf_counter()
        self.items_collected = len(session.items)

    @pytest.hookimpl
    def pytest_collectreport(self, report: pytest.CollectReport) -> None:
        if report.failed:
            self.collection_errors.append({
                "nodeid": report.nodeid,
                "outcome": report.outcome,
                "message": str(report.longrepr),
            })

    @pytest.hookimpl
    def pytest_runtest_logreport(self, report: pytest.TestReport) -> None:
        self.phase_reports[report.nodeid][report.when] = {
            "outcome": report.outcome,
            "duration_seconds": round(report.duration, 6),
            "wasxfail": getattr(report, "wasxfail", None),
        }

    @pytest.hookimpl
    def pytest_sessionfinish(self, session: pytest.Session, exitstatus: int | pytest.ExitCode) -> None:
        del session, exitstatus
        self.session_finished = time.perf_counter()

    def report(self, *, args: list[str], exit_code: int) -> dict[str, object]:
        end = self.session_finished or time.perf_counter()
        collection_end = self.collection_finished or end
        tests: list[dict[str, object]] = []
        outcomes: Counter[str] = Counter()
        for nodeid in sorted(self.phase_reports):
            phases = self.phase_reports[nodeid]
            final = phases.get("call") or phases.get("setup") or phases.get("teardown") or {"outcome": "unknown"}
            outcome = str(final["outcome"])
            outcomes[outcome] += 1
            tests.append({
                "nodeid": nodeid,
                "outcome": outcome,
                "phases": phases,
                "duration_seconds": round(sum(float(phase.get("duration_seconds", 0.0)) for phase in phases.values()), 6),
            })
        return {
            "schema_version": "s11-measurement.v1",
            "kind": "pytest-selection-measurement",
            "started_at": datetime.now(timezone.utc).isoformat(),
            "command": [sys.executable, *args],
            "root": str(ROOT),
            "exit_code": exit_code,
            "status": "pass" if exit_code == 0 else "fail",
            "collection": {
                "items_collected": self.items_collected,
                "duration_seconds": round(collection_end - self.collection_started, 6),
                "errors": self.collection_errors,
            },
            "session": {
                "duration_seconds": round(end - self.started, 6),
            },
            "outcomes": dict(sorted(outcomes.items())),
            "tests": tests,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="JSON report path")
    parser.add_argument("pytest_args", nargs=argparse.REMAINDER, help="arguments passed to pytest")
    args = parser.parse_args()
    pytest_args = list(args.pytest_args)
    if pytest_args[:1] == ["--"]:
        pytest_args = pytest_args[1:]
    if not pytest_args:
        parser.error("provide an explicit pytest selection after --")
    plugin = MeasurementPlugin()
    exit_code = int(pytest.main(pytest_args, plugins=[plugin]))
    report = plugin.report(args=pytest_args, exit_code=exit_code)
    output = args.output if args.output.is_absolute() else ROOT / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(output), "status": report["status"], "outcomes": report["outcomes"]}, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
