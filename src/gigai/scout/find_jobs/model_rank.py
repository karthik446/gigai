"""SCOPE-ADD-3 B1: rank postings with the operator's own model, in batches of digests.

The ranking spike (research/ranking-spike/REPORT.md, verdict GO) measured a
batched, digest-based ranker at AUC 0.95 for pushing not-a-match postings
down (the Jev title-only pre-rank was 0.49), 162/162 valid answers, and
~80 s per 500 postings at K=8, b50 with codex. This module is that ranker in
product code; it does not wire itself into runs (packet C1 does).

One pass, :func:`rank_postings`:

1. **Digest** every row (``rank_digest``, digest v2) and the resume (a few
   lines), so a batch of 50 postings is ~3k prompt tokens.
2. **Cache**: a posting already scored for the same (content, resume digest,
   preferences, prompt version, digest version, model) is served from
   ``<home>/cache/scout/rank/scores/<key>.json`` and never paid for again.
   Only rows from a VALID answer are cached. The cached rows land first, as
   one ``source="cache"`` batch.
3. **Batches** of ``batch_size`` (default 50) run on a pool of at most
   ``concurrency`` workers (default 8; :data:`SMALL_MACHINE_CONCURRENCY` is 4).
   Each batch is one model call through the SAME adapter path the assess
   node and quick assess use (``proposal_execution.resolve_model_adapter``
   looked up as a module attribute, the configured target for the adapter
   kind, the test transport installed first), at low reasoning effort.
   Claude runs in the adapter's lean mode (see ``adapters/claude_cli.py``).
4. **Validation** is strict and id-keyed: a JSON array with every posting id
   exactly once, ``score`` an integer 0-100, ``reasons`` at most 2 strings,
   ``blockers`` a list of strings. An invalid answer is retried ONCE with the
   error fed back; if the retry fails too the batch is split ONCE into two
   halves (one attempt each); rows of a half that still fails are marked
   unscored with the reason. A row is never dropped.
5. **Bounds**: at most ``max_calls`` model calls per pass (every attempt,
   retry and half counts; default :data:`DEFAULT_MAX_CALLS`, enough for ~30
   batches with some retries), and optionally ``max_tokens`` (checked before
   each call, so calls already in flight may overshoot it). A row whose
   batch could not get a call is unscored (``call_budget``/``token_budget``).
6. **Streaming**: ``on_batch(batch_result)`` is called on the CALLER's thread,
   one batch at a time, in the order batches land. A split batch lands as its
   parent (no postings, ``split=True``) followed by its two halves; summing
   ``len(batch.postings)`` over landed batches counts rows done.
7. **Cancel**: once ``cancel`` is set no new model call starts; batches not
   yet started land as unscored (``cancelled``). A call already running is
   not interrupted (the CLI adapters cannot be); its valid answer is kept.
8. **Fail open**: an unresolvable model target, an authentication/denied
   error or a budget stop never raises; the result says what happened in
   ``status`` and ``fail_open_reason``, and unscored rows keep their place.

Blockers DEMOTE, never hide: a posting whose answer names a blocker keeps
its score and gets ``demoted=True``; :func:`ordering_key` orders scored
unblocked postings first, then unscored ones, then blocked ones (each by
score, then input order), and nothing is removed. On the spike's gold set all
6 "false" blockers came from digest flags the full assessment only ASKS
about, so hiding a blocked posting would lose real candidates.

:meth:`RankResult.to_json` is the sealed ``rank.json`` shape (REPORT Q6).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
import json
from pathlib import Path
import re
import threading
import time
from typing import TYPE_CHECKING, Any, Mapping

from ...adapters.claude_cli import ClaudeCLIAdapter
from ...adapters.port import ModelInvocationError
from ...canonical import canonical_json_digest, digest_imported_bytes
from .discovery.storage import atomic_write
from .rank_digest import DIGEST_VERSION, CandidatePrefs, posting_digest, resume_digest

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    from ...config import GigAIConfig
    from .contracts import ModelTarget, PostingRow

PROMPT_VERSION = "rank-v1"
RANK_SCHEMA_VERSION = "scout-rank:1"
DEFAULT_BATCH_SIZE = 50
DEFAULT_CONCURRENCY = 8
SMALL_MACHINE_CONCURRENCY = 4
# ~30 batches of 50 (1,500 postings) plus room for a few retries/splits.
# The run path sets its own cap (C1); this is only the module default.
DEFAULT_MAX_CALLS = 40
RANK_EFFORT = "low"
_ROLE = "reviewer"
_CLI_ADAPTERS = frozenset({"codex_cli", "claude_cli"})
_MAX_FED_BACK_ERROR = 300
_MAX_REASONS = 2
# An adapter error with one of these codes will fail every later call too:
# the pass stops calling instead of spending its budget on it.
_ABORT_CODES = frozenset({"model_authentication_required", "network_denied", "model_denied", "credential_denied"})

PROMPT = """You are pre-ranking job postings for ONE candidate so the best ones get a full assessment first.
Score every posting 0-100 for how likely a full resume-vs-requirements assessment would find it a match.

