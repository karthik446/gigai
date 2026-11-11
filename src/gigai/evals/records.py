"""A gig's eval run records: ``<home>/gigs/<gig>/evals.sqlite`` (gigai 2, V1).

One SQLite file per gig, next to (never inside) the gig's own stores, with five tables (``migrations/0001_evals.sql``):
``runs`` (what was measured, on which dataset slice, under which pre-registration), ``traces`` (one model call or one
direct ``ctx.trace`` of one step on one item), ``scores`` (one scorer's verdict on one trace), ``labels`` and
``judge_validations``. The file carries its schema version in ``PRAGMA user_version`` (1 today) and is walked there by
``gigai.sdk.store.migrate_connection``; a newer file is refused, never touched.

``evals_db_path`` is the only place that knows where the file is. This module opens it by that path and needs nothing
but the gig-folder resolver of ``gigai.gigs.home``; ``ctx.store.db(gig, "evals")`` (``gigai.sdk.store``) reaches the
same file through the same function.

A trace stores the input and the raw output with their SHA-256, so a later scorer re-scores it with no model call
(``replay``). ``model_call`` is the adapter hook: from a binding, its request and the result it returned, it fills
the model identity, the child argv (CLI adapters only; never a credential), the sampling settings the adapter
carried, the token usage and the timing. ``cost_usd`` stays NULL until P12 prices calls.
"""

from __future__ import annotations

import json
import os
import sqlite3
import stat
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..canonical import digest_imported_bytes
from ..gigs.home import check_name, ensure_gig_folder, gig_folder

if TYPE_CHECKING:
    from ..adapters.factory import ModelAdapterBinding
    from ..adapters.port import InvocationRequest, InvocationResult

EVALS_FILE = "evals.sqlite"
#: The ``user_version`` this gigai writes: one per file in ``migrations/``.
SCHEMA_VERSION = 1
_MIGRATIONS = ("0001_evals.sql",)
PARSE_STATUSES = frozenset({"ok", "invalid", "not_json", "not_parsed"})
OUTCOMES = frozenset({"succeeded", "failed"})

#: What a gig's postprocess does with one raw output: the parsed value and its ``PARSE_STATUSES`` entry.
Postprocess = Callable[[str], tuple[Any, str]]


class EvalsError(Exception):
    """An eval record that cannot be written or read as asked."""


def evals_db_path(gig: str, home: Path) -> Path:
    """Where the eval store of ``gig`` lives under the gigai home ``home``: ``<home>/gigs/<gig>/evals.sqlite``."""

    return gig_folder(home, gig) / EVALS_FILE


# --- records ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Dataset:
    """The dataset slice a run measured: which dataset, which bytes, and how the slice was cut from it."""

    id: str
    version: str
    sha256: str
    #: Filter, stratification and count, as the gig cut the slice.
    slice_recipe: Mapping[str, Any]
    #: The sampling seed; ``None`` when the slice is not sampled.
    seed: int | None = None


@dataclass(frozen=True)
class Prereg:
    """What a run promised before it ran: the metric that decides, the rule, the margin and the planned size."""

    primary_metric: str
    pass_rule: str
    margin: float
    n_planned: int
    registered_at: str
    guard_metric: str | None = None


@dataclass(frozen=True)
class RunRecord:
    """One eval run of gig ``gig``; its traces point at ``run_id``."""

    run_id: str
    gig: str
    manifest_digest: str
    dataset: Dataset
    prereg: Prereg | None = None
    schema_version: int = SCHEMA_VERSION
    created_at: str = field(default_factory=lambda: _now())

    def __post_init__(self) -> None:
        check_name("gig", self.gig)
        _require_text("run_id", self.run_id)


@dataclass(frozen=True)
class ItemRef:
    """The dataset item a trace answered; items sharing a ``cluster_id`` are not independent."""

    id: str
    version: str
    cluster_id: str | None = None


@dataclass(frozen=True)
class PromptRef:
    """The prompt template a step used: its id, version and the SHA-256 of its text."""

    id: str
    version: str
    sha256: str

    @classmethod
    def of(cls, id: str, version: str, text: str) -> PromptRef:
        """The reference of template ``text``."""

        return cls(id, version, _sha256(text))


@dataclass(frozen=True)
class Settings:
    """The sampling settings a call carried; a setting the adapter could not carry is ``None`` and in ``unsupported``."""

    temperature: float | None = None
    top_p: float | None = None
    seed: int | None = None
    output_mode: str | None = None
    schema_sha256: str | None = None
    #: ``{"attempts": n, "backoff_seconds": s}``.
    retry_policy: Mapping[str, Any] | None = None
    unsupported: tuple[str, ...] = ()


