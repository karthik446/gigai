"""0.1.11 MODELPIN: the models GigAI's accuracy results are for, and the one notice any other model's assessment carries.

ONE table says which model(s) each model target was measured with (:data:`EVALUATED_MODELS`) and what each measured
model scored (:data:`RESULTS`); ONE function (:func:`model_notice`) decides from it. Three states per (target, model):
:data:`MEETS` the bar (no notice), :data:`BELOW` the bar (a notice with both numbers) and :data:`NOT_MEASURED` (the
plain notice). The results page is rendered from :func:`results_rows`. The assessment API bodies, ``scout jobs assess``, the
agent brief and ``gigai doctor`` all ask that function, and the results page (``scout/accuracy-0-1-11``) is generated
from the same table, so no model name is written anywhere else.

``codex_cli`` keeps the model the Codex CLI picks. Its ``exec --json`` stream does not name the model, so the adapter
reports ``default`` unless an event does, and a ``default`` assessment carries the unreported notice. ``claude_cli`` is asked for :data:`ASKED_BY_DEFAULT` unless the target's
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

#: The fourth set's size: every count below is "accurate on N of this many jobs".
RESULT_JOBS = 15

#: THE PLACEHOLDER. ``True`` until the fourth set's result files are scored and the counts in :data:`RESULTS` are
#: filled in, once, here. While it is ``True`` the counts are ``None``, a measured model keeps today's rule (a model in
#: :data:`EVALUATED_MODELS` meets the bar, any other gets the not-measured notice) and the release-gate test
#: (``tests/behaviors/ci_tooling/test_model_results_filled.py``) fails. Set it ``False`` with the numbers.
RESULTS_PLACEHOLDER = True


@dataclass(frozen=True)
class ModelResult:
    """One measured (target, model) and how many of :data:`RESULT_JOBS` jobs it got fully right (``None``: not scored yet)."""

    target: str
    model: str
    accurate: int | None = None


#: The models the accuracy run measures (page B of the model policy). The bar is :data:`BAR`'s count: a model with
#: fewer accurate jobs is MEASURED BELOW it. Fill the ``accurate`` counts from the fourth set's result files.
RESULTS: tuple[ModelResult, ...] = (
    ModelResult("claude_cli", "claude-opus-5-5"),
    ModelResult("claude_cli", "claude-sonnet-5-5"),
    ModelResult("codex_cli", "gpt-6-astra"),
)

#: The model whose count is the bar: the accurate one.
BAR = ("claude_cli", "claude-opus-5-5")

#: The states of a (target, model).
MEETS = "meets"
BELOW = "below"
NOT_MEASURED = "not_measured"
#: ``codex exec --json`` does not report the model its default resolves to: such an assessment says so.
UNREPORTED = "unreported"


@dataclass(frozen=True)
class ModelNotice:
    """The one plain notice of an assessment a non-evaluated model made.

    ``kind`` is :data:`NOT_MEASURED`, :data:`BELOW` (``accurate`` and ``bar_accurate`` carry the counts) or
    :data:`UNREPORTED` (the CLI did not say which model answered).
    """

    model: str
    evaluated: tuple[str, ...]
    kind: str = NOT_MEASURED
    accurate: int | None = None
    bar_accurate: int | None = None

    @property
    def text(self) -> str:
        if self.kind == UNREPORTED:
            return (
                "Assessed with the Codex CLI's configured model (not reported). "
                f"GigAI's results for Codex are for {' or '.join(self.evaluated)}."
            )
        if self.kind == BELOW:
            return (
                f"Assessed with {self.model}. On GigAI's accuracy run it was accurate on {self.accurate} of {RESULT_JOBS} jobs; "
                f"{BAR[1]} reached {self.bar_accurate} of {RESULT_JOBS}. This assessment may be less accurate."
            )
        return (
            f"Assessed with {self.model}. GigAI's accuracy results are for {' or '.join(self.evaluated)}; "
            "this assessment may be less accurate."
        )

    @property
    def line(self) -> str:
        """The notice as one terminal or brief line: the text and where the results are."""

        return f"{self.text} Results: {RESULTS_PAGE}"

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "text": self.text, "link": RESULTS_PAGE, "model": self.model, "evaluated": list(self.evaluated)
        }
        if self.kind != NOT_MEASURED:
            value["kind"] = self.kind
        if self.kind == BELOW:
            value["accurate"], value["bar_accurate"], value["of"] = self.accurate, self.bar_accurate, RESULT_JOBS
        return value


def evaluated_for(target: str | None) -> tuple[str, ...]:
    """The models ``target``'s accuracy results are for (``()`` for a target with no results)."""

    return EVALUATED_MODELS.get(target or "", ())


