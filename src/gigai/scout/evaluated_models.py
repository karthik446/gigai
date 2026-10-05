"""0.1.11 MODELPIN: the models GigAI's accuracy results are for, and the one notice any other model's assessment carries.

ONE table says which model(s) each model target was measured with (:data:`EVALUATED_MODELS`) and what each measured
model scored (:data:`RESULTS`, with a short ``why`` per row); ONE function (:func:`model_notice`) decides from it. The
notice has no threshold in it: a target's :data:`REFERENCE` model (its best measured) carries none unless it is itself
below the :data:`BAR`; any other measured model carries both numbers; a model nobody measured, the plain notice. The results page is rendered from :func:`results_rows`. The assessment API bodies, ``scout jobs assess``, the
agent brief and ``gigai doctor`` all ask that function, and the results page (``scout/accuracy-0-1-11``) is generated
from the same table, so no model name is written anywhere else.

``codex_cli`` keeps the model the Codex CLI picks. Its ``exec --json`` stream does not name the model, so the adapter
reports ``default`` unless an event does, and a ``default`` assessment carries the unreported notice with its reference's numbers. ``claude_cli`` is asked for :data:`ASKED_BY_DEFAULT` unless the target's
configured model says otherwise (``assess_model``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

#: The accuracy results page, in the form the docs use (a path, never a URL).
RESULTS_PAGE = "scout/accuracy-0-1-11"
#: The words the UI shows for the results link (the notice text excludes the link; the CLI and brief lines append ``Results: <path>``).
RESULTS_LINK_LABEL = "GigAI's accuracy results"

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

#: THE PLACEHOLDER. ``True`` while the counts in :data:`RESULTS` and :data:`BAR` are PROVISIONAL (the orchestrator's
#: working numbers, not yet confirmed from the fourth set's scored result files). The release-gate test
#: (``tests/behaviors/ci_tooling/test_model_results_filled.py``) fails while it is ``True``. Confirm the numbers, then set
#: it ``False``, once, here.
RESULTS_PLACEHOLDER = False


@dataclass(frozen=True)
class ModelResult:
    """One measured (target, model): how many of :data:`RESULT_JOBS` jobs it got fully right, and why when it fell short."""

    target: str
    model: str
    accurate: int | None = None
    #: A short cause for a shortfall, written into the notice ("mostly from unnecessary questions"); ``""`` for none.
    why: str = ""


#: What each measured model scored on the fourth set. CONFIRMED by the orchestrator (fourth set, frozen tree ede2fc2a): 14 / 12 / 10.
RESULTS: tuple[ModelResult, ...] = (
    ModelResult("claude_cli", "claude-opus-5-5", 14),
    ModelResult("claude_cli", "claude-sonnet-5-5", 12, "mostly from unnecessary questions"),
    ModelResult("codex_cli", "gpt-6-astra", 10, "mostly from unnecessary questions"),
)

#: Per target, the model with the best measured result: its assessment carries no notice (unless it is itself below :data:`BAR`).
REFERENCE: Mapping[str, str] = {"claude_cli": "claude-opus-5-5", "codex_cli": "gpt-6-astra"}

#: The bar: a model accurate on at least this many of :data:`RESULT_JOBS` jobs meets it (PROVISIONAL).
BAR = 12

#: What each target is called in the sentence that names another target's reference.
TARGET_LABELS: Mapping[str, str] = {"claude_cli": "Claude Code", "codex_cli": "the Codex CLI"}

#: The states of a (target, model) against the bar, for the results page.
MEETS = "meets"
BELOW = "below"
NOT_MEASURED = "not_measured"
#: The kinds of notice: a measured model that is not its target's reference, the Codex CLI's unreported model.
MEASURED = "measured"
UNREPORTED = "unreported"


@dataclass(frozen=True)
class ModelNotice:
    """The one plain notice of an assessment whose model needs one.

    ``kind``: :data:`NOT_MEASURED` (no result), :data:`MEASURED` (a result, not the target's reference), :data:`BELOW`
    (the target's own reference, itself below the bar) or :data:`UNREPORTED` (the CLI did not say which model answered; the
    counts are its reference's). ``accurate`` is the count of the model the numbers are about, ``reference`` the better
    model to name (and its count ``reference_accurate``, ``reference_target`` its CLI) when it is another model.
    """

    model: str
    evaluated: tuple[str, ...]
    kind: str = NOT_MEASURED
    accurate: int | None = None
    reference: str | None = None
    reference_accurate: int | None = None
    reference_target: str | None = None
    measured_with: str | None = None
    why: str = ""

    @property
    def text(self) -> str:
        if self.kind == NOT_MEASURED:
            return (
                f"Assessed with {self.model}. GigAI's accuracy results are for {' or '.join(self.evaluated)}; "
                "this assessment may be less accurate."
            )
        count = f"accurate on {self.accurate} of {RESULT_JOBS} jobs"
        why = f", {self.why}" if self.why else ""
        if self.kind == UNREPORTED:
            text = f"Assessed with the Codex CLI (model not reported; measured with {self.measured_with}: {count}{why})."
        elif self.kind == BELOW:
            text = f"Assessed with {self.model}: {count} in GigAI's accuracy run{why}, below the bar of {BAR} of {RESULT_JOBS}."
        else:
            text = f"Assessed with {self.model}: {count} in GigAI's accuracy run{why}"
            text += f"; {self.reference} reached {self.reference_accurate} of {RESULT_JOBS}." if self.reference else "."
            return text
        if self.reference:
            who = TARGET_LABELS.get(self.reference_target or "", "")
            named = f"{who} with {self.reference}" if who else self.reference
            text += f" {named[0].upper()}{named[1:]} reached {self.reference_accurate} of {RESULT_JOBS}."
        return text

    @property
    def line(self) -> str:
        """The notice as one terminal or brief line: the text and where the results are."""

        return f"{self.text} Results: {RESULTS_PAGE}"

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "text": self.text, "link": {"label": RESULTS_LINK_LABEL, "path": RESULTS_PAGE}, "model": self.model, "evaluated": list(self.evaluated)
        }
        if self.kind != NOT_MEASURED:
            value["kind"] = self.kind
            value["accurate"], value["of"] = self.accurate, RESULT_JOBS
            if self.reference:
                value["reference"], value["reference_accurate"] = self.reference, self.reference_accurate
        return value


def evaluated_for(target: str | None) -> tuple[str, ...]:
    """The models ``target``'s accuracy results are for (``()`` for a target with no results)."""

    return EVALUATED_MODELS.get(target or "", ())


def _result(target: str | None, model: str | None) -> ModelResult | None:
    return next((row for row in RESULTS if row.target == target and row.model == model), None)


def _best_elsewhere(target: str, accurate: int) -> ModelResult | None:
    """The best measured reference of ANOTHER target when it beat ``accurate`` (what a below-the-bar target is compared with)."""

    others = [row for t, m in REFERENCE.items() if t != target and (row := _result(t, m)) is not None and row.accurate is not None]
    best = max(others, key=lambda row: row.accurate or 0, default=None)
    return best if best is not None and (best.accurate or 0) > accurate else None


def model_state(target: str | None, model: str | None) -> str | None:
    """``MEETS``, ``BELOW`` or ``NOT_MEASURED`` against the bar; ``None`` when nothing is claimed.

    Nothing is claimed for a target with no results, or for a model nobody named (``None``; ``default`` on ``claude_cli``).
    """

    if not evaluated_for(target) or model is None or model == DEFAULT_MODEL:
        return None
    row = _result(target, model)
    if row is None or row.accurate is None:
        return NOT_MEASURED
    return MEETS if row.accurate >= BAR else BELOW


def model_notice(target: str | None, model: str | None) -> ModelNotice | None:
    """The notice for an assessment ``target`` answered with ``model``; ``None`` when no notice is due.

    The rule has no threshold in it: the target's reference model (its best measured) carries no notice, EXCEPT a
    reference that is itself below :data:`BAR`; any other measured model carries both numbers; a model nobody measured
    the plain notice. A target with no results, or a model nobody named (``None``, or ``default`` on ``claude_cli``), is
    not claimed to be anything. ``codex_cli``'s ``default`` is the Codex CLI's own model, which it does not report: the
    notice gives its reference's numbers as measured with that model, never "evaluated".
    """

    evaluated = evaluated_for(target)
    if not evaluated or target is None or model is None:
        return None
    reference = REFERENCE.get(target)
    unreported = model == DEFAULT_MODEL and target == "codex_cli"
    if model == DEFAULT_MODEL and not unreported:
        return None
    row = _result(target, reference if unreported else model)
    if row is None or row.accurate is None:
        return ModelNotice(model, evaluated)
    if row.model == reference:
        if row.accurate >= BAR and not unreported:
            return None
        elsewhere = _best_elsewhere(target, row.accurate) if row.accurate < BAR else None
        kind = UNREPORTED if unreported else BELOW
        if unreported and row.accurate >= BAR:
            elsewhere = None
        return ModelNotice(
            model, evaluated, kind, row.accurate, None if elsewhere is None else elsewhere.model,
            None if elsewhere is None else elsewhere.accurate, None if elsewhere is None else elsewhere.target,
            row.model, row.why,
        )
    best = _result(target, reference)
    return ModelNotice(
        model, evaluated, MEASURED, row.accurate, reference, None if best is None else best.accurate, target, row.model, row.why
    )


def results_rows() -> list[dict[str, object]]:
    """The results page's table rows, one per measured model: ``target``, ``model``, ``accurate``, ``of``, ``state``.

    ``reference`` marks the target's reference model; ``why`` is the row's short cause. The page
    (``scout/accuracy-0-1-11``) is generated from these rows, never typed by hand.
    """

    return [
        {
            "target": row.target,
            "model": row.model,
            "accurate": row.accurate,
            "of": RESULT_JOBS,
            "state": "not_scored" if row.accurate is None else model_state(row.target, row.model),
            "reference": REFERENCE.get(row.target) == row.model,
            "why": row.why,
        }
        for row in RESULTS
    ]


def notice_lines(batch: Mapping[str, object]) -> list[str]:
    """The plain notice of an assessed batch (``model_notices``), one line per model GigAI's results are not for."""

    return [f"{item['text']} Results: {item['link']['path']}" for item in batch.get("model_notices") or ()]  # type: ignore[union-attr]


def asked_model(target: str | None, configured: str) -> str:
    """The model an assessment on ``target`` asks for, given the model its configured target names."""

    if configured == DEFAULT_MODEL:
        return ASKED_BY_DEFAULT.get(target or "", configured)
    return configured


__all__ = [
    "BAR",
    "BELOW",
    "MEASURED",
    "MEETS",
    "REFERENCE",
    "NOT_MEASURED",
    "RESULTS",
    "RESULTS_PLACEHOLDER",
    "RESULT_JOBS",
    "UNREPORTED",
    "ModelResult",
    "ASKED_BY_DEFAULT",
    "DEFAULT_MODEL",
    "EVALUATED_MODELS",
    "RESULTS_LINK_LABEL",
    "RESULTS_PAGE",
    "ModelNotice",
    "asked_model",
    "evaluated_for",
    "model_notice",
    "model_state",
    "results_rows",
    "notice_lines",
]
