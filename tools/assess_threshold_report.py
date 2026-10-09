#!/usr/bin/env python3
"""0110-10-02: what an assess rank threshold would have cost on a real home. READ ONLY, numbers only.

``gigai scout new`` and "Assess these" assess only postings whose rank score
is at least ``fit.assess_min_rank`` (50). This answers, for the assessments a
home already has: how many would have been SKIPPED at a threshold (their rank
score is below it), and how many of those came out matched or needs-answers
(the cost of the threshold: verdicts that would not have been found). It also
counts, for the weak-fit rule, how many needs-answers postings a pair of
numbers (share of requirements met, rank) would move to "weak fit".

It reads ONE file, the project's ``pipeline.sqlite`` (the posting read model:
ids, digests, numbers and codes; it holds no posting, resume or answer text),
opened read-only. It prints counts only: no URL, no title, no company. It
never writes the database and makes no network or model call (SQLite, like
any reader of this file, may touch its own ``-wal`` / ``-shm`` bookkeeping
files beside it; they hold no data of their own). Standard library only, so
it runs with any Python 3.10+ and needs no GigAI install::

    python3 tools/assess_threshold_report.py                      # ~/.gigai, the one project there
    python3 tools/assess_threshold_report.py --day 2026-10-03     # only what was assessed that UTC day
    python3 tools/assess_threshold_report.py --db /path/to/pipeline.sqlite --thresholds 40,50,60 --json

WHAT IS COUNTED. One row per JOB that has an assessment (``state`` is not
``not_assessed``), postings the board no longer lists included. 0.1.11.9: a
job has one assessment, whichever roles found it, so a job two roles tag is
counted once, by its best tag's row (``match_rank`` 1: its best rank score);
until then every (posting, profile) row was counted. ``--day`` / ``--from`` / ``--to`` keep the rows whose assessment was
made in that UTC window (``assessed_at``); the "by day" table shows which days
there are. The rank score is the one stored NOW: a posting ranked after it was
assessed is counted with its rank of today. A row with no rank score is never
skipped by a threshold (the product assesses a posting that is not ranked yet).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys

DEFAULT_THRESHOLDS = (40, 50, 60)
WEAK_PERCENTS = (30, 40, 50)
WEAK_RANKS = (40, 50, 60)
NOT_ASSESSED = "not_assessed"
#: A weak fit IS a needs-answers verdict (a build with 0110-10-02 stores it as its own state).
WAITING = ("needs_answers", "weak_fit")


def find_db(home: Path) -> Path:
    """The one ``<home>/scout/<project>/pipeline/pipeline.sqlite``; exits with the choices when there are several."""

    found = sorted(home.glob("scout/*/pipeline/pipeline.sqlite"))
    if len(found) == 1:
        return found[0]
    if not found:
        sys.exit(f"no pipeline.sqlite under {home}/scout/*/pipeline: pass --db PATH")
    listed = "\n".join(f"  {path}" for path in found)
    sys.exit(f"several projects have a pipeline.sqlite; pass one with --db:\n{listed}")


def read_rows(path: Path) -> tuple[list[dict[str, object]], str]:
    """Every assessed JOB (its best tag's row) as numbers and codes, and how the file was opened. Never writes."""

    opened = "read-only"
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        connection.execute("SELECT 1 FROM posting LIMIT 1").fetchall()
    except sqlite3.Error:
        # A folder this user cannot create the -shm file in: the file as it is on disk, still never written.
        opened = "read-only, immutable (changes not yet checkpointed by a running server are not seen)"
        connection = sqlite3.connect(f"file:{path}?mode=ro&immutable=1", uri=True)
    try:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(posting)")}
        fit = "fit" if "fit" in columns else "NULL"
        cursor = connection.execute(
            f"SELECT state, rank_score, reqs_met, reqs_total, assessed_at, stale_code, {fit} FROM posting WHERE state != ? AND match_rank = 1",
            (NOT_ASSESSED,),
        )
        rows = [
            {"state": state, "rank": rank, "met": met, "total": total, "assessed_at": assessed_at, "stale": stale, "fit": fit_value}
            for state, rank, met, total, assessed_at, stale, fit_value in cursor.fetchall()
        ]
    finally:
        connection.close()
    return rows, opened


def share(row: dict[str, object]) -> int | None:
    """The row's fit number when the file has one, else the plain share of requirements met; ``None`` with none."""

    if isinstance(row["fit"], int):
        return row["fit"]
    total, met = row["total"], row["met"]
    if not isinstance(total, int) or total <= 0:
        return None
    return round(100 * (met if isinstance(met, int) else 0) / total)


def in_window(row: dict[str, object], start: str | None, end: str | None) -> bool:
    stamp = row["assessed_at"]
    if start is None and end is None:
        return True
    if not isinstance(stamp, str):
        return False
    return (start is None or stamp >= start) and (end is None or stamp < end)


def report(rows: list[dict[str, object]], thresholds: tuple[int, ...]) -> dict[str, object]:
    """The counts: per threshold what would have been skipped and what it came out as; the weak-fit grid; by day."""

    ranked = [row for row in rows if isinstance(row["rank"], int)]
    by_state: dict[str, int] = {}
    for row in rows:
        by_state[str(row["state"])] = by_state.get(str(row["state"]), 0) + 1
    table = []
    for threshold in thresholds:
        skipped = [row for row in ranked if row["rank"] < threshold]  # type: ignore[operator]
        states: dict[str, int] = {}
        for row in skipped:
            states[str(row["state"])] = states.get(str(row["state"]), 0) + 1
        matched = states.get("matched", 0)
        waiting = sum(states.get(state, 0) for state in WAITING)
        table.append({
            "threshold": threshold, "skipped": len(skipped), "assessed": len(rows) - len(skipped),
            "skipped_matched": matched, "skipped_needs_answers": waiting, "skipped_not_a_match": states.get("not_a_match", 0),
            "skipped_other": len(skipped) - matched - waiting - states.get("not_a_match", 0),
            "cost_of_threshold": matched + waiting,
        })
    waiting_rows = [row for row in rows if row["state"] in WAITING]
    grid = []
    for percent in WEAK_PERCENTS:
        for rank in WEAK_RANKS:
            weak = sum(
                1 for row in waiting_rows
                if isinstance(row["rank"], int) and row["rank"] < rank and share(row) is not None and share(row) < percent  # type: ignore[operator]
            )
            grid.append({"below_percent": percent, "below_rank": rank, "weak_fit": weak, "still_needs_answers": len(waiting_rows) - weak})
    by_day: dict[str, int] = {}
    for row in rows:
        day = str(row["assessed_at"])[:10] if isinstance(row["assessed_at"], str) else "unknown"
        by_day[day] = by_day.get(day, 0) + 1
    return {
        "assessments": len(rows), "ranked": len(ranked), "not_ranked": len(rows) - len(ranked),
        "stale": sum(1 for row in rows if row["stale"] is not None), "by_state": dict(sorted(by_state.items())),
        "thresholds": table, "needs_answers": len(waiting_rows), "weak_fit_grid": grid, "by_day": dict(sorted(by_day.items())),
    }


def render(result: dict[str, object], opened: str, window: str) -> str:
    lines = [
        f"Assessments counted: {result['assessments']} ({window}); {result['ranked']} have a rank score, "
        f"{result['not_ranked']} have none (a threshold never skips those); {result['stale']} are old assessments.",
        "By verdict: " + ", ".join(f"{state} {count}" for state, count in result["by_state"].items()),  # type: ignore[union-attr]
        "",
        "ASSESS THRESHOLD: what a yes would have skipped (rank below the threshold), and what those came out as",
        "threshold | skipped | still assessed | skipped: matched | needs answers | not a match | other | cost (matched + needs answers)",
    ]
    for row in result["thresholds"]:  # type: ignore[union-attr]
        lines.append(
            f"{row['threshold']:>9} | {row['skipped']:>7} | {row['assessed']:>14} | {row['skipped_matched']:>16} | "
            f"{row['skipped_needs_answers']:>13} | {row['skipped_not_a_match']:>11} | {row['skipped_other']:>5} | {row['cost_of_threshold']:>4}"
        )
    lines += [
        "",
        f"WEAK FIT: of the {result['needs_answers']} needs-answers assessments, how many a pair of numbers moves to \"weak fit\"",
        "(the share of requirements met is below the percent AND the rank is below the rank)",
        "below percent | below rank | weak fit | still needs answers",
    ]
    for row in result["weak_fit_grid"]:  # type: ignore[union-attr]
        lines.append(f"{row['below_percent']:>13} | {row['below_rank']:>10} | {row['weak_fit']:>8} | {row['still_needs_answers']:>19}")
    lines += ["", "By day assessed (UTC): " + ", ".join(f"{day} {count}" for day, count in result["by_day"].items())]  # type: ignore[union-attr]
    lines.append(f"The file was opened {opened}. The database was not written; no posting, resume or answer text was read.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="What an assess rank threshold would have cost on this home. Read only, numbers only.")
    parser.add_argument("--home", type=Path, default=Path.home() / ".gigai", help="the GigAI home (default: ~/.gigai)")
    parser.add_argument("--db", type=Path, help="the project's pipeline.sqlite, instead of finding it under --home")
    parser.add_argument("--thresholds", default=",".join(str(item) for item in DEFAULT_THRESHOLDS), help="rank thresholds, comma separated")
    parser.add_argument("--day", help="only assessments made on this UTC day (YYYY-MM-DD)")
    parser.add_argument("--from", dest="start", help="only assessments made at or after this UTC time (ISO, e.g. 2026-10-03T14:00)")
    parser.add_argument("--to", dest="end", help="only assessments made before this UTC time (ISO)")
    parser.add_argument("--json", action="store_true", help="print one JSON object instead of the tables")
    args = parser.parse_args(argv)
    try:
        thresholds = tuple(int(item) for item in str(args.thresholds).split(",") if item.strip())
    except ValueError:
        parser.error("--thresholds must be whole numbers, comma separated")
    start, end = args.start, args.end
    if args.day:
        start, end = f"{args.day}T00:00", f"{args.day}T99"
    path = args.db if args.db is not None else find_db(args.home)
    if not path.is_file():
        sys.exit(f"not a file: {path}")
    rows, opened = read_rows(path)
    rows = [row for row in rows if in_window(row, start, end)]
    result = report(rows, thresholds)
    window = "every assessment" if start is None and end is None else f"assessed from {start or 'the start'} to {end or 'now'} UTC"
    if args.json:
        print(json.dumps({**result, "window": window, "opened": opened}, indent=2, sort_keys=True))
    else:
        print(render(result, opened, window))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