@dataclass(frozen=True)
class ModelCall:
    """What the adapter hook (``model_call``) records about one model call."""

    model_id: str  # the configured model target
    model_provider: str  # the endpoint's adapter kind
    model_endpoint: str
    model_adapter: str  # the adapter class identity
    model_name: str  # the model the request named
    resolved_model: str
    adapter_version: str | None
    argv: tuple[str, ...]
    settings: Settings
    #: ``{"input_tokens", "output_tokens", "total_tokens", "raw"}``; a token count the adapter did not report is null.
    usage: Mapping[str, Any]
    latency_ms: int | None
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True)
class TraceRecord:
    """One step of run ``run_id`` on one item: input, raw output, the parsed output, and the call that produced it."""

    trace_id: str
    run_id: str
    step_id: str
    item: ItemRef
    input: str
    output_raw_text: str
    repeat_index: int = 0
    attempt_index: int = 0
    prompt: PromptRef | None = None
    output_parsed: Any = None
    parse_status: str = "not_parsed"
    model: ModelCall | None = None
    outcome: str = "succeeded"
    #: ``{"code", "message", "retryable"}`` when the call failed; ``None`` when it succeeded.
    error: Mapping[str, Any] | None = None
    recorded_at: str = field(default_factory=lambda: _now())

    def __post_init__(self) -> None:
        _require_text("trace_id", self.trace_id)
        _require_text("run_id", self.run_id)
        check_name("step", self.step_id)
        if not isinstance(self.input, str) or not isinstance(self.output_raw_text, str):
            raise EvalsError("a trace's input and output are text")
        for name in ("repeat_index", "attempt_index"):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise EvalsError(f"trace {name} must be a non-negative integer, not {value!r}")
        if self.parse_status not in PARSE_STATUSES:
            raise EvalsError(f"trace parse_status must be one of {sorted(PARSE_STATUSES)}, not {self.parse_status!r}")
        if self.outcome not in OUTCOMES:
            raise EvalsError(f"trace outcome must be one of {sorted(OUTCOMES)}, not {self.outcome!r}")
        if (self.outcome == "failed") != (self.error is not None):
            raise EvalsError("a failed trace carries an error and a succeeded one does not")

    @property
    def input_sha256(self) -> str:
        return _sha256(self.input)

    @property
    def output_sha256(self) -> str:
        return _sha256(self.output_raw_text)


@dataclass(frozen=True)
class ScoreRecord:
    """One scorer's verdict on one trace; the judge fields stay ``None`` for a code scorer."""

    score_id: str
    trace_id: str
    scorer_id: str
    scorer_version: str
    value: float
    explanation: str | None = None
    judge_model: str | None = None
    judge_prompt_sha256: str | None = None
    position_swapped: bool | None = None
    judge_invocation_id: str | None = None
    recorded_at: str = field(default_factory=lambda: _now())

    def __post_init__(self) -> None:
        _require_text("score_id", self.score_id)
        _require_text("trace_id", self.trace_id)
        if isinstance(self.value, bool) or not isinstance(self.value, int | float):
            raise EvalsError(f"score value must be a number, not {self.value!r}")


@dataclass(frozen=True)
class ReplayRow:
    """One stored trace re-scored by a postprocess, with no model call."""

    trace_id: str
    step_id: str
    item_id: str
    item_version: str
    repeat_index: int
    attempt_index: int
    output_sha256: str
    output_parsed: Any
    parse_status: str


def new_id(kind: str) -> str:
    """A fresh record id: ``run_<uuid4>``, ``trace_<uuid4>`` or ``score_<uuid4>``."""

    if kind not in {"run", "trace", "score"}:
        raise ValueError(f"unknown record kind {kind!r}")
    return f"{kind}_{uuid.uuid4()}"


# --- the adapter hook ------------------------------------------------------------------------------------------


