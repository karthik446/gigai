"""0.1.10.7 E: what every Scout model call cost, recorded once and read back as averages.

One recorder, ``record_call``, used by every call site through a
``CallMeter``: assess (the job page's Assess / Re-assess, assess-all, an
answer's re-assessment, a find-jobs run's assess step), rank, tag, tailor,
the resume extract and the interview-prep categories. A call site never
writes a row of its own.

Where it is stored: the ``model_call`` table of the project's
``pipeline.sqlite`` (``pipeline/store.py``), beside ``step_run``, with the
same metrics columns and the same checks. It is in the project's own Scout
folder, next to the quick-assess and tailored-resume stores the calls write
to. A per-call row and not a field on the produced record, because a failed
call produces no record, a rank or tag call produces many, and an average
over files would mean reading every stored assessment.

No text, ever: a row holds the call's kind, ids (profile, adapter, model),
the job identity, a digest of the prompt, numbers and bounded codes. The
prompt, the answer, an error message, resume or posting text never reach it:
``record_call`` takes the request and the result and reads only their
numbers, and the store refuses any value that is not shaped like its column.

Recording never fails a model call: a project that is not bound, a file that
cannot be written or a value that does not fit is dropped (logged at debug).

Tokens are comparable across adapters: ``input_tokens`` is everything the
model read (Claude reports cached input apart from ``input_tokens``; it is
added back here), ``cached_tokens`` the part of it served from cache.

The read side (``metrics_report``, ``estimate``) is what ``GET /api/metrics``
and ``gigai scout metrics`` answer, and what ``scout new`` will size its
approval question with.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
import hashlib
import logging
import math
from pathlib import Path
import threading
import time

from .pipeline.store import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    CallTotals,
    PipelineStore,
    StepMetrics,
    fits,
    pipeline_path,
)

SCHEMA_VERSION = "scout-metrics:1"

KIND_ASSESS = "assess"
KIND_RANK = "rank"
KIND_TAG = "tag"
KIND_TAILOR = "tailor"
KIND_EXTRACT = "extract"
KIND_INTERVIEW = "interview"
KINDS: tuple[str, ...] = (KIND_ASSESS, KIND_RANK, KIND_TAG, KIND_TAILOR, KIND_EXTRACT, KIND_INTERVIEW)

#: ``error_code`` of a call that raised with no code of its own, timed out, or answered something unusable.
ERROR_FAILED = "model_call_failed"
ERROR_TIMEOUT = "model_timeout"
ERROR_INVALID_OUTPUT = "model_output_invalid"

_LOGGER = logging.getLogger("gigai.scout.call_metrics")
_API_ADAPTERS = frozenset({"openai_api", "openrouter_api"})


class CallMetricsError(ValueError):
    """A metrics read was asked for something it does not know; ``code`` is the error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# --- one call -> one row -----------------------------------------------------------------


def lane_for(adapter: str, target_name: str | None = None) -> str:
    """The pipeline lane (``store.py``'s vocabulary) a call through ``adapter`` runs in."""

    if adapter in ("claude_cli", "codex_cli"):
        return adapter
    if adapter == "ollama_local":
        return "ollama"
    if adapter in _API_ADAPTERS:
        lane = f"api:{(target_name or adapter).lower()}"
        return lane if fits("lane", lane) else f"api:{adapter}"
    return "local"


def _count(value: object) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _model(value: object) -> str | None:
    """``value`` when it is shaped like one model id (the first, when an adapter names several)."""

    if not isinstance(value, str):
        return None
    first = value.split(",", 1)[0].strip()
    return first if fits("model", first) else None


def usage_metrics(adapter: str, result: object, *, requested_model: str | None = None) -> StepMetrics:
    """``result``'s numbers as a ``StepMetrics`` (the shape a pipeline step's attempt also records)."""

    normalized = getattr(result, "normalized_usage", None)
    raw = getattr(result, "raw_usage", None)
    raw = raw if isinstance(raw, Mapping) else {}
    input_tokens = _count(getattr(normalized, "input_tokens", None))
    output_tokens = _count(getattr(normalized, "output_tokens", None))
    cache_read = _count(raw.get("cache_read_input_tokens"))
    cache_written = _count(raw.get("cache_creation_input_tokens"))
    if cache_read is not None or cache_written is not None:
        # Claude: ``input_tokens`` leaves the cached input out.
        cached = cache_read
        if input_tokens is not None:
            input_tokens += (cache_read or 0) + (cache_written or 0)
    else:
        cached = _count(raw.get("cached_input_tokens"))  # codex
        if cached is None:
            details = raw.get("prompt_tokens_details") or raw.get("input_tokens_details")
            cached = _count(details.get("cached_tokens")) if isinstance(details, Mapping) else None
    cost = getattr(result, "cost_usd", None)
    if cost is None:
        cost = raw.get("cost")  # OpenRouter's usage accounting
    if not isinstance(cost, (int, float)) or isinstance(cost, bool) or not 0 <= cost < math.inf:
        cost = None
    return StepMetrics(
        adapter=adapter if fits("id", adapter) else None,
        model=_model(getattr(result, "resolved_model", None)) or _model(requested_model),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached,
        cost_usd=None if cost is None else float(cost),
        cost_status="unavailable" if cost is None else "provider_reported",
    )