Scoring guide:
- 80-100: level and title fit the candidate's targets, most required skills/tech appear in the candidate's skills, no blocker.
- 50-79: plausible fit with gaps (some key tech missing, domain unfamiliar, level one step off).
- 20-49: weak fit (core stack or domain mostly absent, or level clearly off).
- 0-19: a blocker applies or the role is a different job family.

Blockers are ONLY hard facts stated in the posting line that rule the candidate out: flags=no_sponsor when the candidate needs sponsorship, flags=clearance, flags=citizen, a location/countries list that excludes the candidate's countries. Do not invent blockers; leave the list empty when none apply. Missing skills are NOT blockers.

Posting lines are: id | title @ company | lvl | loc [countries] | yrs=min years | req=skills/tech from the requirements section | flags=hard-constraint hints.

{candidate}

POSTINGS ({n}):
{lines}

Answer with ONLY a JSON array, no prose, no code fences, one object per posting, every id exactly once:
[{{"posting_id": "p000", "score": 0, "reasons": ["<=12 words", "<=12 words"], "blockers": []}}]
reasons: at most 2 short strings. blockers: strings, [] when none."""

RETRY = """

Your previous answer was rejected: {error}
You cannot see that answer. Produce a fresh, complete answer following the format exactly."""


# --- prompt and validation -----------------------------------------------------------------


def render_rank_prompt(lines: Sequence[str], candidate: str, error: str | None = None) -> str:
    """The rank-v1 prompt for one batch of digest lines (the spike's text, unchanged)."""

    text = PROMPT.format(candidate=candidate, n=len(lines), lines="\n".join(lines))
    if error:
        text += RETRY.format(error=error[:_MAX_FED_BACK_ERROR])
    return text


class RankAnswerError(ValueError):
    """A model answer that does not match the strict rank schema."""


def _extract_array(text: str) -> object:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.S)
    if fence:
        text = fence.group(1)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("["), text.rfind("]")
        if start < 0 or end <= start:
            raise RankAnswerError("not a JSON array: no JSON array in the answer") from None
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise RankAnswerError(f"not a JSON array: {exc}") from None


def validate_rank_answer(text: str, ids: Sequence[str]) -> list[dict[str, object]]:
    """The answer's items in ``ids`` order, or :class:`RankAnswerError` naming the first problem.

    Tolerates a code fence or prose around the array (the spike saw Haiku
    wrap its JSON); everything inside the array is strict.
    """

    data = _extract_array(text)
    if not isinstance(data, list):
        raise RankAnswerError("top level is not an array")
    wanted = set(ids)
    seen: dict[str, dict[str, object]] = {}
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise RankAnswerError(f"item {index} is not an object")
        extra = set(item) - {"posting_id", "score", "reasons", "blockers"}
        if extra:
            raise RankAnswerError(f"item {index} has unknown keys {sorted(extra)}")
        pid, score = item.get("posting_id"), item.get("score")
        reasons, blockers = item.get("reasons"), item.get("blockers")
        if not isinstance(pid, str) or pid not in wanted:
            raise RankAnswerError(f"item {index} has unknown posting_id {pid!r}")
        if pid in seen:
            raise RankAnswerError(f"posting_id {pid} appears twice")
        if type(score) is not int or not 0 <= score <= 100:
            raise RankAnswerError(f"{pid}: score must be an integer 0-100")
        if not isinstance(reasons, list) or len(reasons) > _MAX_REASONS or not all(isinstance(r, str) for r in reasons):
            raise RankAnswerError(f"{pid}: reasons must be a list of at most 2 strings")
        if not isinstance(blockers, list) or not all(isinstance(b, str) for b in blockers):
            raise RankAnswerError(f"{pid}: blockers must be a list of strings")
        seen[pid] = item
    missing = [pid for pid in ids if pid not in seen]
    if missing:
        raise RankAnswerError(f"{len(missing)} of {len(ids)} posting_ids missing (e.g. {missing[:3]})")
    return [seen[pid] for pid in ids]


# --- results -------------------------------------------------------------------------------


