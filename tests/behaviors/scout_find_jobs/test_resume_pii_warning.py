"""0.1.10-001: the resume personal-info warning, CLI side, and the local detector."""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout.resume_pii import RESUME_WARNING, detect_contact_details, heads_up

from tests.behaviors.scout_find_jobs.test_scout_cli import _setup_and_init

WITH_CONTACT = (
    "Jordan Rivera\njordan@example.com | (415) 555-0134\nlinkedin.com/in/jordan | 12 Market Street\n"
    "Staff engineer: nine years of Go and Kafka.\n"
)
CLEAN = "Staff engineer 2019 - 2023: nine years of Go and Kafka. Led a team of 12.\n"


def test_detector_flags_email_and_phone_and_says_nothing_on_a_clean_resume() -> None:
    assert detect_contact_details("Reach me: jordan@example.com or 415-555-0134.") == ["email", "phone"]
    assert heads_up(["email", "phone"]) == "This resume seems to contain: email, phone. Remove them before continuing?"
    assert detect_contact_details(WITH_CONTACT) == ["email", "phone", "links", "address"]
    assert detect_contact_details(CLEAN) == []
    assert heads_up([]) is None


def test_resume_add_prints_the_warning_and_json_carries_it(tmp_path: Path) -> None:
    home, target = _setup_and_init(tmp_path)
    source = tmp_path / "resume.md"
    source.write_text(CLEAN, encoding="utf-8")
    base = ["scout", "resume", "add", str(source), "--home", str(home), "--target", str(target)]

    plain = CliRunner().invoke(cli, base)
    assert plain.exit_code == 0, plain.output
    assert f"Warning: {RESUME_WARNING}" in plain.output

    as_json = CliRunner().invoke(cli, [*base, "--json"])
    assert as_json.exit_code == 0, as_json.output
    assert json.loads(as_json.output)["warning"] == RESUME_WARNING
