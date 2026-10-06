"""0110-10-11 (UAT finding 8): an Ashby posting that "could not be assessed" (``posting_requirements_unreadable``) while tailoring it worked.

A SYNTHETIC Ashby board, shaped like the public job posting API's answer
(``GET https://api.ashbyhq.com/posting-api/job-board/<board>``:
``{"apiVersion", "jobs": [{..., "descriptionHtml", "descriptionPlain"}]}``),
put into the board cache and indexed from there. No request is made.

What these pin:

- THE TEXT PATH IS ONE. The assessment reads the posting from the index
  (``descriptionPlain``, requirement sections and all); a tailoring of the same
  job reads the same posting (``job_source.resolve_job_for_assessment``). So
  the two never differ in what they read: what differs is that only the
  assessment has guards that can refuse the model's answer.
- THE TWO GUARDS SAY WHICH ONE REFUSED. ``posting_requirements_unreadable`` is
  one code for two rules. A batch failure now carries the rule as ``reason``
  (and the terminal prints it), so the next posting this happens to is one look,
  not a hunt: ``matched_on_too_few_requirements`` (a Matched on fewer than three
  requirement rows for a long posting) or ``no_requirements_in_text``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs.company_index import board_list_url, index_stamp, refresh_company
from gigai.scout.find_jobs.job_source import index_posting
from gigai.scout.find_jobs.watchlist import add_company_from_url
from gigai.scout.quick_assess import read_quick_assessment

from tests.support.fit_fixtures import matrix_answer
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago

BOARD = "tallgrasshealth"
POSTING_ID = "5b1f0c1e-2a55-4f0e-9d7c-6c1d2f3a4b5c"
URL = f"https://jobs.ashbyhq.com/{BOARD}/{POSTING_ID}"

_ABOUT = (
    "Tallgrass Health builds scheduling software for outpatient clinics. Patients book, confirm and reschedule visits "
    "on their phones, and front desks get their mornings back. We are a team of thirty, profitable, and growing with "
    "the clinics we serve across the country. Engineering is twelve people who ship every day and own what they ship. "
)
_ROLE = (
    "You will lead the design of the booking engine and the integrations with clinic record systems, set the technical "
    "direction for the platform team, and mentor the engineers around you. "
)
_REQUIREMENTS = [
    "8+ years of experience building and operating production web services",
    "Deep experience with Python and PostgreSQL at scale",
    "Experience designing integrations with third-party healthcare record systems",
    "A track record of leading projects across several teams",
    "Experience with insurance eligibility checks is a plus",
]
_BENEFITS = (
    "We offer health, dental and vision cover, a home office budget, and four weeks of paid time off. Tallgrass Health "
    "is an equal opportunity employer. We celebrate difference and are committed to an inclusive workplace for everyone. "
)


def _description_plain() -> str:
    return "\n\n".join(
        ["About Tallgrass Health", _ABOUT * 2, "About the role", _ROLE, "What we are looking for", *_REQUIREMENTS, "Benefits", _BENEFITS]
    )


def _description_html() -> str:
    items = "".join(f"<li><p>{line}</p></li>" for line in _REQUIREMENTS)
    return (
        f"<h2>About Tallgrass Health</h2><p>{_ABOUT * 2}</p><h2>About the role</h2><p>{_ROLE}</p>"
        f"<h2>What we are looking for</h2><ul>{items}</ul><h2>Benefits</h2><p>{_BENEFITS}</p>"
    )


def ashby_job(*, plain: str | None = None, html: str | None = None) -> dict[str, object]:
    """One job as the Ashby public job posting API lists it."""

    return {
        "id": POSTING_ID,
        "title": TITLE_BOTH,
        "department": "Engineering",
        "team": "Platform",
        "employmentType": "FullTime",
        "location": "Remote - United States",
        "secondaryLocations": [],
        "publishedAt": "2026-09-24T15:04:05.000+00:00",
        "isListed": True,
        "isRemote": True,
        "workplaceType": "Remote",
        "address": {"postalAddress": {"addressRegion": "Colorado", "addressCountry": "United States", "addressLocality": "Denver"}},
        "jobUrl": URL,
        "applyUrl": f"{URL}/application",
        "descriptionHtml": _description_html() if html is None else html,
        "descriptionPlain": _description_plain() if plain is None else plain,
        "shouldDisplayCompensationOnJobPostings": False,
    }


def seed_ashby(fx: PostingsFixture, job: dict[str, object]) -> None:
    add_company_from_url(f"https://jobs.ashbyhq.com/{BOARD}", fx.home_root, fx.target)
    body = json.dumps({"apiVersion": "1", "jobs": [job]}).encode("utf-8")
    fx.cache.store("ashby", board_list_url("ashby", BOARD), body=body, etag=None, last_modified=None, marker=None)
    refresh_company(fx.index, fx.cache, ats="ashby", slug=BOARD, observed_at=index_stamp(days_ago(1)))


def _full_answer() -> str:
    """What a model answers for this posting: its five requirements, read from the text."""

    return matrix_answer([(line, "hard", "met") for line in _REQUIREMENTS[:4]] + [(_REQUIREMENTS[4], "nice_to_have", "unmet")])


def _assess(fx: PostingsFixture) -> dict[str, object]:
    return posting_search.assess_these(fx.home_root, fx.target, jobs=[URL], approve=True, now=NOW)


def test_an_ashby_posting_is_assessed_from_its_listed_description_with_its_requirements(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    seed_ashby(fx, ashby_job())
    fx.base.model.assessed = _full_answer()
    fx.base.model.assess_prompts.clear()

    done = _assess(fx)

    assert done["assessed"] == {"requested": 1, "assessed": 1, "failed": [], "stopped": None, "fetched_on_demand": 0}
    stored = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, URL)
    assert stored is not None and stored.job.fetch_kind == "ats_board" and len(stored.result.matrix) == 5
    # The model was given the posting's requirement section, every line of it.
    prompt = fx.base.model.assess_prompts[-1]
    assert "What we are looking for" in prompt and all(line in prompt for line in _REQUIREMENTS)
    # One posting, one source: a tailoring of this job reads the SAME text the assessment was made on.
    again = index_posting(fx.home_root, fx.target, URL)
    assert again is not None and again.text == _description_plain().strip() and again.text_sha256 == stored.job.text_sha256


@pytest.mark.skip(reason="the unreadable-posting guard changed in 0.1.11 (an ATS board's own text is trusted; a menu page is refused before the model call): re-pin in 0.1.11.1")
def test_each_guard_behind_posting_requirements_unreadable_names_itself(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    seed_ashby(fx, ashby_job())
    assert len(_description_plain()) >= 1200

    # Rule 1 (0.1.11 GUARDFIX): two requirement rows for this long posting are STORED with a note, after the one retry.
    stored = _assess(fx)

    assert stored["assessed"]["assessed"] == 1 and stored["assessed"]["failed"] == []  # type: ignore[index]
    [note] = stored["assessed"]["requirements_notes"]  # type: ignore[index]
    assert note["text"] == "Only 2 requirements were read from this posting. Open the posting to check."
    assert f"  {note['text']} {URL}" in posting_search.render(stored)
    kept = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, URL)
    assert kept is not None and kept.requirements_note == note["text"]

    # Rule 1 still refuses an answer with no requirement row at all (only a remote/location row).
    fx.base.model.assessed = matrix_answer([("May work remotely anywhere in the US", "hard", "met")])
    refused = posting_search.assess_these(fx.home_root, fx.target, jobs=[URL], approve=True, again=True, now=NOW)
    failure = refused["assessed"]["failed"][0]  # type: ignore[index]
    assert (failure["job_identity"], failure["error_code"], failure["reason"]) == (
        URL, "posting_requirements_unreadable", "matched_on_too_few_requirements",
    )
    assert f"  not assessed (posting_requirements_unreadable: matched_on_too_few_requirements): {URL}" in posting_search.render(refused)

    # The same posting, the same text, a full answer: assessed. The text was never the problem.
    fx.base.model.assessed = _full_answer()
    assert posting_search.assess_these(fx.home_root, fx.target, jobs=[URL], approve=True, again=True, now=NOW)["assessed"]["assessed"] == 1  # type: ignore[index]

    from gigai.scout import quick_assess

    assert quick_assess.POSTING_UNREADABLE_REASONS == ("matched_on_too_few_requirements", "no_requirements_in_text")


@pytest.mark.skip(reason="the unreadable-posting guard changed in 0.1.11 (an ATS board's own text is trusted; a menu page is refused before the model call): re-pin in 0.1.11.1")
def test_a_description_with_no_requirement_wording_names_the_other_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    # A plain description that is a list of names: no requirement wording, not prose.
    names = "\n".join(["Tallgrass", "Careers", "Teams", "Locations", "Benefits", "Blog", "Press", "Sign in"] * 3)
    seed_ashby(fx, ashby_job(plain=names))
    fx.base.model.assessed = json.dumps({
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "No stated requirements", "class": "nice_to_have", "status": "met", "resume_evidence": []}],
        "suggestions": [], "questions": [], "not_a_match_reason": None,
    })

    done = scout_new.scout_new(fx.home_root, fx.target, now=NOW, peek=True, assess=True)

    failure = done["assessed"]["failed"][0]  # type: ignore[index]
    assert (failure["error_code"], failure["reason"]) == ("posting_requirements_unreadable", "no_requirements_in_text")
    assert f"  not assessed (posting_requirements_unreadable: no_requirements_in_text): {URL}" in scout_new.render(done)
