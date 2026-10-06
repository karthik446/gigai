"""0110-10-02: how well a posting fits, as ONE number, and the two rules made from it. The one place for both.

THE FIT NUMBER (:func:`fit_percent`). The share of an assessment's
requirements that are met, 0 to 100, with the must-haves weighted: a
requirement the assessment classed ``nice_to_have`` counts once, every other
one (``hard``, ``askable``, or not classed) counts :data:`MUST_HAVE_WEIGHT`
times. An assessment with no requirement detail (an old run's imported row:
counts only) has the plain share (:func:`plain_percent`). A posting nothing
assessed has no fit number: its rank score is a different scale and is never
shown as one.

WEAK FIT (:func:`is_weak_fit`). A posting whose verdict waits on the user's
answers (``needs_answers``) while few of its requirements are met AND the
rank score is low is not worth the user's answers: its state is
:data:`WEAK_FIT` instead. Both must hold, each on a KNOWN number: a posting
with no rank score yet is never weak fit. A weak-fit posting has its own
chip, is left out of the default list and of "Need your answers", asks no
question and is not queued by a saved answer or story. The posting read
model stores the state per row (``postings.py``); a reader that holds one
stored assessment (a job's state, a pipeline trigger) asks
:func:`assessment_is_weak_fit` with the row's rank score
(:func:`stored_rank_scores`), so every surface says the same.

THE ASSESS THRESHOLD (:func:`is_low_rank`). ``scout new`` and "Assess these"
assess only the postings whose rank score is at least ``assess_min_rank``;
the ones below are counted and offered as their own question. A posting with
no rank score yet is not low-ranked: nothing says so.

RANKED LOW (:func:`is_ranked_low`, 0.1.11.2). A posting nothing assessed yet
whose KNOWN rank score is below ``weak_fit_below_rank`` is a weak fit by its
rank alone: the search leaves it out of the default list, counts it
(``counts.ranked_low``) and lists it under the ``ranked_low`` filter
(:data:`RANKED_LOW`). It is a collapse, never a hard filter: the count opens
the list. A posting not ranked yet is never ranked low (it stays in the list,
at the bottom, "not ranked yet"), and an assessed posting keeps the rules
above. The row's stored state stays ``not_assessed``.

THIN POSTING (:func:`is_thin_posting`, :func:`thin_state`, 0.1.11.2). A match
read from fewer than :data:`THIN_POSTING_ROWS` requirement rows says little:
every surface says :data:`THIN_LABEL` instead of "Matched · fit 100%", and no
surface shows or serves a percentage for it (the row's ``fit`` is null). With 1
to 3 rows that is a LABEL only, judged when the row is shown from the counts
already stored: the state, the order, the filters and the counts stay a
match's. A match with NO row about the job (an empty matrix, a lone "No
stated requirements" row, eligibility rows alone) has its own stored state,
:data:`THIN_POSTING`: never a match in a count or a filter, and ordered below
every other assessed posting of its group.

TUNING. The defaults are the constants below. A project overrides them in
the ``fit`` block of its settings file
(``<home>/scout/<project_id>/settings.json``, beside ``pipeline`` and
``rank``)::

    {"schema_version": "scout-settings:1",
     "fit": {"assess_min_rank": 50, "weak_fit_below_percent": 40, "weak_fit_below_rank": 50}}

Each key is optional and a whole number 0 to 100; ``0`` switches that rule
off (``assess_min_rank`` 0: every posting is assessed; either weak-fit key
0: nothing is weak fit). A file that cannot be read, or a value of the wrong
kind, leaves the defaults (``source``: ``settings_unreadable``): these rules
hide and skip, they never spend, so a default is the safe answer.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
from pathlib import Path

#: The state of a needs-answers posting that is a weak fit (``posting.state``, the search's ``state`` filter).
WEAK_FIT = "weak_fit"
NEEDS_ANSWERS = "needs_answers"
NOT_ASSESSED = "not_assessed"
#: 0.1.11.2: the search's filter for the not-assessed postings ranked below ``weak_fit_below_rank`` (never a stored state).
RANKED_LOW = "ranked_low"

#: 0.1.11.2: the state of a match with no row about the job (``thin_state``); its words are :data:`THIN_LABEL`.
THIN_POSTING = "thin_posting"
#: A match needs this many requirement rows (every matrix row, the "N of M" a reader sees) to read as a match.
THIN_POSTING_ROWS = 4
THIN_LABEL = "thin posting, not enough requirements to score"
_MATCHED = "matched"

#: Weak fit: the fit number is below this AND the rank score is below :data:`DEFAULT_WEAK_FIT_BELOW_RANK`.
DEFAULT_WEAK_FIT_BELOW_PERCENT = 40
DEFAULT_WEAK_FIT_BELOW_RANK = 50
#: ``scout new`` and "Assess these" assess a posting only when its rank score is at least this.
DEFAULT_ASSESS_MIN_RANK = 50
#: How many times a must-have counts in the fit number; a ``nice_to_have`` counts once.
MUST_HAVE_WEIGHT = 2

SETTINGS_BLOCK = "fit"
SOURCE_DEFAULT = "default"
SOURCE_SETTING = "setting"
SOURCE_UNREADABLE = "settings_unreadable"

_NICE_TO_HAVE = "nice_to_have"
_MET = "met"
_PENDING = "pending_user_answers"


@dataclass(frozen=True)
class FitSetting:
    """The three numbers the rules use for one project, and what said so."""

    weak_fit_below_percent: int = DEFAULT_WEAK_FIT_BELOW_PERCENT
    weak_fit_below_rank: int = DEFAULT_WEAK_FIT_BELOW_RANK
    assess_min_rank: int = DEFAULT_ASSESS_MIN_RANK
    source: str = SOURCE_DEFAULT

    def to_json(self) -> dict[str, object]:
        return {
            "assess_min_rank": self.assess_min_rank,
            "weak_fit_below_percent": self.weak_fit_below_percent,
            "weak_fit_below_rank": self.weak_fit_below_rank,
            "source": self.source,
        }

    def stamp(self) -> tuple[int, int]:
        """What a stored row's state depends on (the read model's facts digest)."""

        return self.weak_fit_below_percent, self.weak_fit_below_rank


DEFAULT_SETTING = FitSetting()
_UNREADABLE = FitSetting(source=SOURCE_UNREADABLE)


def _percent(block: Mapping[str, object], key: str, default: int) -> int | None:
    if key not in block:
        return default
    value = block[key]
    return value if type(value) is int and 0 <= value <= 100 else None


def fit_setting(home_root: Path, target: Path | None, *, path: Path | None = None) -> FitSetting:
    """The ``fit`` block of this project's settings file; the defaults when there is none. Never raises."""

    from .find_jobs.refresh_tick import SETTINGS_SCHEMA, settings_path

    if target is None and path is None:
        return DEFAULT_SETTING
    if path is None:
        try:
            path = settings_path(Path(home_root), Path(target))  # type: ignore[arg-type]
        except Exception:  # noqa: BLE001 - no bound project yet: there is no settings file to read
            return DEFAULT_SETTING
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return DEFAULT_SETTING
    except (OSError, ValueError):
        return _UNREADABLE
    if not isinstance(payload, dict) or payload.get("schema_version") != SETTINGS_SCHEMA:
        return _UNREADABLE
    block = payload.get(SETTINGS_BLOCK, {})
    if not isinstance(block, dict):
        return _UNREADABLE
    percent = _percent(block, "weak_fit_below_percent", DEFAULT_WEAK_FIT_BELOW_PERCENT)
    rank = _percent(block, "weak_fit_below_rank", DEFAULT_WEAK_FIT_BELOW_RANK)
    minimum = _percent(block, "assess_min_rank", DEFAULT_ASSESS_MIN_RANK)
    if percent is None or rank is None or minimum is None:
        return _UNREADABLE
    return FitSetting(percent, rank, minimum, SOURCE_SETTING if block else SOURCE_DEFAULT)


