"""SCOPE-ADD-3 E: the README's privacy and ranking claims match the code they describe."""

from __future__ import annotations

from pathlib import Path

from gigai.scout.find_jobs import model_rank, rank_digest
from gigai.scout.find_jobs.contracts import ModelTarget
from gigai.scout.find_jobs.rank_run import RUN_MAX_CALLS

README = Path(__file__).resolve().parents[3] / "README.md"


def _text() -> str:
    return README.read_text(encoding="utf-8")


def _flat(section: str) -> str:
    return " ".join(section.split())


def _privacy() -> str:
    return _flat(_text().split("## Privacy and security", 1)[1].split("\n## ", 1)[0])


def test_the_readme_names_no_jev_service() -> None:
    assert "jev" not in _text().lower()


def test_the_intro_no_longer_claims_a_second_service_sees_your_resume() -> None:
    intro = _flat(_text().split("## Scout, the first Gig", 1)[1].split("\n## ", 1)[0])
    assert "Nothing about you leaves your machine except" not in intro
    assert "What your model target sees is exactly what leaves your machine for ranking and assessment" in intro
    assert "[Privacy and security](#privacy-and-security)" in intro


def test_the_ranking_copy_is_honest_about_the_order() -> None:
    text = _flat(_text())
    assert "likely fits first, likely no-matches last" in text
    assert "never by hiding them" in text
    assert "does not claim to put the best match first" in text


def test_the_named_model_targets_are_the_ones_scout_can_select() -> None:
    assert {target.value for target in ModelTarget} == {"ollama_local", "codex_cli", "openrouter_api"}
    privacy = _privacy()
    for target in ModelTarget:
        assert f"`{target.value}`" in privacy


_MESSY_RESUME = """Robin T. Sample
robin.sample@example.com | +1 (555) 010-4477 | 555.010.9988
77 Birch Avenue Apt 2, Boulder, CO 80301
github.com/robinsample | https://www.linkedin.com/in/robin-sample

Staff Software Engineer | Senior Backend Engineer
Backend engineer with 9 years building payment platforms in Python and Go on AWS with Postgres.
"""


def test_the_resume_digest_claims_match_what_the_digest_contains() -> None:
    prefs = rank_digest.CandidatePrefs(titles=("Staff Engineer",), countries=("US",), location="Denver, CO")
    digest = rank_digest.resume_digest(_MESSY_RESUME, prefs)
    for line in ("targets:", "countries:", "needs visa sponsorship:", "location:", "skills:"):
        assert line in digest
    # titles, skills, domain and years found in the resume are present
    assert "titles: Staff Software Engineer; Senior Backend Engineer" in digest
    assert "Python" in digest and "domain: payments" in digest and "9+ yrs" in digest
    # no free-text excerpt of the resume is copied, and no header detail survives
    assert "Backend engineer with 9 years" not in digest and "building payment platforms" not in digest
    for private in ("Robin", "Sample", "example.com", "555", "Birch", "Boulder", "80301", "github", "linkedin", "http"):
        assert private not in digest, private
    privacy = _privacy()
    assert "the job titles, skills and domain found in your resume, and an experience-years figure" in privacy
    assert "Your name, email, phone, address and links are never sent for ranking" in privacy
    assert "characters copied from the start of your resume" not in privacy


def test_a_posting_digest_carries_no_description_text() -> None:
    class Posting:
        title, company, location, countries = "Engineer", "Acme", "Remote", ("US",)
        text = "Requirements\n- Python and AWS experience required for this role, several years.\n" + "SECRET-BODY " * 30

    line = rank_digest.posting_digest(Posting(), "p0")
    assert "SECRET-BODY" not in line and "\n" not in line


def test_the_rank_score_cache_path_and_contents_match_the_readme() -> None:
    assert str(model_rank.cache_dir(Path("<home>"))) == "<home>/cache/scout/rank/scores"
    assert "`<home>/cache/scout/rank/scores/`" in _privacy()
    assert model_rank._MAX_REASONS == 2 and "up to two short reasons" in _privacy()


def test_the_bounded_call_count_is_stated_and_exists() -> None:
    assert RUN_MAX_CALLS > 0 and "bounded number of ranking calls" in _privacy()


def test_the_privacy_section_lists_the_other_network_traffic() -> None:
    privacy = _privacy()
    assert "Network traffic besides your model." in privacy
    assert "Job boards (Greenhouse, Lever, Ashby) get plain public requests" in privacy and "see your IP address" in privacy
    assert "Exa, only if enabled in your sources, receives the search query (your target roles), a start date, a country code and your Exa key" in privacy
    assert "The setup interview itself makes no network request" in privacy
    assert "Discover companies button, only if an OpenAI key is set" in privacy
    assert "and nothing else" not in privacy


def test_the_pasted_resume_and_status_claims_are_the_decided_wording() -> None:
    privacy = _privacy()
    assert (
        "A pasted resume is used for that assessment only: its full text is never saved and never sent anywhere but "
        "your assessment model; the stored result keeps short evidence quotes on your machine."
    ) in privacy
    assert "is used once, and is never saved" not in privacy
    status = _flat(_text().split("## Status", 1)[1].split("\n## ", 1)[0])
    assert (
        "0.1.9 is an alpha: it has been through hands-on testing by the maintainer "
        "(two UAT rounds, 2026-09-27 and 2026-09-29); expect rough edges."
    ) in status
    assert "not yet had a live-provider" not in status
