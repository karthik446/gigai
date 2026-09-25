"""P6: the Jev pre-rank HTTP client.

One HTTP call per posting against Jev's documented ``/v1/decide`` endpoint
(``orchestrator/research/S28-jev-matching/jev-api-notes.md``, EXECUTED
against the real API: 162/162 calls succeeded, 0 malformed responses).  Jev
has no free-text or array output type -- only ``choice``/``score``/``noul``
typed questions -- so the target ``{fit, score, reasons, mismatch_flags}``
shape is built from several small typed questions, exactly as S28's
``jev_classify.py`` proved out (design-decision section of the same notes
file). This module issues the request and maps the response; it never
decides ranking order, caching, or the cost cap -- that is ``jev_rank.py``.

Key resolution mirrors ``exa_client.py``'s ``_require_api_key`` exactly: the
``JEV_API_KEY`` environment variable first, then ``secrets_store`` (``gigai
secrets add jev``). No key -> :class:`JevMissingKeyError`, a distinct,
narrower exception from the mapped HTTP errors below so callers can choose
to fail open (P6's own behavior: no key -> ``rank_scores == ()``, today's
ordering) instead of failing the whole run.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ... import secrets_store
from .contracts import FindJobsContractError

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx

JEV_DECIDE_URL = "https://jevtypesafeai.com/api/v1/decide"
JEV_API_KEY_ENV_VAR = "JEV_API_KEY"
JEV_MODEL = "jev-latest"

# jev-api-notes.md EXECUTED section: score is a fractional 0-9 level scale;
# mapped to 0-100 exactly as S28's jev_classify.py did (score * 100 / 9).
_SCORE_LEVELS = 9

# Fixed, short reason categories (jev-api-notes.md's design-decision
# mapping) -- Jev cannot emit prose, so "reasons" are ranked category picks,
# never free text. Mirrors S28's jev_classify.py `top_reason` choice set,
# trimmed to what the target schema needs (<=3 reasons; one `choice`
# question already ranks the single strongest reason, keeping this a
# single Jev question rather than three).
_REASON_CRITERIA = {
    "title_match": "job title matches the candidate's target titles",
    "stack_match": "technical stack/domain matches the resume's skills",
    "seniority_match": "seniority level matches the resume",
    "domain_mismatch": "the role is in a different technical domain",
    "seniority_mismatch": "the role is a different level (junior/manager/etc.)",
    "location_mismatch": "the role's location is outside stated preferences",
}

_MISMATCH_FLAGS = ("domain", "seniority", "stack", "location", "sponsorship")

_JEV_HTTP_REASONS: dict[int, str] = {
    400: "validation error",
    401: "invalid or missing API key",
    402: "insufficient credits",
    403: "inactive account",
    404: "unknown endpoint",
    502: "Jev upstream error",
}


def _jev_http_reason(status_code: int) -> str:
    return _JEV_HTTP_REASONS.get(status_code, "request failed")


class JevClientError(FindJobsContractError):
    """A Jev client failure whose message never carries key material or a raw response body."""


class JevMissingKeyError(JevClientError):
    """No ``JEV_API_KEY`` resolved; callers fail open on this (P6 behavior)."""


def require_api_key(*, home_root: Path | None = None) -> str:
    """Resolve ``JEV_API_KEY``: env first, then ``secrets_store`` (exa_client.py's pattern)."""

    api_key = os.environ.get(JEV_API_KEY_ENV_VAR) or secrets_store.get(
        JEV_API_KEY_ENV_VAR, home_root=home_root
    )
    if not api_key:
        raise JevMissingKeyError(
            "jev_missing_key",
            "JEV_API_KEY is not set; run `gigai secrets add jev` (or export JEV_API_KEY)",
        )
    return api_key


def has_api_key(*, home_root: Path | None = None) -> bool:
    try:
        require_api_key(home_root=home_root)
    except JevMissingKeyError:
        return False
    return True


class JevScore:
    """One posting's decoded Jev answer -- ``jev_rank.py`` maps this onto ``RankScore``."""

    __slots__ = ("fit", "score", "reasons", "mismatch_flags", "cost_usd")

    def __init__(
        self,
        *,
        fit: str,
        score: int,
        reasons: tuple[str, ...],
        mismatch_flags: tuple[str, ...],
        cost_usd: str,
    ) -> None:
        self.fit = fit
        self.score = score
        self.reasons = reasons
        self.mismatch_flags = mismatch_flags
        self.cost_usd = cost_usd


def _score_level_to_100(value: object) -> int:
    try:
        raw = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise JevClientError("jev_bad_response", "Jev score answer was not numeric") from None
    return max(0, min(100, round(raw * 100 / _SCORE_LEVELS)))


def _build_request(*, state: dict[str, object], model: str) -> dict[str, object]:
    questions: dict[str, object] = {
        "fit": {
            "type": "choice",
            "instructions": "Overall fit of this posting for this candidate's resume and preferences.",
            "criteria": {
                "strong": "clear match on domain, seniority, and no disqualifying mismatch",
                "maybe": "partial match, or a real but non-fatal mismatch (e.g. location)",
                "no": "wrong domain, wrong seniority, or a disqualifying mismatch",
            },
        },
        "score": {
            "type": "score",
            "instructions": "Fit score, 0 (no fit) to 9 (perfect fit).",
            "criteria": [f"level {i}" for i in range(_SCORE_LEVELS + 1)],
        },
        "top_reason": {
            "type": "choice",
            "instructions": "The single strongest reason for the fit verdict.",
            "criteria": dict(_REASON_CRITERIA),
        },
    }
    for flag in _MISMATCH_FLAGS:
        questions[f"flag_{flag}"] = {
            "type": "noul",
            "instructions": f"Is there a {flag} mismatch between this candidate and this posting?",
        }
    return {"model": model, "state": state, "questions": questions}


def _parse_response(payload: object) -> JevScore:
    if type(payload) is not dict:
        raise JevClientError("jev_bad_response", "Jev response was not a JSON object")
    answers = payload.get("answers")
    if type(answers) is not dict:
        raise JevClientError("jev_bad_response", "Jev response was missing answers")
    fit_answer = answers.get("fit")
    fit = fit_answer.get("choice") if type(fit_answer) is dict else None
    if fit not in {"strong", "maybe", "no"}:
        raise JevClientError("jev_bad_response", "Jev fit answer was missing or invalid")
    score_answer = answers.get("score")
    score_raw = score_answer.get("score") if type(score_answer) is dict else None
    score = _score_level_to_100(score_raw)
    reason_answer = answers.get("top_reason")
    top_reason = reason_answer.get("choice") if type(reason_answer) is dict else None
    reasons = (top_reason,) if isinstance(top_reason, str) and top_reason else ()
    flags: list[str] = []
    for flag in _MISMATCH_FLAGS:
        flag_answer = answers.get(f"flag_{flag}")
        noul = flag_answer.get("noul") if type(flag_answer) is dict else None
        if isinstance(noul, (int, float)) and noul >= 0.5:
            flags.append(flag)
    usage = payload.get("usage")
    cost_usd = usage.get("cost_usd") if type(usage) is dict else None
    cost_str = f"{float(cost_usd):.6f}" if isinstance(cost_usd, (int, float)) else "0"
    return JevScore(fit=fit, score=score, reasons=reasons, mismatch_flags=tuple(flags), cost_usd=cost_str)


class JevClient:
    """Concrete client for one Jev ``/v1/decide`` call per posting."""

    def __init__(self, api_key: str, client: "httpx.Client", *, model: str = JEV_MODEL) -> None:
        self._api_key = api_key
        self._client = client
        self._model = model

    def score(self, state: dict[str, object]) -> JevScore:
        body = _build_request(state=state, model=self._model)
        try:
            response = self._client.post(
                JEV_DECIDE_URL,
                json=body,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
            )
        except Exception as exc:  # noqa: BLE001 - transport failures are redacted before re-raising
            raise JevClientError("jev_transport", f"Jev request failed: {type(exc).__name__}") from None
        if response.status_code >= 400:
            raise JevClientError(
                f"jev_http_{response.status_code}",
                f"Jev returned {response.status_code} ({_jev_http_reason(response.status_code)})",
            )
        try:
            payload = response.json()
        except ValueError:
            raise JevClientError("jev_bad_json", "Jev response was not valid JSON") from None
        return _parse_response(payload)


__all__ = [
    "JEV_API_KEY_ENV_VAR",
    "JEV_DECIDE_URL",
    "JevClient",
    "JevClientError",
    "JevMissingKeyError",
    "JevScore",
    "has_api_key",
    "require_api_key",
]
