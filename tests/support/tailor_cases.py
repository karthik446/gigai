"""0110-10-05 C: the synthetic tailor cases (``tests/evals/fixtures/tailor_cases.json``) and a model that only copies.

Shared by the outcome tests and the HTTP journey: ``copy_everything`` is the
answer of a model that "barely tailors" (every resume line copied, in the
resume's order), and ``ScriptedBinding`` returns scripted answers at the C1
seam (``proposal_execution.resolve_model_adapter``).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.config import Endpoint, GigAIConfig
from gigai.config import ModelTarget as ConfigModelTarget
from gigai.scout.tailored_resume import resume_lines
from gigai.setup import build_config

CASES: dict[str, dict] = {
    case["id"]: case
    for case in json.loads((Path(__file__).resolve().parents[1] / "evals" / "fixtures" / "tailor_cases.json").read_text(encoding="utf-8"))["cases"]
}
ENTRY_SECTIONS = ("experience", "education", "projects")


def copy_everything(resume_text: str) -> dict[str, object]:
    """The answer of a model that tailors nothing: every resume line copied, in the resume's own order."""

    sections: dict[str, list[dict[str, object]]] = {}
    order: list[str] = []
    current = ""
    for number, line in enumerate(resume_lines(resume_text), 1):
        if line.startswith("## "):
            current = line[3:].strip().lower()
            sections[current] = []
            order.append(current)
        elif current in ENTRY_SECTIONS and line.startswith("**"):
            sections[current].append({"heading_ref": [{"copy": number}], "bullets": []})
        elif current in ENTRY_SECTIONS:
            sections[current][-1]["bullets"].append({"copy": number})  # type: ignore[union-attr]
        else:
            sections[current].append({"copy": number})
    return {"sections": [{"heading": name, ("entries" if name in ENTRY_SECTIONS else "lines"): sections[name]} for name in order]}


class _ScriptedPort:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.prompts: list[str] = []
        self.name = "fixture"
        self.timed_out = False

    def invoke(self, request):
        self.prompts.append(request.prompt)
        return InvocationResult(
            status="success", output_text=self._outputs.pop(0), resolved_model="fixture", raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30), cost_status="unavailable",
        )


class ScriptedBinding:
    def __init__(self, outputs: list[str]) -> None:
        self.port = _ScriptedPort(outputs)

    def request(self, *, role: str, prompt: str, required_capabilities=frozenset({"text"})):
        return SimpleNamespace(prompt=prompt, role=role)

    def close(self) -> None:
        pass


def install_scripted_model(monkeypatch, replies: list[dict[str, object]]) -> ScriptedBinding:
    """Every model adapter resolved in this process answers ``replies``, in order."""

    binding = ScriptedBinding([json.dumps(reply) for reply in replies])

    def resolve(config, adapter_target, **_kwargs):
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return binding


def ollama_config(home: Path) -> GigAIConfig:
    return build_config(
        home_root=home, workpad_root=home.parent / "workpads", editor_argv=("/usr/bin/true",), open_with_target=False,
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
