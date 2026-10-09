"""P5: the standalone quick assessment (``gigai.scout.quick_assess``).

Real gig (``build_gig_with_resume``: journal-authoritative workpad, one
committed resume, a real ``find-jobs.json`` with ``countries=("US",)`` and
two roles -> the migrated default profile's titles); the model is a
scripted binding installed by patching ``proposal_execution.
resolve_model_adapter`` -- the exact module attribute plan constraint C1
names -- so every prompt the service renders is captured and asserted on.

Covers: store/list (path shape, latest-wins with ``created_at`` kept, the
profile/verdict filters), the no-leak rule (no resume text or job text in a
response or a stored file), the orchestrator's MUST ({{countries}} from
find-jobs.json + {{titles}} from the profile reach the prompt; request
preferences override both), the model-target fallback (C11), and the error
mapping (input, fetch, profile, model target, output-invalid, timeout vs
unavailable, seam deadline).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gigai.adapters.ollama_local import OllamaLocalAdapterError
from gigai.adapters.port import InvocationResult, ModelInvocationError, NormalizedUsage
from gigai.config import Endpoint, GigAIConfig, load_config
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.private_records import list_imports
from gigai.scout import quick_assess
from gigai.scout.assessment_core import INSTRUCTIONS_DIGEST
from gigai.scout.find_jobs.assess_contracts import (
    AssessJobInput,
    AssessPreferences,
    AssessRequest,
    AssessResponse,
    AssessResumeInput,
)
from gigai.scout.find_jobs.contracts import ModelTarget, Verdict
from gigai.scout.profile_records import create_profile, selected_profile
from gigai.scout.quick_assess import QuickAssessError, list_quick_assessments, run_quick_assessment
from gigai.setup import build_config

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_RESUME = b"# Fixture Resume\n\nStaff AI Engineer. Built Python services for six years. (fixture only.)\n"
_POSTING = (
    "Acme is hiring a Staff AI Engineer to own our Python inference services end to end. "
    "Requirements: 5+ years of Python in production; experience operating GCP workloads; "
    "clear written communication. Remote within the United States."
)

_GOOD_PENDING = json.dumps(
    {
        "verdict": "pending_user_answers",
        "matrix": [
            {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
            {"requirement": "GCP", "class": "askable", "status": "unclear", "resume_evidence": []},
        ],
        "suggestions": ["Lead with the inference services."],
        "questions": [{"question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "requirement": "GCP"}],
        "not_a_match_reason": None,
    }
)
_GOOD_MATCH = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": None,
    }
)


class _ScriptedPort:
    def __init__(self, outputs: list[object], *, sleep_seconds: float = 0.0) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []
        self.sleep_seconds = sleep_seconds

    def invoke(self, request):
        self.prompts.append(request.prompt)
        if self.sleep_seconds:
            time.sleep(self.sleep_seconds)
        item = self._outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return InvocationResult(
            status="success",
            output_text=item,
            resolved_model="fixture",
            raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30),
            cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self, outputs: list[object], **port_kwargs) -> None:
        self.port = _ScriptedPort(outputs, **port_kwargs)
        self.closed = False

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        self.closed = True


def _config_with_ollama(home: Path) -> GigAIConfig:
    """A config whose ONLY enabled ollama_local-adapter target is ``ollama-default``
    (the shape ``gigai setup`` produces), so the C11 resolution has something real to map onto."""

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
def _no_ambient_jev_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """uat-bug-015: quick assess scores with Jev when a key exists; an
    exported ``JEV_API_KEY`` must never turn these tests into live Jev calls
    (the Jev path has its own file, ``test_quick_assess_jev.py``)."""

    monkeypatch.delenv("JEV_API_KEY", raising=False)


def _install(monkeypatch: pytest.MonkeyPatch, outputs: list[object], **port_kwargs) -> tuple[_ScriptedBinding, list[str]]:
    """Patch the C1 seam; return the binding and the adapter-target names it was asked for."""

    binding = _ScriptedBinding(outputs, **port_kwargs)
    asked: list[str] = []

    def resolve(config, adapter_target, **_kwargs):
        asked.append(adapter_target)
        return binding

    # bindings._patch_test_model_transport leaves a resolver alone when it
    # carries this flag (it is only ever consulted when the model seam is on).
    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return binding, asked


def _run(fx: ProfileFixtureGig, request: AssessRequest, *, config: GigAIConfig | None = None) -> AssessResponse:
    return run_quick_assessment(
        request, home_root=fx.home_root, target=fx.target, config=config or _config_with_ollama(fx.home_root)
    )


def _pasted(**overrides) -> AssessRequest:
    return AssessRequest(job=AssessJobInput(job_text=_POSTING), **overrides)


def _constraints(prompt: str) -> str:
    """The rendered CANDIDATE CONSTRAINTS paragraph (one line in the template).  Asserted on by VALUE, never
    by the surrounding wording: P2-r2 is tuning ``assess.md`` in parallel."""

    lines = [line for line in prompt.splitlines() if line.startswith("CANDIDATE CONSTRAINTS")]
    assert len(lines) == 1, prompt
    return lines[0]


# --- store / list -----------------------------------------------------------------------


def test_pasted_job_against_selected_profile_returns_verdict_and_stores_json(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _asked = _install(monkeypatch, [_GOOD_PENDING])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None

    response = _run(fx, _pasted())

    assert response.result.verdict is Verdict.PENDING_USER_ANSWERS
    assert response.result.structured_questions[0].question_id == "cloud:gcp"
    assert response.resume.profile_id == selected.profile_id
    assert response.job.fetch_kind == "pasted"
    assert response.producer.callable == "scout.assess" and response.producer.actor == "scout-assess"
    assert response.producer.model_target is ModelTarget.OLLAMA_LOCAL
    assert response.usage is not None and response.usage.input_tokens == 10 and response.usage.output_tokens == 20
    assert response.instructions_digest == INSTRUCTIONS_DIGEST
    assert binding.closed is True

    stored = Path(response.stored_path)
    assert stored.is_file()
    assert stored.parent.name == "job"  # 0.1.11.9: the job's own folder; the role that asked is recorded inside
    assert response.resume.profile_id == selected.profile_id
    assert stored.parent.parent.name == "quick_assess"
    assert stored.parents[3] == fx.home_root / "scout"
    payload = json.loads(stored.read_text(encoding="utf-8"))
    assert payload == response.to_json()
    assert AssessResponse.from_json(payload).result == response.result
    # (h)/no-leak: neither the response nor the file carries resume text or the job text.
    flat = json.dumps(payload)
    assert "text" not in payload["job"] and payload["job"]["text_sha256"] == response.job.text_sha256
    assert _RESUME.decode("utf-8").splitlines()[2] not in flat
    assert _POSTING not in flat

    listed = list_quick_assessments(fx.home_root, fx.target)
    assert [item.stored_path for item in listed] == [response.stored_path]


def test_rerun_overwrites_the_same_file_and_keeps_created_at(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_PENDING, _GOOD_MATCH])

    first = _run(fx, _pasted())
    second = _run(fx, _pasted())

    assert second.stored_path == first.stored_path
    assert second.created_at == first.created_at
    assert second.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    listed = list_quick_assessments(fx.home_root, fx.target)
    assert len(listed) == 1 and listed[0].result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD


def test_list_filters_by_profile_id_and_verdict_newest_first(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_PENDING, _GOOD_MATCH, _GOOD_MATCH])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None

    a = _run(fx, _pasted())
    b = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING + " Second posting.")))
    c = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING), resume=AssessResumeInput(resume_text="Pasted resume.")))

    everything = list_quick_assessments(fx.home_root, fx.target)
    assert {item.stored_path for item in everything} == {a.stored_path, b.stored_path, c.stored_path}
    assert [item.created_at for item in everything] == sorted((item.created_at for item in everything), reverse=True)

    # 0.1.11.9: a role's id leaves the pasted-resume ones out and narrows no further (a job's assessment is not one role's).
    by_profile = list_quick_assessments(fx.home_root, fx.target, profile_id=selected.profile_id)
    assert {item.stored_path for item in by_profile} == {a.stored_path, b.stored_path}
    ephemeral = list_quick_assessments(fx.home_root, fx.target, profile_id=quick_assess.EPHEMERAL_RESUME_KEY)
    assert [item.stored_path for item in ephemeral] == [c.stored_path]
    matched = list_quick_assessments(fx.home_root, fx.target, verdict="matched_above_threshold")
    assert {item.stored_path for item in matched} == {b.stored_path, c.stored_path}
    assert {item.stored_path for item in list_quick_assessments(fx.home_root, fx.target, profile_id="profile_nobody")} == {a.stored_path, b.stored_path}


def test_unparsable_stored_file_is_skipped_not_raised(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_MATCH])
    response = _run(fx, _pasted())
    (Path(response.stored_path).parent / "junk.json").write_text("{not json", encoding="utf-8")
    assert [item.stored_path for item in list_quick_assessments(fx.home_root, fx.target)] == [response.stored_path]


# --- ephemeral resume ---------------------------------------------------------------------


def test_ephemeral_resume_is_never_imported_and_never_stored(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _ = _install(monkeypatch, [_GOOD_MATCH])
    before = list_imports(home_root=fx.home_root, requested_target=fx.target, family="reference", gig_id=fx.resolved.gig_id)
    secret = "Pasted resume: eleven years of Python at Globex."

    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING), resume=AssessResumeInput(resume_text=secret)))

    after = list_imports(home_root=fx.home_root, requested_target=fx.target, family="reference", gig_id=fx.resolved.gig_id)
    assert after == before
    assert response.resume.is_ephemeral and response.resume.profile_id is None
    assert Path(response.stored_path).parent.name == "ephemeral"
    assert secret in binding.port.prompts[0]  # the model saw it ...
    assert secret not in Path(response.stored_path).read_text(encoding="utf-8")  # ... the disk never does
    assert secret not in json.dumps(response.to_json())
    # An ephemeral resume has no profile: titles fall back to none.
    assert response.preferences.titles == ()
    assert _constraints(binding.port.prompts[0]).endswith("= unspecified.")


# --- the orchestrator's MUST: countries + titles reach the prompt -------------------------------


def test_prompt_carries_countries_from_find_jobs_json_and_no_role_titles(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _ = _install(monkeypatch, [_GOOD_MATCH])

    response = _run(fx, _pasted())

    prompt = binding.port.prompts[0]
    constraints = _constraints(prompt)
    # find-jobs.json (the fixture's shared config) says countries=("US",).
    # 0.1.11.9: no role's titles go into a job's one assessment (the default role's are the config's two roles).
    assert re.search(r"\bUS\b", constraints), constraints
    assert "target titles the candidate is looking for = unspecified." in constraints
    assert "staff ai engineer" not in prompt
    assert "visa sponsorship required = no" in constraints
    assert response.preferences == AssessPreferences(visa_sponsorship_required=False, titles=(), countries=("US",))
    assert _POSTING in prompt and "six years" in prompt


def test_prompt_carries_the_candidate_location_from_find_jobs_json(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """assess-prompt-v2: the shared config's ``location`` ("Remote" in this
    fixture) is the candidate's own location on the constraints line; it is
    NOT echoed into ``preferences`` unless the request carried one, so the
    stored/served preferences object is unchanged for every existing caller."""
    binding, _ = _install(monkeypatch, [_GOOD_MATCH])

    response = _run(fx, _pasted())

    constraints = _constraints(binding.port.prompts[0])
    assert "the candidate's own location (city, state/province, country as they wrote it; " in constraints
    assert "applied by rule 4): Remote; target titles" in constraints
    assert response.preferences.location is None
    assert "location" not in response.preferences.to_json()


def test_request_preferences_location_overrides_the_config_location(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _ = _install(monkeypatch, [_GOOD_MATCH])

    response = _run(fx, _pasted(preferences=AssessPreferences(location="Toronto, ON, Canada")))

    constraints = _constraints(binding.port.prompts[0])
    assert "applied by rule 4): Toronto, ON, Canada; target titles" in constraints
    assert "): Remote;" not in constraints
    # The other preferences still resolve from the config; the
    # override is echoed and round-trips through the wire shape.
    assert response.preferences == AssessPreferences(
        visa_sponsorship_required=False,
        titles=(),
        countries=("US",),
        location="Toronto, ON, Canada",
    )
    assert AssessPreferences.from_json(response.preferences.to_json()) == response.preferences


def test_request_preferences_override_countries_titles_and_visa(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _ = _install(monkeypatch, [_GOOD_MATCH])

    response = _run(
        fx,
        _pasted(preferences=AssessPreferences(visa_sponsorship_required=True, titles=("platform lead",), countries=("CA", "GB"))),
    )

    constraints = _constraints(binding.port.prompts[0])
    assert "CA, GB" in constraints
    assert "platform lead" in constraints
    assert "visa sponsorship required = yes" in constraints
    assert "staff ai engineer" not in constraints and not re.search(r"\bUS\b", constraints)
    assert response.preferences == AssessPreferences(visa_sponsorship_required=True, titles=("platform lead",), countries=("CA", "GB"))


def test_a_role_with_its_own_search_settings_is_assessed_with_the_shared_candidate_facts(
    fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """0.1.11.9: ONE set of candidate facts. The shared config says "Remote" / US; this role's own search settings say
    Houston / hybrid / CA, and they are search filters only (until 0.1.11.9 it was assessed for them, 0110-022)."""
    from gigai.scout.profile_records import ProfileSearchSettings

    binding, _ = _install(monkeypatch, [_GOOD_MATCH, _GOOD_MATCH])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None
    created = create_profile(
        fx.resolved, label="Director", titles=("director of ai",), titles_to_avoid=(), queries=("director of ai",),
        resume_ref=selected.resume_ref,
        search_settings=ProfileSearchSettings(location="Houston, TX", work_mode="hybrid", countries=("CA",), max_age_days=None),
    )

    response = _run(fx, _pasted(resume=AssessResumeInput(profile_id=created.profile_id)))

    constraints = _constraints(binding.port.prompts[0])
    assert "applied by rule 4): Remote; target titles" in constraints and "Houston" not in binding.port.prompts[0]
    assert re.search(r"\bUS\b", constraints) and not re.search(r"\bCA\b", constraints)
    assert response.preferences.countries == ("US",) and response.preferences.location is None

    # ... and so the same job assessed under the default role gets the same constraints, and the same basis.
    again = _run(fx, _pasted(resume=AssessResumeInput(profile_id=selected.profile_id)))
    assert _constraints(binding.port.prompts[1]) == constraints
    assert (again.constraints_digest, again.prompt_version) == (response.constraints_digest, response.prompt_version)


def test_an_explicit_role_is_recorded_and_its_titles_are_not_in_the_prompt(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _ = _install(monkeypatch, [_GOOD_MATCH])
    selected = selected_profile(fx.resolved, home_root=fx.home_root, target=fx.target)
    assert selected is not None
    created = create_profile(
        fx.resolved, label="Backend", titles=("staff backend engineer",), titles_to_avoid=(), queries=("staff backend engineer",),
        resume_ref=selected.resume_ref,
    )

    response = _run(fx, _pasted(resume=AssessResumeInput(profile_id=created.profile_id)))

    assert response.resume.profile_id == created.profile_id
    assert "staff backend engineer" not in binding.port.prompts[0]
    assert Path(response.stored_path).parent.name == "job"


# --- job overrides + fetch errors -----------------------------------------------------------


def test_title_and_company_overrides_apply_to_pasted_text(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _ = _install(monkeypatch, [_GOOD_MATCH])
    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_POSTING, title="Staff AI Engineer", company="Acme")))
    assert (response.job.title, response.job.company) == ("Staff AI Engineer", "Acme")
    assert "ROLE: Staff AI Engineer" in binding.port.prompts[0] and "COMPANY: Acme" in binding.port.prompts[0]


def test_job_url_uses_the_fixture_transport_and_fetch_failure_is_typed(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_MATCH])

    response = _run(fx, AssessRequest(job=AssessJobInput(job_url="https://careers.example.test/jobs/9")))
    assert response.job.fetch_kind == "generic"
    assert response.job.normalized_url == "https://careers.example.test/jobs/9"
    assert response.job.job_identity == response.job.normalized_url

    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, AssessRequest(job=AssessJobInput(job_url="https://nowhere.example.test/jobs/1")))
    assert excinfo.value.code == "job_fetch_failed"


def test_fetched_public_posting_text_is_stored_with_the_result(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """uat-bug-014: the text fetched from a public posting URL is stored and
    listed (the job page's description); ``job.text`` is still never serialized."""

    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_MATCH])

    response = _run(fx, AssessRequest(job=AssessJobInput(job_url="https://boards.greenhouse.io/acme/jobs/101")))

    assert response.job.fetch_kind == "ats_single"
    assert response.posting_text == response.job.text == "Build reliable Python services."
    payload = json.loads(Path(response.stored_path).read_text(encoding="utf-8"))
    assert payload == response.to_json()
    assert payload["posting_text"] == "Build reliable Python services."
    assert "text" not in payload["job"] and payload["job"]["text_sha256"] == response.job.text_sha256
    # The resume is still never stored.
    assert _RESUME.decode("utf-8").splitlines()[2] not in json.dumps(payload)

    listed = list_quick_assessments(fx.home_root, fx.target)
    assert [item.posting_text for item in listed] == ["Build reliable Python services."]
    assert listed[0].to_json() == payload
    found = quick_assess.find_quick_assessment_by_job_identity(fx.home_root, fx.target, response.job.job_identity)
    assert found is not None and found.posting_text == "Build reliable Python services."


