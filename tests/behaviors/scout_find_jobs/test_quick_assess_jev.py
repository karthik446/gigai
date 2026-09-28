"""uat-bug-015: a quick assessment also gets ONE Jev score when a Jev key exists.

Real gig (``build_gig_with_resume``), a scripted model binding (the C1 seam,
as ``test_quick_assess.py``), and a fake Jev: ``market_acquisition.
_jev_http_client`` -- the module attribute ``quick_assess._jev_rank`` looks
up at call time -- is replaced with an ``httpx.MockTransport`` that records
every request.  The key lives in the fixture home's secrets store (the
resolution find-jobs uses); ``JEV_API_KEY`` is removed from the environment
so no test can ever reach the live Jev.

Covers: scored + stored in the run rows' ``RankScore`` shape, the shared
cache (a re-assessment makes no second call), the cost cap, fail open (no
key / Jev error / cap -> no score, the assessment unaffected, the reason
recorded), and the two skip rules (a pasted/ephemeral resume is never sent
to Jev; a job that names neither a title nor a company).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gigai import secrets_store
from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.config import Endpoint, GigAIConfig
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs import bindings, market_acquisition
from gigai.scout.find_jobs.api import rank as rank_api
from gigai.scout.find_jobs.assess_contracts import (
    RANK_SKIP_REASONS,
    AssessJobInput,
    AssessRequest,
    AssessResponse,
    AssessResumeInput,
)
from gigai.scout.find_jobs.contracts import FindJobsContractError, Verdict
from gigai.scout.find_jobs.jev_client import JEV_API_KEY_ENV_VAR, JEV_DECIDE_URL
from gigai.scout.find_jobs.jev_contracts import RankScore
from gigai.scout.quick_assess import list_quick_assessments, run_quick_assessment
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"
_POSTING = (
    "Acme is hiring a Staff AI Engineer to own our Python inference services end to end. "
    "Requirements: 5+ years of Python in production; experience operating GCP workloads; "
    "clear written communication. Remote within the United States."
)
_JOB_URL = "https://boards.greenhouse.io/acme/jobs/101"
_MATCH = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": None,
    }
)


class _ScriptedPort:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success",
            output_text=_MATCH,
            resolved_model="fixture",
            raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30),
            cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self) -> None:
        self.port = _ScriptedPort()

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


class _FakeJev:
    """Records every Jev request; answers ``strong`` / level 8 unless told to fail."""

    def __init__(self, *, status: int = 200) -> None:
        self.status = status
        self.requests: list[dict] = []
        self.keys: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == JEV_DECIDE_URL
        self.requests.append(json.loads(request.content))
        self.keys.append(request.headers["Authorization"])
        if self.status != 200:
            return httpx.Response(self.status, json={"error": "upstream"}, request=request)
        return httpx.Response(
            200,
            json={
                "model": "jev-test",
                "answers": {
                    "fit": {"choice": "strong"},
                    "score": {"score": 8},
                    "top_reason": {"choice": "stack_match"},
                    "flag_domain": {"noul": 0.0},
                    "flag_seniority": {"noul": 0.0},
                    "flag_stack": {"noul": 0.0},
                    "flag_location": {"noul": 0.9},
                    "flag_sponsorship": {"noul": 0.0},
                },
                "usage": {"cost_usd": 0.0005},
            },
            request=request,
        )

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self.handler))


def _config_with_ollama(home: Path) -> GigAIConfig:
    return build_config(
        home_root=home,
        workpad_root=home.parent / "workpads",
        editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="ollama", adapter="ollama_local", base_url="http://127.0.0.1:11434"),
        ),
        model_targets=(
            ConfigModelTarget(name="offline-default", endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(
                name="ollama-default", endpoint="ollama", model="fixture-model", capabilities=("text",),
                max_output_tokens=512, model_digest="sha256:" + "c" * 64,
            ),
        ),
    )


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=_RESUME)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> _ScriptedBinding:
    """No ambient Jev key or cap, the HTTP fixture for job URLs, a scripted model."""

    monkeypatch.delenv(JEV_API_KEY_ENV_VAR, raising=False)
    monkeypatch.delenv("GIGAI_JEV_COST_CAP_USD", raising=False)
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_JEV", raising=False)
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    binding = _ScriptedBinding()

    def resolve(config, adapter_target, **_kwargs):
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return binding


@pytest.fixture
def jev(monkeypatch: pytest.MonkeyPatch) -> _FakeJev:
    fake = _FakeJev()
    monkeypatch.setattr(market_acquisition, "_jev_http_client", fake.client)
    return fake


def _add_key(fx: ProfileFixtureGig) -> None:
    secrets_store.set(JEV_API_KEY_ENV_VAR, "fixture-jev-key", home_root=fx.home_root)


def _run(fx: ProfileFixtureGig, request: AssessRequest) -> AssessResponse:
    return run_quick_assessment(
        request, home_root=fx.home_root, target=fx.target, config=_config_with_ollama(fx.home_root)
    )


def _url_job() -> AssessRequest:
    return AssessRequest(job=AssessJobInput(job_url=_JOB_URL))


def _stored(response: AssessResponse) -> dict:
    return json.loads(Path(response.stored_path).read_text(encoding="utf-8"))


# --- scored ---------------------------------------------------------------------------------


def test_url_posting_with_a_key_stores_one_jev_score_in_the_run_row_shape(fx: ProfileFixtureGig, jev: _FakeJev) -> None:
    _add_key(fx)

    response = _run(fx, _url_job())

    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert len(jev.requests) == 1 and jev.keys == ["Bearer fixture-jev-key"]
    score = response.rank_score
    assert isinstance(score, RankScore) and response.rank_skip_reason is None
    assert (score.fit, score.score, score.reasons, score.mismatch_flags) == ("strong", 89, ("stack_match",), ("location",))
    assert score.normalized_url == response.job.job_identity == _JOB_URL
    assert score.content_sha256 == response.job.text_sha256
    assert score.cached is False and score.cost_usd == "0.000500" and score.hidden_by_default is False

    # Jev saw the profile resume, the effective preferences and the posting's identity.
    state = jev.requests[0]["state"]
    assert state["resume"] == _RESUME.decode("utf-8")[:2000]
    assert state["posting"] == {"company": "Acme", "title": "Software Engineer", "location": "Denver, CO"}
    assert state["preferences"] == {
        "target_titles": list(response.preferences.titles or ()),
        "countries": ["US"],
        "visa_sponsorship_required": False,
    }

    # Stored and listed in the exact shape run results carry in ``rank_scores``.
    payload = _stored(response)
    assert payload == response.to_json()
    assert payload["rank_score"] == score.to_json() and "rank_skip_reason" not in payload
    assert set(payload["rank_score"]) == {
        "normalized_url", "content_sha256", "fit", "score", "reasons", "mismatch_flags",
        "hidden_by_default", "cost_usd", "cached",
    }
    listed = list_quick_assessments(fx.home_root, fx.target)
    assert [item.rank_score for item in listed] == [score]


def test_reassessment_reads_the_jev_cache_and_makes_no_second_call(fx: ProfileFixtureGig, jev: _FakeJev) -> None:
    _add_key(fx)

    first = _run(fx, _url_job())
    second = _run(fx, _url_job())

    assert len(jev.requests) == 1, "the second assessment must be a Jev cache hit"
    assert first.rank_score is not None and second.rank_score is not None
    assert first.rank_score.cached is False and second.rank_score.cached is True
    assert (second.rank_score.fit, second.rank_score.score) == (first.rank_score.fit, first.rank_score.score)
    assert second.stored_path == first.stored_path and _stored(second)["rank_score"]["cached"] is True
    # The cache is find-jobs' own directory, next to quick_assess/.
    cache_dir = Path(first.stored_path).parents[2] / "jev_cache"
    assert len(list(cache_dir.glob("*.json"))) == 1


def test_cache_hit_for_the_same_text_under_another_url_carries_this_jobs_identity(
    fx: ProfileFixtureGig, jev: _FakeJev
) -> None:
    """The Jev cache is keyed by content, not URL: a cached score written
    under another URL (the same posting text reachable at two URLs) is
    served with THIS job's identity."""

    _add_key(fx)
    first = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL)))
    assert first.rank_score is not None and len(jev.requests) == 1
    # Re-key the one cached score to another URL, as a second URL with identical text would have written it.
    cache_file = next((Path(first.stored_path).parents[2] / "jev_cache").glob("*.json"))
    cached = json.loads(cache_file.read_text(encoding="utf-8"))
    cached["normalized_url"] = "https://boards.greenhouse.io/acme/jobs/999"
    cache_file.write_text(json.dumps(cached), encoding="utf-8")

    second = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL)))

    assert len(jev.requests) == 1
    assert second.rank_score is not None and second.rank_score.cached is True
    assert second.rank_score.normalized_url == second.job.job_identity == _JOB_URL


