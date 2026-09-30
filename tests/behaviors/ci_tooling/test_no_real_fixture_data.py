"""0110-014: the public repo holds no real operator data. Fixtures are always synthetic."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SKIP_PARTS = {"node_modules", "dist"}
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
SAFE_EMAIL_DOMAINS = {"example.test", "example.com"}
# Public company contact addresses quoted inside public job-posting fixtures (not the operator's data).
POSTING_EMAILS = {
    "tests/evals/fixtures/postings.json": {"reasonableaccommodations@airbnb.com", "hr@cloudflare.com"},
    "tests/fixtures/scout/nexhealth_junk_posting.txt": {"security@nexhealth.com"},
}
# This file's own pattern strings are the only allowed mention of these markers.
SELF = Path(__file__).resolve()


def _tracked_text_files() -> list[Path]:
    """Tracked files plus untracked-not-ignored ones (a new fixture is checked before it is committed)."""
    try:
        out = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, check=True, capture_output=True,
        ).stdout.decode().split("\0")
    except (subprocess.CalledProcessError, FileNotFoundError):
        # The offline container image ships no .git (containers/debian-offline/Dockerfile, .dockerignore).
        pytest.skip("not a git checkout: the offline container build context excludes .git")
    files = []
    for rel in filter(None, out):
        path = ROOT / rel
        if SKIP_PARTS & set(path.relative_to(ROOT).parts) or not path.is_file() or path.is_symlink():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        files.append(path)
    return files


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def test_denylist_entries_appear_in_no_tracked_text_file() -> None:
    denylist = os.environ.get("GIGAI_FIXTURE_DENYLIST")
    if not denylist:
        pytest.skip("GIGAI_FIXTURE_DENYLIST is not set (CI): the operator's private denylist is local only")
    entries = [l.strip().lower() for l in Path(denylist).read_text(encoding="utf-8").splitlines() if l.strip()]
    assert entries, "the denylist file is empty"
    hits = []
    for path in _tracked_text_files():
        if path == SELF:
            continue
        for number, line in enumerate(_text(path).lower().splitlines(), 1):
            for index, entry in enumerate(entries, 1):
                if entry in line:
                    hits.append(f"{path.relative_to(ROOT)}:{number} (denylist line {index})")
    assert not hits, "real operator data in tracked files (entry text withheld):\n" + "\n".join(hits)


def test_fixtures_carry_no_home_paths_or_real_emails() -> None:
    hits = []
    fixtures = [p for p in _tracked_text_files() if "tests" in p.relative_to(ROOT).parts and "fixtures" in p.relative_to(ROOT).parts]
    assert fixtures, "no fixtures found under tests/"
    for path in fixtures:
        text = _text(path)
        if "/Users/" in text:
            hits.append(f"{path.relative_to(ROOT)}: contains a home path")
        for match in EMAIL.finditer(text):
            allowed = POSTING_EMAILS.get(str(path.relative_to(ROOT)), set())
            if match.group(1).lower() not in SAFE_EMAIL_DOMAINS and match.group(0) not in allowed:
                hits.append(f"{path.relative_to(ROOT)}: email outside example.test/example.com")
    assert not hits, "\n".join(hits)
