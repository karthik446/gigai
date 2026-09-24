"""End-to-end ``build_prep``/``load_prep``: idempotency, --refresh, privacy (no live calls)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from gigai import secrets_store
from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.config import Endpoint
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.find_jobs.discovery.openai_source import OPENAI_API_KEY_ENV_VAR
from gigai.scout.interview_prep import build_prep, load_prep
from gigai.setup import build_config, run_setup

from .test_interview_prep_fixtures import (
    NORMALIZED_URL,
    POSTING_URL,
    RESUME_TEXT,
    add_resume,
    bound_project,
    write_acquire_output,
    write_assessment,
)

_RealClient = httpx.Client  # captured before any monkeypatch below


class _ScriptedPort:
    def __init__(self, outputs: list[object]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success", output_text=self._outputs.pop(0), resolved_model="fixture",
            raw_usage={}, normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self, outputs: list[object]) -> None:
        self.port = _ScriptedPort(outputs)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def _write_find_jobs_config(target: Path) -> None:
    (target / "find-jobs.json").write_text(
        json.dumps({
            "schema_version": "find-jobs-config:1", "roles": ["staff backend"], "merged_queries": ["staff backend"],
            "location": None, "remote": True, "published_after": None,
            "sources": {"exa": False, "ats": True, "hiringcafe": False},
            "default_assess_cap": 10, "default_model_target": "ollama_local",
        }),
        encoding="utf-8",
    )


def _configure_ollama_target(home: Path, tmp_path: Path) -> None:
    run_setup(build_config(
        home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",),
        open_with_target=False,
        endpoints=(
            Endpoint(name="offline", adapter="deterministic"),
            Endpoint(name="ollama", adapter="ollama_local", base_url="http://127.0.0.1:11434"),
        ),
        model_targets=(
            ConfigModelTarget(name="offline-default", endpoint="offline", model="fixture-v1", capabilities=("text",), max_output_tokens=64),
            ConfigModelTarget(name="ollama-default", endpoint="ollama", model="fixture-model", capabilities=("text",), max_output_tokens=512, model_digest="sha256:" + "c" * 64),
        ),
    ))


def _good_categories() -> str:
    return json.dumps({"categories": [
        {"category": "system_design", "why": "The posting is about owning a distributed payments platform.", "grounded_in": ["payments platform"]},
    ]})


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, with_matrix: bool = False):
    home, target, gig_id = bound_project(tmp_path)
    add_resume(home, target, gig_id, tmp_path)
    posting = write_acquire_output(home, target, "run_1")
    if with_matrix:
        write_assessment(home, target, posting)
    _write_find_jobs_config(target)
    _configure_ollama_target(home, tmp_path)
    monkeypatch.setenv("GIGAI_HOME", str(home))
    return home, target, gig_id, posting


def _company_research_payload() -> dict:
    return {
        "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({
            "claims": [{"claim": "Acme uses Go across its backend.", "source_url": "https://acme.example/eng"}]
        })}]}],
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def test_build_prep_end_to_end_no_resume_in_search_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, gig_id, posting = _setup(tmp_path, monkeypatch)
    secrets_store.set(OPENAI_API_KEY_ENV_VAR, "sk-test-key")

    captured_search_bodies: list[bytes] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            captured_search_bodies.append(request.content)
            return httpx.Response(200, json=_company_research_payload())
        return httpx.Response(200)  # source verification HEAD/GET

    binding = _ScriptedBinding([_good_categories()])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, tgt, **_kwargs: binding)
    monkeypatch.setattr("gigai.scout.interview_prep.prep.httpx.Client", lambda *a, **k: _RealClient(*a, transport=httpx.MockTransport(handler), **{k2: v for k2, v in k.items() if k2 != "transport"}))

    prep = build_prep(home_root=home, target=target, posting_url=POSTING_URL, gig_id=gig_id)

    assert prep.posting_id == NORMALIZED_URL
    assert prep.company_research.skipped is None
    assert len(prep.company_research.claims) == 1
    assert len(prep.question_categories) == 1
    assert prep.model_target == "ollama-default"

    # Privacy: assert on the actual captured web-search request body.
    assert len(captured_search_bodies) == 1
    body_text = captured_search_bodies[0].decode("utf-8")
    assert RESUME_TEXT.decode("utf-8").strip() not in body_text
    assert "Jane Doe" not in body_text
    # But the resume DID reach the category-prediction model prompt.
    assert "Jane Doe" in binding.port.prompts[0] or "distributed payments systems in Go" in binding.port.prompts[0]


def test_idempotent_same_resume_revision_returns_cached_without_new_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, gig_id, posting = _setup(tmp_path, monkeypatch)
    secrets_store.set(OPENAI_API_KEY_ENV_VAR, "sk-test-key")

    call_count = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        call_count["n"] += 1
        if request.method == "POST":
            return httpx.Response(200, json=_company_research_payload())
        return httpx.Response(200)

    binding = _ScriptedBinding([_good_categories()])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, tgt, **_kwargs: binding)
    monkeypatch.setattr("gigai.scout.interview_prep.prep.httpx.Client", lambda *a, **k: _RealClient(*a, transport=httpx.MockTransport(handler), **{k2: v for k2, v in k.items() if k2 != "transport"}))

    first = build_prep(home_root=home, target=target, posting_url=POSTING_URL, gig_id=gig_id)
    calls_after_first = call_count["n"]
    assert calls_after_first > 0

    second = build_prep(home_root=home, target=target, posting_url=POSTING_URL, gig_id=gig_id)
    assert call_count["n"] == calls_after_first  # no new HTTP calls
    assert second.created_at == first.created_at
    assert second.to_json() == first.to_json()


def test_refresh_forces_a_new_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, gig_id, posting = _setup(tmp_path, monkeypatch)
    secrets_store.set(OPENAI_API_KEY_ENV_VAR, "sk-test-key")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(200, json=_company_research_payload())
        return httpx.Response(200)

    binding = _ScriptedBinding([_good_categories(), _good_categories()])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, tgt, **_kwargs: binding)
    monkeypatch.setattr("gigai.scout.interview_prep.prep.httpx.Client", lambda *a, **k: _RealClient(*a, transport=httpx.MockTransport(handler), **{k2: v for k2, v in k.items() if k2 != "transport"}))

    first = build_prep(home_root=home, target=target, posting_url=POSTING_URL, gig_id=gig_id)
    refreshed = build_prep(home_root=home, target=target, posting_url=POSTING_URL, gig_id=gig_id, refresh=True)
    assert refreshed.created_at == first.created_at  # created_at preserved
    assert refreshed.refreshed_at >= first.refreshed_at
    assert len(binding.port.prompts) == 2  # a real second model call happened


def test_missing_key_still_builds_partial_prep_with_categories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, gig_id, posting = _setup(tmp_path, monkeypatch)
    monkeypatch.delenv(OPENAI_API_KEY_ENV_VAR, raising=False)  # no key

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no HTTP call should be made without an OpenAI key")

    binding = _ScriptedBinding([_good_categories()])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, tgt, **_kwargs: binding)
    monkeypatch.setattr("gigai.scout.interview_prep.prep.httpx.Client", lambda *a, **k: _RealClient(*a, transport=httpx.MockTransport(handler), **{k2: v for k2, v in k.items() if k2 != "transport"}))

    prep = build_prep(home_root=home, target=target, posting_url=POSTING_URL, gig_id=gig_id)

    assert prep.company_research.skipped is not None
    assert "gigai secrets add openai" in prep.company_research.skipped
    assert len(prep.question_categories) == 1  # rest of the prep still builds


def test_prep_notes_reuse_assess_matrix_when_it_exists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home, target, gig_id, posting = _setup(tmp_path, monkeypatch, with_matrix=True)
    monkeypatch.delenv(OPENAI_API_KEY_ENV_VAR, raising=False)

    binding = _ScriptedBinding([_good_categories()])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, tgt, **_kwargs: binding)

    prep = build_prep(home_root=home, target=target, posting_url=POSTING_URL, gig_id=gig_id)

    assert prep.prep_notes.matrix_source == "assess"
    assert prep.prep_notes.resume_points  # the "met" row
    assert prep.prep_notes.gaps  # the "gap" row


def test_load_prep_returns_none_when_never_built(tmp_path: Path) -> None:
    home, target, gig_id = bound_project(tmp_path)
    assert load_prep(home_root=home, target=target, posting_url=POSTING_URL) is None