def test_pasted_job_with_a_title_is_scored_under_its_text_identity(fx: ProfileFixtureGig, jev: _FakeJev) -> None:
    _add_key(fx)

    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING, title="Staff AI Engineer", company="Acme")))

    assert len(jev.requests) == 1
    assert response.rank_score is not None
    assert response.rank_score.normalized_url == response.job.job_identity
    assert response.job.job_identity.startswith("text:sha256:")
    # Jev never receives posting text, and pasted text is still never stored.
    assert _POSTING not in json.dumps(jev.requests[0])
    assert _POSTING not in json.dumps(_stored(response)) and "posting_text" not in _stored(response)


# --- fail open: the assessment is never affected -----------------------------------------------


def test_no_key_means_no_jev_call_and_an_unchanged_assessment(fx: ProfileFixtureGig, jev: _FakeJev) -> None:
    response = _run(fx, _url_job())

    assert jev.requests == []
    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert response.rank_score is None and response.rank_skip_reason == "no_key"
    payload = _stored(response)
    assert "rank_score" not in payload and payload["rank_skip_reason"] == "no_key"
    assert not (Path(response.stored_path).parents[2] / "jev_cache").exists()


def test_key_from_the_environment_is_used_like_find_jobs(fx: ProfileFixtureGig, jev: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(JEV_API_KEY_ENV_VAR, "env-jev-key")

    response = _run(fx, _url_job())

    assert jev.keys == ["Bearer env-jev-key"] and response.rank_score is not None


def test_jev_http_error_is_recorded_and_never_fails_the_assessment(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_key(fx)
    failing = _FakeJev(status=502)
    monkeypatch.setattr(market_acquisition, "_jev_http_client", failing.client)

    response = _run(fx, _url_job())

    assert len(failing.requests) == 1
    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert response.rank_score is None and response.rank_skip_reason == "error"
    assert Path(response.stored_path).is_file() and _stored(response)["rank_skip_reason"] == "error"
    # An error is never cached: the next assessment calls Jev again.
    working = _FakeJev()
    monkeypatch.setattr(market_acquisition, "_jev_http_client", working.client)
    again = _run(fx, _url_job())
    assert len(working.requests) == 1 and again.rank_score is not None and again.rank_skip_reason is None


def test_an_unexpected_jev_failure_is_recorded_as_error(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_key(fx)

    def boom() -> httpx.Client:
        raise RuntimeError("no transport")

    monkeypatch.setattr(market_acquisition, "_jev_http_client", boom)

    response = _run(fx, _url_job())

    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert response.rank_score is None and response.rank_skip_reason == "error"


def test_a_spent_cost_cap_means_no_call_and_says_so(fx: ProfileFixtureGig, jev: _FakeJev, monkeypatch: pytest.MonkeyPatch) -> None:
    _add_key(fx)
    monkeypatch.setenv("GIGAI_JEV_COST_CAP_USD", "0")

    response = _run(fx, _url_job())

    assert jev.requests == []
    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert response.rank_score is None and response.rank_skip_reason == "cost_cap"


# --- skip rules -------------------------------------------------------------------------------


def test_job_with_neither_title_nor_company_is_not_sent_to_jev(fx: ProfileFixtureGig, jev: _FakeJev) -> None:
    _add_key(fx)

    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING)))

    assert jev.requests == []
    assert (response.job.title, response.job.company) == ("", "")
    assert response.rank_score is None and response.rank_skip_reason == "no_title_or_company"