@dataclass(frozen=True)
class RankedPosting:
    """One input row's ranking outcome. ``score is None`` means unscored (see ``unscored_reason``)."""

    posting_id: str
    normalized_url: str
    content_sha256: str
    score: int | None
    reasons: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    batch_id: str | None = None
    cached: bool = False
    unscored_reason: str | None = None

    @property
    def scored(self) -> bool:
        return self.score is not None

    @property
    def demoted(self) -> bool:
        """A blocker was named: order below unblocked postings, never hide."""

        return bool(self.blockers)

    def to_json(self) -> dict[str, object]:
        return {
            "posting_id": self.posting_id,
            "normalized_url": self.normalized_url,
            "content_sha256": self.content_sha256,
            "score": self.score,
            "reasons": list(self.reasons),
            "blockers": list(self.blockers),
            "demoted": self.demoted,
            "batch_id": self.batch_id,
            "cached": self.cached,
            "unscored_reason": self.unscored_reason,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "RankedPosting":
        return cls(
            posting_id=str(value["posting_id"]),
            normalized_url=str(value["normalized_url"]),
            content_sha256=str(value["content_sha256"]),
            score=value["score"] if type(value.get("score")) is int else None,
            reasons=tuple(str(item) for item in value.get("reasons") or ()),
            blockers=tuple(str(item) for item in value.get("blockers") or ()),
            batch_id=value.get("batch_id"),
            cached=bool(value.get("cached")),
            unscored_reason=value.get("unscored_reason"),
        )


@dataclass(frozen=True)
class BatchUsage:
    """Token usage summed over a batch's attempts; ``None`` when no attempt reported it."""

    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cached_input_tokens: int | None = None

    def plus(self, other: "BatchUsage") -> "BatchUsage":
        def add(a: int | None, b: int | None) -> int | None:
            return None if a is None and b is None else (a or 0) + (b or 0)

        return BatchUsage(
            add(self.input_tokens, other.input_tokens),
            add(self.output_tokens, other.output_tokens),
            add(self.total_tokens, other.total_tokens),
            add(self.cached_input_tokens, other.cached_input_tokens),
        )

    @property
    def spent(self) -> int:
        """What counts against ``max_tokens``: the total, else input + output."""

        if self.total_tokens is not None:
            return self.total_tokens
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    def to_json(self) -> dict[str, object]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cached_input_tokens": self.cached_input_tokens,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "BatchUsage":
        def integer(name: str) -> int | None:
            item = value.get(name)
            return item if type(item) is int else None

        return cls(integer("input_tokens"), integer("output_tokens"), integer("total_tokens"), integer("cached_input_tokens"))


@dataclass(frozen=True)
class BatchResult:
    """One landed batch: what ``on_batch`` receives and ``rank.json`` records.

    ``source="cache"`` is the one batch of cache hits (no model call).
    ``split=True`` is a parent whose answer stayed invalid after the retry:
    it carries no postings; its two halves (``split_from`` = its id) follow.
    """

    batch_id: str
    posting_ids: tuple[str, ...]
    source: str
    valid: bool
    attempts: int
    seconds: float
    usage: BatchUsage = field(default_factory=BatchUsage)
    error: str | None = None
    split: bool = False
    split_from: str | None = None
    resolved_model: str | None = None
    postings: tuple[RankedPosting, ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "batch_id": self.batch_id,
            "posting_ids": list(self.posting_ids),
            "source": self.source,
            "valid": self.valid,
            "attempts": self.attempts,
            "seconds": self.seconds,
            "usage": self.usage.to_json(),
            "error": self.error,
            "split": self.split,
            "split_from": self.split_from,
            "resolved_model": self.resolved_model,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any], postings: Sequence[RankedPosting] = ()) -> "BatchResult":
        batch_id = str(value["batch_id"])
        return cls(
            batch_id=batch_id,
            posting_ids=tuple(str(item) for item in value.get("posting_ids") or ()),
            source=str(value["source"]),
            valid=bool(value["valid"]),
            attempts=int(value["attempts"]),
            seconds=float(value["seconds"]),
            usage=BatchUsage.from_json(value.get("usage") or {}),
            error=value.get("error"),
            split=bool(value.get("split")),
            split_from=value.get("split_from"),
            resolved_model=value.get("resolved_model"),
            postings=tuple(item for item in postings if item.batch_id == batch_id),
        )