def model_call(binding: ModelAdapterBinding, request: InvocationRequest, result: InvocationResult) -> ModelCall:
    """What a trace records about the call ``binding.port.invoke(request)`` that returned ``result``."""

    from ..adapters.factory import adapter_identity

    unsupported = tuple(str(name) for name in result.raw_usage.get("unsupported", ()))  # type: ignore[union-attr]

    def carried(name: str) -> Any:
        return None if name in unsupported else getattr(request, name)

    schema, retry = carried("schema"), carried("retry_policy")
    usage = result.normalized_usage
    return ModelCall(
        model_id=binding.current.target.name,
        model_provider=binding.current.endpoint.adapter,
        model_endpoint=binding.current.endpoint.name,
        model_adapter=adapter_identity(binding.port),
        model_name=request.model,
        resolved_model=result.resolved_model,
        adapter_version=result.adapter_version,
        argv=binding.argv(request),
        settings=Settings(
            temperature=carried("temperature"),
            top_p=carried("top_p"),
            seed=carried("seed"),
            output_mode=carried("output_mode"),
            schema_sha256=None if schema is None else _sha256(_json(schema)),
            retry_policy=None if retry is None else {"attempts": retry.attempts, "backoff_seconds": retry.backoff_seconds},
            unsupported=unsupported,
        ),
        usage={
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "total_tokens": usage.total_tokens,
            "raw": result.raw_usage,
        },
        latency_ms=result.latency_ms,
        started_at=result.started_at,
        completed_at=result.completed_at,
    )


# --- the file --------------------------------------------------------------------------------------------------


def open_evals_db(gig: str, home: Path) -> sqlite3.Connection:
    """Open the eval store of ``gig``, creating it (mode 0600) and walking it to ``SCHEMA_VERSION`` as needed.

    Refuses a symlink at ``<home>/gigs``, the gig folder or the file, and (``StoreVersionError``) a file newer than
    this gigai. The connection is in autocommit mode with foreign keys on; ``write_*`` open their own transactions.
    Two processes may hold the file open at once: a writer waits up to 30 s for the other.
    """

    from ..sdk.store import migrate_connection

    ensure_gig_folder(home, gig)
    path = _checked_path(gig, home)
    try:
        os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600))
    except OSError as exc:
        raise EvalsError(f"cannot open the eval store {path}: {exc}") from exc
    connection = sqlite3.connect(path, timeout=30)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        migrate_connection(connection, tuple(_migration_step(name) for name in _MIGRATIONS), EVALS_FILE, core=True)
    except BaseException:
        connection.close()
        raise
    return connection


def write_run(connection: sqlite3.Connection, run: RunRecord) -> None:
    """Record ``run``; recording the same run again is a no-op, and a different run under its id is refused."""

    with _transaction(connection):
        _put_run(connection, run)


def write_trace(connection: sqlite3.Connection, trace: TraceRecord) -> None:
    """Record ``trace``; its run must be recorded already."""

    with _transaction(connection):
        _put_trace(connection, trace)


def write_score(connection: sqlite3.Connection, score: ScoreRecord) -> None:
    """Record ``score``; its trace must be recorded already."""

    with _transaction(connection):
        connection.execute(
            "INSERT INTO scores (score_id, trace_id, scorer_id, scorer_version, value, explanation, judge_model,"
            " judge_prompt_sha256, position_swapped, judge_invocation_id, recorded_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                score.score_id,
                score.trace_id,
                score.scorer_id,
                score.scorer_version,
                float(score.value),
                score.explanation,
                score.judge_model,
                score.judge_prompt_sha256,
                None if score.position_swapped is None else int(score.position_swapped),
                score.judge_invocation_id,
                score.recorded_at,
            ),
        )


def trace(
    home: Path,
    gig: str,
    step_id: str,
    input: str,
    output: str,
    *,
    run: RunRecord,
    item: ItemRef,
    prompt: PromptRef | None = None,
    model: ModelCall | None = None,
    repeat_index: int = 0,
    attempt_index: int = 0,
    output_parsed: Any = None,
    parse_status: str = "not_parsed",
    outcome: str = "succeeded",
    error: Mapping[str, Any] | None = None,
) -> TraceRecord:
    """Record one trace of ``gig``'s step ``step_id`` in its eval store (``run`` too, the first time); returns it.

    ``model`` is ``model_call(binding, request, result)`` when the output came from a model call.
    """

    if run.gig != gig:
        raise EvalsError(f"run {run.run_id} belongs to gig {run.gig!r}, not {gig!r}")
    record = TraceRecord(
        trace_id=new_id("trace"),
        run_id=run.run_id,
        step_id=step_id,
        item=item,
        input=input,
        output_raw_text=output,
        repeat_index=repeat_index,
        attempt_index=attempt_index,
        prompt=prompt,
        output_parsed=output_parsed,
        parse_status=parse_status,
        model=model,
        outcome=outcome,
        error=error,
    )
    connection = open_evals_db(gig, home)
    try:
        with _transaction(connection):
            _put_run(connection, run)
            _put_trace(connection, record)
    finally:
        connection.close()
    return record


