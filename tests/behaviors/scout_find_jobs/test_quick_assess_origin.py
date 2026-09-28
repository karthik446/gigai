"""assess-origin-field (v0.1.9): where a stored quick assessment was started.

``AssessRequest.origin`` / ``AssessResponse.origin`` is ``quick_assess``
("+ Assess a job", ``gigai scout assess``, a bare ``POST /api/assess``) or
``job_page`` (the job page of a posting a find-jobs run found). The UI lists
only the first kind under Assessments, so what is pinned here is what gets
stored:

* a request that names an origin stores it;
* a request that names none stores ``quick_assess`` for a job never
  assessed before, and otherwise leaves the stored origin as it is (every
  re-assessment after an answer);
* a file written before the field has none: it loads, re-serializes
  byte-identically, and stays without one until a request names one.

Reuses ``test_quick_assess.py``'s gig fixture, scripted model binding (the
C1 seam) and fixture replies.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import quick_assess
from gigai.scout.find_jobs.assess_contracts import (
    ASSESS_ORIGINS,
    ORIGIN_JOB_PAGE,
    ORIGIN_QUICK_ASSESS,
    AssessJobInput,
    AssessRequest,
    AssessResponse,
)
from gigai.scout.find_jobs.contracts import FindJobsContractError
from gigai.scout.quick_assess import TRIGGER_ANSWER_PREFIX, list_quick_assessments, run_quick_assessment

from tests.behaviors.scout_find_jobs.test_quick_assess import (
    _GOOD_MATCH,
    _GOOD_PENDING,
    _POSTING,
    _config_with_ollama,
    _install,
    _pasted,
    _run,
)
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"
_JOB_URL = "https://boards.greenhouse.io/acme/jobs/101"


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


@pytest.fixture(autouse=True)
def _no_ambient_jev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JEV_API_KEY", raising=False)


def _stored(response: AssessResponse) -> dict[str, object]:
    return json.loads(Path(response.stored_path).read_text(encoding="utf-8"))


def _after_an_answer(fx: ProfileFixtureGig, request: AssessRequest) -> AssessResponse:
    """What ``POST /api/answers`` with ``reassess`` runs: no origin of its own."""

    return run_quick_assessment(
        request, home_root=fx.home_root, target=fx.target, config=_config_with_ollama(fx.home_root),
        trigger=TRIGGER_ANSWER_PREFIX + "cloud:gcp",
    )


def _without_origin(response: AssessResponse) -> bytes:
    """Rewrite the stored file the way a server before this field left it."""

    old = {key: value for key, value in _stored(response).items() if key != "origin"}
    old_bytes = json.dumps(old, indent=2, sort_keys=True).encode("utf-8")
    Path(response.stored_path).write_bytes(old_bytes)
    return old_bytes


# --- what is stored ---------------------------------------------------------------------


def test_a_new_assessment_with_no_origin_is_a_quick_assessment(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_PENDING])

    response = _run(fx, _pasted())

    assert response.origin == ORIGIN_QUICK_ASSESS == "quick_assess"
    assert _stored(response)["origin"] == "quick_assess"
    assert response.to_json()["origin"] == "quick_assess"
    assert [item.origin for item in list_quick_assessments(fx.home_root, fx.target)] == ["quick_assess"]


def test_the_job_pages_origin_is_stored_and_listed(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_PENDING])

    response = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL), origin=ORIGIN_JOB_PAGE))

    assert response.origin == "job_page"
    assert _stored(response) == response.to_json()
    assert _stored(response)["origin"] == "job_page"
    listed = list_quick_assessments(fx.home_root, fx.target)
    assert [item.origin for item in listed] == ["job_page"]
    assert listed[0].to_json() == _stored(response)
    found = quick_assess.find_quick_assessment_by_job_identity(fx.home_root, fx.target, response.job.job_identity)
    assert found is not None and found.origin == "job_page"


@pytest.mark.parametrize("origin", ASSESS_ORIGINS)
def test_a_reassessment_after_an_answer_keeps_the_stored_origin(
    fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_PENDING, _GOOD_MATCH, _GOOD_MATCH])
    first = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL), origin=origin))

    second = _after_an_answer(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL)))
    # `gigai scout answer --reassess` and a bare POST on a known job: the same.
    third = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL)))

    assert second.stored_path == first.stored_path == third.stored_path
    assert (first.origin, second.origin, third.origin) == (origin, origin, origin)
    assert _stored(third)["origin"] == origin
    assert [entry.trigger for entry in third.history] == ["assess", "answer:cloud:gcp", "reassess"]


def test_a_request_that_names_an_origin_replaces_the_stored_one(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_PENDING, _GOOD_MATCH])
    first = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL), origin=ORIGIN_JOB_PAGE))

    # The operator pastes the same address under "+ Assess a job".
    second = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL), origin=ORIGIN_QUICK_ASSESS))

    assert second.stored_path == first.stored_path
    assert (first.origin, second.origin) == ("job_page", "quick_assess")
    assert _stored(second)["origin"] == "quick_assess"


# --- a file written before the field ---------------------------------------------------


def test_a_stored_result_without_an_origin_still_loads_byte_identically(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_MATCH])
    response = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL)))
    old_bytes = _without_origin(response)

    listed = list_quick_assessments(fx.home_root, fx.target)

    assert len(listed) == 1 and listed[0].origin is None
    assert "origin" not in listed[0].to_json()
    assert listed[0].result == response.result and listed[0].history == response.history
    assert json.dumps(listed[0].to_json(), indent=2, sort_keys=True).encode("utf-8") == old_bytes


def test_a_result_without_an_origin_gets_none_from_an_answer(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_PENDING, _GOOD_MATCH, _GOOD_MATCH])
    first = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL)))
    _without_origin(first)

    second = _after_an_answer(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL)))

    assert second.stored_path == first.stored_path
    assert second.origin is None and "origin" not in _stored(second)

    # The next request that says where it was started is what sets it.
    third = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL), origin=ORIGIN_JOB_PAGE))
    assert third.origin == "job_page" and _stored(third)["origin"] == "job_page"


# --- the contract -----------------------------------------------------------------------


def test_the_request_carries_the_origin_only_when_it_has_one() -> None:
    bare = AssessRequest(job=AssessJobInput(job_text=_POSTING))
    assert bare.origin is None and "origin" not in bare.to_json()
    assert AssessRequest.from_json({"job": {"job_text": _POSTING}}).origin is None
    assert AssessRequest.from_json({"job": {"job_text": _POSTING}, "origin": None}).origin is None

    named = AssessRequest.from_json({"job": {"job_text": _POSTING}, "origin": "job_page"})
    assert named.origin == "job_page" and named.to_json()["origin"] == "job_page"
    assert AssessRequest.from_json(named.to_json()) == named
    assert ASSESS_ORIGINS == ("quick_assess", "job_page")


def test_an_unknown_origin_fails_closed(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    for bad in ("run", "", "Job_Page"):
        with pytest.raises(FindJobsContractError) as excinfo:
            AssessRequest(job=AssessJobInput(job_text=_POSTING), origin=bad)
        assert excinfo.value.code == "bad_enum"
    with pytest.raises(FindJobsContractError):
        AssessRequest.from_json({"job": {"job_text": _POSTING}, "origin": 7})

    _install(monkeypatch, [_GOOD_MATCH])
    payload = _run(fx, _pasted()).to_json()
    with pytest.raises(FindJobsContractError) as excinfo:
        AssessResponse.from_json({**payload, "origin": "run"})
    assert excinfo.value.code == "bad_enum"


# --- gigai scout assess -----------------------------------------------------------------


def test_the_cli_assesses_on_demand_unless_told_otherwise(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    config = _config_with_ollama(fx.home_root)
    monkeypatch.setattr(quick_assess, "load_config", lambda home_root: config)
    _install(monkeypatch, [_GOOD_PENDING, _GOOD_MATCH])
    posting = tmp_path / "posting.txt"
    posting.write_text(_POSTING, encoding="utf-8")
    base = ["scout", "assess", "--home", str(fx.home_root), "--target", str(fx.target), "--job-text", str(posting), "--json"]
    runner = CliRunner()

    default = runner.invoke(cli, base)
    assert default.exit_code == 0, default.output
    first = json.loads(default.output)
    assert first["origin"] == "quick_assess"
    assert json.loads(Path(first["stored_path"]).read_text(encoding="utf-8"))["origin"] == "quick_assess"

    named = runner.invoke(cli, [*base, "--origin", "job_page"])
    assert named.exit_code == 0, named.output
    second = json.loads(named.output)
    assert second["stored_path"] == first["stored_path"] and second["origin"] == "job_page"

    refused = runner.invoke(cli, [*base, "--origin", "somewhere"])
    assert refused.exit_code != 0
