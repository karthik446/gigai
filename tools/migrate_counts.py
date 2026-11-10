#!/usr/bin/env python3
"""Compare two fixture-home ``.counts`` files table by table.

usage: python tools/migrate_counts.py A.counts B.counts

A ``.counts`` line is ``<home-relative sqlite path>\\t<table>\\t<rows>``
(``tools/build_fixture_home.sh`` writes them). The files are joined on the
table name and the path is ignored, so a store that moved or a project folder
with another id still compares. A table name found in several SQLite files
compares as the sorted list of its row counts. One line per difference, then
``N differences``; exit 0 when there are none, 1 otherwise, 2 on bad input.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
import sys


def load(path: Path) -> dict[str, list[int]]:
    tables: dict[str, list[int]] = defaultdict(list)
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) != 3 or not parts[2].isdigit():
            raise ValueError(f"{path}:{number}: expected '<path>\\t<table>\\t<rows>', got {line!r}")
        tables[parts[1]].append(int(parts[2]))
    return {table: sorted(rows) for table, rows in tables.items()}


def _show(rows: list[int]) -> str:
    return str(rows[0]) if len(rows) == 1 else "[" + ", ".join(map(str, rows)) + "]"


def differences(a: dict[str, list[int]], b: dict[str, list[int]]) -> list[str]:
    lines: list[str] = []
    for table in sorted(a.keys() | b.keys()):
        if table not in b:
            lines.append(f"{table}: only in A ({_show(a[table])} rows)")
        elif table not in a:
            lines.append(f"{table}: only in B ({_show(b[table])} rows)")
        elif a[table] != b[table]:
            lines.append(f"{table}: {_show(a[table])} -> {_show(b[table])} rows")
    return lines


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    try:
        lines = differences(load(Path(argv[0])), load(Path(argv[1])))
    except (OSError, ValueError) as error:
        print(f"migrate_counts: {error}", file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    print(f"{len(lines)} differences")
    return 1 if lines else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
