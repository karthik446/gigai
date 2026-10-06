"""The Install page's release-pin example names the version in pyproject, and no page says the pipeline is on (0.1.11.2)."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "gigai-docs" / "src" / "content" / "docs"


def test_install_pin_example_is_the_project_version() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
    pins = re.findall(r"gigai@v([0-9][0-9.]*)", (DOCS / "install.md").read_text(encoding="utf-8"))
    assert pins, "install.md has no release-pin example"
    assert set(pins) == {version}, f"install.md pins {pins}, pyproject says {version}"


def test_agents_page_does_not_say_the_pipeline_is_on() -> None:
    text = (DOCS / "scout" / "agents.md").read_text(encoding="utf-8")
    assert "| Pipeline | on |" not in text
    assert "The pipeline is on by default" not in text


def test_first_10_minutes_does_not_promise_background_tailoring() -> None:
    text = (DOCS / "scout" / "first-10-minutes.md").read_text(encoding="utf-8")
    assert "tailored and scored in the background" not in text
    assert "resume tailor --job-url" not in text.replace("`gigai scout resume tailor` is", "")


def test_start_fresh_section_is_linked_from_first_10_minutes_and_agents() -> None:
    start = (DOCS / "scout" / "agents" / "start.md").read_text(encoding="utf-8")
    assert "## 11. Start fresh" in start and "gigai scout stop" in start
    assert "resume master init --from FILE" in start
    anchor = "start/#11-start-fresh-reset-the-data-keep-the-master-resume-file"
    assert anchor in (DOCS / "scout" / "agents.md").read_text(encoding="utf-8")
    assert anchor in (DOCS / "scout" / "first-10-minutes.md").read_text(encoding="utf-8")