def _timed_out(exc: BaseException) -> bool:
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, TimeoutError) or type(current).__name__ in ("TimeoutExpired", "TimeoutException", "ReadTimeout"):
            return True
        current = current.__cause__ or current.__context__
    return "timed out" in str(exc).lower()


def error_code_of(exc: BaseException) -> str:
    """A bounded code for a call that raised: never its message."""

    if _timed_out(exc):
        return ERROR_TIMEOUT
    code = getattr(exc, "code", None)
    return code if isinstance(code, str) and fits("code", code) else ERROR_FAILED


def _open(home_root: Path, target: Path) -> PipelineStore:
    return PipelineStore(pipeline_path(Path(home_root), Path(target)))


def record_call(
    home_root: Path | None,
    target: Path | None,
    *,
    kind: str,
    adapter: str,
    seconds: float,
    target_name: str | None = None,
    request: object | None = None,
    result: object | None = None,
    error: BaseException | None = None,
    profile_id: str | None = None,
    job: str | None = None,
    items: int = 1,
    started_at: float | None = None,
) -> int | None:
    """Store one model call's metrics; the row's id, or ``None`` when nothing was stored.

    ``result`` is what the adapter returned, ``error`` what it raised: only
    numbers, the model id and a bounded error code are read from them, and
    only a digest from the request's prompt. A ``profile_id`` or ``job`` that
    is not shaped like one (an ephemeral resume, a job with no identity) is
    left out, not stored as it is.
    """

    if home_root is None or target is None:
        return None
    try:
        requested = getattr(request, "model", None)
        if error is None and result is not None:
            metrics = usage_metrics(adapter, result, requested_model=requested)
        else:
            metrics = StepMetrics(adapter=adapter if fits("id", adapter) else None, model=_model(requested))
        prompt = getattr(request, "prompt", None)
        store = _open(home_root, target)
        try:
            return store.record_call(
                kind=kind,
                lane=lane_for(adapter, target_name),
                seconds=max(0.0, float(seconds)),
                outcome=OUTCOME_OK if error is None else OUTCOME_ERROR,
                error_code=None if error is None else error_code_of(error),
                metrics=metrics,
                profile_id=profile_id if fits("id", profile_id) else None,
                job=job if fits("job", job) else None,
                items=items if type(items) is int and items >= 0 else 1,
                input_digest=(
                    "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest() if isinstance(prompt, str) else None
                ),
                started_at=started_at,
            )
        finally:
            store.close()
    except Exception as exc:  # noqa: BLE001 - metrics never fail the call they describe
        _LOGGER.debug("model call metrics were not recorded (%s)", type(exc).__name__)
        return None