# --- the fit number -------------------------------------------------------------------------


def plain_percent(met: int | None, total: int | None) -> int | None:
    """``met`` of ``total`` requirements as a whole percent; ``None`` with no requirements."""

    if not total:
        return None
    return round(100 * (met or 0) / total)


def fit_percent(matrix: Iterable[object]) -> int | None:
    """The fit number of an assessment's requirement matrix (``RequirementMatrixRow``s); ``None`` for an empty one."""

    met = total = 0
    for row in matrix:
        kind = getattr(getattr(row, "requirement_class", None), "value", None)
        weight = 1 if kind == _NICE_TO_HAVE else MUST_HAVE_WEIGHT
        total += weight
        if getattr(getattr(row, "status", None), "value", None) == _MET:
            met += weight
    return plain_percent(met, total)


# --- the two rules --------------------------------------------------------------------------


def is_weak_fit(state: str, fit: int | None, rank_score: int | None, setting: FitSetting = DEFAULT_SETTING) -> bool:
    """A needs-answers posting with a fit number below the percent AND a known rank score below the rank."""

    return (
        state == NEEDS_ANSWERS
        and fit is not None
        and rank_score is not None
        and fit < setting.weak_fit_below_percent
        and rank_score < setting.weak_fit_below_rank
    )


def shown_state(state: str, fit: int | None, rank_score: int | None, setting: FitSetting = DEFAULT_SETTING) -> str:
    """``state`` as the read model stores it: :data:`WEAK_FIT` for a weak fit, else ``state`` itself."""

    return WEAK_FIT if is_weak_fit(state, fit, rank_score, setting) else state