def test_ephemeral_resume_is_never_sent_to_jev(fx: ProfileFixtureGig, jev: _FakeJev) -> None:
    """Operator decision (2026-09-27): a pasted resume goes to the chosen
    assessment model and nowhere else -- never to Jev, even with a key."""

    _add_key(fx)
    secret = "Pasted resume: eleven years of Python."

    response = _run(fx, AssessRequest(job=AssessJobInput(job_url=_JOB_URL), resume=AssessResumeInput(resume_text=secret)))

    assert jev.requests == []
    assert response.resume.is_ephemeral
    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert response.rank_score is None and response.rank_skip_reason == "ephemeral_resume"
    assert secret not in Path(response.stored_path).read_text(encoding="utf-8")
    assert not (Path(response.stored_path).parents[2] / "jev_cache").exists()


# --- the production test seam ---------------------------------------------------------------------


def test_the_fake_jev_seam_intercepts_quick_assess(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """``GIGAI_SCOUT_FIND_JOBS_TEST_JEV=1`` (the api-e2e harness's fake Jev) reaches this path too."""

    # Registered with monkeypatch first so the seam's own setattr is undone at teardown.
    monkeypatch.setattr(market_acquisition, "_jev_http_client", market_acquisition._jev_http_client)
    monkeypatch.setattr(rank_api, "_jev_http_client", rank_api._jev_http_client)
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_JEV", "1")
    _add_key(fx)

    response = _run(fx, _url_job())

    assert getattr(market_acquisition._jev_http_client, "_scout_test_transport", False) is True
    assert bindings._test_jev_enabled()
    assert response.rank_score is not None
    assert (response.rank_score.fit, response.rank_score.score) == ("strong", 89)


# --- the contract -------------------------------------------------------------------------------


def test_response_contract_guards_the_new_fields(fx: ProfileFixtureGig, jev: _FakeJev) -> None:
    _add_key(fx)
    response = _run(fx, _url_job())
    payload: dict = response.to_json()

    assert set(RANK_SKIP_REASONS) == {"no_key", "ephemeral_resume", "no_title_or_company", "cost_cap", "error"}
    assert AssessResponse.from_json(payload) == AssessResponse.from_json(json.loads(json.dumps(payload)))
    assert AssessResponse.from_json(payload).to_json() == payload

    with pytest.raises(FindJobsContractError) as both:
        AssessResponse.from_json({**payload, "rank_skip_reason": "error"})
    assert both.value.code == "invalid_value"
    without_score = {key: value for key, value in payload.items() if key != "rank_score"}
    with pytest.raises(FindJobsContractError) as unknown:
        AssessResponse.from_json({**without_score, "rank_skip_reason": "because"})
    assert unknown.value.code == "bad_enum"
    with pytest.raises(FindJobsContractError) as pasted:
        AssessResponse.from_json(
            {
                **without_score,
                "job": {
                    **payload["job"],
                    "fetch_kind": "pasted",
                    "source_url": None,
                    "normalized_url": None,
                    "job_identity": "text:" + payload["job"]["text_sha256"],
                },
            }
        )
    assert pasted.value.code == "invalid_value"
