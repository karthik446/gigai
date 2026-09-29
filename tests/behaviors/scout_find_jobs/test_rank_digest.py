"""SCOPE-ADD-3 B1: digest v2 (``scout/find_jobs/rank_digest.py``).

The flag fixtures are the sentences from the ranking spike's 9 sponsorship/
citizenship/clearance not-a-match postings (research/ranking-spike
results/gold_table.csv, verdict ``not_a_match``: p067 p100 p101 p201 p219 p222
p328 p371 p396), quoted from the public posting text; v1 caught 3 of them.
The resume here is synthetic.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from gigai.scout.find_jobs.rank_digest import (
    DIGEST_VERSION,
    CandidatePrefs,
    constraint_flags,
    guard_name,
    guard_private,
    posting_digest,
    requirements_section,
    resume_digest,
    resume_titles,
    split_resume_header,
)


@dataclass(frozen=True)
class _Posting:
    title: str
    company: str
    location: str
    text: str | None
    countries: tuple[str, ...] | None = None


# (spike posting id, the flag that must be caught, the sentence from the posting)
SPIKE_BLOCKER_CASES = [
    ("p067", "no_sponsor", "Please note that sponsorship of new applicants for employment authorization, or any other "
     "immigration-related support, is not available for this position at this time."),
    ("p100", "no_sponsor", "This role is based in our Sunnyvale, CA location and is onsite Mon - Friday. This role is not "
     "eligible for current Visa sponsorship. This role is not eligible for relocation."),
    ("p101", "no_sponsor", "*Please note that Cast AI does not provide any form of visa sponsorship/work permit. #LI-Remote"),
    ("p201", "citizen_or_gc", "maintaining documentation Must be a US citizen or green card holder Preferred Attributes:"),
    ("p219", "no_sponsor", "Candidates must be legally authorized to work in the United States. LendingTree and its affiliates "
     "will not provide or assume sponsorship for employment visas for this position, now or in the future."),
    ("p222", "no_sponsor", "MariaDB is an equal opportunity employer. MariaDB does not sponsor work visas or relocation."),
    ("p328", "citizen_or_gc", "U.S citizenship required, due to program requirements. THESE QUALIFICATIONS WOULD BE NICE TO "
     "HAVE: Active Secret clearance, Top Secret clearance, or ability to obtain and maintain one"),
    ("p371", "no_sponsor", "Applicants must be legally authorized to work in the United States. SmartAsset is unable to "
     "provide employment visa sponsorship for this position, now or in the future."),
    ("p396", "clearance", "Active TS/SCI security clearance and US citizenship, as well as eligibility and willingness to "
     "undergo a Counterintelligence (CI) Scope Polygraph"),
]


@pytest.mark.parametrize(("spike_id", "flag", "sentence"), SPIKE_BLOCKER_CASES, ids=[case[0] for case in SPIKE_BLOCKER_CASES])
def test_v2_flags_catch_every_spike_sponsorship_citizenship_clearance_case(spike_id: str, flag: str, sentence: str) -> None:
    text = "We build things.\n\nWhat you'll bring\n- 5+ years of Python\n\n" + sentence
    assert flag in constraint_flags(text), spike_id


def test_all_nine_spike_cases_are_flagged_as_hard_constraints() -> None:
    hard = {"no_sponsor", "citizen_or_gc", "clearance"}
    caught = [case[0] for case in SPIKE_BLOCKER_CASES if hard & set(constraint_flags(case[2]))]
    assert len(caught) == 9


def test_p328_and_p396_carry_both_citizenship_and_clearance() -> None:
    assert constraint_flags(SPIKE_BLOCKER_CASES[6][2]) == ["citizen_or_gc", "clearance"]
    assert constraint_flags(SPIKE_BLOCKER_CASES[8][2]) == ["citizen_or_gc", "clearance"]


def test_a_positive_sponsorship_statement_is_not_a_blocker() -> None:
    # spike p241 (Monzo): relocation + sponsorship offered; the gold verdict failed it on location only.
    text = "£130,000 - £170,000 + Equity. We can help you relocate to the UK. We can sponsor visas. Based in London."
    assert "no_sponsor" not in constraint_flags(text)


def test_sponsorship_negation_must_be_within_the_window() -> None:
    far = "We do not offer unlimited snacks." + " filler" * 40 + " Visa sponsorship details are on our careers page."
    assert "no_sponsor" not in constraint_flags(far)
    assert constraint_flags("We are unable to sponsor visas.") == ["no_sponsor"]


def test_onsite_flag() -> None:
    assert constraint_flags("This is a hybrid role, three days in-office.") == ["onsite"]
    assert constraint_flags("Fully remote.") == []


_TEXT = """About us
We make payment software for clinics.