def assessment_is_weak_fit(item: object, rank_score: int | None, setting: FitSetting = DEFAULT_SETTING) -> bool:
    """A stored assessment (``AssessResponse``) that waits on answers and is a weak fit at ``rank_score``."""

    result = getattr(item, "result", None)
    if rank_score is None or getattr(getattr(result, "verdict", None), "value", None) != _PENDING:
        return False
    return is_weak_fit(NEEDS_ANSWERS, fit_percent(getattr(result, "matrix", ())), rank_score, setting)


# --- thin postings (0.1.11.2) ----------------------------------------------------------------


def is_thin_posting(state: str, reqs_total: int | None) -> bool:
    """A match that rests on fewer than :data:`THIN_POSTING_ROWS` requirement rows, or the :data:`THIN_POSTING` state.

    ``reqs_total`` counts every matrix row, the "N of M requirements" a reader sees. Judged when a row is shown, so an
    assessment stored before the rule reads thin too. Only a match is relabelled: no other state claims a fit to trust.
    """

    return state == THIN_POSTING or (state == _MATCHED and (reqs_total or 0) < THIN_POSTING_ROWS)


def thin_state(state: str, real_rows: int | None) -> str:
    """``state`` as the read model stores it: :data:`THIN_POSTING` for a match with NO row about the job, else ``state``.

    ``real_rows`` is ``assessment_core.requirement_row_count``: a lone "No stated requirements" row and rows about
    location or eligibility are not about the job. ``None`` (the count is not known) changes nothing.
    """

    return THIN_POSTING if state == _MATCHED and real_rows == 0 else state


def stored_rank_scores(home_root: Path, target: Path) -> dict[tuple[str, str], int | None]:
    """``(profile_id, job) -> rank score`` as the posting read model holds them. Read only: no file is created.

    Empty when the project has no pipeline file yet, or it cannot be read: with no rank score nothing is a weak fit.
    """

    import sqlite3

    from .pipeline.store import PipelineStore, PipelineStoreError, pipeline_path

    try:
        path = pipeline_path(Path(home_root), Path(target))
        if not path.is_file():
            return {}
        store = PipelineStore(path)
    except Exception:  # noqa: BLE001 - no bound project, or a file this build cannot open: no rank score is known
        return {}
    try:
        return {(profile_id, job): rank_score for job, profile_id, rank_score, *_rest in store.posting_rank_inputs()}
    except (PipelineStoreError, sqlite3.Error):
        return {}
    finally:
        store.close()


def is_low_rank(rank_score: int | None, setting: FitSetting = DEFAULT_SETTING) -> bool:
    """A KNOWN rank score below the assess threshold. A posting not ranked yet is not low-ranked."""

    return rank_score is not None and rank_score < setting.assess_min_rank


def is_ranked_low(state: str, rank_score: int | None, setting: FitSetting = DEFAULT_SETTING) -> bool:
    """0.1.11.2: a NOT-ASSESSED posting with a KNOWN rank score below the weak-fit rank. One not ranked yet is never ranked low."""

    return state == NOT_ASSESSED and rank_score is not None and rank_score < setting.weak_fit_below_rank


__all__ = [
    "DEFAULT_ASSESS_MIN_RANK",
    "DEFAULT_SETTING",
    "DEFAULT_WEAK_FIT_BELOW_PERCENT",
    "DEFAULT_WEAK_FIT_BELOW_RANK",
    "MUST_HAVE_WEIGHT",
    "NEEDS_ANSWERS",
    "NOT_ASSESSED",
    "RANKED_LOW",
    "THIN_LABEL",
    "THIN_POSTING",
    "THIN_POSTING_ROWS",
    "WEAK_FIT",
    "FitSetting",
    "assessment_is_weak_fit",
    "fit_percent",
    "fit_setting",
    "is_low_rank",
    "is_ranked_low",
    "is_thin_posting",
    "is_weak_fit",
    "plain_percent",
    "shown_state",
    "stored_rank_scores",
    "thin_state",
]
