"""0110-010: the README carries no product-version literals, so it cannot go stale."""

from __future__ import annotations

import re
from pathlib import Path

README = Path(__file__).resolve().parents[3] / "README.md"

_VERSION = re.compile(r"(?<![\w.])v?\d+\.\d+\.\d+(\.\d+)?\b")
# Not product versions: the numeric loopback address the README documents.
_ALLOWED = {"127.0.0.1"}


def test_the_readme_has_no_product_version_literals() -> None:
    hits = []
    for number, line in enumerate(README.read_text(encoding="utf-8").splitlines(), start=1):
        for match in _VERSION.finditer(line):
            if match.group(0) not in _ALLOWED:
                hits.append(f"README.md:{number}: {match.group(0)}")
    assert not hits, "version literals in README.md:\n" + "\n".join(hits)


def test_the_install_and_upgrade_commands_are_version_free() -> None:
    text = README.read_text(encoding="utf-8")
    assert "uv tool install gigai" in text and "uv tool upgrade gigai" in text
    assert "releases" in text.lower() and "CHANGELOG" in text
