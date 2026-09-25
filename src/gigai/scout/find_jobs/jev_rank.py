"""P6: rank a batch of postings against a resume with Jev, cached and cost-capped.

``rank_postings`` is the one entry point acquire's selection ordering and
the ``/rank`` route both call. It never touches the network directly (that
is ``jev_client.py``); it owns the cache, the per-run cost cap, and the
fail-open contract: no key, or any per-call failure after the first
success, degrades to an unscored row rather than failing the caller.

Cache: ``<home>/scout/<project_id>/jev_cache/<key>.json`` (plan section,
P6), ``key = sha256((content_sha256, profile_id, resume_revision_id,
JEV_PROMPT_VERSION, model))`` -- ``discovery/storage.py``'s
``project_id``/``atomic_write`` pattern, same convention as the discovery
package's own per-project cache dirs. Only a *successful* score is cached
(an error is never memoized, so a transient Jev outage doesn't permanently
blank a posting's score for this resume).

Cost cap: default $0.25/run (plan section 8, answer 7), overridable via the
``cost_cap_usd`` request field or ``GIGAI_JEV_COST_CAP_USD`` for the run
path (``bindings.py`` reads the env var and passes it down; this module
only takes the resolved float). Ranking stops calling Jev once the running
total would exceed the cap; every row not yet scored is returned as an
unscored :class:`RankScore` (``fit=None, score=None``), never dropped from
the output -- so ``AcquireOutput.rank_scores`` always has one entry per
input row, cached or fresh or unscored.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from ...canonical import canonical_json_digest, digest_imported_bytes
from .discovery.storage import atomic_write, project_id
from .jev_client import JevClient, JevClientError, JevScore
from .jev_contracts import RankScore

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    import httpx

    from .contracts import PostingRow

JEV_PROMPT_VERSION = "1"
DEFAULT_COST_CAP_USD = 0.25
# A row is hidden by default in the UI once Jev is confident it's a poor
# fit (S28/S29 design intent: "no" fit at a high score-confidence gap is a
# clear miss) -- score threshold matches the plan's target schema
# (<=? not specified numerically; a conservative low-score "no" verdict).
_HIDDEN_FIT = "no"
_HIDDEN_SCORE_MAX = 30

_logger = logging.getLogger("gigai.scout.server")


@dataclass(frozen=True)
class RankPreferences:
    """The small preference slice Jev's ``state`` needs (never the full config)."""

    target_titles: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    visa_sponsorship_required: bool = False


def _cache_key(
    *, content_sha256: str, profile_id: str | None, resume_revision_id: str | None, model: str
) -> str:
    payload = {
        "content_sha256": content_sha256,
        "profile_id": profile_id,
        "resume_revision_id": resume_revision_id,
        "prompt_version": JEV_PROMPT_VERSION,
        "model": model,
    }
    digest = canonical_json_digest(payload)  # "sha256:<hex>"
    return digest.split(":", 1)[1]


def _cache_dir(home_root: Path, target: Path) -> Path:
    return home_root / "scout" / project_id(home_root, target) / "jev_cache"


def _cache_path(home_root: Path, target: Path, key: str) -> Path:
    return _cache_dir(home_root, target) / f"{key}.json"


def _read_cache(path: Path) -> RankScore | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
        return RankScore.from_json(payload)
    except (OSError, ValueError, TypeError):
        return None


def _write_cache(path: Path, score: RankScore) -> None:
    import json

    atomic_write(path, json.dumps(score.to_json(), separators=(",", ":")).encode("utf-8"))


def _hidden_by_default(fit: str, score: int) -> bool:
    return fit == _HIDDEN_FIT and score <= _HIDDEN_SCORE_MAX


def _state_for(row: "PostingRow", *, resume_text: str, prefs: RankPreferences) -> dict[str, object]:
    return {
        "resume": resume_text[:2000],
        "preferences": {
            "target_titles": list(prefs.target_titles),
            "countries": list(prefs.countries),
            "visa_sponsorship_required": prefs.visa_sponsorship_required,
        },
        "posting": {
            "company": row.company,
            "title": row.title,
            "location": row.location,
        },
    }


def _score_to_rank_score(row: "PostingRow", jev: JevScore, *, cached: bool) -> RankScore:
    return RankScore(
        normalized_url=row.normalized_url,
        content_sha256=row.content_sha256 or digest_imported_bytes((row.text or "").encode("utf-8")),
        fit=jev.fit,
        score=jev.score,
        reasons=jev.reasons,
        mismatch_flags=jev.mismatch_flags,
        hidden_by_default=_hidden_by_default(jev.fit, jev.score),
        cost_usd=jev.cost_usd,
        cached=cached,
    )


