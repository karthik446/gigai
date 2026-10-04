"""Turn a browser-test run's ``budgets.json`` into the Markdown of a GitHub job summary.

The ``ui`` job and the ``operator-home`` job of ``.github/workflows/pull_request.yaml``
run this after ``make ui-test`` / ``make ui-test-operator`` and append the output to
``GITHUB_STEP_SUMMARY``::

    python3 tools/ui_budgets_summary.py build/ui-artifacts/budgets.json --title "small home" >> "${GITHUB_STEP_SUMMARY}"

``budgets.json`` is written by ``tests/ui/conftest.py`` at the end of a run: every timing
ceiling a flow measured (wall-clock, server CPU seconds, server memory), its limit, and what
the run itself took. The summary shows the ceilings that are over, the ten closest to their
limit, and every ceiling in a folded table, so a week of runs can be read off the run pages
when the limits are set from CI numbers (tests/ui/README.md).

It reports and never judges: the exit code is 0 whatever the numbers say (a ceiling that is
over fails its own test, when ``GIGAI_UI_BUDGETS=enforce``), and a missing or unreadable
file is a sentence in the summary, not an error: the step runs after a failed test step too.

Runtime requirements: stdlib only, so it runs on the runner's own ``python3`` (3.11 or newer).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

CLOSEST = 10
_HEADER = ["| Kind | Measured | Limit | Of the limit | Ceiling | Test |", "| --- | ---: | ---: | ---: | --- | --- |"]
_RUN_LABELS = (
    ("small_home_build_seconds", "small home built in {} s"),
    ("operator_home_seconds", "operator-sized home ready in {} s"),
    ("operator_server_start_seconds", "its server up in {} s"),
)


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def _share(row: dict) -> float:
    limit = float(row.get("limit") or 0)
    return float(row.get("measured") or 0) / limit if limit else 0.0


def _amount(value: float | None, unit: str) -> str:
    number = float(value or 0)
    return f"{number:.0f} {unit}" if unit == "MB" else f"{number:.2f} {unit}"


def _row(row: dict) -> str:
    unit = str(row.get("unit") or "s")
    flag = " **OVER**" if row.get("over") else ""
    return (
        f"| {_cell(row.get('kind', ''))} | {_amount(row.get('measured'), unit)} | {_amount(row.get('limit'), unit)} "
        f"| {_share(row) * 100:.0f}%{flag} | {_cell(row.get('name', ''))} | {_cell(row.get('test', ''))} |"
    )


def _run_line(run: dict) -> str:
    parts = [f"The run took {run['seconds']} s"] if "seconds" in run else []
    for key, label in _RUN_LABELS:
        if key in run:
            parts.append(label.format(run[key]))
    if run.get("operator_home_prebuilt"):
        parts.append("the home was built once by an earlier step, not by this run")
    return ("; ".join(parts) + ".") if parts else ""


def render(data: dict, *, title: str) -> str:
    """The Markdown for one run's ``budgets.json`` (already parsed)."""

    rows = [row for row in data.get("budgets", []) if isinstance(row, dict)]
    over = [row for row in rows if row.get("over")]
    mode = str(data.get("mode") or "report")
    rule = (
        "`enforce`: a ceiling that is over failed its test."
        if mode == "enforce"
        else "`report`: a ceiling that is over is listed here and is not a failure (`GIGAI_UI_BUDGETS=enforce` makes it one)."
    )
    lines = [f"## UI budgets: {title}", "", f"**{len(rows)} ceilings measured, {len(over)} over.** Mode {rule}"]
    run_line = _run_line(data.get("run") or {})
    if run_line:
        lines += ["", run_line]
    if over:
        lines += ["", "### Over their limit", "", *_HEADER, *(_row(row) for row in over)]
    if rows:
        closest = sorted(rows, key=_share, reverse=True)[:CLOSEST]
        lines += ["", f"### The {len(closest)} closest to their limit", "", *_HEADER, *(_row(row) for row in closest)]
        lines += ["", "<details><summary>Every ceiling, in the order it was measured</summary>", "", *_HEADER, *(_row(row) for row in rows), "", "</details>"]
    return "\n".join(lines) + "\n"


def summary(path: Path, *, title: str) -> str:
    """The Markdown for the file at ``path``; a sentence when it is missing or unreadable."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not a JSON object")
    except FileNotFoundError:
        return (
            f"## UI budgets: {title}\n\n`{path}` was not written: the run ended before pytest's summary "
            "(a crash, or the step's time limit), or no flow measured a ceiling. The job log has what there is.\n"
        )
    except (OSError, ValueError) as error:
        return f"## UI budgets: {title}\n\n`{path}` could not be read: {error}\n"
    return render(data, title=title)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("path", type=Path, help="budgets.json of the run")
    parser.add_argument("--title", default="browser tests", help="what ran, for the heading")
    args = parser.parse_args(argv)
    sys.stdout.write(summary(args.path, title=args.title))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