def _result(target: str | None, model: str) -> ModelResult | None:
    return next((row for row in RESULTS if row.target == target and row.model == model), None)


def _bar_count() -> int | None:
    row = _result(*BAR)
    return None if row is None else row.accurate


def model_state(target: str | None, model: str | None) -> str | None:
    """``MEETS``, ``BELOW`` or ``NOT_MEASURED`` for ``model`` on ``target``; ``None`` when nothing is claimed.

    Nothing is claimed for a target with no results, or for a model nobody named (``None``; ``default`` on
    ``claude_cli``). With the counts filled in, a measured model meets the bar when its count is not below the bar's.
    While they are not (:data:`RESULTS_PLACEHOLDER`), a model in :data:`EVALUATED_MODELS` meets it.
    """

    evaluated = evaluated_for(target)
    if not evaluated or model is None or model == DEFAULT_MODEL:
        return None
    row, bar = _result(target, model), _bar_count()
    if row is None or row.accurate is None or bar is None:
        return MEETS if model in evaluated else NOT_MEASURED
    return MEETS if row.accurate >= bar else BELOW


def model_notice(target: str | None, model: str | None) -> ModelNotice | None:
    """The notice for an assessment ``target`` answered with ``model``; ``None`` when no notice is due.

    No notice: the model meets the bar, the target has no row in the table, or the model is not known. A model
    nobody named (``None``, or ``default`` on ``claude_cli``) is not claimed to be anything. ``codex_cli``'s
    ``default`` is NOT claimed to be the model the accuracy run measured: the Codex CLI does not report the model its
    default resolves to, so that assessment says so (:data:`UNREPORTED`).
    """

    evaluated = evaluated_for(target)
    if target == "codex_cli" and model == DEFAULT_MODEL:
        return ModelNotice(model, evaluated, UNREPORTED)
    state = model_state(target, model)
    if state is None or state == MEETS:
        return None
    assert model is not None
    if state == BELOW:
        row = _result(target, model)
        assert row is not None
        return ModelNotice(model, evaluated, BELOW, row.accurate, _bar_count())
    return ModelNotice(model, evaluated)


def results_rows() -> list[dict[str, object]]:
    """The results page's table rows, one per measured model: ``target``, ``model``, ``accurate``, ``of``, ``state``.

    ``accurate`` is ``None`` and ``state`` ``not_scored`` while :data:`RESULTS_PLACEHOLDER` holds. The page
    (``scout/accuracy-0-1-11``) is generated from these rows, never typed by hand.
    """

    return [
        {
            "target": row.target,
            "model": row.model,
            "accurate": row.accurate,
            "of": RESULT_JOBS,
            "state": "not_scored" if row.accurate is None else model_state(row.target, row.model),
        }
        for row in RESULTS
    ]


def notice_lines(batch: Mapping[str, object]) -> list[str]:
    """The plain notice of an assessed batch (``model_notices``), one line per model GigAI's results are not for."""

    return [f"{item['text']} Results: {item['link']}" for item in batch.get("model_notices") or ()]  # type: ignore[union-attr]


def asked_model(target: str | None, configured: str) -> str:
    """The model an assessment on ``target`` asks for, given the model its configured target names."""

    if configured == DEFAULT_MODEL:
        return ASKED_BY_DEFAULT.get(target or "", configured)
    return configured


__all__ = [
    "BAR",
    "BELOW",
    "MEETS",
    "NOT_MEASURED",
    "RESULTS",
    "RESULTS_PLACEHOLDER",
    "RESULT_JOBS",
    "UNREPORTED",
    "ModelResult",
    "ASKED_BY_DEFAULT",
    "DEFAULT_MODEL",
    "EVALUATED_MODELS",
    "RESULTS_PAGE",
    "ModelNotice",
    "asked_model",
    "evaluated_for",
    "model_notice",
    "model_state",
    "results_rows",
    "notice_lines",
]