def test_pasted_posting_text_is_still_never_stored(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_MATCH])

    response = _run(fx, _pasted())

    assert response.posting_text is None
    payload = json.loads(Path(response.stored_path).read_text(encoding="utf-8"))
    assert "posting_text" not in payload and "posting_text" not in response.to_json()
    assert _POSTING not in json.dumps(payload)


def test_stored_result_written_before_the_text_and_jev_fields_still_loads(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """A quick-assess file from before uat-bug-014/015 has none of
    ``posting_text``/``rank_score``/``rank_skip_reason``: it lists, reads back
    with all three ``None``, and re-serializes byte-identically."""

    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_HTTP", "1")
    _install(monkeypatch, [_GOOD_MATCH])
    response = _run(fx, AssessRequest(job=AssessJobInput(job_url="https://boards.greenhouse.io/acme/jobs/101")))
    stored = Path(response.stored_path)
    old = {
        key: value
        for key, value in json.loads(stored.read_text(encoding="utf-8")).items()
        if key not in {"posting_text", "rank_score", "rank_skip_reason"}
    }
    old_bytes = json.dumps(old, indent=2, sort_keys=True).encode("utf-8")
    stored.write_bytes(old_bytes)

    listed = list_quick_assessments(fx.home_root, fx.target)

    assert len(listed) == 1
    assert (listed[0].posting_text, listed[0].rank_score, listed[0].rank_skip_reason) == (None, None, None)
    assert listed[0].result == response.result and listed[0].job.job_identity == response.job.job_identity
    assert json.dumps(listed[0].to_json(), indent=2, sort_keys=True).encode("utf-8") == old_bytes


def test_empty_pasted_job_text_is_job_text_unavailable(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(monkeypatch, [_GOOD_MATCH])
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, AssessRequest(job=AssessJobInput(job_text="   \n")))
    assert excinfo.value.code == "job_text_unavailable"


# --- profile / model-target errors -----------------------------------------------------------


def test_unknown_profile_is_profile_not_found_before_any_model_call(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, asked = _install(monkeypatch, [_GOOD_MATCH])
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted(resume=AssessResumeInput(profile_id="profile_00000000-0000-4000-8000-00000000dead")))
    assert excinfo.value.code == "profile_not_found"
    assert asked == [] and binding.port.prompts == []


def test_model_target_defaults_to_find_jobs_json_and_maps_to_the_configured_target(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _, asked = _install(monkeypatch, [_GOOD_MATCH])
    response = _run(fx, _pasted())  # find-jobs.json: default_model_target=ollama_local (the contract default)
    assert asked == ["ollama-default"]  # C11: adapter kind -> the operator's configured target NAME
    assert response.producer.model_target is ModelTarget.OLLAMA_LOCAL
    assert response.producer.adapter == "ollama_local"  # scripted port has no name: falls back to the kind


def test_model_target_unavailable_when_no_configured_target_uses_that_adapter(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _, asked = _install(monkeypatch, [_GOOD_MATCH])
    # The fixture home's own config (build_config defaults) has only the
    # deterministic "offline" endpoint: nothing maps onto ollama_local.
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted(), config=load_config(fx.home_root))
    assert excinfo.value.code == "model_target_unavailable"
    assert asked == []


def test_explicit_model_target_overrides_the_config_default(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    _, asked = _install(monkeypatch, [_GOOD_MATCH])
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted(model_target=ModelTarget.CODEX_CLI))
    # Honoured (not silently swapped for the config default): no codex_cli
    # target is configured, so it fails loudly for THAT kind.
    assert excinfo.value.code == "model_target_unavailable"
    assert "codex_cli" in str(excinfo.value)
    assert asked == []


# --- model-boundary errors --------------------------------------------------------------------


def test_invalid_model_output_retries_once_then_fails_typed_and_stores_nothing(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding, _ = _install(monkeypatch, ["not json at all", "still not json"])
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted())
    assert excinfo.value.code == "model_output_invalid"
    assert len(binding.port.prompts) == 2
    assert "A previous attempt at this same prompt was rejected by the validator: " in binding.port.prompts[1]
    assert list_quick_assessments(fx.home_root, fx.target) == ()


def test_adapter_timeout_is_assess_timeout_but_other_failures_are_model_unavailable(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    timed_out = OllamaLocalAdapterError("local Ollama request timed out")
    timed_out.__cause__ = httpx.ReadTimeout("read timed out")
    _install(monkeypatch, [timed_out])
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted())
    assert excinfo.value.code == "assess_timeout"

    cli_timeout = ModelInvocationError("CLI model invocation timed out")
    import subprocess

    cli_timeout.__cause__ = subprocess.TimeoutExpired(cmd="codex", timeout=120)
    _install(monkeypatch, [cli_timeout])
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted())
    assert excinfo.value.code == "assess_timeout"

    _install(monkeypatch, [ModelInvocationError("local Ollama transport request failed")])
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted())
    assert excinfo.value.code == "model_unavailable"


