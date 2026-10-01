"""Every docs link the product emits points at a version or latest page: the site has no unversioned paths (0110-027)."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
LINK = re.compile(r"https://karthik446\.github\.io/gigai/([^\s)>\"'`]*)")
OK_FIRST_SEGMENT = re.compile(r"^(latest|dev|\d+(\.\d+)+)/")


def _sources() -> list[Path]:
    paths = [ROOT / "README.md"]
    ui = ROOT / "src" / "gigai" / "scout" / "ui" / "src"
    paths += [p for p in ui.rglob("*") if p.suffix in {".js", ".jsx"}]
    paths += [p for p in (ROOT / "src" / "gigai").rglob("*.py")]
    return paths


def test_every_emitted_docs_link_has_a_version_or_latest_segment() -> None:
    bad: list[str] = []
    for path in _sources():
        for match in LINK.finditer(path.read_text(encoding="utf-8")):
            rest = match.group(1)
            if rest and not OK_FIRST_SEGMENT.match(rest):  # the bare site root redirects, a page path must be versioned
                bad.append(f"{path.relative_to(ROOT)}: {match.group(0)}")
    assert not bad, bad
