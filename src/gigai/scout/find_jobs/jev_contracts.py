"""P6: frozen DTOs for Jev pre-ranking.

Deliberately its own module (plan's shared-DTO rule, PLAN-api-first-
granular.md section 2): ``contracts.py`` only gains one additive field
(``AcquireOutput.rank_scores``) referencing :class:`RankScore` from here,
never these classes' own definitions -- P2's verdict DTOs land in
``contracts.py`` itself, so the two packets never touch the same class.

Same ``_Contract``/parsing-helper conventions as ``contracts.py`` (closed
object keys, explicit ``to_json``/``from_json``), imported from there rather
than redefined.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from .contracts import (
    _Contract,
    _bool,
    _fail,
    _integer,
    _json_strings,
    _object,
    _object_with_optional,
    _string,
    _strings,
)

# Jev has no free-text output type (jev-api-notes.md); `fit` is one of these
# three fixed category picks, never prose.
_FIT_VALUES = frozenset({"strong", "maybe", "no"})


def _optional_fit(value: object) -> str | None:
    if value is None:
        return None
    if type(value) is not str or value not in _FIT_VALUES:
        _fail("bad_enum", "rank_score.fit must be strong|maybe|no or null")
        raise AssertionError("unreachable")
    return value


@dataclass(frozen=True)
class RankScore(_Contract):
    """One posting's Jev pre-rank score, or an unscored placeholder past the cap.

    ``reasons``/``mismatch_flags`` are category ids (jev-api-notes.md's
    design-decision mapping), never prose: Jev's typed primitives
    (choice/score/noul) cannot emit free text.
    """

    schema_version: ClassVar[str] = "scout-jev-rank-score:1"
    normalized_url: str
    content_sha256: str
    fit: str | None  # "strong" | "maybe" | "no"; None when unscored (past the cap)
    score: int | None  # 0-100; None when unscored
    reasons: tuple[str, ...]
    mismatch_flags: tuple[str, ...]
    hidden_by_default: bool
    cost_usd: str  # decimal string, "0" for a cache hit or an unscored row
    cached: bool

    def to_json(self) -> dict[str, object]:
        return {
            "normalized_url": self.normalized_url,
            "content_sha256": self.content_sha256,
            "fit": self.fit,
            "score": self.score,
            "reasons": _json_strings(self.reasons),
            "mismatch_flags": _json_strings(self.mismatch_flags),
            "hidden_by_default": self.hidden_by_default,
            "cost_usd": self.cost_usd,
            "cached": self.cached,
        }

    @classmethod
    def from_json(cls, obj: object) -> "RankScore":
        value = _object(
            obj,
            (
                "normalized_url",
                "content_sha256",
                "fit",
                "score",
                "reasons",
                "mismatch_flags",
                "hidden_by_default",
                "cost_usd",
                "cached",
            ),
            "rank_score",
        )
        fit = _optional_fit(value["fit"])
        raw_score = value["score"]
        score = _integer(raw_score, "rank_score.score", minimum=0, maximum=100) if raw_score is not None else None
        return cls(
            _string(value["normalized_url"], "normalized_url"),
            _string(value["content_sha256"], "content_sha256"),
            fit,
            score,
            _strings(value["reasons"], "reasons", allow_empty=True),
            _strings(value["mismatch_flags"], "mismatch_flags", allow_empty=True),
            _bool(value["hidden_by_default"], "hidden_by_default"),
            _string(value["cost_usd"], "cost_usd"),
            _bool(value["cached"], "cached"),
        )


@dataclass(frozen=True)
class RankRequest(_Contract):
    schema_version: ClassVar[str] = "scout-jev-rank-request:1"
    run_id: str
    profile_id: str | None = None
    cost_cap_usd: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
        }
        if self.profile_id is not None:
            value["profile_id"] = self.profile_id
        if self.cost_cap_usd is not None:
            value["cost_cap_usd"] = self.cost_cap_usd
        return value

    @classmethod
    def from_json(cls, obj: object) -> "RankRequest":
        value = _object_with_optional(obj, ("run_id",), ("profile_id", "cost_cap_usd", "schema_version"), "rank_request")
        profile_id = value.get("profile_id")
        if profile_id is not None:
            profile_id = _string(profile_id, "rank_request.profile_id")
        cost_cap_usd = value.get("cost_cap_usd")
        if cost_cap_usd is not None:
            cost_cap_usd = _string(cost_cap_usd, "rank_request.cost_cap_usd")
        return cls(_string(value["run_id"], "run_id"), profile_id, cost_cap_usd)


@dataclass(frozen=True)
class RankResponse(_Contract):
    schema_version: ClassVar[str] = "scout-jev-rank-response:1"
    run_id: str
    scores: tuple[RankScore, ...]
    total_cost_usd: str
    capped: bool
    unscored: int

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "scores": [item.to_json() for item in self.scores],
            "total_cost_usd": self.total_cost_usd,
            "capped": self.capped,
            "unscored": self.unscored,
        }

    @classmethod
    def from_json(cls, obj: object) -> "RankResponse":
        value = _object(obj, ("schema_version", "run_id", "scores", "total_cost_usd", "capped", "unscored"), "rank_response")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "rank_response.schema_version is unsupported")
        if type(value["scores"]) is not list:
            _fail("wrong_type", "rank_response.scores must be an array")
        return cls(
            _string(value["run_id"], "run_id"),
            tuple(RankScore.from_json(item) for item in value["scores"]),
            _string(value["total_cost_usd"], "total_cost_usd"),
            _bool(value["capped"], "capped"),
            _integer(value["unscored"], "unscored", minimum=0),
        )


__all__ = ["RankScore", "RankRequest", "RankResponse"]