@dataclass(frozen=True)
class RankResult:
    """One ranking pass. ``to_json`` is the sealed ``rank.json`` (REPORT Q6).

    ``status``: ``complete`` (every row scored), ``partial`` (some rows
    unscored: invalid batches or a budget stop), ``cancelled``, or
    ``skipped`` (no model call was possible; every row unscored).
    ``fail_open_reason`` is ``None`` only when complete.
    """

    model_target: str
    configured_target: str | None
    model: str | None
    resolved_model: str | None
    effort: str
    effort_applied: str | None
    batch_size: int
    concurrency: int
    max_calls: int
    max_tokens: int | None
    postings: tuple[RankedPosting, ...]
    batches: tuple[BatchResult, ...]
    status: str
    fail_open_reason: str | None
    seconds: float
    prompt_version: str = PROMPT_VERSION
    digest_version: str = DIGEST_VERSION

    def totals(self) -> dict[str, object]:
        usage = BatchUsage()
        for batch in self.batches:
            usage = usage.plus(batch.usage)
        model_batches = [batch for batch in self.batches if batch.source == "model"]
        return {
            "postings": len(self.postings),
            "scored": sum(item.scored for item in self.postings),
            "unscored": sum(not item.scored for item in self.postings),
            "cached": sum(item.cached for item in self.postings),
            "demoted": sum(item.demoted for item in self.postings),
            "calls": sum(batch.attempts for batch in model_batches),
            "batches": len(model_batches),
            "valid_batches": sum(batch.valid for batch in model_batches),
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "cached_input_tokens": usage.cached_input_tokens,
            "seconds": self.seconds,
        }

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": RANK_SCHEMA_VERSION,
            "prompt_version": self.prompt_version,
            "digest_version": self.digest_version,
            "model_target": self.model_target,
            "configured_target": self.configured_target,
            "model": self.model,
            "resolved_model": self.resolved_model,
            "effort": self.effort,
            "effort_applied": self.effort_applied,
            "batch_size": self.batch_size,
            "concurrency": self.concurrency,
            "max_calls": self.max_calls,
            "max_tokens": self.max_tokens,
            "status": self.status,
            "fail_open_reason": self.fail_open_reason,
            "totals": self.totals(),
            "postings": [item.to_json() for item in self.postings],
            "batches": [batch.to_json() for batch in self.batches],
        }

    @classmethod
    def from_json(cls, value: Mapping[str, Any]) -> "RankResult":
        if value.get("schema_version") != RANK_SCHEMA_VERSION:
            raise ValueError(f"not a {RANK_SCHEMA_VERSION} document")
        postings = tuple(RankedPosting.from_json(item) for item in value["postings"])
        return cls(
            model_target=str(value["model_target"]),
            configured_target=value.get("configured_target"),
            model=value.get("model"),
            resolved_model=value.get("resolved_model"),
            effort=str(value["effort"]),
            effort_applied=value.get("effort_applied"),
            batch_size=int(value["batch_size"]),
            concurrency=int(value["concurrency"]),
            max_calls=int(value["max_calls"]),
            max_tokens=value.get("max_tokens"),
            postings=postings,
            batches=tuple(BatchResult.from_json(item, postings) for item in value["batches"]),
            status=str(value["status"]),
            fail_open_reason=value.get("fail_open_reason"),
            seconds=float(value["totals"]["seconds"]),
            prompt_version=str(value["prompt_version"]),
            digest_version=str(value["digest_version"]),
        )

    def by_url(self) -> dict[str, RankedPosting]:
        return {item.normalized_url: item for item in self.postings}


def ordering_key(posting: RankedPosting, index: int) -> tuple[int, int, int]:
    """Sort key for selection: scored-unblocked, then unscored, then blocked (demoted).

    Within a tier: higher score first, then input order. Nothing is dropped.
    """

    if posting.score is None:
        return (1, 0, index)
    return (2 if posting.demoted else 0, -posting.score, index)


def ranked_order(result: RankResult) -> list[RankedPosting]:
    return [item for _, item in sorted(enumerate(result.postings), key=lambda pair: ordering_key(pair[1], pair[0]))]


# --- cache ---------------------------------------------------------------------------------


def cache_dir(home_root: Path) -> Path:
    """The shared model-rank score cache (the home's, not a project's; not the Jev cache)."""

    return Path(home_root) / "cache" / "scout" / "rank" / "scores"


def _hex(payload: Mapping[str, object]) -> str:
    return canonical_json_digest(dict(payload)).split(":", 1)[1]


def content_digest(row: "PostingRow") -> str:
    """The row's content identity: its ``content_sha256``, else its text's, else its public identity."""

    if row.content_sha256:
        return row.content_sha256
    if row.text:
        return digest_imported_bytes(row.text.encode("utf-8"))
    identity = "\n".join((row.normalized_url, row.company or "", row.title or "", row.location or ""))
    return digest_imported_bytes(identity.encode("utf-8"))


def prefs_digest(prefs: CandidatePrefs) -> str:
    return _hex({
        "titles": list(prefs.titles),
        "countries": list(prefs.countries),
        "visa_sponsorship_required": bool(prefs.visa_sponsorship_required),
        "location": prefs.location,
        "remote_preferred": bool(prefs.remote_preferred),
    })