What you'll do
- Build services in Java and Kafka

Requirements
- 6+ years building backend systems in Python and Go
- Production experience with AWS, Kubernetes and Postgres
- Comfortable with distributed systems

Benefits
- Great healthcare, 401k
"""


def test_requirements_section_is_found_by_heading_and_stops_at_benefits() -> None:
    section, found = requirements_section(_TEXT)
    assert found
    assert "Python" in section and "Kafka" not in section and "healthcare" not in section


def test_posting_digest_golden_line() -> None:
    posting = _Posting("Senior Backend Engineer", "acme", "Remote - US", _TEXT + "\nWe cannot sponsor visas.", ("US",))
    assert posting_digest(posting, "p7") == (
        "p7 | Senior Backend Engineer @ acme | lvl=senior | loc=Remote - US [US] | yrs=6+ | "
        "req=Python, Go, AWS, Kubernetes, Postgres, distributed systems | flags=no_sponsor"
    )


def test_posting_digest_without_a_requirements_heading_reads_the_full_text() -> None:
    posting = _Posting("Staff Engineer | Platform", "beta", "NYC | Hybrid", "We use Rust and Linux. Hybrid, in NYC.", None)
    line = posting_digest(posting, "p0")
    assert line == "p0 | Staff Engineer / Platform @ beta | lvl=staff | loc=NYC / Hybrid [?] | yrs=? | req=Rust, Linux (req=full) | flags=onsite"


def test_posting_digest_tolerates_missing_text() -> None:
    posting = _Posting("Engineer", "gamma", "", None)
    assert posting_digest(posting, "p1") == "p1 | Engineer @ gamma | lvl=mid | loc=? [?] | yrs=? | req=- (req=full)"


_RESUME = """Sam Example
Senior Software Engineer

Backend engineer with 9 years building payment and data platforms in Python and Go on AWS, with Kubernetes and Postgres in production.

Experience
- Acme Pay: Python, Kafka, Terraform
"""


def test_resume_digest_golden() -> None:
    prefs = CandidatePrefs(
        titles=("Staff Software Engineer", "Senior Software Engineer"),
        countries=("US",),
        visa_sponsorship_required=True,
        location="Denver, CO",
        remote_preferred=True,
    )
    assert resume_digest(_RESUME, prefs) == "\n".join([
        "CANDIDATE: level=staff/senior; 9+ yrs; titles: -; domain: payments",
        "skills: Python, Go, AWS, Kubernetes, Terraform, Kafka, Postgres, payments",
        "targets: Staff Software Engineer; Senior Software Engineer",
        "countries: US",
        "needs visa sponsorship: yes",
        "location: Denver, CO (remote preferred)",
    ])


def test_resume_digest_defaults() -> None:
    digest = resume_digest("Short.", CandidatePrefs())
    assert digest.splitlines()[0] == "CANDIDATE: level=?; ?+ yrs; titles: -; domain: -"
    assert digest.splitlines()[2:] == ["targets: unspecified", "countries: any", "needs visa sponsorship: no", "location: unknown"]


def test_digest_version() -> None:
    assert DIGEST_VERSION == "digest-v4"


# uat-bug-030: the ranking model never sees the resume header. Synthetic data only.
MESSY_RESUME = """Sam Q. Example
sam.example@example.com | +1 (555) 123-4567 | 555.987.6543 | +1 555 246 8101
1234 Maple Street Apt 5, Denver, CO 80202
github.com/samexample | https://www.linkedin.com/in/sam-example | www.sam-example.dev
Reach me: sam.example@example.com or +1-555-321-0987 or linkedin.com/in/sam-example

Staff Software Engineer | Senior Backend Engineer
Backend engineer with 9 years building payment platforms in Python and Go on AWS with Postgres.