def test_policy_refusal_is_model_denied(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    denied = ModelInvocationError("network denied by policy")
    denied.code = "network_denied"  # type: ignore[attr-defined]
    _install(monkeypatch, [denied])
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted())
    assert excinfo.value.code == "model_denied"


def test_seam_deadline_is_read_only_when_the_model_seam_is_on(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIGAI_SCOUT_ASSESS_TIMEOUT_SECONDS", "0.2")

    # Seam OFF: the env var is ignored; a slow fake still completes.
    monkeypatch.delenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", raising=False)
    _install(monkeypatch, [_GOOD_MATCH], sleep_seconds=0.5)
    assert _run(fx, _pasted()).result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD

    # Seam ON: the deadline applies and the failure is the typed timeout.
    monkeypatch.setenv("GIGAI_SCOUT_FIND_JOBS_TEST_MODEL", "1")
    _install(monkeypatch, [_GOOD_MATCH], sleep_seconds=0.5)
    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, _pasted())
    assert excinfo.value.code == "assess_timeout"


# --- uat-bug-029: junk posting text is never Matched ---------------------------------------

_NEXHEALTH_JUNK = (Path(__file__).resolve().parents[2] / "fixtures" / "scout" / "nexhealth_junk_posting.txt").read_text(
    encoding="utf-8"
)
_NO_STATED = json.dumps(
    {
        "verdict": "matched_above_threshold",
        "matrix": [{"requirement": "No stated requirements", "class": "nice_to_have", "status": "met", "resume_evidence": []}],
        "suggestions": [],
        "questions": [],
        "not_a_match_reason": None,
    }
)