@dataclass(frozen=True)
class CallMeter:
    """What every call of one flow shares: its kind, its project, its adapter, who and what it is for.

    ``invoke`` times one adapter call and records it (``record_call``);
    ``bind`` gives a binding whose port does that on every ``invoke``, for the
    flows that hand a binding to a shared loop (``assessment_core``). With no
    ``home_root``/``target`` the call is made and nothing is recorded.
    """

    kind: str
    adapter: str
    home_root: Path | None = None
    target: Path | None = None
    target_name: str | None = None
    profile_id: str | None = None
    job: str | None = None
    _last: threading.local = field(default_factory=threading.local, init=False, repr=False, compare=False)

    def invoke(self, port: object, request: object, *, items: int = 1, job: str | None = None) -> object:
        started_at = time.time()
        started = time.monotonic()
        try:
            result = port.invoke(request)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - recorded with a bounded code, then raised again as it was
            self._last.call_id = self._record(request, None, exc, time.monotonic() - started, started_at, items, job)
            raise
        self._last.call_id = self._record(request, result, None, time.monotonic() - started, started_at, items, job)
        return result

    def _record(
        self, request: object, result: object | None, error: BaseException | None, seconds: float, started_at: float,
        items: int, job: str | None,
    ) -> int | None:
        return record_call(
            self.home_root, self.target, kind=self.kind, adapter=self.adapter, seconds=seconds,
            target_name=self.target_name, request=request, result=result, error=error, profile_id=self.profile_id,
            job=job if job is not None else self.job, items=items, started_at=started_at,
        )

    def invalid_output(self) -> None:
        """This thread's last call answered, but with something the caller could not use: count it as an error."""

        call_id = getattr(self._last, "call_id", None)
        if call_id is None or self.home_root is None or self.target is None:
            return
        self._last.call_id = None
        try:
            store = _open(self.home_root, self.target)
            try:
                store.fail_call(call_id, ERROR_INVALID_OUTPUT)
            finally:
                store.close()
        except Exception as exc:  # noqa: BLE001 - as record_call
            _LOGGER.debug("model call metrics were not updated (%s)", type(exc).__name__)

    def bind(self, binding: object, *, job: str | None = None) -> "MeteredBinding":
        """``binding`` with a metered port; ``job`` names what this binding's calls are about."""

        meter = self if job is None else replace(self, job=job)
        return MeteredBinding(binding, MeteredPort(binding.port, meter))  # type: ignore[attr-defined]


class MeteredPort:
    """A port whose every ``invoke`` is recorded; everything else is the wrapped port's."""

    def __init__(self, inner: object, meter: CallMeter) -> None:
        self._inner = inner
        self._meter = meter

    def invoke(self, request: object) -> object:
        return self._meter.invoke(self._inner, request)

    def invalid_output(self) -> None:
        self._meter.invalid_output()

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


class MeteredBinding:
    """A binding whose ``port`` is metered; ``request``, ``close`` and the rest are the wrapped binding's."""

    def __init__(self, inner: object, port: MeteredPort) -> None:
        self._inner = inner
        self.port = port

    def __getattr__(self, name: str) -> object:
        return getattr(self._inner, name)


def note_invalid_output(port: object) -> None:
    """Tell a metered ``port`` its last answer was unusable; nothing for any other port."""

    mark = getattr(port, "invalid_output", None)
    if callable(mark):
        mark()


# --- the read side ---------------------------------------------------------------------


def _average(total: float, count: int, digits: int | None = None) -> float | int | None:
    if count <= 0:
        return None
    value = total / count
    return round(value) if digits is None else round(value, digits)


@dataclass(frozen=True)
class _Sum:
    """``CallTotals`` of one or more groups, added up."""

    calls: int = 0
    ok: int = 0
    items: int = 0
    input_tokens: int = 0
    input_n: int = 0
    output_tokens: int = 0
    output_n: int = 0
    cached_tokens: int = 0
    cached_n: int = 0
    cost_usd: float = 0.0
    cost_n: int = 0
    seconds: float = 0.0
    last_at: str = ""

    def plus(self, row: CallTotals) -> "_Sum":
        return _Sum(
            self.calls + row.calls, self.ok + row.ok, self.items + row.items,
            self.input_tokens + row.input_tokens, self.input_n + row.input_n,
            self.output_tokens + row.output_tokens, self.output_n + row.output_n,
            self.cached_tokens + row.cached_tokens, self.cached_n + row.cached_n,
            self.cost_usd + row.cost_usd, self.cost_n + row.cost_n,
            self.seconds + row.seconds, max(self.last_at, row.last_at),
        )

    def to_json(self) -> dict[str, object]:
        """The averages. Tokens and cost are over the calls that reported them, seconds over the ``ok`` calls."""

        avg_input = _average(self.input_tokens, self.input_n)
        avg_output = _average(self.output_tokens, self.output_n)
        errors = self.calls - self.ok
        return {
            "calls": self.calls,
            "errors": errors,
            "error_rate": round(errors / self.calls, 4) if self.calls else None,
            "items": self.items,
            "avg_input_tokens": avg_input,
            "avg_output_tokens": avg_output,
            "avg_cached_tokens": _average(self.cached_tokens, self.cached_n),
            "avg_tokens": None if avg_input is None and avg_output is None else (avg_input or 0) + (avg_output or 0),
            "avg_seconds": _average(self.seconds, self.ok, 3),
            "avg_cost_usd": _average(self.cost_usd, self.cost_n, 6),
            "last_at": self.last_at or None,
        }


