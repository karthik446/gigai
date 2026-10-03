"""0110-048: a hybrid profile keeps fully on-site roles in its own metro area.

One rule (``work_mode.in_person_modes``) feeds the acquire filter
(``work_mode_fit``) AND the assess prompt's CANDIDATE WORK MODE wording
(``assessment_core``); the twin test proves they agree on the same locations.
"""

from __future__ import annotations

import re

import pytest

from gigai.scout.assessment_core import AssessJob, build_assess_context, render_assess_prompt
from gigai.scout.find_jobs.work_mode import in_area, parse_area, work_mode_fit

from .test_work_mode_filter import _config, _passing, _row

_HOUSTON_ONSITE = _row("bayou", "On-site, Houston TX", job="1")
_NEW_YORK_ONSITE = _row("bayou", "On-site, New York", job="2")
_HOUSTON_HYBRID = _row("bayou", "Houston, TX (Hybrid)", job="3")
_REMOTE = _row("bayou", "Remote - United States", job="4")
_ROWS = [_HOUSTON_ONSITE, _NEW_YORK_ONSITE, _HOUSTON_HYBRID, _REMOTE]


def test_a_hybrid_houston_profile_keeps_onsite_houston_and_drops_onsite_new_york() -> None:
    kept = _passing(_config("hybrid", "Houston, TX"), _ROWS)

    assert kept == [_HOUSTON_ONSITE, _HOUSTON_HYBRID, _REMOTE]
    assert _NEW_YORK_ONSITE not in kept


def test_a_remote_only_profile_still_drops_onsite_anywhere() -> None:
    assert _passing(_config("remote", "Houston, TX"), _ROWS) == [_REMOTE]


_JOB = AssessJob(title="Software Engineer", company="Bayou", location="Houston, TX", posting_text="Build things.")
_LOCATIONS = ["On-site, Houston TX", "On-site, New York", "Houston, TX (Hybrid)", "Hybrid - New York, NY", "Remote - United States"]


def _prompt_accepts(prompt: str, mode: str, location: str, area_text: str) -> bool:
    """What the prompt's own wording says about a posting: a fake reader of the CANDIDATE WORK MODE line."""

    line = next(block for block in prompt.split("\n\n") if block.startswith("CANDIDATE WORK MODE"))
    if mode == "remote":
        return True
    stated = re.search(r"CANDIDATE WORK MODE: \S+ \(remote roles, and (?P<modes>.*?) roles in their own area\)", line)
    assert stated is not None, line[:200]
    allowed = {"hybrid"} if stated.group("modes") == "hybrid" else {"hybrid", "on-site"}
    posting_mode = "on-site" if "on-site" in location.lower() else "hybrid"
    area = parse_area(area_text)
    assert area is not None
    return posting_mode in allowed and in_area(location, area) is not False


@pytest.mark.parametrize("location", _LOCATIONS)
def test_the_filter_and_the_assess_prompt_agree_for_a_hybrid_houston_profile(location: str) -> None:
    config = _config("hybrid", "Houston, TX")
    prompt = render_assess_prompt(_JOB, build_assess_context(resume_text="A resume.", countries=("US",), location="Houston, TX", work_mode="hybrid"))
    row = _row("bayou", location)
    if "remote" in location.lower():
        return  # a remote posting always passes both; nothing to disagree on
    assert work_mode_fit(row, config).passes is _prompt_accepts(prompt, "hybrid", location, "Houston, TX")
