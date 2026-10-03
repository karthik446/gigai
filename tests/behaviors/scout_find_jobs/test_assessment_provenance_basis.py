"""0.1.10.7 PL2: a new assessment record keeps what a run sealed (DESIGN 10.2).

Each record ``run_quick_assessment`` writes now also carries ``profile_ref``
(the profile's id, revision and content digest), ``posting_sha256`` (the
company index's ``content_sha256`` for the same posting) and ``model`` (the
model id that answered). Posting text stays stored for a fetched posting,
index postings included, and never for pasted text. A record written before
these fields reads, serves and re-serializes exactly as before.

Real gig (``build_gig_with_resume``), scripted model (the C1 seam), the
fixture HTTP transport for the Greenhouse postings. Synthetic data only.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.scout import assessment_basis
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessRequest, AssessResponse, AssessResumeInput
from gigai.scout.find_jobs.ats_board_clients import _greenhouse_row
from gigai.scout.find_jobs.contracts import FindJobsContractError, ProfileRef
from gigai.scout.profile_records import selected_profile
from gigai.scout.quick_assess import list_quick_assessments, run_quick_assessment

from tests.behaviors.scout_find_jobs.test_quick_assess import _GOOD_MATCH, _POSTING, _RESUME, _config_with_ollama, _install
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_NEW_FIELDS = ("profile_ref", "posting_sha256", "model")


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


def _run(fx: ProfileFixtureGig, request: AssessRequest) -> AssessResponse:
    return run_quick_assessment(request, home_root=fx.home_root, target=fx.target, config=_config_with_ollama(fx.home_root))


def _index_digest(job: dict[str, str], board: str) -> str:
    """The ``content_sha256`` the company index stores for this board listing row (the ATS parser's own)."""

    row = _greenhouse_row(dict(job), job["title"], job["absolute_url"], job["content"], board)
    assert row.content_sha256 is not None
    return row.content_sha256


def test_a_new_record_carries_the_profile_identity_the_posting_digest_and_the_model(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_MATCH])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None

    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING)))

    assert response.profile_ref == ProfileRef(selected.profile_id, selected.revision, selected.content_digest)
    assert response.posting_sha256 == assessment_basis.posting_sha256("", _POSTING)
    assert response.model == "fixture"  # the scripted port's resolved_model
    payload = json.loads(Path(response.stored_path).read_text(encoding="utf-8"))
    assert payload["profile_ref"] == {"profile_id": selected.profile_id, "revision": selected.revision, "content_digest": selected.content_digest}
    assert payload["posting_sha256"] == response.posting_sha256 and payload["model"] == "fixture"
    assert AssessResponse.from_json(payload).to_json() == payload == response.to_json()
    # Ids and digests only: the pasted posting text is still never stored.
    assert "posting_text" not in payload and _POSTING not in json.dumps(payload)


def test_an_index_posting_keeps_its_text_and_the_index_content_digest(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_MATCH, _GOOD_MATCH])

    single = _run(fx, AssessRequest(job=AssessJobInput(job_url="https://boards.greenhouse.io/acme/jobs/101")))
    listed = _run(fx, AssessRequest(job=AssessJobInput(job_url="https://boards.greenhouse.io/shell/jobs/303")))

    # The board listings the index is built from (bindings._test_provider_handler's fixtures).
    acme = {"id": "101", "title": "Software Engineer", "absolute_url": "https://boards.greenhouse.io/acme/jobs/101", "content": "Build reliable Python services."}
    shell = {
        "id": "303", "title": "Platform Engineer", "absolute_url": "https://boards.greenhouse.io/shell/jobs/303",
        "content": "&lt;p&gt;Operate Python platform services on GCP.&lt;/p&gt;",
    }
    assert single.job.fetch_kind == "ats_single" and single.posting_sha256 == _index_digest(acme, "acme")
    assert listed.job.fetch_kind == "ats_board" and listed.posting_sha256 == _index_digest(shell, "shell")
    assert single.posting_text == "Build reliable Python services."
    assert listed.posting_text == "Operate Python platform services on GCP."
    stored = {item.job.job_identity: item for item in list_quick_assessments(fx.home_root, fx.target)}
    assert stored[listed.job.job_identity].posting_sha256 == listed.posting_sha256
    assert stored[listed.job.job_identity].posting_text == listed.posting_text


def test_a_pasted_resume_has_no_profile_ref_but_keeps_the_posting_digest_and_model(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_MATCH])

    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING), resume=AssessResumeInput(resume_text=_RESUME.decode("utf-8"))))

    assert response.resume.is_ephemeral and response.profile_ref is None
    assert response.posting_sha256 == assessment_basis.posting_sha256("", _POSTING) and response.model == "fixture"
    assert "profile_ref" not in json.loads(Path(response.stored_path).read_text(encoding="utf-8"))


def test_a_title_override_does_not_change_the_posting_digest(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_MATCH])

    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING, title="Staff AI Engineer", company="Acme")))

    assert response.job.title == "Staff AI Engineer"
    assert response.posting_sha256 == assessment_basis.posting_sha256("", _POSTING)


def test_a_model_name_that_is_not_an_id_is_not_stored(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _asked = _install(monkeypatch, [_GOOD_MATCH])

    def invoke(request):
        return InvocationResult(
            status="success", output_text=_GOOD_MATCH, resolved_model="a model\nwith words",
            raw_usage={}, normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
        )

    monkeypatch.setattr(binding.port, "invoke", invoke)
    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING)))
    assert response.model is None
    assert "model" not in json.loads(Path(response.stored_path).read_text(encoding="utf-8"))


def test_a_record_written_before_these_fields_reads_serves_and_reserializes_unchanged(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_MATCH])
    response = _run(fx, AssessRequest(job=AssessJobInput(job_url="https://boards.greenhouse.io/acme/jobs/101")))
    stored = Path(response.stored_path)
    payload = json.loads(stored.read_text(encoding="utf-8"))
    assert all(field in payload for field in _NEW_FIELDS)
    for shape, dropped in (
        ("0.1.10.6", _NEW_FIELDS),  # 0110-039's basis, none of PL2's
        ("0.1.9", (*_NEW_FIELDS, "prompt_version", "constraints_digest", "story_bank")),  # no basis at all
    ):
        old = {key: value for key, value in payload.items() if key not in dropped}
        old_bytes = json.dumps(old, indent=2, sort_keys=True).encode("utf-8")
        stored.write_bytes(old_bytes)

        (item,) = list_quick_assessments(fx.home_root, fx.target)

        assert (item.profile_ref, item.posting_sha256, item.model) == (None, None, None), shape
        assert item.result == response.result and item.posting_text == response.posting_text
        assert json.dumps(item.to_json(), indent=2, sort_keys=True).encode("utf-8") == old_bytes, shape
        served = assessment_basis.BasisCheck(home_root=fx.home_root, target=fx.target, resolved=fx.resolved).served(item)
        assert served["basis_stale"] in (True, False), shape


def test_the_contract_refuses_a_posting_digest_or_profile_ref_that_is_not_one(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_MATCH])
    payload = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING))).to_json()
    for key, bad in (("posting_sha256", "the posting text"), ("profile_ref", {"profile_id": "p", "revision": 0, "content_digest": "x"}), ("model", "")):
        with pytest.raises(FindJobsContractError):
            AssessResponse.from_json({**payload, key: bad})