def replay(home: Path, gig: str, run_id: str, postprocess: Postprocess, *, step_id: str | None = None) -> list[ReplayRow]:
    """Re-score the stored traces of run ``run_id`` (one step, or all) through ``postprocess``; no model call.

    Reads the file without writing it, in a fixed order (step, item, repeat, attempt). A trace whose stored input or
    output no longer matches its SHA-256 is refused.
    """

    connection = _open_existing(gig, home)
    try:
        rows = connection.execute(
            "SELECT trace_id, step_id, item_id, item_version, repeat_index, attempt_index, input, input_sha256,"
            " output_raw_text, output_sha256 FROM traces WHERE run_id = ? AND (? IS NULL OR step_id = ?)"
            " ORDER BY step_id, item_id, repeat_index, attempt_index, trace_id",
            (run_id, step_id, step_id),
        ).fetchall()
    finally:
        connection.close()
    replayed = []
    for trace_id, step, item_id, item_version, repeat_index, attempt_index, text, text_sha, output, output_sha in rows:
        if _sha256(text) != text_sha or _sha256(output) != output_sha:
            raise EvalsError(f"trace {trace_id}: the stored input or output does not match its sha256")
        parsed, status = postprocess(output)
        if status not in PARSE_STATUSES:
            raise EvalsError(f"postprocess returned parse status {status!r}; use one of {sorted(PARSE_STATUSES)}")
        replayed.append(
            ReplayRow(trace_id, step, item_id, item_version, repeat_index, attempt_index, output_sha, parsed, status)
        )
    return replayed


# --- internals -------------------------------------------------------------------------------------------------


def _checked_path(gig: str, home: Path) -> Path:
    """``evals_db_path`` with ``home`` resolved; refuses a symlink at ``<home>/gigs``, the gig folder or the file."""

    path = evals_db_path(gig, Path(home).resolve())
    for part in (path.parent.parent, path.parent, path):
        try:
            mode = part.lstat().st_mode
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(mode):
            raise EvalsError(f"refusing a symlink in the gig folder: {part}")
    return path


def _open_existing(gig: str, home: Path) -> sqlite3.Connection:
    """Open an existing eval store read-only, at exactly ``SCHEMA_VERSION``."""

    from ..sdk.store import StoreVersionError

    path = _checked_path(gig, home)
    if not path.is_file():
        raise EvalsError(f"no eval store at {path}: nothing of gig {gig!r} has been traced")
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30)
    try:
        (version,) = connection.execute("PRAGMA user_version").fetchone()
        if version > SCHEMA_VERSION:
            raise StoreVersionError(
                f"store {EVALS_FILE} is at version {version}, newer than version {SCHEMA_VERSION} this gigai knows;"
                " update gigai"
            )
        if version < SCHEMA_VERSION:
            raise EvalsError(f"the eval store {path} is at version {version}; open it with open_evals_db first")
    except BaseException:
        connection.close()
        raise
    return connection


def _migration_step(name: str) -> Callable[[sqlite3.Connection], None]:
    """A ``migrate_connection`` step running ``migrations/<name>`` statement by statement (never ``executescript``)."""

    def step(connection: sqlite3.Connection) -> None:
        for statement in _statements((resources.files(__package__) / "migrations" / name).read_text("utf-8")):
            connection.execute(statement)

    return step


def _statements(script: str) -> list[str]:
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if any(line.strip() and not line.strip().startswith("--") for line in buffer.splitlines()):
        raise EvalsError("a migration ends inside a statement")
    return statements