def _unscored(row: "PostingRow") -> RankScore:
    return RankScore(
        normalized_url=row.normalized_url,
        content_sha256=row.content_sha256 or digest_imported_bytes((row.text or "").encode("utf-8")),
        fit=None,
        score=None,
        reasons=(),
        mismatch_flags=(),
        hidden_by_default=False,
        cost_usd="0",
        cached=False,
    )


def rank_postings(
    rows: "tuple[PostingRow, ...]",
    *,
    client: JevClient,
    resume_text: str,
    prefs: RankPreferences,
    profile_id: str | None,
    resume_revision_id: str | None,
    home_root: Path,
    target: Path,
    cost_cap_usd: float = DEFAULT_COST_CAP_USD,
    model: str = "jev-latest",
) -> tuple[tuple[RankScore, ...], float, bool]:
    """Score every row, cache-first, stopping before the cap.

    Returns ``(scores, total_cost_usd, capped)`` -- ``scores`` has exactly
    one entry per input row, in ``rows``' own order (never re-sorted here;
    ordering by score is the caller's job, e.g.
    ``market_acquisition.py``'s selection call). A row past the cap, or one
    whose call raised (after at least the cache lookup), is returned
    unscored rather than dropped or raising -- Jev failures never fail
    acquire (fail open, same contract as a missing key).
    """

    scores: list[RankScore] = []
    total_cost = 0.0
    capped = False
    for row in rows:
        content_sha256 = row.content_sha256 or digest_imported_bytes((row.text or "").encode("utf-8"))
        key = _cache_key(
            content_sha256=content_sha256,
            profile_id=profile_id,
            resume_revision_id=resume_revision_id,
            model=model,
        )
        path = _cache_path(home_root, target, key)
        cached_score = _read_cache(path)
        if cached_score is not None:
            scores.append(cached_score)
            continue
        if capped or total_cost >= cost_cap_usd:
            capped = True
            scores.append(_unscored(row))
            continue
        try:
            jev_score = client.score(_state_for(row, resume_text=resume_text, prefs=prefs))
        except JevClientError as exc:
            # Never the key, never the response body -- exc.code + a short
            # reason only (JevClientError's own message is already redacted).
            _logger.warning("jev rank call failed for %s: %s", row.normalized_url, exc.code)
            scores.append(_unscored(row))
            continue
        cost = float(jev_score.cost_usd)
        total_cost += cost
        rank_score = _score_to_rank_score(row, jev_score, cached=False)
        scores.append(rank_score)
        # Cached copy is stored with cached=True: the file on disk always
        # represents "what a cache hit returns", so a later run that reads
        # it back sees cached=True (this call's own in-memory result
        # correctly reports cached=False -- it just paid for the call).
        _write_cache(path, _score_to_rank_score(row, jev_score, cached=True))
        _logger.info(
            "jev rank: url=%s fit=%s score=%s cost_usd=%.6f running_total_usd=%.6f",
            row.normalized_url,
            rank_score.fit,
            rank_score.score,
            cost,
            total_cost,
        )
        if total_cost >= cost_cap_usd:
            capped = True
    return tuple(scores), total_cost, capped


def order_by_rank(
    rows: "tuple[PostingRow, ...]", scores: tuple[RankScore, ...]
) -> "tuple[PostingRow, ...]":
    """Stable sort ``rows`` by their matching score, unscored last.

    ``scores`` need not be the same length/order as ``rows`` -- indexed by
    ``normalized_url``; a row with no matching score (a fail-open empty
    ``scores`` tuple, the common no-key case) sorts as unscored, i.e. this
    is a no-op ordering. Python's ``sorted`` is stable, so within each
    scored/unscored partition, ``rows``' own relative order is preserved --
    the existing (pre-P6) ordering when every row is unscored.
    """

    by_url = {item.normalized_url: item for item in scores}

    def sort_key(row: "PostingRow") -> tuple[int, int]:
        score = by_url.get(row.normalized_url)
        if score is None or score.score is None:
            return (1, 0)
        return (0, -score.score)

    return tuple(sorted(rows, key=sort_key))


__all__ = [
    "DEFAULT_COST_CAP_USD",
    "JEV_PROMPT_VERSION",
    "RankPreferences",
    "order_by_rank",
    "rank_postings",
]