Experience
Senior Software Engineer at Acme Pay (2019-2023): Python, Kafka, Terraform
"""

_PRIVATE = (
    "Sam", "Example", "sam.example", "example.com", "+1", "555", "123-4567", "987.6543", "246 8101", "321-0987",
    "1234 Maple", "Maple Street", "Denver", "80202", "github", "linkedin", "sam-example", "www.", "http",
)


def _prefs() -> CandidatePrefs:
    return CandidatePrefs(titles=("Staff Software Engineer",), countries=("US",), location="Remote")


def test_resume_digest_never_carries_the_header_block() -> None:
    digest = resume_digest(MESSY_RESUME, _prefs())
    for private in _PRIVATE:
        assert private not in digest, private
    assert "Staff Software Engineer" in digest and "Senior Backend Engineer" in digest
    assert "9+ yrs" in digest and "Python" in digest and "Kafka" in digest and "domain: payments" in digest


def test_rendered_rank_prompt_never_carries_the_header_block() -> None:
    from gigai.scout.find_jobs.model_rank import render_rank_prompt

    prompt = render_rank_prompt(["p1 | Engineer @ acme | lvl=mid | loc=? [US] | yrs=5+ | req=Python"], resume_digest(MESSY_RESUME, _prefs()))
    for private in _PRIVATE:
        assert private not in prompt, private
    assert "Staff Software Engineer" in prompt and "9+ yrs" in prompt


@pytest.mark.parametrize("text", [
    "mail sam@example.com now", "call +1 555 123 4567", "call (555) 123-4567", "call 555.123.4567", "see github.com/sam",
    "see https://x.example.org/p", "see www.example.dev", "see linkedin.com/in/sam", "handle @samexample",
])
def test_guard_strips_contact_details_from_free_text(text: str) -> None:
    guarded = guard_private(text)
    assert not any(t in guarded for t in ("@", "555", "github", "http", "www", "linkedin", "example"))


def test_guard_drops_street_address_lines_and_keeps_years() -> None:
    assert guard_private("keep 9+ yrs\n12 Oak Avenue, Springfield") == "keep 9+ yrs"
    assert guard_private("2019-2023 | 9+ yrs; Python") == "2019-2023 | 9+ yrs; Python"


def test_resume_titles_come_from_title_shaped_segments_only() -> None:
    assert resume_titles(MESSY_RESUME) == ["Staff Software Engineer", "Senior Backend Engineer", "Senior Software Engineer"]


# uat-bug-032: a title-shaped name and a messy header never reach the digest or the rendered prompt.
TITLE_NAME_RESUME = """Senior Engineer Sam Example
Sam Example, Denver, CO 80202
sam.example@example.com | (555) 123-4567 | +44 20 7946 0958 | 555 987 6543
https://sam-example.dev | github.com/samexample | linkedin.com/in/sam-example | Denver CO / sam@x.io / 555.111.2222

Staff Software Engineer | Senior Backend Engineer
Backend engineer with 9 years building payment platforms in Python and Go on AWS with Postgres.

Experience
Senior Software Engineer at Acme Pay (2019-2023): Python, Kafka, Terraform
"""

_TITLE_NAME_PRIVATE = (
    "Sam", "Example", "sam.example", "example.com", "x.io", "555", "123-4567", "7946", "0958", "987 6543", "111.2222",
    "Denver", "CO", "80202", "github", "linkedin", "sam-example", "http", "www.",
)


@pytest.mark.parametrize("resume", [MESSY_RESUME, TITLE_NAME_RESUME])
def test_title_shaped_name_and_header_never_reach_digest_or_prompt(resume: str) -> None:
    from gigai.scout.find_jobs.model_rank import render_rank_prompt

    digest = resume_digest(resume, _prefs())
    prompt = render_rank_prompt(["p1 | Engineer @ acme | lvl=mid | loc=? [US] | yrs=5+ | req=Python"], digest)
    for text in (digest, prompt):
        for private in _TITLE_NAME_PRIVATE:
            assert private not in text, private
        assert "Staff Software Engineer" in text and "Senior Backend Engineer" in text
        assert "9+ yrs" in text and "Python" in text and "Kafka" in text and "domain: payments" in text
    assert "Senior Engineer Sam" not in digest


def test_split_header_by_structure_and_no_heading_fallback() -> None:
    header, body = split_resume_header(TITLE_NAME_RESUME)
    assert header[0] == "Senior Engineer Sam Example" and len(header) == 4
    assert body[0] == "" and "Staff Software Engineer | Senior Backend Engineer" in body
    header, body = split_resume_header("Sam Example\nDenver, CO 80202\nsam@example.com\n\nPython developer, 7 years of Go.\nCall 555-123-4567\n")
    assert header == ["Sam Example", "Denver, CO 80202", "sam@example.com"]
    assert not any("555" in line for line in body) and "Python developer, 7 years of Go." in body


def test_name_guard_removes_profile_name_tokens_whole_word_case_insensitively() -> None:
    assert guard_name("built by SAM at Examples inc; example", {"sam", "example"}) == "built by at Examples inc; "
    digest = resume_digest("Sam Example\n\nStaff Engineer\nPython, 6 years", CandidatePrefs(titles=("Sam Lead Engineer",)), name="Sam Example")
    assert "sam" not in digest.lower().replace("sampling", "")
    assert "Lead Engineer" in digest


def test_location_line_comes_only_from_prefs() -> None:
    digest = resume_digest(TITLE_NAME_RESUME, CandidatePrefs(countries=("US",)))
    assert "location: unknown" in digest and "Denver" not in digest
    assert "location: Remote (remote preferred)" in resume_digest(TITLE_NAME_RESUME, CandidatePrefs(location="Remote", remote_preferred=True))
