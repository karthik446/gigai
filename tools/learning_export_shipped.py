"""Export a finished course's work folder as a SHIPPED course folder (0.1.11.10 shipped lessons, slice 1).

A developer tool, not a user command: it is run at release time on the machine that generated the course.

    python tools/learning_export_shipped.py --work <home>/scout/<project>/learning/work/<id> \
        --out src/gigai/scout/data/learning/shipped/<role-key> --role-key <role-key> \
        (--master-home <home> [--master-target <target>] | --no-master)

``--master-home`` names a GigAI home whose stored master (``master_store.load_master``) and story ids the scan
searches the shipped text for; ``--no-master`` scans for the postings' side only (and for anything shaped like a
master id). One of the two must be given: the choice is never implied. The postings' side always comes from the
work folder.

It prints what the strip removed, what the scan searched for, the files with their raw and deflated sizes, and,
when the scan refuses the export, every hit as kind, file, field and source (never the text). Nothing final is
written on a refusal. Exit code: 0 written, 1 refused by the scan, 2 the inputs were refused.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from gigai.scout import learning_shipped  # noqa: E402
from gigai.scout.learning_store import LearningError  # noqa: E402


def read_master(home: Path, target: Path | None) -> learning_shipped.MasterParts:
    """The scan's master side of ``home``: the stored master's lines, ids and names, and the ids of its stories."""

    from gigai.scout import stories
    from gigai.scout.master_store import load_master

    target = target or home / "scout"
    try:
        stored = load_master(home_root=home, target=target)
    except Exception as exc:  # noqa: BLE001 - whatever makes the home unreadable, the export must not go on without its master
        raise LearningError("master_unreadable", f"the master of {home} could not be read ({type(exc).__name__}): nothing was scanned") from exc
    if stored is None:
        raise LearningError("no_master", f"{home} holds no stored master: pass --no-master to scan without one")
    story_ids = [story.story_id for story in stories.list_stories(home_root=home, target=target)]
    return learning_shipped.master_parts(stored.master, extra_ids=[stored.record_id, stored.revision.revision_id, *story_ids])


def _table(rows: dict[str, int] | object) -> list[str]:
    return [f"  {count:>7}  {name}" for name, count in dict(rows).items()] or ["        0  nothing"]  # type: ignore[call-overload]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export a finished course's work folder as a shipped course folder, behind the scan.")
    parser.add_argument("--work", type=Path, required=True, help="the finished course's work folder")
    parser.add_argument("--out", type=Path, required=True, help="the shipped course folder to write")
    parser.add_argument("--role-key", required=True, help="the shipped course's key, lower-case words joined by dashes")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--master-home", type=Path, help="a GigAI home whose stored master the scan searches for")
    source.add_argument("--no-master", action="store_true", help="scan for the postings' side only")
    parser.add_argument("--master-target", type=Path, help="the target of --master-home (default: <home>/scout)")
    args = parser.parse_args(argv)

    try:
        master = None if args.no_master else read_master(args.master_home, args.master_target)
        if master is not None:
            print(f"master: {len(master.lines)} lines, {len(master.ids)} ids, {len(master.names)} names")
        else:
            print("master: none")
        report = learning_shipped.export_shipped(args.work, args.out, role_key=args.role_key, master=master)
    except learning_shipped.ShippedScanError as refused:
        print(f"REFUSED by the scan: {len(refused.hits)} hit(s); nothing was written to {args.out}")
        by_kind: dict[str, int] = {}
        for hit in refused.hits:
            by_kind[hit.kind] = by_kind.get(hit.kind, 0) + 1
        print("\n".join(_table(dict(sorted(by_kind.items())))))
        for hit in refused.hits:
            print("  " + hit.line())
        return 1
    except LearningError as refused:
        print(f"REFUSED ({refused.code}): {refused}")
        return 2

    print(f"wrote {report.out}: role key {report.role_key}, {report.lessons} lessons, {report.paths} practice paths")
    print("removed by the strip:")
    print("\n".join(_table(report.removed)))
    print("searched for by the scan:")
    print("\n".join(_table(report.forbidden)))
    for name in report.exempt:
        print(f"  not searched (a term the course teaches): {name}")
    print(f"scan: 0 hits in {report.scan.files} files, {report.scan.strings} strings, {report.scan.words} words")
    kinds = {"course.json": [0, 0, 0], "catalog.json": [0, 0, 0], "paths/*.json": [0, 0, 0]}
    for name, raw, packed in report.files:
        row = kinds["paths/*.json" if name.startswith("paths/") else name]
        row[0], row[1], row[2] = row[0] + 1, row[1] + raw, row[2] + packed
    print("size (raw bytes, deflated bytes as a wheel stores them):")
    for name, (count, raw, packed) in kinds.items():
        print(f"  {name:<14} {count:>3} file(s) {raw:>10,} {packed:>10,}")
    print(f"  {'total':<14} {len(report.files):>3} file(s) {report.raw_bytes:>10,} {report.deflated_bytes:>10,}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