def cache_key(*, content_sha256: str, resume_digest_sha256: str, prefs_sha256: str, model: str) -> str:
    """sha256(content, resume digest, prefs digest, rank prompt version, digest version, model)."""

    return _hex({
        "content_sha256": content_sha256,
        "resume_digest_sha256": resume_digest_sha256,
        "prefs_sha256": prefs_sha256,
        "prompt_version": PROMPT_VERSION,
        "digest_version": DIGEST_VERSION,
        "model": model,
    })


def _read_cached(path: Path) -> dict[str, object] | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    score, reasons, blockers = value.get("score"), value.get("reasons"), value.get("blockers")
    if type(score) is not int or not 0 <= score <= 100:
        return None
    if not isinstance(reasons, list) or not isinstance(blockers, list):
        return None
    return value


def _write_cached(path: Path, item: Mapping[str, object], *, resolved_model: str | None) -> None:
    payload = {
        "score": item["score"],
        "reasons": list(item["reasons"]),  # type: ignore[arg-type]
        "blockers": list(item["blockers"]),  # type: ignore[arg-type]
        "prompt_version": PROMPT_VERSION,
        "digest_version": DIGEST_VERSION,
        "resolved_model": resolved_model,
    }
    try:
        atomic_write(path, json.dumps(payload, separators=(",", ":")).encode("utf-8"))
    except OSError:
        pass  # a cache write never fails the pass


# --- the pass ------------------------------------------------------------------------------