class _transaction:
    """``BEGIN IMMEDIATE`` ... ``COMMIT``, rolled back on any error."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def __enter__(self) -> None:
        self._connection.execute("BEGIN IMMEDIATE")

    def __exit__(self, kind: object, value: object, traceback: object) -> None:
        if kind is None:
            self._connection.execute("COMMIT")
        elif self._connection.in_transaction:
            self._connection.execute("ROLLBACK")


_RUN_FACTS = (
    "gig",
    "schema_version",
    "manifest_digest",
    "dataset_id",
    "dataset_version",
    "dataset_sha256",
    "dataset_slice_recipe",
    "dataset_seed",
    "prereg",
)


def _put_run(connection: sqlite3.Connection, run: RunRecord) -> None:
    facts = (
        run.gig,
        run.schema_version,
        run.manifest_digest,
        run.dataset.id,
        run.dataset.version,
        run.dataset.sha256,
        _json(run.dataset.slice_recipe),
        run.dataset.seed,
        None if run.prereg is None else _json(run.prereg),
    )
    stored = connection.execute(f"SELECT {', '.join(_RUN_FACTS)} FROM runs WHERE run_id = ?", (run.run_id,)).fetchone()
    if stored is None:
        connection.execute(
            f"INSERT INTO runs (run_id, {', '.join(_RUN_FACTS)}, created_at) VALUES (?, {', '.join('?' * len(facts))}, ?)",
            (run.run_id, *facts, run.created_at),
        )
    elif tuple(stored) != facts:
        raise EvalsError(f"run {run.run_id} is already recorded with different facts")


def _put_trace(connection: sqlite3.Connection, record: TraceRecord) -> None:
    model, prompt = record.model, record.prompt
    settings = model.settings if model is not None else Settings()
    columns = {
        "trace_id": record.trace_id,
        "run_id": record.run_id,
        "step_id": record.step_id,
        "item_id": record.item.id,
        "item_version": record.item.version,
        "cluster_id": record.item.cluster_id,
        "repeat_index": record.repeat_index,
        "attempt_index": record.attempt_index,
        "prompt_id": None if prompt is None else prompt.id,
        "prompt_version": None if prompt is None else prompt.version,
        "prompt_sha256": None if prompt is None else prompt.sha256,
        "input": record.input,
        "input_sha256": record.input_sha256,
        "output_raw_text": record.output_raw_text,
        "output_sha256": record.output_sha256,
        "output_parsed": None if record.output_parsed is None else _json(record.output_parsed),
        "parse_status": record.parse_status,
        "model_id": None if model is None else model.model_id,
        "model_provider": None if model is None else model.model_provider,
        "model_endpoint": None if model is None else model.model_endpoint,
        "model_adapter": None if model is None else model.model_adapter,
        "model_name": None if model is None else model.model_name,
        "resolved_model": None if model is None else model.resolved_model,
        "adapter_version": None if model is None else model.adapter_version,
        "argv": _json(list(model.argv) if model is not None else []),
        "temperature": settings.temperature,
        "top_p": settings.top_p,
        "seed": settings.seed,
        "output_mode": settings.output_mode,
        "schema_sha256": settings.schema_sha256,
        "retry_policy": None if settings.retry_policy is None else _json(settings.retry_policy),
        "settings_unsupported": _json(list(settings.unsupported)),
        "usage": _json(
            model.usage
            if model is not None
            else {"input_tokens": None, "output_tokens": None, "total_tokens": None, "raw": {}}
        ),
        "latency_ms": None if model is None else model.latency_ms,
        "started_at": None if model is None else model.started_at,
        "completed_at": None if model is None else model.completed_at,
        "cost_usd": None,  # P12 prices calls
        "outcome": record.outcome,
        "error": None if record.error is None else _json(record.error),
        "recorded_at": record.recorded_at,
    }
    connection.execute(
        f"INSERT INTO traces ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
        tuple(columns.values()),
    )


def _json(value: Any) -> str:
    """Sorted, compact JSON text (floats allowed: settings and scores carry them)."""

    return json.dumps(value, default=_plain, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True)


def _plain(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, Mapping):
        return dict(value)
    if isinstance(value, frozenset | set):
        return sorted(value)
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return list(value)
    raise TypeError(f"cannot record a {type(value).__name__} as JSON")


def _sha256(text: str) -> str:
    return digest_imported_bytes(text.encode("utf-8"))


def _require_text(name: str, value: object) -> None:
    if not isinstance(value, str) or not value:
        raise EvalsError(f"{name} must be a non-empty string, not {value!r}")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


__all__ = [
    "EVALS_FILE",
    "OUTCOMES",
    "PARSE_STATUSES",
    "SCHEMA_VERSION",
    "Dataset",
    "EvalsError",
    "ItemRef",
    "ModelCall",
    "Postprocess",
    "Prereg",
    "PromptRef",
    "ReplayRow",
    "RunRecord",
    "ScoreRecord",
    "Settings",
    "TraceRecord",
    "evals_db_path",
    "model_call",
    "new_id",
    "open_evals_db",
    "replay",
    "trace",
    "write_run",
    "write_score",
    "write_trace",
]
