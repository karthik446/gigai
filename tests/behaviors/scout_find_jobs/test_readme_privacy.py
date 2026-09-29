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
    # uat-bug-035: claude_cli joined the sealed enum.
    assert {target.value for target in ModelTarget} == {"ollama_local", "codex_cli", "openrouter_api", "claude_cli"}
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


def test_the_readme_says_exa_is_optional_and_off_for_a_new_setup() -> None:
    text = _flat(_text())
    # uat-bug-035 (operator decision): one model target is required, Codex or Claude.
    assert (
        "You need one model target to start: Codex (`codex_cli`) or Claude (`claude_cli`); "
        "`ollama_local` and `openrouter_api` are optional alternatives."
    ) in text
    assert "Exa is an optional extra, off for a new setup" in text
    assert "Also search the open web with Exa (needs an Exa key)" in text
    assert "gigai secrets add exa" in text
    privacy = _privacy()
    assert "Exa, only if enabled in your sources" in privacy


def test_the_readme_names_claude_as_a_required_choice_and_says_where_it_sends() -> None:
    """uat-bug-035: Codex or Claude is the one required target; Claude's traffic goes to Anthropic."""

    text = _flat(_text())
    install = _flat(_text().split("## Install", 1)[1].split("\n## ", 1)[0])
    assert "Scout also needs one model target: Codex (`codex_cli`, the `codex` CLI) or Claude (`claude_cli`, the `claude` CLI)" in install
    privacy = _privacy()
    assert "`claude_cli` (the Claude Code CLI sends it to Anthropic)" in privacy
    assert "`codex_cli` (the Codex CLI sends it to OpenAI)" in privacy
    assert "`default_model_target` accepts `ollama_local`, `codex_cli`, `claude_cli`, or `openrouter_api`" in text
    assert "which ignores `--model`, so assessments run Claude Code's default model" in text


def test_the_readme_says_tailored_resumes_are_drafts_and_states_the_evidence_exactly() -> None:
    """uat-bug-038: the draft sentence, the evidence note (judge count and adjudication, no rounding) and the wizard's saved target."""

    text = _flat(_text())
    assert "Tailored resumes are drafts: review each line; every line shows its sources." in text
    assert "155 and 151 lines" in text and "0.6% and 0.66% of lines" in text
    assert "one judge-flagged plural" in text and "not a new fact" in text
    assert "the setup wizard's \"Model for Scout\" choice is saved here when you press Finish" in text
    changelog = _flat((Path(__file__).resolve().parents[3] / "CHANGELOG.md").read_text(encoding="utf-8"))
    assert "Tailored resumes are drafts." in changelog and "0.6% and 0.66% of lines" in changelog
    assert "The setup wizard saves your model choice as the default model target." in changelog


def test_assess_all_new_is_described_and_keeps_the_privacy_statement_true() -> None:
    # uat-bug-042: quick_assess sends the posting and resume (assessment_core.render_assess_prompt); the
    # background job calls that same path (find_jobs/assess_all.quick_assess_one) with the run's model target.
    from gigai.scout.find_jobs import assess_all

    assert assess_all.ASSESS_CONCURRENCY == 4
    text = _flat(_text())
    assert "**Assess all new.** A run assesses its top-ranked postings automatically" in text
    assert "one model call per posting, 4 at a time" in text
    assert "per-token" not in text and "Scout passes them no API key" in text
    assert "each posting's assessment sends your resume and that posting to that provider" in text
    assert "including each posting \"Assess all new\" assesses" in _privacy()
