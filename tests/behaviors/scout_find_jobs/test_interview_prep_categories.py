"""Question-category prediction: grounded, no-silent-fallback (a stand-in model, no live calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.adapters.port import InvocationResult, ModelInvocationError, NormalizedUsage
from gigai.config import Endpoint, load_config
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.interview_prep.categories import CategoryPredictionError, predict_categories
from gigai.setup import build_config, run_setup


class _ScriptedPort:
    def __init__(self, outputs: list[object]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []
        self.closed = False

    def invoke(self, request):
        self.prompts.append(request.prompt)
        item = self._outputs.pop(0)
        if isinstance(item, BaseException):
            raise item
        return InvocationResult(
            status="success", output_text=item, resolved_model="fixture",
            raw_usage={}, normalized_usage=NormalizedUsage(1, 1, 2), cost_status="unavailable",
        )


class _ScriptedBinding:
    def __init__(self, outputs: list[object]) -> None:
        self.port = _ScriptedPort(outputs)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        from types import SimpleNamespace
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        self.port.closed = True


def _config_with_ollama_target(home: Path, tmp_path: Path):
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
    return load_config(home)


def _good_output(categories: list[dict]) -> str:
    return json.dumps({"categories": categories})


def test_grounded_categories_cite_posting_resume_and_company(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = _config_with_ollama_target(home, tmp_path)
    good = _good_output([
        {"category": "system_design", "why": "The posting emphasizes distributed payments architecture.", "grounded_in": ["own the payments platform"]},
        {"category": "coding_in_their_stack", "why": "The role requires Go and the resume shows Go experience.", "grounded_in": ["proficiency in Kubernetes and Go", "9 years building distributed payments systems in Go"]},
    ])
    binding = _ScriptedBinding([good])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, target: binding)

    categories, resolved_target = predict_categories(
        config=config, model_target="ollama_local", title="Staff Backend Engineer", company="Acme Corp",
        posting_text="You will own the payments platform. Must have proficiency in Kubernetes and Go.",
        resume_text="9 years building distributed payments systems in Go.",
        company_claims=("Acme uses Go across its backend services.",),
    )
    assert resolved_target == "ollama-default"
    assert len(categories) == 2
    assert categories[0].category == "system_design"
    assert "payments platform" in binding.port.prompts[0]
    assert "9 years building distributed payments systems in Go" in binding.port.prompts[0]
    assert "Acme uses Go across its backend services." in binding.port.prompts[0]
    for prediction in categories:
        assert prediction.grounded_in  # every category cites at least one source
    assert binding.port.closed


def test_unconfigured_model_target_fails_loudly_not_silently(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    # Default build_config has only a "deterministic" endpoint, no "ollama_local" adapter.
    run_setup(build_config(home_root=home, workpad_root=tmp_path / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False))
    config = load_config(home)
    with pytest.raises(CategoryPredictionError) as excinfo:
        predict_categories(
            config=config, model_target="ollama_local", title="t", company="c",
            posting_text="text", resume_text="resume", company_claims=(),
        )
    assert excinfo.value.code == "category_model_unavailable"


def test_model_denied_raises_not_returns_empty(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = _config_with_ollama_target(home, tmp_path)
    denied = ModelInvocationError("model denial")
    denied.code = "model_denied"
    binding = _ScriptedBinding([denied])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, target: binding)
    with pytest.raises(CategoryPredictionError) as excinfo:
        predict_categories(
            config=config, model_target="ollama_local", title="t", company="c",
            posting_text="text", resume_text="resume", company_claims=(),
        )
    assert excinfo.value.code == "model_denied"


def test_invalid_category_in_output_is_dropped_not_fabricated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = _config_with_ollama_target(home, tmp_path)
    good = _good_output([
        {"category": "not_a_real_category", "why": "invalid", "grounded_in": []},
        {"category": "domain", "why": "The posting is about payments domain knowledge.", "grounded_in": ["payments platform"]},
    ])
    binding = _ScriptedBinding([good])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, target: binding)
    categories, _ = predict_categories(
        config=config, model_target="ollama_local", title="t", company="c",
        posting_text="payments platform", resume_text="resume", company_claims=(),
    )
    assert len(categories) == 1
    assert categories[0].category == "domain"


def test_all_invalid_categories_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = _config_with_ollama_target(home, tmp_path)
    good = _good_output([{"category": "nonsense", "why": "x", "grounded_in": []}])
    binding = _ScriptedBinding([good])
    monkeypatch.setattr("gigai.scout.interview_prep.categories.resolve_model_adapter", lambda cfg, target: binding)
    with pytest.raises(CategoryPredictionError) as excinfo:
        predict_categories(
            config=config, model_target="ollama_local", title="t", company="c",
            posting_text="text", resume_text="resume", company_claims=(),
        )
    assert excinfo.value.code == "category_prediction_invalid"
