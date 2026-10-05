"""0.1.11 MODELPIN: the models GigAI's accuracy results are for, and the one notice any other model's assessment carries.

ONE table (:data:`EVALUATED_MODELS`) says which model(s) each model target was measured with; ONE function
(:func:`model_notice`) decides "evaluated or not" from it. The assessment API bodies, ``scout jobs assess``, the
agent brief and ``gigai doctor`` all ask that function, and the results page (``scout/accuracy-0-1-11``) is generated
from the same table, so no model name is written anywhere else.

``codex_cli`` keeps the model the Codex CLI picks (its adapter reports ``default``): the table's model is what the
accuracy run measured with that default. ``claude_cli`` is asked for :data:`ASKED_BY_DEFAULT` unless the target's
configured model says otherwise (``assess_model``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: The accuracy results page, in the form the docs use (a path, never a URL).
RESULTS_PAGE = "scout/accuracy-0-1-11"

#: ``{model target (the adapter kind): the model(s) its accuracy results are for}``.
EVALUATED_MODELS: Mapping[str, tuple[str, ...]] = {
    "claude_cli": ("claude-opus-5-5",),
    "codex_cli": ("gpt-6-astra",),
}

#: The model an assessment on this target asks for when the target's configured model is ``default``.
ASKED_BY_DEFAULT: Mapping[str, str] = {"claude_cli": "claude-opus-5-5"}

#: What an adapter reports when it does not name the model (the CLI's own default).
DEFAULT_MODEL = "default"


@dataclass(frozen=True)
class ModelNotice:
    """The one plain notice of an assessment a non-evaluated model made."""

    model: str
    evaluated: tuple[str, ...]

    @property
    def text(self) -> str:
        return (
            f"Assessed with {self.model}. GigAI's accuracy results are for {' or '.join(self.evaluated)}; "
            "this assessment may be less accurate."
        )

    @property
    def line(self) -> str:
        """The notice as one terminal or brief line: the text and where the results are."""

        return f"{self.text} Results: {RESULTS_PAGE}"

    def to_json(self) -> dict[str, object]:
        return {"text": self.text, "link": RESULTS_PAGE, "model": self.model, "evaluated": list(self.evaluated)}


def evaluated_for(target: str | None) -> tuple[str, ...]:
    """The models ``target``'s accuracy results are for (``()`` for a target with no results)."""

    return EVALUATED_MODELS.get(target or "", ())


def model_notice(target: str | None, model: str | None) -> ModelNotice | None:
    """The notice for an assessment ``target`` answered with ``model``; ``None`` when no notice is due.

    No notice: the model is one the target was evaluated with, the target has no row in the table, or the model is
    not known. A model nobody named (``None``, or ``default`` on ``claude_cli``) is not claimed to be anything;
    ``codex_cli``'s ``default`` is the model the accuracy run measured.
    """

    evaluated = evaluated_for(target)
    if not evaluated or model is None or model == DEFAULT_MODEL or model in evaluated:
        return None
    return ModelNotice(model, evaluated)


def notice_lines(batch: Mapping[str, object]) -> list[str]:
    """The plain notice of an assessed batch (``model_notices``), one line per model GigAI's results are not for."""

    return [f"{item['text']} Results: {item['link']}" for item in batch.get("model_notices") or ()]  # type: ignore[union-attr]


def asked_model(target: str | None, configured: str) -> str:
    """The model an assessment on ``target`` asks for, given the model its configured target names."""

    if configured == DEFAULT_MODEL:
        return ASKED_BY_DEFAULT.get(target or "", configured)
    return configured


__all__ = [
    "ASKED_BY_DEFAULT",
    "DEFAULT_MODEL",
    "EVALUATED_MODELS",
    "RESULTS_PAGE",
    "ModelNotice",
    "asked_model",
    "evaluated_for",
    "model_notice",
    "notice_lines",
]
