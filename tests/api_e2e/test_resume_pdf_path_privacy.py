"""0.1.10.7-fix2: ``gigai scout resume pdf`` prints the path the user gave, never an absolute one it made up.

An absolute path ("/Users/<name>/...", "/private/var/...") in an agent's
transcript is a home-path leak. The default name prints as the bare file name;
``--out`` prints as typed. Both the human line and ``--json`` ``out_path``.
"""

from __future__ import annotations

import json
from pathlib import Path
import re

import pytest
from click.testing import CliRunner

from gigai.cli import cli

from tests.api_e2e.harness import setup_and_init

_RESUME = """## Summary

- Clinical applications manager with ten years in hospital systems.

## Experience

### Example Corp
Clinical Applications Manager | 2020 - Present

- Managed 4 analysts running scheduling and billing systems across 2 hospitals.

## Skills

- Python, SQL, HL7
"""


def _run(tmp_path: Path, *args: str) -> tuple[str, dict]:
    home, target = setup_and_init(tmp_path / "h")
    (tmp_path / "resume.md").write_text(_RESUME, encoding="utf-8")
    base = ["scout", "resume", "pdf", "--in", "resume.md", "--home", str(home), "--target", str(target), *args]
    human = CliRunner().invoke(cli, base)
    as_json = CliRunner().invoke(cli, [*base, "--json"])
    assert human.exit_code == 0 and as_json.exit_code == 0, (human.output, as_json.output)
    return human.output, json.loads(as_json.output)


def test_a_relative_out_prints_as_typed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    human, payload = _run(tmp_path, "--out", "out/me.pdf")
    assert human.startswith("Wrote out/me.pdf (") and payload["out_path"] == "out/me.pdf"
    assert (tmp_path / "out" / "me.pdf").read_bytes().startswith(b"%PDF")


def test_the_default_name_prints_as_the_bare_file_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    human, payload = _run(tmp_path)
    written = re.match(r"Wrote (\S+\.pdf) \(", human).group(1)
    assert "/" not in written and payload["out_path"] == written
    assert str(tmp_path.resolve()) not in human and str(tmp_path) not in human
    assert (tmp_path / written).read_bytes().startswith(b"%PDF")


def test_an_absolute_out_the_user_typed_stays_absolute(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "abs.pdf"
    human, payload = _run(tmp_path, "--out", str(target))
    assert human.startswith(f"Wrote {target} (") and payload["out_path"] == str(target)
