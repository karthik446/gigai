"""0.1.10.9 master P4: a scripted model that reads the tailor prompt it is sent and copies the lines it lists.

Shared by the outcome tests of the master resume's tailor path and the pipeline test: the answer is built
from the prompt alone (``copies_what_it_is_shown``), so the same script answers a tailoring of a profile's
own resume and a tailoring of a candidate set of the master, whatever lines code put in front of it.
The resume must be in GigAI's format (``## Section``, ``### entry``, ``- `` bullets).
"""

from __future__ import annotations

from collections.abc import Callable
import json
import re
from types import SimpleNamespace

from gigai.adapters.port import InvocationResult, NormalizedUsage

ENTRY_SECTIONS = ("experience", "projects", "education")
_LISTED = re.compile(r"^R(\d+): (.*)$", re.MULTILINE)


def listed(prompt: str) -> list[tuple[int, str]]:
    """The numbered resume lines a tailor prompt lists."""

    return [(int(found.group(1)), found.group(2).strip()) for found in _LISTED.finditer(prompt.split("RESUME LINES:", 1)[1])]


def copies_what_it_is_shown(
    prompt: str, *, bullets: Callable[[list[int]], list[int]] = lambda numbers: numbers, skip: tuple[str, ...] = ()
) -> dict[str, object]:
    """The answer of a model that tailors nothing: every listed line copied, in the order listed.

    ``bullets`` reorders one entry's bullets (a model that leads with other lines); ``skip`` leaves sections out.
    """

    sections: list[dict[str, object]] = []
    for number, text in listed(prompt):
        if text.startswith("## "):
            heading = text[3:].strip().lower()
            sections.append({"heading": heading, ("entries" if heading in ENTRY_SECTIONS else "lines"): []})
        elif text.startswith("### "):
            sections[-1]["entries"].append({"heading_ref": [{"copy": number}], "bullets": []})  # type: ignore[union-attr]
        elif sections[-1]["heading"] not in ENTRY_SECTIONS:
            sections[-1]["lines"].append({"copy": number})  # type: ignore[union-attr]
        elif text.startswith("- "):
            sections[-1]["entries"][-1]["bullets"].append({"copy": number})  # type: ignore[index]
        else:
            sections[-1]["entries"][-1]["heading_ref"].append({"copy": number})  # type: ignore[index]
    for section in sections:
        for entry in section.get("entries", []):  # type: ignore[union-attr]
            entry["bullets"] = [{"copy": number} for number in bullets([item["copy"] for item in entry["bullets"]])]
    return {"sections": [section for section in sections if section["heading"] not in skip and (section.get("entries") or section.get("lines"))]}


class PromptPort:
    """A model port whose answer is ``answer(prompt)``: a dict (sent as JSON), a string, or an exception to raise."""

    name = "fixture"
    timed_out = False

    def __init__(self, answer: Callable[[str], object]) -> None:
        self._answer = answer
        self.prompts: list[str] = []

    def invoke(self, request):  # noqa: ANN001, ANN201 - the model port's own shape
        self.prompts.append(request.prompt)
        answer = self._answer(request.prompt)
        if isinstance(answer, BaseException):
            raise answer
        return InvocationResult(
            status="success", output_text=answer if isinstance(answer, str) else json.dumps(answer), resolved_model="fixture", raw_usage={},
            normalized_usage=NormalizedUsage(10, 20, 30), cost_status="unavailable",
        )


def install_prompt_model(monkeypatch, answer: Callable[[str], object]) -> PromptPort:  # noqa: ANN001 - pytest's MonkeyPatch
    """Every model adapter resolved in this process answers ``answer(prompt)``; the port keeps the prompts."""

    port = PromptPort(answer)
    binding = SimpleNamespace(port=port, request=lambda *, role, prompt, required_capabilities=frozenset({"text"}): SimpleNamespace(prompt=prompt, role=role), close=lambda: None)

    def resolve(config, adapter_target, **_kwargs):  # noqa: ANN001, ANN202
        return binding

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    return port


__all__ = ["ENTRY_SECTIONS", "PromptPort", "copies_what_it_is_shown", "install_prompt_model", "listed"]