def _matches(row: CallTotals, model: str | None) -> bool:
    return model is None or model in (row.adapter, row.model)


def _totals(home_root: Path, target: Path | None, *, kind: str | None, model: str | None) -> list[CallTotals]:
    if kind is not None and kind not in KINDS:
        raise CallMetricsError("invalid_value", f"kind must be one of: {', '.join(KINDS)}")
    if model is not None and not fits("model", model):
        raise CallMetricsError("invalid_value", "model must be a model target (codex_cli, claude_cli, ...) or a model id")
    if target is None:
        return []  # no project yet
    path = pipeline_path(Path(home_root), Path(target))
    if not path.is_file():
        return []  # nothing recorded yet: a read never creates the file
    store = PipelineStore(path)
    try:
        return [row for row in store.call_totals(kind=kind) if _matches(row, model)]
    finally:
        store.close()


def metrics_report(
    home_root: Path, target: Path | None, *, kind: str | None = None, model: str | None = None
) -> dict[str, object]:
    """The averages per ``(kind, model_target, model)``, and per ``(kind, model_target)`` to compare models.

    ``model`` filters on the model target (the adapter kind: ``codex_cli``,
    ``claude_cli``, ...) or on the model id the adapter answered with.
    ``aggregates`` has one entry per model id; ``comparison`` adds the model
    ids of one target together, which is what a person picks between.
    """

    rows = _totals(home_root, target, kind=kind, model=model)
    aggregates = [
        {"kind": row.kind, "model_target": row.adapter, "model": row.model, **_Sum().plus(row).to_json()} for row in rows
    ]
    grouped: dict[tuple[str, str | None], tuple[_Sum, list[str]]] = {}
    for row in rows:
        total, models = grouped.get((row.kind, row.adapter), (_Sum(), []))
        if row.model is not None and row.model not in models:
            models.append(row.model)
        grouped[(row.kind, row.adapter)] = (total.plus(row), models)
    comparison = [
        {"kind": group_kind, "model_target": adapter, "models": models, **total.to_json()}
        for (group_kind, adapter), (total, models) in grouped.items()
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": kind,
        "model": model,
        "aggregates": aggregates,
        "comparison": comparison,
    }


def estimate(kind: str, model: str | None, n: int, *, home_root: Path, target: Path | None) -> dict[str, object]:
    """What ``n`` more items of ``kind`` on ``model`` would take, from the recorded averages.

    ``{calls, tokens, seconds, cost}``: each is ``None`` when the history
    cannot say (no call of this kind on this model yet, or none that reported
    it). ``seconds`` is the calls one after another; ``basis_calls`` is how
    many recorded calls the averages come from.
    """

    if type(n) is not int or n < 0:
        raise CallMetricsError("invalid_value", "n must be a non-negative integer")
    total = _Sum()
    for row in _totals(home_root, target, kind=kind, model=model):
        total = total.plus(row)
    answer: dict[str, object] = {
        "kind": kind, "model": model, "n": n, "calls": None, "tokens": None, "seconds": None, "cost": None,
        "basis_calls": total.calls,
    }
    if total.calls == 0 or total.items == 0:
        return answer
    averages = total.to_json()
    calls = math.ceil(n * total.calls / total.items)
    answer["calls"] = calls
    if averages["avg_tokens"] is not None:
        answer["tokens"] = calls * int(averages["avg_tokens"])  # type: ignore[call-overload]
    if averages["avg_seconds"] is not None:
        answer["seconds"] = round(calls * float(averages["avg_seconds"]), 1)  # type: ignore[arg-type]
    if averages["avg_cost_usd"] is not None:
        answer["cost"] = round(calls * float(averages["avg_cost_usd"]), 4)  # type: ignore[arg-type]
    return answer


__all__ = [
    "ERROR_FAILED",
    "ERROR_INVALID_OUTPUT",
    "ERROR_TIMEOUT",
    "KINDS",
    "KIND_ASSESS",
    "KIND_EXTRACT",
    "KIND_INTERVIEW",
    "KIND_RANK",
    "KIND_TAG",
    "KIND_TAILOR",
    "SCHEMA_VERSION",
    "CallMeter",
    "CallMetricsError",
    "MeteredBinding",
    "MeteredPort",
    "error_code_of",
    "estimate",
    "lane_for",
    "metrics_report",
    "note_invalid_output",
    "record_call",
    "usage_metrics",
]