class _Stop(Exception):
    """No further model call may start (cancel, abort, or a budget)."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Budget:
    max_calls: int
    max_tokens: int | None
    cancel: threading.Event
    calls: int = 0
    tokens: int = 0
    abort_reason: str | None = None
    stop_reasons: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reserve_call(self) -> None:
        with self.lock:
            if self.cancel.is_set():
                reason = "cancelled"
            elif self.abort_reason is not None:
                reason = self.abort_reason
            elif self.calls >= self.max_calls:
                reason = "call_budget"
            elif self.max_tokens is not None and self.tokens >= self.max_tokens:
                reason = "token_budget"
            else:
                self.calls += 1
                return
            if reason not in self.stop_reasons:
                self.stop_reasons.append(reason)
        raise _Stop(reason)

    def spend(self, tokens: int) -> None:
        with self.lock:
            self.tokens += tokens

    def abort(self, reason: str) -> None:
        with self.lock:
            if self.abort_reason is None:
                self.abort_reason = reason


@dataclass(frozen=True)
class _Entry:
    posting_id: str
    row: "PostingRow"
    line: str
    content_sha256: str
    cache_path: Path


@dataclass(frozen=True)
class _Attempt:
    items: list[dict[str, object]] | None
    error: str | None
    usage: BatchUsage
    resolved_model: str | None
    calls: int
    stopped: str | None = None


def _usage_of(result: object) -> BatchUsage:
    normalized = getattr(result, "normalized_usage", None)
    raw = getattr(result, "raw_usage", None) or {}
    cached = raw.get("cached_input_tokens") if isinstance(raw, Mapping) else None
    if cached is None and isinstance(raw, Mapping):
        cached = raw.get("cache_read_input_tokens")
    return BatchUsage(
        getattr(normalized, "input_tokens", None),
        getattr(normalized, "output_tokens", None),
        getattr(normalized, "total_tokens", None),
        cached if type(cached) is int else None,
    )


class _Ranker:
    def __init__(self, *, binding: object, port: object, effort: str | None, candidate: str, budget: _Budget) -> None:
        self._binding = binding
        self._port = port
        self._effort = effort
        self._candidate = candidate
        self._budget = budget

    def _call(self, entries: Sequence[_Entry], *, max_attempts: int) -> _Attempt:
        ids = [entry.posting_id for entry in entries]
        lines = [entry.line for entry in entries]
        error: str | None = None
        usage = BatchUsage()
        resolved: str | None = None
        calls = 0
        for _ in range(max_attempts):
            try:
                self._budget.reserve_call()
            except _Stop as stop:
                return _Attempt(None, error or stop.reason, usage, resolved, calls, stopped=stop.reason)
            calls += 1
            request = self._binding.request(role=_ROLE, prompt=render_rank_prompt(lines, self._candidate, error))  # type: ignore[attr-defined]
            if self._effort is not None:
                request = replace(request, reasoning_effort=self._effort)
            try:
                result = self._port.invoke(request)  # type: ignore[attr-defined]
            except (ModelInvocationError, OSError, TimeoutError) as exc:
                code = getattr(exc, "code", "")
                error = f"model call failed ({code or type(exc).__name__}): {str(exc)[:200]}"
                if code in _ABORT_CODES:
                    self._budget.abort(f"model_unavailable: {code}")
                    return _Attempt(None, error, usage, resolved, calls, stopped=f"model_unavailable: {code}")
                continue
            attempt_usage = _usage_of(result)
            usage = usage.plus(attempt_usage)
            self._budget.spend(attempt_usage.spent)
            resolved = getattr(result, "resolved_model", None) or resolved
            try:
                items = validate_rank_answer(result.output_text, ids)  # type: ignore[attr-defined]
            except RankAnswerError as exc:
                error = str(exc)
                continue
            return _Attempt(items, None, usage, resolved, calls)
        return _Attempt(None, error, usage, resolved, calls)

    def _leaf(
        self,
        batch_id: str,
        entries: Sequence[_Entry],
        attempt: _Attempt,
        seconds: float,
        *,
        split_from: str | None = None,
        unscored_reason: str | None = None,
    ) -> BatchResult:
        postings: list[RankedPosting] = []
        if attempt.items is not None:
            for entry, item in zip(entries, attempt.items):
                _write_cached(entry.cache_path, item, resolved_model=attempt.resolved_model)
                postings.append(RankedPosting(
                    posting_id=entry.posting_id,
                    normalized_url=entry.row.normalized_url,
                    content_sha256=entry.content_sha256,
                    score=int(item["score"]),  # type: ignore[call-overload]
                    reasons=tuple(item["reasons"]),  # type: ignore[arg-type]
                    blockers=tuple(item["blockers"]),  # type: ignore[arg-type]
                    batch_id=batch_id,
                ))
        else:
            reason = unscored_reason or attempt.stopped or f"invalid: {attempt.error}"
            postings = [_unscored(entry, reason, batch_id) for entry in entries]
        return BatchResult(
            batch_id=batch_id,
            posting_ids=tuple(entry.posting_id for entry in entries),
            source="model",
            valid=attempt.items is not None,
            attempts=attempt.calls,
            seconds=round(seconds, 3),
            usage=attempt.usage,
            error=attempt.error,
            split_from=split_from,
            resolved_model=attempt.resolved_model,
            postings=tuple(postings),
        )

    def run_batch(self, batch_id: str, entries: Sequence[_Entry]) -> list[BatchResult]:
        """One batch: an attempt + one retry, then ONE split into halves (one attempt each)."""

        started = time.monotonic()
        attempt = self._call(entries, max_attempts=2)
        if attempt.items is not None or attempt.stopped is not None or len(entries) < 2:
            return [self._leaf(batch_id, entries, attempt, time.monotonic() - started)]
        parent = BatchResult(
            batch_id=batch_id,
            posting_ids=tuple(entry.posting_id for entry in entries),
            source="model",
            valid=False,
            attempts=attempt.calls,
            seconds=round(time.monotonic() - started, 3),
            usage=attempt.usage,
            error=attempt.error,
            split=True,
            resolved_model=attempt.resolved_model,
        )
        results = [parent]
        middle = len(entries) // 2
        for suffix, half in (("a", entries[:middle]), ("b", entries[middle:])):
            half_started = time.monotonic()
            half_attempt = self._call(half, max_attempts=1)
            results.append(self._leaf(f"{batch_id}{suffix}", half, half_attempt, time.monotonic() - half_started, split_from=batch_id))
        return results


def _unscored(entry: _Entry, reason: str, batch_id: str | None) -> RankedPosting:
    return RankedPosting(
        posting_id=entry.posting_id,
        normalized_url=entry.row.normalized_url,
        content_sha256=entry.content_sha256,
        score=None,
        batch_id=batch_id,
        unscored_reason=reason,
    )


@dataclass(frozen=True)
class _ResolvedPort:
    binding: object
    port: object
    configured_target: str
    model: str
    effort_applied: str | None


def _adapter_kind(model_target: "str | ModelTarget") -> str:
    return str(getattr(model_target, "value", model_target))


def _resolve(config: "GigAIConfig", adapter_kind: str, *, home_root: Path) -> _ResolvedPort:
    """The assess path's adapter resolution, then the ranker's call mode on top.

    Same seams as ``quick_assess._resolve_binding``: the fixture transport is
    installed first (inert unless ``GIGAI_SCOUT_FIND_JOBS_TEST_MODEL=1``) and
    ``proposal_execution.resolve_model_adapter`` is looked up as a module
    attribute at call time so tests and the test transport intercept it.
    """

    from .. import proposal_execution
    from . import bindings

    bindings._patch_test_model_transport(config)
    configured = proposal_execution._resolve_configured_target_name_for_adapter(config, adapter_kind)
    binding = proposal_execution.resolve_model_adapter(config, configured, home_root=home_root)
    port = binding.port
    effort: str | None = None
    if isinstance(port, ClaudeCLIAdapter):
        port = port.lean_copy()
        effort = RANK_EFFORT
    elif adapter_kind in _CLI_ADAPTERS:
        effort = RANK_EFFORT
    return _ResolvedPort(binding, port, configured, binding.current.target.model, effort)


def _effort_applied(resolved: _ResolvedPort, adapter_kind: str) -> str | None:
    """What the adapter actually does with the requested effort."""

    if isinstance(resolved.port, ClaudeCLIAdapter):
        return resolved.effort_applied
    if adapter_kind == "codex_cli" and getattr(resolved.port, "honours_reasoning_effort", False):
        return resolved.effort_applied
    return "provider-default"


def rank_postings(
    rows: Sequence["PostingRow"],
    *,
    resume_text: str,
    prefs: CandidatePrefs,
    model_target: "str | ModelTarget",
    home_root: Path,
    on_batch: Callable[[BatchResult], None] | None = None,
    cancel: threading.Event | None = None,
    config: "GigAIConfig | None" = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
    concurrency: int = DEFAULT_CONCURRENCY,
    max_calls: int = DEFAULT_MAX_CALLS,
    max_tokens: int | None = None,
) -> RankResult:
    """Rank ``rows`` for one candidate; one :class:`RankedPosting` per row, in input order.

    ``model_target`` is the adapter kind (``codex_cli`` -- the operator's
    ``default_model_target`` -- ``claude_cli``, ``ollama_local``,
    ``openrouter_api``), mapped to the configured target exactly as quick
    assess and the assess node map it. See the module docstring for the
    cache, batching, bounds, streaming, cancel and fail-open contract.
    """

    if batch_size < 1 or concurrency < 1 or max_calls < 0:
        raise ValueError("batch_size and concurrency must be positive; max_calls must not be negative")
    started = time.monotonic()
    cancel = cancel if cancel is not None else threading.Event()
    adapter_kind = _adapter_kind(model_target)
    home_root = Path(home_root)
    candidate = resume_digest(resume_text, prefs)
    resume_sha = _hex({"resume_digest": candidate})
    prefs_sha = prefs_digest(prefs)
    ids = [f"p{index}" for index in range(len(rows))]

    def finish(
        postings: Sequence[RankedPosting],
        batches: Sequence[BatchResult],
        *,
        resolved: _ResolvedPort | None,
        reason: str | None,
        skipped: bool = False,
    ) -> RankResult:
        unscored = sum(not item.scored for item in postings)
        if unscored and cancel.is_set():
            status = "cancelled"
        elif skipped:
            status = "skipped"
        elif unscored:
            status = "partial"
        else:
            status = "complete"
        if status != "complete" and reason is None:
            reason = f"{unscored} of {len(postings)} postings unscored"
        resolved_models = [batch.resolved_model for batch in batches if batch.resolved_model]
        return RankResult(
            model_target=adapter_kind,
            configured_target=resolved.configured_target if resolved else None,
            model=resolved.model if resolved else None,
            resolved_model=resolved_models[0] if resolved_models else None,
            effort=RANK_EFFORT,
            effort_applied=_effort_applied(resolved, adapter_kind) if resolved else None,
            batch_size=batch_size,
            concurrency=concurrency,
            max_calls=max_calls,
            max_tokens=max_tokens,
            postings=tuple(postings),
            batches=tuple(batches),
            status=status,
            fail_open_reason=None if status == "complete" else reason,
            seconds=round(time.monotonic() - started, 3),
        )

    def skip_all(reason: str) -> RankResult:
        postings = [
            RankedPosting(pid, row.normalized_url, content_digest(row), None, unscored_reason=reason)
            for pid, row in zip(ids, rows)
        ]
        return finish(postings, [], resolved=None, reason=reason, skipped=True)

    if not rows:
        return finish([], [], resolved=None, reason=None)
    if cancel.is_set():
        return skip_all("cancelled")
    try:
        from ...config import load_config

        active = config if config is not None else load_config(home_root)
        resolved = _resolve(active, adapter_kind, home_root=home_root)
    # Config, target and adapter-factory errors are all ValueErrors
    # (ConfigurationError, ModelTargetResolutionError, AdapterFactoryError,
    # ScoutProposalExecutionError); a missing CLI executable is a
    # ModelInvocationError. Each skips the pass; anything else is a bug.
    except (ValueError, KeyError, OSError, ModelInvocationError) as exc:
        return skip_all(f"model_target_unavailable: {str(exc)[:200] or type(exc).__name__}")

    cache_model = f"{adapter_kind}:{resolved.model}"
    root = cache_dir(home_root)
    entries: list[_Entry] = []
    for pid, row in zip(ids, rows):
        content = content_digest(row)
        key = cache_key(content_sha256=content, resume_digest_sha256=resume_sha, prefs_sha256=prefs_sha, model=cache_model)
        entries.append(_Entry(pid, row, posting_digest(row, pid), content, root / f"{key}.json"))

    by_id: dict[str, RankedPosting] = {}
    landed: list[BatchResult] = []
    pending: list[_Entry] = []
    for entry in entries:
        hit = _read_cached(entry.cache_path)
        if hit is None:
            pending.append(entry)
            continue
        by_id[entry.posting_id] = RankedPosting(
            posting_id=entry.posting_id,
            normalized_url=entry.row.normalized_url,
            content_sha256=entry.content_sha256,
            score=int(hit["score"]),  # type: ignore[call-overload]
            reasons=tuple(str(item) for item in hit["reasons"]),  # type: ignore[union-attr]
            blockers=tuple(str(item) for item in hit["blockers"]),  # type: ignore[union-attr]
            batch_id="cache",
            cached=True,
        )

    def land(batch: BatchResult) -> None:
        landed.append(batch)
        for item in batch.postings:
            by_id[item.posting_id] = item
        if on_batch is not None:
            on_batch(batch)

    if by_id:
        cached_ids = tuple(pid for pid in ids if pid in by_id)
        land(BatchResult(
            batch_id="cache",
            posting_ids=cached_ids,
            source="cache",
            valid=True,
            attempts=0,
            seconds=0.0,
            postings=tuple(by_id[pid] for pid in cached_ids),
        ))

    budget = _Budget(max_calls=max_calls, max_tokens=max_tokens, cancel=cancel)
    ranker = _Ranker(binding=resolved.binding, port=resolved.port, effort=resolved.effort_applied, candidate=candidate, budget=budget)
    batches = [
        (f"b{number:03d}", pending[start:start + batch_size])
        for number, start in enumerate(range(0, len(pending), batch_size))
    ]
    callback_error: BaseException | None = None
    try:
        if batches:
            # At most K batches are ever submitted: the next one is submitted
            # only after one lands (and ``on_batch`` saw it), so a cancel or a
            # failing callback lets no further call start.
            queue = iter(batches)
            with ThreadPoolExecutor(max_workers=min(concurrency, len(batches)), thread_name_prefix="gigai-rank") as pool:
                futures: set[Future[list[BatchResult]]] = set()

                def submit_next() -> None:
                    item = next(queue, None)
                    if item is not None:
                        futures.add(pool.submit(ranker.run_batch, *item))

                for _ in range(min(concurrency, len(batches))):
                    submit_next()
                while futures:
                    done, futures = wait(futures, return_when=FIRST_COMPLETED)
                    for future in done:
                        submit_next()
                        for batch in future.result():
                            if callback_error is None:
                                try:
                                    land(batch)
                                except BaseException as exc:  # noqa: BLE001 - re-raised after the pool stops calling
                                    callback_error = exc
                                    budget.abort("on_batch callback failed")
                            else:
                                landed.append(batch)
    finally:
        if hasattr(resolved.binding, "close"):
            resolved.binding.close()  # type: ignore[attr-defined]
    if callback_error is not None:
        raise callback_error

    postings = [by_id[pid] for pid in ids]
    reason: str | None = None
    if budget.abort_reason is not None:
        reason = budget.abort_reason
    elif budget.stop_reasons:
        reason = budget.stop_reasons[0]
    unscored = sum(not item.scored for item in postings)
    if reason is not None and unscored:
        reason = f"{reason}: {unscored} of {len(postings)} postings unscored"
    return finish(postings, landed, resolved=resolved, reason=reason)


__all__ = [
    "BatchResult",
    "BatchUsage",
    "CandidatePrefs",
    "DEFAULT_BATCH_SIZE",
    "DEFAULT_CONCURRENCY",
    "DEFAULT_MAX_CALLS",
    "PROMPT_VERSION",
    "RANK_EFFORT",
    "RANK_SCHEMA_VERSION",
    "RankAnswerError",
    "RankResult",
    "RankedPosting",
    "SMALL_MACHINE_CONCURRENCY",
    "cache_dir",
    "cache_key",
    "content_digest",
    "ordering_key",
    "prefs_digest",
    "rank_postings",
    "ranked_order",
    "render_rank_prompt",
    "validate_rank_answer",
]