def test_nexhealth_junk_text_pasted_is_held_with_a_note_and_never_matched(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """The scraped NexHealth text PASTED, the model answering as in the UAT ("No stated requirements", MATCHED):
    BLOCK1: it is stored HELD (pending, no resume suggested) with a visible note, never Matched."""

    _install(monkeypatch, [_NO_STATED, _NO_STATED])  # the refused answer gets one more call first

    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=_NEXHEALTH_JUNK)))

    assert response.result.verdict is Verdict.PENDING_USER_ANSWERS
    assert response.requirements_note == "No requirements could be read from this text. Check that the whole posting was pasted."
    assert response.result.pick is None


def _fetched_shell(fetch_kind: str = "generic"):
    from gigai.canonical import digest_imported_bytes
    from gigai.scout.find_jobs.assess_contracts import ResolvedJob

    url = "https://careers.example.test/job/?gh_jid=4400123"
    return ResolvedJob(
        job_identity=url, source_url=url, normalized_url=url, fetch_kind=fetch_kind, title="Careers", company="", location="",
        text=_NEXHEALTH_JUNK, text_sha256=digest_imported_bytes(_NEXHEALTH_JUNK.encode("utf-8")),
    )


def test_a_url_that_fetched_as_a_site_shell_is_refused_before_any_model_call(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """BLOCK1: a generic fetch with no posting body costs no model call and stores nothing."""

    binding, asked = _install(monkeypatch, [_NO_STATED])
    job = _fetched_shell()

    with pytest.raises(QuickAssessError) as excinfo:
        run_quick_assessment(
            AssessRequest(job=AssessJobInput(job_url=job.source_url)), home_root=fx.home_root, target=fx.target,
            config=_config_with_ollama(fx.home_root), resolved_job=job,
        )

    assert excinfo.value.code == "posting_requirements_unreadable"
    assert excinfo.value.reason == "no_posting_body"
    assert "open the posting and paste its text" in str(excinfo.value)
    assert binding.port.prompts == [] and asked == []  # zero model calls
    assert list_quick_assessments(fx.home_root, fx.target) == ()


def test_a_shell_answered_with_no_stated_requirements_is_unreadable_by_the_gate_never_matched() -> None:
    """The gate alone (the model answered): menu-and-footer text + only "No stated requirements" -> unreadable; a short real posting is not."""

    from gigai.scout.find_jobs.assess_contracts import AssessmentBody

    body = AssessmentBody.from_json(json.loads(_NO_STATED) | {"suggestions": [], "questions": []})
    assert quick_assess.posting_requirements_unreadable(_NEXHEALTH_JUNK, body, "generic") is True
    assert quick_assess.posting_requirements_unreadable(_NEXHEALTH_JUNK, body, "pasted") is True
    short_real = "You will help our team ship. There are no formal qualifications."
    assert quick_assess.posting_requirements_unreadable(short_real, body, "generic") is False
    assert quick_assess.posting_requirements_unreadable("Short. Menu", body, "ats_single") is False  # the board's own text
    empty = AssessmentBody.from_json({"matrix": [], "suggestions": [], "questions": [], "verdict": "matched_above_threshold"})
    assert quick_assess.posting_requirements_unreadable(short_real, empty, "generic") is True  # no requirement row at all


def test_requirement_free_wording_with_real_requirements_or_cues_stays_assessed(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    # Real posting with requirements: unchanged.
    _install(monkeypatch, [_GOOD_MATCH])
    assert _run(fx, _pasted()).result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    # A real posting that has requirement wording but states none of substance: the legitimate path.
    _install(monkeypatch, [_NO_STATED])
    response = _run(fx, AssessRequest(job=AssessJobInput(job_text="You will help our team ship. There are no formal qualifications.")))
    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD


def test_short_prose_blurb_without_cue_words_is_assessed_not_refused(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard's false positive: a real requirement-free blurb with none of the cue words is assessed."""

    _install(monkeypatch, [_NO_STATED])
    blurb = "Our small team builds scheduling software for dental clinics across the country and we are hiring an engineer to join the crew in Austin."

    response = _run(fx, AssessRequest(job=AssessJobInput(job_text=blurb)))

    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD


def test_matched_on_a_thin_matrix_of_a_long_posting_is_not_assessed_and_stores_nothing(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """uat-bug-046: the Lever posting stored without its requirements got Matched on one 'remote' row."""

    thin = json.dumps(
        {
            "verdict": "matched_above_threshold",
            "matrix": [{"requirement": "May work remotely anywhere in the US", "class": "hard", "status": "met", "resume_evidence": ["Denver"]}],
            "suggestions": [],
            "questions": [],
            "not_a_match_reason": None,
        }
    )
    _install(monkeypatch, [thin, thin])
    long_text = "We build clinical software and value careful engineering. " * 30

    with pytest.raises(QuickAssessError) as excinfo:
        _run(fx, AssessRequest(job=AssessJobInput(job_text=long_text)))

    assert excinfo.value.code == "posting_requirements_unreadable"
    assert str(excinfo.value) == "Posting text looks incomplete: open the posting"
    assert list_quick_assessments(fx.home_root, fx.target) == ()


def test_a_refused_answer_gets_one_more_call_and_a_good_second_answer_is_stored(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """0.1.11 (orchestrator #44): too few requirement rows for a posting that states many is retried once before the user sees an error."""

    thin = json.dumps(
        {
            "verdict": "matched_above_threshold",
            "matrix": [{"requirement": "May work remotely anywhere in the US", "class": "hard", "status": "met", "resume_evidence": ["Denver"]}],
            "suggestions": [], "questions": [], "not_a_match_reason": None,
        }
    )
    full = json.dumps(
        {
            "verdict": "matched_above_threshold",
            "matrix": [{"requirement": f"Requirement {n}", "class": "askable", "status": "met", "resume_evidence": ["six years"]} for n in range(8)],
            "suggestions": [], "questions": [], "not_a_match_reason": None,
        }
    )
    binding, _closed = _install(monkeypatch, [thin, full])
    before = len(quick_assess.GUARD_RETRIES)
    response = _run(fx, AssessRequest(job=AssessJobInput(job_text="We build clinical software and value careful engineering. " * 30)))
    assert response.result.verdict is Verdict.MATCHED_ABOVE_THRESHOLD
    assert len(binding.port.prompts) == 2 and len(quick_assess.GUARD_RETRIES) == before + 1
    assert len(list_quick_assessments(fx.home_root, fx.target)) == 1
