"""0110-049: the release screenshots' demo data is invented, varied, and carries no real-looking contact data.

Pure: imports only tools/media/persona.py and the text half of the privacy gate, so it runs
without the `media` dependency group (no Playwright, no Pillow).
"""

from __future__ import annotations

from pathlib import Path
import re
import tempfile

import pytest

from tools.media import demo_home, persona, privacy_scan

REPO = Path(__file__).resolve().parents[3]
#: Placeholder names every demo reaches for; the ticket: never "Acme x9".
STOCK_NAMES = ("acme", "example", "globex", "initech", "umbrella", "northwind", "contoso", "foo", "demo", "test")


def test_companies_are_invented_and_all_different() -> None:
    names = [company.name for company in persona.COMPANIES]
    assert len(names) >= 5
    assert len(set(names)) == len(names)
    for company in persona.COMPANIES:
        assert not any(stock in company.slug for stock in STOCK_NAMES), company.slug
        assert re.fullmatch(r"[a-z]+(?:-[a-z]+)+", company.slug), "a two-word slug, so the UI shows a proper name"
        assert company.name.lower().replace(" ", "-") == company.slug
        assert company.blurb.startswith(company.name)


def test_postings_vary_in_role_place_mode_and_pay() -> None:
    postings = [posting for company in persona.COMPANIES for posting in company.postings]
    assert persona.posting_count() == len(postings) >= 10
    assert len({posting.title for posting in postings}) == len(postings)
    assert {posting.mode for posting in postings} == {"remote", "hybrid", "on-site"}
    assert len({posting.place for posting in postings}) >= 5
    paid = [posting.pay for posting in postings if posting.pay is not None]
    assert 0 < len(paid) < len(postings), "some postings state pay and some do not"
    for low, high in paid:
        assert 90_000 <= low < high <= 400_000
        assert high - low <= 60_000
    assert len(set(paid)) == len(paid)
    assert persona.posting_count(wave=2) >= 2, "something is new since the last check"


def test_the_persona_has_no_real_looking_contact_data() -> None:
    assert persona.PERSONA_EMAIL.endswith("@example.test")
    assert persona.PERSONA_LINK.endswith(".example.test")
    assert re.search(r"555-01\d\d$", persona.PERSONA_PHONE), "555-0100..0199 is reserved for fiction"
    # No allowlist at all: the only contact-shaped strings in the whole data set are the persona's own.
    # (the email is on a reserved domain, which the gate lets through by itself).
    found = {hit.shown for hit in privacy_scan.scan_text(persona.all_text(), allow=())}
    assert found == {persona.PERSONA_PHONE}
    assert set(privacy_scan.EMAIL.findall(persona.all_text())) == {persona.PERSONA_EMAIL}
    assert privacy_scan.scan_text(persona.all_text()) == []
    assert set(persona.PDF_HEADER.values()) <= {persona.PERSONA_NAME, persona.PERSONA_LOCATION, *persona.ALLOWED_CONTACT_VALUES}


def test_the_resume_is_headerless() -> None:
    # GigAI stores no name or contact details; the demo resume gives it none to strip.
    assert persona.PERSONA_NAME not in persona.RESUME_MARKDOWN
    assert "@" not in persona.RESUME_MARKDOWN
    assert privacy_scan.scan_text(persona.RESUME_MARKDOWN, allow=()) == []


def test_the_build_refuses_a_home_that_is_not_temporary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOME", str(REPO))
    with pytest.raises(demo_home.DemoHomeError, match="temporary directory"):
        demo_home.assert_synthetic_home(REPO / "demo")


def test_the_build_refuses_a_root_outside_its_temporary_home_or_a_home_with_gigai_data(monkeypatch: pytest.MonkeyPatch) -> None:
    with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
        home = Path(first).resolve()
        monkeypatch.setenv("HOME", str(home))
        demo_home.assert_synthetic_home(home / "demo")
        with pytest.raises(demo_home.DemoHomeError, match="inside the temporary HOME"):
            demo_home.assert_synthetic_home(Path(second).resolve() / "demo")
        (home / ".gigai").mkdir()
        with pytest.raises(demo_home.DemoHomeError, match="already has a .gigai"):
            demo_home.assert_synthetic_home(home / "demo")


def test_the_build_points_home_at_a_temporary_directory_before_any_gigai_import() -> None:
    source = (REPO / "tools" / "media" / "build.py").read_text(encoding="utf-8")
    moved = source.index('os.environ["HOME"] = str(temp_home)')
    assert moved < source.index("from . import demo_home")
    assert moved < source.index("demo_home.assert_synthetic_home(root)") < source.index("demo_home.build(root")
    # No GigAI module is imported at the top of the driver: only inside main(), after HOME moved.
    assert not re.search(r"^(?:from|import) (?:gigai|\.)", source, re.MULTILINE)
