"""0.1.10.7 PL1: the pipeline queue, one local SQLite file per project.

``<home>/scout/<project_id>/pipeline/pipeline.sqlite``: stdlib ``sqlite3``,
WAL, so readers never block the one writer. The file is STATE, not
authority: every step's output lives in the store it already uses (the
tailored-resume store, the quick-assess store, the ATS store) and this file
only points at it (``output_ref``, ``output_digest``). Deleting it loses the
history and the metrics, nothing else.

No text, anywhere. Every column holds an id, a digest, a bounded code, a
timestamp or a number (``COLUMN_KINDS`` names each one), and every value is
checked against its shape before it is written: a free-text error message, a
resume line or an email address cannot reach this file. ``job`` is the
find-jobs job identity: a public posting URL, or ``text:sha256:<hex>`` for
pasted text.

What it holds (DESIGN 5.1):

- ``step``: one row per ``(profile_id, job, name)``. The DAG is fixed in
  code (``DEPS``): ``tailor -> {reassess, ats} -> label``. ``label`` is the
  Scout label step (the operator's name for "ready to apply").
- ``step_run``: one row per attempt, the metrics record (tokens, cost,
  seconds, outcome, error code). An attempt whose holder died is recorded
  as ``interrupted`` when it is reclaimed.
- ``lane``: a model lane that is backed off (``model_target_unavailable``:
  300 s, doubling to 6 h; cleared by the lane's next success).
- ``approval``: an approval-gated batch (the per-trigger cap).
- ``anchor``: the ONE "new since" anchor per install (``scope = 'user'``).
- ``cap_counter``: the daily counters (``pipeline_calls``, ``rank_calls``).

Idempotency (DESIGN 5.2): ``enqueue`` with the digest the step was last done
with is ``noop_unchanged``; the digest it is already queued with is
``noop_already_queued``; a changed digest re-opens the step and re-blocks
everything downstream of it. A downstream step's digest is the runner's to
compute at claim time from its upstream outputs, so a re-blocked downstream
step keeps its ``done_digest`` and the runner can finish it without a call
when its inputs come out the same.

Claim (``BEGIN IMMEDIATE``, the litequeue pattern): within one write
transaction the claimer reclaims dead holders, unblocks the steps whose
dependencies are all done, counts what is running per lane and picks the
first ready step whose lane has room. Both the per-lane caps (``LANE_CAPS``)
and the total of model calls (``MODEL_TOTAL_CAP``) are counted from the
file, so they hold across every process and thread that opens it.

Leases. A claim holds a lease (``LEASE_SECONDS``, renewed by the runner
while the call runs). A holder is gone, and its step goes back to ``ready``
at once, when its lease has expired OR its process is no longer alive (the
owner's pid and process token, the ``assess_all`` owner rule). Lease expiry
alone is not enough: in the design prototype a crashed holder kept one of
the two claude slots for its whole lease.

A step that changes while it runs (a new digest, an upstream re-open, a
cancel) keeps its lease: the call in flight finishes, its output is kept in
its store, and ``finish`` then re-opens the step (``generation`` moved on)
or settles it ``cancelled`` instead of ``done``.

Retries (DESIGN 7): ``assess_timeout`` / ``model_unavailable`` /
``rate_limited`` retry at 60 s x 2^(n-1), at most 4 attempts;
``model_output_invalid`` gets one more attempt; ``model_target_unavailable``
backs the lane off and the step waits for it; any other code fails the step
(``retry`` re-opens it).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import os
from pathlib import Path
import re
import sqlite3
import threading
import time
import uuid

#: ``PRAGMA user_version`` of a file this module writes.
SCHEMA_VERSION = 1

#: The fixed DAG, in topological order.
STEPS: tuple[str, ...] = ("tailor", "reassess", "ats", "label")
DEPS: Mapping[str, tuple[str, ...]] = {"tailor": (), "reassess": ("tailor",), "ats": ("tailor",), "label": ("reassess", "ats")}
#: The steps that call a model; the others run in the ``local`` lane.
MODEL_STEPS = frozenset({"tailor", "reassess"})

LOCAL_LANE = "local"
#: Running steps per lane (DESIGN 7: each CLI child is ~260-330 MB RSS; a
#: local model saturates the machine). An ``api:<target>`` lane gets ``API_LANE_CAP``.
LANE_CAPS: Mapping[str, int] = {"claude_cli": 2, "codex_cli": 2, "ollama": 1, LOCAL_LANE: 4}
API_LANE_CAP = 2
#: Model calls running at once over every model lane: ``assess_all.ASSESS_CONCURRENCY``
#: (``model_rank.SMALL_MACHINE_CONCURRENCY``), not imported so this module stays stdlib-only.
MODEL_TOTAL_CAP = 4

#: A claim's lease; the runner renews it every ``RENEW_SECONDS`` while the call runs.
LEASE_SECONDS = 600.0
RENEW_SECONDS = 60.0

STATE_BLOCKED = "blocked"
STATE_READY = "ready"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_FAILED = "failed"
STATE_CANCELLED = "cancelled"
STATE_AWAITING_APPROVAL = "awaiting_approval"
STATES = frozenset(
    {STATE_BLOCKED, STATE_READY, STATE_RUNNING, STATE_DONE, STATE_FAILED, STATE_CANCELLED, STATE_AWAITING_APPROVAL}
)
#: A step in one of these can still run without a new enqueue.
_QUEUED = frozenset({STATE_BLOCKED, STATE_READY, STATE_RUNNING, STATE_AWAITING_APPROVAL})

TRIGGERS = frozenset({"answer_saved", "story_saved", "process_now", "approval", "startup_reconcile"})

#: ``enqueue`` results.
ENQUEUED = "enqueued"
NOOP_UNCHANGED = "noop_unchanged"
NOOP_ALREADY_QUEUED = "noop_already_queued"
NOOP_FAILED = "noop_failed"

#: ``finish`` / ``fail`` results besides a step state.
LOST_LEASE = "lost_lease"

#: ``step_run.outcome``.
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"
OUTCOME_INTERRUPTED = "interrupted"
OUTCOME_LOST_LEASE = "lost_lease"

TRANSIENT_CODES = frozenset({"assess_timeout", "model_unavailable", "rate_limited"})
INVALID_OUTPUT_CODES = frozenset({"model_output_invalid"})
LANE_BACKOFF_CODES = frozenset({"model_target_unavailable"})
MAX_TRANSIENT_ATTEMPTS = 4
MAX_INVALID_OUTPUT_ATTEMPTS = 2
RETRY_BASE_SECONDS = 60.0
LANE_BACKOFF_SECONDS = 300.0
LANE_BACKOFF_MAX_SECONDS = 6 * 3600.0

APPROVAL_PENDING = "pending"
APPROVAL_APPROVED = "approved"
APPROVAL_DECLINED = "declined"
APPROVAL_EXPIRED = "expired"
APPROVAL_STATES = frozenset({APPROVAL_PENDING, APPROVAL_APPROVED, APPROVAL_DECLINED, APPROVAL_EXPIRED})
DECIDED_BY = frozenset({"operator", "agent"})
ANCHOR_SET_BY = frozenset({"scout_new", "mark_all_seen"})
COST_STATUSES = frozenset({"provider_reported", "derived", "unavailable"})

#: Daily counters (``spend``): pipeline model calls, rank calls.
CAP_PIPELINE_CALLS = "pipeline_calls"
CAP_RANK_CALLS = "rank_calls"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS step (
  profile_id TEXT NOT NULL, job TEXT NOT NULL, name TEXT NOT NULL,
  lane TEXT NOT NULL, model_target TEXT,
  state TEXT NOT NULL,
  input_digest TEXT, done_digest TEXT,
  output_ref TEXT, output_digest TEXT,
  generation INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0, not_before REAL NOT NULL DEFAULT 0,
  error_code TEXT,
  lease_owner TEXT, lease_pid INTEGER, lease_until REAL, claimed_at REAL,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  approval_id TEXT,
  trigger TEXT NOT NULL,
  enqueued_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (profile_id, job, name));
CREATE INDEX IF NOT EXISTS step_claim ON step(state, lane, not_before);
CREATE INDEX IF NOT EXISTS step_approval ON step(approval_id);
CREATE TABLE IF NOT EXISTS step_run (
  id INTEGER PRIMARY KEY,
  profile_id TEXT NOT NULL, job TEXT NOT NULL, name TEXT NOT NULL, attempt INTEGER NOT NULL,
  owner TEXT NOT NULL, lane TEXT NOT NULL, adapter TEXT, model TEXT,
  input_tokens INTEGER, output_tokens INTEGER, cached_tokens INTEGER,
  cost_usd REAL, cost_status TEXT,
  started_at TEXT NOT NULL, seconds REAL NOT NULL, outcome TEXT NOT NULL, error_code TEXT, input_digest TEXT);
CREATE INDEX IF NOT EXISTS step_run_step ON step_run(profile_id, job, name);
CREATE TABLE IF NOT EXISTS lane (
  lane TEXT PRIMARY KEY, not_before REAL NOT NULL, backoff_seconds REAL NOT NULL,
  error_code TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS approval (
  id TEXT PRIMARY KEY, profile_id TEXT, trigger TEXT NOT NULL, jobs INTEGER NOT NULL, est_calls INTEGER NOT NULL,
  est_tokens INTEGER, state TEXT NOT NULL, created_at TEXT NOT NULL, decided_at TEXT, decided_by TEXT);
CREATE TABLE IF NOT EXISTS anchor (
  scope TEXT PRIMARY KEY CHECK (scope = 'user'), last_checked_at TEXT NOT NULL, set_by TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS cap_counter (
  cap TEXT NOT NULL, day TEXT NOT NULL, used INTEGER NOT NULL, PRIMARY KEY (cap, day));
"""

#: What every column holds. No column is a text payload: ``id`` / ``job`` /
#: ``digest`` / ``code`` / ``ref`` / ``timestamp`` / ``day`` / ``owner``
#: strings each have a fixed shape (``_SHAPES``), the rest are numbers.
COLUMN_KINDS: Mapping[str, Mapping[str, str]] = {
    "step": {
        "profile_id": "id", "job": "job", "name": "code", "lane": "lane", "model_target": "id", "state": "code",
        "input_digest": "digest", "done_digest": "digest", "output_ref": "ref", "output_digest": "digest",
        "generation": "integer", "attempts": "integer", "not_before": "real", "error_code": "code",
        "lease_owner": "owner", "lease_pid": "integer", "lease_until": "real", "claimed_at": "real",
        "cancel_requested": "integer", "approval_id": "id", "trigger": "code",
        "enqueued_at": "timestamp", "updated_at": "timestamp",
    },
    "step_run": {
        "id": "integer", "profile_id": "id", "job": "job", "name": "code", "attempt": "integer", "owner": "owner",
        "lane": "lane", "adapter": "id", "model": "model", "input_tokens": "integer", "output_tokens": "integer",
        "cached_tokens": "integer", "cost_usd": "real", "cost_status": "code", "started_at": "timestamp",
        "seconds": "real", "outcome": "code", "error_code": "code", "input_digest": "digest",
    },
    "lane": {"lane": "lane", "not_before": "real", "backoff_seconds": "real", "error_code": "code", "updated_at": "timestamp"},
    "approval": {
        "id": "id", "profile_id": "id", "trigger": "code", "jobs": "integer", "est_calls": "integer",
        "est_tokens": "integer", "state": "code", "created_at": "timestamp", "decided_at": "timestamp",
        "decided_by": "code",
    },
    "anchor": {"scope": "code", "last_checked_at": "timestamp", "set_by": "code"},
    "cap_counter": {"cap": "code", "day": "day", "used": "integer"},
}

_SHAPES: Mapping[str, re.Pattern[str]] = {
    "id": re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}"),
    # A public posting URL (no whitespace, no "@": nothing contact-shaped) or pasted text's digest identity.
    "job": re.compile(r"(?:https?://[^\s@]{1,2040}|text:sha256:[0-9a-f]{64})"),
    "digest": re.compile(r"sha256:[0-9a-f]{64}"),
    "code": re.compile(r"[a-z][a-z0-9_]{0,63}"),
    "lane": re.compile(r"(?:claude_cli|codex_cli|ollama|local|api:[a-z0-9][a-z0-9_.-]{0,62})"),
    "model": re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/+-]{0,127}"),
    "ref": re.compile(r"(?!/)(?!.*(?:^|/)\.\.(?:/|$))[A-Za-z0-9_][A-Za-z0-9_.:/-]{0,255}"),
    "owner": re.compile(r"[0-9a-f]{32}:[A-Za-z0-9_-]{1,32}"),
    "timestamp": re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z"),
    "day": re.compile(r"\d{4}-\d{2}-\d{2}"),
}
_WORKER = re.compile(r"[A-Za-z0-9_-]{1,32}")


class PipelineStoreError(ValueError):
    """A value the pipeline file never holds, or a file it cannot use; ``code`` is the error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _check(kind: str, value: object, what: str) -> str:
    if type(value) is not str or not _SHAPES[kind].fullmatch(value):
        raise PipelineStoreError("invalid_value", f"{what} is not a pipeline {kind}")
    return value


def _check_optional(kind: str, value: object, what: str) -> str | None:
    return None if value is None else _check(kind, value, what)


def _check_member(value: object, allowed: Iterable[str], what: str) -> str:
    if type(value) is not str or value not in allowed:
        raise PipelineStoreError("invalid_value", f"{what} has an unsupported value")
    return value


def _check_count(value: object, what: str, *, optional: bool = False) -> int | None:
    if value is None and optional:
        return None
    if type(value) is not int or value < 0:
        raise PipelineStoreError("invalid_value", f"{what} must be a non-negative integer")
    return value


def _check_lane(lane: object) -> str:
    return _check("lane", lane, "lane")


def _check_step(name: object) -> str:
    return _check_member(name, STEPS, "step name")


# --- the owner of a claim -----------------------------------------------------------------

_TOKEN_LOCK = threading.Lock()
_token: tuple[int, str] | None = None


def process_token() -> str:
    """This process's token: new after a fork or a restart, so a reused pid is never taken for its old owner."""

    global _token
    pid = os.getpid()
    with _TOKEN_LOCK:
        if _token is None or _token[0] != pid:
            _token = (pid, uuid.uuid4().hex)
        return _token[1]


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":  # os.kill would end the process there: the lease alone decides
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _holder_gone(owner: str | None, pid: int | None, lease_until: float | None, now: float) -> bool:
    """A running step's holder is gone: its lease expired, or its process is not alive (DESIGN 7)."""

    if owner is None or pid is None or lease_until is None or lease_until < now:
        return True
    token = owner.split(":", 1)[0]
    if pid == os.getpid():
        return token != process_token()  # this pid, an earlier process: gone
    return not _pid_alive(pid)


# --- rows ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """One claimed step: what the runner needs to run it and to settle it."""

    profile_id: str
    job: str
    name: str
    lane: str
    model_target: str | None
    #: ``None`` for a downstream step: the runner computes it from the upstream outputs.
    input_digest: str | None
    #: The digest the step was last done with (``None``: never done).
    done_digest: str | None
    generation: int
    attempt: int
    owner: str
    claimed_at: float
    lease_until: float


@dataclass(frozen=True)
class StepMetrics:
    """One attempt's metrics (``InvocationResult``'s usage); ``None`` where the adapter reports nothing."""

    adapter: str | None = None
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    cost_usd: float | None = None
    cost_status: str | None = None


@dataclass(frozen=True)
class Step:
    profile_id: str
    job: str
    name: str
    lane: str
    model_target: str | None
    state: str
    input_digest: str | None
    done_digest: str | None
    output_ref: str | None
    output_digest: str | None
    generation: int
    attempts: int
    not_before: float
    error_code: str | None
    lease_owner: str | None
    lease_pid: int | None
    lease_until: float | None
    cancel_requested: bool
    approval_id: str | None
    trigger: str
    enqueued_at: str
    updated_at: str


@dataclass(frozen=True)
class StepRun:
    id: int
    profile_id: str
    job: str
    name: str
    attempt: int
    owner: str
    lane: str
    adapter: str | None
    model: str | None
    input_tokens: int | None
    output_tokens: int | None
    cached_tokens: int | None
    cost_usd: float | None
    cost_status: str | None
    started_at: str
    seconds: float
    outcome: str
    error_code: str | None
    input_digest: str | None


@dataclass(frozen=True)
class LaneBackoff:
    lane: str
    not_before: float
    backoff_seconds: float
    error_code: str | None


@dataclass(frozen=True)
class Approval:
    id: str
    profile_id: str | None
    trigger: str
    jobs: int
    est_calls: int
    est_tokens: int | None
    state: str
    created_at: str
    decided_at: str | None
    decided_by: str | None


@dataclass(frozen=True)
class Anchor:
    last_checked_at: str
    set_by: str


_STEP_COLUMNS = (
    "profile_id, job, name, lane, model_target, state, input_digest, done_digest, output_ref, output_digest, "
    "generation, attempts, not_before, error_code, lease_owner, lease_pid, lease_until, cancel_requested, "
    "approval_id, trigger, enqueued_at, updated_at"
)
_RUN_COLUMNS = (
    "id, profile_id, job, name, attempt, owner, lane, adapter, model, input_tokens, output_tokens, cached_tokens, "
    "cost_usd, cost_status, started_at, seconds, outcome, error_code, input_digest"
)
_APPROVAL_COLUMNS = "id, profile_id, trigger, jobs, est_calls, est_tokens, state, created_at, decided_at, decided_by"


def _step(row: tuple) -> Step:
    values = list(row)
    values[17] = bool(values[17])
    return Step(*values)


def _downstream(name: str) -> tuple[str, ...]:
    """Every step that depends on ``name``, directly or not, in topological order."""

    found: list[str] = []
    for candidate in STEPS:
        if any(dep == name or dep in found for dep in DEPS[candidate]):
            found.append(candidate)
    return tuple(found)


# --- the file -----------------------------------------------------------------------------


def pipeline_path(home_root: Path, target: Path) -> Path:
    """``<home>/scout/<project_id>/pipeline/pipeline.sqlite`` for the project bound to ``target``."""

    from ..find_jobs.discovery.storage import project_id

    return Path(home_root) / "scout" / project_id(Path(home_root), Path(target)) / "pipeline" / "pipeline.sqlite"


class PipelineStore:
    """The queue in one SQLite file. Thread-safe (one connection per thread); safe across processes.

    ``clock`` (epoch seconds) is injectable for tests; leases, ``not_before``
    and every timestamp come from it.
    """

    def __init__(
        self,
        path: Path,
        *,
        lane_caps: Mapping[str, int] | None = None,
        model_total_cap: int = MODEL_TOTAL_CAP,
        lease_seconds: float = LEASE_SECONDS,
        clock=time.time,
    ) -> None:
        self.path = Path(path)
        self._caps = dict(LANE_CAPS if lane_caps is None else lane_caps)
        for lane, cap in self._caps.items():
            _check_lane(lane)
            _check_count(cap, "lane cap")
        self._model_total_cap = model_total_cap
        self._lease_seconds = float(lease_seconds)
        self._clock = clock
        self._local = threading.local()
        self._connections: list[sqlite3.Connection] = []
        self._connections_lock = threading.Lock()
        #: Where an unreadable file was moved before this one was created, if it was.
        self.recovered_from: Path | None = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._create()
        except sqlite3.DatabaseError as exc:
            if isinstance(exc, sqlite3.OperationalError):
                raise
            # Corrupt or not a database (DESIGN 12): set it aside and start again. The
            # outputs are in their own stores; the history and metrics are what is lost.
            self.close()
            self.recovered_from = self._set_aside()
            self._create()

    # connections

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(os.fspath(self.path), timeout=30.0, isolation_level=None, check_same_thread=False)
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA synchronous=NORMAL")
        with self._connections_lock:
            self._connections.append(connection)
        return connection

    def _conn(self) -> sqlite3.Connection:
        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._local.connection = self._connect()
        return connection

    def close(self) -> None:
        with self._connections_lock:
            connections, self._connections = self._connections, []
        for connection in connections:
            try:
                connection.close()
            except sqlite3.Error:
                pass
        self._local = threading.local()

    def _set_aside(self) -> Path:
        aside = self.path.with_name(f"{self.path.name}.corrupt-{int(self._clock())}")
        for suffix in ("", "-wal", "-shm"):
            source = Path(os.fspath(self.path) + suffix)
            if source.exists():
                os.replace(source, Path(os.fspath(aside) + suffix))
        return aside

    def _create(self) -> None:
        connection = self._conn()
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise PipelineStoreError("pipeline_schema_newer", "pipeline.sqlite was written by a newer GigAI")
        connection.execute("PRAGMA journal_mode=WAL")
        if version < SCHEMA_VERSION:
            # One statement at a time: ``executescript`` would COMMIT the open transaction.
            with self._write() as c:
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        c.execute(statement)
                c.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self._conn()
        connection.execute("BEGIN IMMEDIATE")
        try:
            yield connection
        except BaseException:  # noqa: BLE001 - re-raised: any failure inside the transaction rolls it back
            connection.execute("ROLLBACK")
            raise
        connection.execute("COMMIT")

    # helpers

    def _now_iso(self, now: float | None = None) -> str:
        moment = datetime.fromtimestamp(self._clock() if now is None else now, UTC)
        return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")

    def _cap(self, lane: str) -> int:
        if lane in self._caps:
            return self._caps[lane]
        return API_LANE_CAP if lane.startswith("api:") else 0

    @staticmethod
    def _open_state(c: sqlite3.Connection, profile_id: str, job: str, name: str) -> str:
        """``ready`` when every dependency of the step is done, else ``blocked``."""

        deps = DEPS[name]
        if not deps:
            return STATE_READY
        marks = ",".join("?" * len(deps))
        done = c.execute(
            f"SELECT COUNT(*) FROM step WHERE profile_id=? AND job=? AND state=? AND name IN ({marks})",
            (profile_id, job, STATE_DONE, *deps),
        ).fetchone()[0]
        return STATE_READY if done == len(deps) else STATE_BLOCKED

    def _record_run(
        self,
        c: sqlite3.Connection,
        *,
        profile_id: str,
        job: str,
        name: str,
        attempt: int,
        owner: str,
        lane: str,
        claimed_at: float,
        now: float,
        outcome: str,
        error_code: str | None = None,
        input_digest: str | None = None,
        metrics: StepMetrics | None = None,
    ) -> None:
        m = metrics or StepMetrics()
        c.execute(
            "INSERT INTO step_run(profile_id, job, name, attempt, owner, lane, adapter, model, input_tokens, output_tokens, "
            "cached_tokens, cost_usd, cost_status, started_at, seconds, outcome, error_code, input_digest) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                profile_id, job, name, attempt, owner, lane, m.adapter, m.model, m.input_tokens, m.output_tokens,
                m.cached_tokens, m.cost_usd, m.cost_status, self._now_iso(claimed_at), max(0.0, now - claimed_at),
                outcome, error_code, input_digest,
            ),
        )

    def _record_claim(
        self,
        c: sqlite3.Connection,
        claim: Claim,
        now: float,
        outcome: str,
        *,
        input_digest: str | None = None,
        error_code: str | None = None,
        metrics: StepMetrics | None = None,
    ) -> None:
        self._record_run(
            c, profile_id=claim.profile_id, job=claim.job, name=claim.name, attempt=claim.attempt, owner=claim.owner,
            lane=claim.lane, claimed_at=claim.claimed_at, now=now, outcome=outcome, error_code=error_code,
            input_digest=claim.input_digest if input_digest is None else input_digest, metrics=metrics,
        )

    @staticmethod
    def _check_metrics(metrics: StepMetrics | None) -> None:
        if metrics is None:
            return
        _check_optional("id", metrics.adapter, "metrics.adapter")
        _check_optional("model", metrics.model, "metrics.model")
        for what in ("input_tokens", "output_tokens", "cached_tokens"):
            _check_count(getattr(metrics, what), f"metrics.{what}", optional=True)
        if metrics.cost_usd is not None and (type(metrics.cost_usd) not in (int, float) or metrics.cost_usd < 0):
            raise PipelineStoreError("invalid_value", "metrics.cost_usd must be a non-negative number")
        if metrics.cost_status is not None:
            _check_member(metrics.cost_status, COST_STATUSES, "metrics.cost_status")

    # enqueue

    def enqueue(
        self,
        profile_id: str,
        job: str,
        name: str,
        *,
        input_digest: str,
        trigger: str,
        lane: str,
        model_target: str | None = None,
        downstream_lanes: Mapping[str, tuple[str, str | None]] | None = None,
        approval_id: str | None = None,
        force: bool = False,
    ) -> str:
        """Queue ``name`` for ``(profile_id, job)`` with its input digest; re-block what depends on it.

        Returns ``noop_unchanged`` (done with this digest), ``noop_already_queued``
        (queued with this digest), ``noop_failed`` (failed with this digest: ``retry``
        re-opens it) or ``enqueued``. ``force`` (process now ``--force``) re-opens it
        whatever its digest. With ``approval_id`` the step waits in ``awaiting_approval``
        until ``decide_approval``.

        ``downstream_lanes`` maps a downstream step to its ``(lane, model_target)``; a
        step left out runs in the ``local`` lane, or for a model step in ``lane``.
        """

        _check("id", profile_id, "profile_id")
        _check("job", job, "job")
        _check_step(name)
        _check("digest", input_digest, "input_digest")
        _check_member(trigger, TRIGGERS, "trigger")
        _check_lane(lane)
        _check_optional("id", model_target, "model_target")
        _check_optional("id", approval_id, "approval_id")
        lanes: dict[str, tuple[str, str | None]] = {}
        for step in _downstream(name):
            chosen = (downstream_lanes or {}).get(step)
            if chosen is None:
                chosen = (lane, model_target) if step in MODEL_STEPS else (LOCAL_LANE, None)
            lanes[step] = (_check_lane(chosen[0]), _check_optional("id", chosen[1], "model_target"))
        now = self._clock()
        stamp = self._now_iso(now)
        with self._write() as c:
            row = c.execute(
                "SELECT state, input_digest, done_digest FROM step WHERE profile_id=? AND job=? AND name=?",
                (profile_id, job, name),
            ).fetchone()
            if row is not None and not force:
                state, queued, done = row
                if state == STATE_DONE and done == input_digest:
                    return NOOP_UNCHANGED
                if state in _QUEUED and queued == input_digest:
                    return NOOP_ALREADY_QUEUED
                if state == STATE_FAILED and queued == input_digest:
                    return NOOP_FAILED
            opened = STATE_AWAITING_APPROVAL if approval_id is not None else self._open_state(c, profile_id, job, name)
            if row is None:
                c.execute(
                    "INSERT INTO step(profile_id, job, name, lane, model_target, state, input_digest, approval_id, trigger, "
                    "enqueued_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (profile_id, job, name, lane, model_target, opened, input_digest, approval_id, trigger, stamp, stamp),
                )
            elif row[0] == STATE_RUNNING:
                # The call in flight finishes (its output is valid for ITS inputs); ``finish`` re-opens it.
                c.execute(
                    "UPDATE step SET input_digest=?, generation=generation+1, trigger=?, updated_at=? "
                    "WHERE profile_id=? AND job=? AND name=?",
                    (input_digest, trigger, stamp, profile_id, job, name),
                )
            else:
                c.execute(
                    "UPDATE step SET lane=?, model_target=?, state=?, input_digest=?, generation=generation+1, attempts=0, "
                    "not_before=0, error_code=NULL, cancel_requested=0, approval_id=?, trigger=?, updated_at=? "
                    "WHERE profile_id=? AND job=? AND name=?",
                    (lane, model_target, opened, input_digest, approval_id, trigger, stamp, profile_id, job, name),
                )
            for step, (step_lane, step_target) in lanes.items():
                self._reblock(c, profile_id, job, step, step_lane, step_target, approval_id, trigger, stamp)
        return ENQUEUED

    @staticmethod
    def _reblock(
        c: sqlite3.Connection,
        profile_id: str,
        job: str,
        name: str,
        lane: str,
        model_target: str | None,
        approval_id: str | None,
        trigger: str,
        stamp: str,
    ) -> None:
        """A downstream step goes back to ``blocked``; its ``done_digest`` stays (DESIGN 5.2)."""

        state = c.execute(
            "SELECT state FROM step WHERE profile_id=? AND job=? AND name=?", (profile_id, job, name)
        ).fetchone()
        if state is None:
            c.execute(
                "INSERT INTO step(profile_id, job, name, lane, model_target, state, approval_id, trigger, enqueued_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (profile_id, job, name, lane, model_target, STATE_BLOCKED, approval_id, trigger, stamp, stamp),
            )
        elif state[0] == STATE_RUNNING:
            c.execute(
                "UPDATE step SET generation=generation+1, updated_at=? WHERE profile_id=? AND job=? AND name=?",
                (stamp, profile_id, job, name),
            )
        else:
            c.execute(
                "UPDATE step SET lane=?, model_target=?, state=?, input_digest=NULL, generation=generation+1, attempts=0, "
                "not_before=0, error_code=NULL, cancel_requested=0, approval_id=?, trigger=?, updated_at=? "
                "WHERE profile_id=? AND job=? AND name=?",
                (lane, model_target, STATE_BLOCKED, approval_id, trigger, stamp, profile_id, job, name),
            )

    # claim, lease

    def _reclaim(self, c: sqlite3.Connection, now: float) -> int:
        rows = c.execute(
            "SELECT profile_id, job, name, lane, lease_owner, lease_pid, lease_until, claimed_at, attempts, "
            "cancel_requested, input_digest FROM step WHERE state=?",
            (STATE_RUNNING,),
        ).fetchall()
        reclaimed = 0
        stamp = self._now_iso(now)
        for profile_id, job, name, lane, owner, pid, until, claimed_at, attempts, cancel, digest in rows:
            if not _holder_gone(owner, pid, until, now):
                continue
            state = STATE_CANCELLED if cancel else self._open_state(c, profile_id, job, name)
            c.execute(
                "UPDATE step SET state=?, lease_owner=NULL, lease_pid=NULL, lease_until=NULL, claimed_at=NULL, "
                "cancel_requested=0, not_before=0, updated_at=? WHERE profile_id=? AND job=? AND name=?",
                (state, stamp, profile_id, job, name),
            )
            if owner is not None:
                self._record_run(
                    c, profile_id=profile_id, job=job, name=name, attempt=attempts, owner=owner, lane=lane,
                    claimed_at=claimed_at if claimed_at is not None else now, now=now,
                    outcome=OUTCOME_INTERRUPTED, input_digest=digest,
                )
            reclaimed += 1
        return reclaimed

    def _unblock(self, c: sqlite3.Connection, stamp: str) -> None:
        for name in STEPS:
            deps = DEPS[name]
            if not deps:
                continue
            marks = ",".join("?" * len(deps))
            c.execute(
                f"UPDATE step SET state=?, updated_at=? WHERE state=? AND name=? AND "
                f"(SELECT COUNT(*) FROM step d WHERE d.profile_id=step.profile_id AND d.job=step.job "
                f"AND d.state=? AND d.name IN ({marks}))=?",
                (STATE_READY, stamp, STATE_BLOCKED, name, STATE_DONE, *deps, len(deps)),
            )

    def reclaim(self) -> int:
        """Put back every running step whose holder is gone (startup); returns how many."""

        now = self._clock()
        with self._write() as c:
            return self._reclaim(c, now)

    def claim(self, *, worker: str = "0", lanes: Iterable[str] | None = None) -> Claim | None:
        """Claim the next ready step whose lane has room, or ``None``.

        ``worker`` names the claiming thread within this process (the owner is
        ``<process token>:<worker>``); ``lanes`` limits the claim to those lanes.
        """

        if type(worker) is not str or not _WORKER.fullmatch(worker):
            raise PipelineStoreError("invalid_value", "worker is not a worker name")
        wanted = None if lanes is None else frozenset(_check_lane(lane) for lane in lanes)
        owner = f"{process_token()}:{worker}"
        now = self._clock()
        stamp = self._now_iso(now)
        with self._write() as c:
            self._reclaim(c, now)
            self._unblock(c, stamp)
            running = dict(c.execute("SELECT lane, COUNT(*) FROM step WHERE state=? GROUP BY lane", (STATE_RUNNING,)).fetchall())
            model_running = sum(count for lane, count in running.items() if lane != LOCAL_LANE)
            backed_off = {lane for (lane,) in c.execute("SELECT lane FROM lane WHERE not_before > ?", (now,))}
            picked = None
            for profile_id, job, name, lane in c.execute(
                "SELECT profile_id, job, name, lane FROM step WHERE state=? AND not_before <= ? "
                "ORDER BY not_before, enqueued_at, profile_id, job, name",
                (STATE_READY, now),
            ).fetchall():
                if wanted is not None and lane not in wanted:
                    continue
                if lane in backed_off or running.get(lane, 0) >= self._cap(lane):
                    continue
                if lane != LOCAL_LANE and model_running >= self._model_total_cap:
                    continue
                picked = (profile_id, job, name)
                break
            if picked is None:
                return None
            until = now + self._lease_seconds
            c.execute(
                "UPDATE step SET state=?, lease_owner=?, lease_pid=?, lease_until=?, claimed_at=?, attempts=attempts+1, "
                "updated_at=? WHERE profile_id=? AND job=? AND name=?",
                (STATE_RUNNING, owner, os.getpid(), until, now, stamp, *picked),
            )
            lane, model_target, input_digest, done_digest, generation, attempts = c.execute(
                "SELECT lane, model_target, input_digest, done_digest, generation, attempts FROM step "
                "WHERE profile_id=? AND job=? AND name=?",
                picked,
            ).fetchone()
        return Claim(*picked, lane, model_target, input_digest, done_digest, generation, attempts, owner, now, until)

    def renew(self, claim: Claim) -> Claim | None:
        """Extend ``claim``'s lease; ``None`` when it is no longer held (reclaimed, or re-enqueued by force)."""

        now = self._clock()
        until = now + self._lease_seconds
        with self._write() as c:
            updated = c.execute(
                "UPDATE step SET lease_until=? WHERE profile_id=? AND job=? AND name=? AND state=? AND lease_owner=?",
                (until, claim.profile_id, claim.job, claim.name, STATE_RUNNING, claim.owner),
            ).rowcount
        if not updated:
            return None
        return replace(claim, lease_until=until)

    def _held(self, c: sqlite3.Connection, claim: Claim) -> tuple[int, int, int] | None:
        """``(generation, attempts, cancel_requested)`` while ``claim`` still holds its step."""

        row = c.execute(
            "SELECT generation, attempts, cancel_requested FROM step WHERE profile_id=? AND job=? AND name=? "
            "AND state=? AND lease_owner=?",
            (claim.profile_id, claim.job, claim.name, STATE_RUNNING, claim.owner),
        ).fetchone()
        return None if row is None else tuple(row)  # type: ignore[return-value]

    def finish(
        self,
        claim: Claim,
        *,
        input_digest: str | None = None,
        output_ref: str | None = None,
        output_digest: str | None = None,
        metrics: StepMetrics | None = None,
    ) -> str:
        """The call succeeded: record its output and metrics; returns the step's new state or ``lost_lease``.

        ``input_digest`` is the digest the call was made with (a downstream step's,
        computed by the runner at claim time); default: the claim's. The step is
        ``done`` unless it changed while it ran: then it re-opens (``ready`` /
        ``blocked``), or is ``cancelled`` when a cancel came in. Either way the
        output is recorded: it is valid for the inputs it was made from.
        """

        digest = claim.input_digest if input_digest is None else _check("digest", input_digest, "input_digest")
        _check_optional("ref", output_ref, "output_ref")
        _check_optional("digest", output_digest, "output_digest")
        self._check_metrics(metrics)
        now = self._clock()
        stamp = self._now_iso(now)
        key = (claim.profile_id, claim.job, claim.name)
        with self._write() as c:
            held = self._held(c, claim)
            if held is None:
                self._record_claim(c, claim, now, OUTCOME_LOST_LEASE, input_digest=digest, metrics=metrics)
                return LOST_LEASE
            generation, _attempts, cancel = held
            if cancel:
                state = STATE_CANCELLED
            elif generation != claim.generation:
                state = self._open_state(c, *key)
            else:
                state = STATE_DONE
            c.execute(
                "UPDATE step SET state=?, done_digest=?, input_digest=CASE WHEN ? THEN ? ELSE input_digest END, "
                "output_ref=COALESCE(?, output_ref), output_digest=COALESCE(?, output_digest), error_code=NULL, "
                "lease_owner=NULL, lease_pid=NULL, lease_until=NULL, claimed_at=NULL, cancel_requested=0, not_before=0, "
                "attempts=CASE WHEN ? THEN attempts ELSE 0 END, updated_at=? WHERE profile_id=? AND job=? AND name=?",
                (state, digest, state == STATE_DONE, digest, output_ref, output_digest, state == STATE_DONE, stamp, *key),
            )
            c.execute("DELETE FROM lane WHERE lane=?", (claim.lane,))
            self._record_claim(c, claim, now, OUTCOME_OK, input_digest=digest, metrics=metrics)
            if state == STATE_DONE:
                self._unblock(c, stamp)
        return state

    def fail(self, claim: Claim, error_code: str, *, metrics: StepMetrics | None = None) -> str:
        """The call failed with ``error_code``; returns the step's new state or ``lost_lease`` (see the module docstring)."""

        _check("code", error_code, "error_code")
        self._check_metrics(metrics)
        now = self._clock()
        stamp = self._now_iso(now)
        key = (claim.profile_id, claim.job, claim.name)
        with self._write() as c:
            held = self._held(c, claim)
            if held is None:
                self._record_claim(c, claim, now, OUTCOME_LOST_LEASE, error_code=error_code, metrics=metrics)
                return LOST_LEASE
            generation, attempts, cancel = held
            not_before = 0.0
            if cancel:
                state = STATE_CANCELLED
            elif generation != claim.generation:
                state, attempts = self._open_state(c, *key), 0  # new inputs: this failure was not theirs
            elif error_code in LANE_BACKOFF_CODES:
                not_before = self._back_off_lane(c, claim.lane, error_code, now, stamp)
                state, attempts = self._open_state(c, *key), attempts - 1  # the step waits for its lane
            elif error_code in TRANSIENT_CODES and attempts < MAX_TRANSIENT_ATTEMPTS:
                state, not_before = self._open_state(c, *key), now + RETRY_BASE_SECONDS * 2 ** (attempts - 1)
            elif error_code in INVALID_OUTPUT_CODES and attempts < MAX_INVALID_OUTPUT_ATTEMPTS:
                state = self._open_state(c, *key)
            else:
                state = STATE_FAILED
            c.execute(
                "UPDATE step SET state=?, error_code=?, attempts=?, not_before=?, lease_owner=NULL, lease_pid=NULL, "
                "lease_until=NULL, claimed_at=NULL, cancel_requested=0, updated_at=? WHERE profile_id=? AND job=? AND name=?",
                (state, error_code, max(0, attempts), not_before, stamp, *key),
            )
            self._record_claim(c, claim, now, OUTCOME_ERROR, error_code=error_code, metrics=metrics)
        return state

    def _back_off_lane(self, c: sqlite3.Connection, lane: str, error_code: str, now: float, stamp: str) -> float:
        row = c.execute("SELECT backoff_seconds FROM lane WHERE lane=?", (lane,)).fetchone()
        backoff = LANE_BACKOFF_SECONDS if row is None else min(LANE_BACKOFF_MAX_SECONDS, row[0] * 2)
        c.execute(
            "INSERT INTO lane(lane, not_before, backoff_seconds, error_code, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(lane) DO UPDATE SET not_before=excluded.not_before, backoff_seconds=excluded.backoff_seconds, "
            "error_code=excluded.error_code, updated_at=excluded.updated_at",
            (lane, now + backoff, backoff, error_code, stamp),
        )
        return now + backoff

    # cancel, retry

    def cancel(self, profile_id: str, job: str) -> int:
        """Cancel the job's open steps; a running one finishes its call (output kept) and settles ``cancelled``."""

        _check("id", profile_id, "profile_id")
        _check("job", job, "job")
        stamp = self._now_iso()
        with self._write() as c:
            waiting = c.execute(
                "UPDATE step SET state=?, approval_id=NULL, updated_at=? WHERE profile_id=? AND job=? AND state IN (?,?,?)",
                (STATE_CANCELLED, stamp, profile_id, job, STATE_BLOCKED, STATE_READY, STATE_AWAITING_APPROVAL),
            ).rowcount
            running = c.execute(
                "UPDATE step SET cancel_requested=1, updated_at=? WHERE profile_id=? AND job=? AND state=?",
                (stamp, profile_id, job, STATE_RUNNING),
            ).rowcount
        return waiting + running

    def retry(self, profile_id: str, job: str, name: str | None = None) -> int:
        """Re-open the job's failed / cancelled steps (or just ``name``) with their inputs; returns how many."""

        _check("id", profile_id, "profile_id")
        _check("job", job, "job")
        names = STEPS if name is None else (_check_step(name),)
        stamp = self._now_iso()
        reopened = 0
        with self._write() as c:
            for step in names:
                row = c.execute(
                    "SELECT state FROM step WHERE profile_id=? AND job=? AND name=?", (profile_id, job, step)
                ).fetchone()
                if row is None or row[0] not in (STATE_FAILED, STATE_CANCELLED):
                    continue
                c.execute(
                    "UPDATE step SET state=?, attempts=0, not_before=0, error_code=NULL, generation=generation+1, "
                    "updated_at=? WHERE profile_id=? AND job=? AND name=?",
                    (self._open_state(c, profile_id, job, step), stamp, profile_id, job, step),
                )
                reopened += 1
        return reopened

    # reads

    def step(self, profile_id: str, job: str, name: str) -> Step | None:
        row = self._conn().execute(
            f"SELECT {_STEP_COLUMNS} FROM step WHERE profile_id=? AND job=? AND name=?", (profile_id, job, name)
        ).fetchone()
        return None if row is None else _step(row)

    def steps(self, *, profile_id: str | None = None, job: str | None = None) -> tuple[Step, ...]:
        clauses, params = [], []
        if profile_id is not None:
            clauses.append("profile_id=?")
            params.append(profile_id)
        if job is not None:
            clauses.append("job=?")
            params.append(job)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn().execute(f"SELECT {_STEP_COLUMNS} FROM step{where} ORDER BY profile_id, job, name", params)
        return tuple(_step(row) for row in rows.fetchall())

    def counts(self) -> dict[str, int]:
        """Steps per state."""

        return dict(self._conn().execute("SELECT state, COUNT(*) FROM step GROUP BY state").fetchall())

    def runs(self, *, profile_id: str | None = None, job: str | None = None) -> tuple[StepRun, ...]:
        clauses, params = [], []
        if profile_id is not None:
            clauses.append("profile_id=?")
            params.append(profile_id)
        if job is not None:
            clauses.append("job=?")
            params.append(job)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._conn().execute(f"SELECT {_RUN_COLUMNS} FROM step_run{where} ORDER BY id", params)
        return tuple(StepRun(*row) for row in rows.fetchall())

    def lane_backoffs(self) -> tuple[LaneBackoff, ...]:
        rows = self._conn().execute("SELECT lane, not_before, backoff_seconds, error_code FROM lane ORDER BY lane")
        return tuple(LaneBackoff(*row) for row in rows.fetchall())

    # approvals

    def create_approval(
        self, *, trigger: str, jobs: int, est_calls: int, est_tokens: int | None = None, profile_id: str | None = None
    ) -> str:
        """A pending approval for a batch over the per-trigger cap; returns its id (``apv_<hex>``)."""

        _check_member(trigger, TRIGGERS, "trigger")
        _check_count(jobs, "jobs")
        _check_count(est_calls, "est_calls")
        _check_count(est_tokens, "est_tokens", optional=True)
        _check_optional("id", profile_id, "profile_id")
        approval_id = f"apv_{uuid.uuid4().hex}"
        with self._write() as c:
            c.execute(
                f"INSERT INTO approval({_APPROVAL_COLUMNS}) VALUES (?,?,?,?,?,?,?,?,NULL,NULL)",
                (approval_id, profile_id, trigger, jobs, est_calls, est_tokens, APPROVAL_PENDING, self._now_iso()),
            )
        return approval_id

    def decide_approval(self, approval_id: str, *, approved: bool, decided_by: str) -> str:
        """Approve (its steps open) or decline (its steps are cancelled) a pending approval; returns its state."""

        _check("id", approval_id, "approval_id")
        _check_member(decided_by, DECIDED_BY, "decided_by")
        stamp = self._now_iso()
        with self._write() as c:
            row = c.execute("SELECT state FROM approval WHERE id=?", (approval_id,)).fetchone()
            if row is None:
                raise PipelineStoreError("approval_not_found", "no such approval")
            if row[0] != APPROVAL_PENDING:
                return row[0]
            state = APPROVAL_APPROVED if approved else APPROVAL_DECLINED
            c.execute(
                "UPDATE approval SET state=?, decided_at=?, decided_by=? WHERE id=?", (state, stamp, decided_by, approval_id)
            )
            waiting = c.execute(
                "SELECT profile_id, job, name FROM step WHERE approval_id=? AND state=?",
                (approval_id, STATE_AWAITING_APPROVAL),
            ).fetchall()
            for profile_id, job, name in sorted(waiting, key=lambda row: STEPS.index(row[2])):
                opened = self._open_state(c, profile_id, job, name) if approved else STATE_CANCELLED
                c.execute(
                    "UPDATE step SET state=?, updated_at=? WHERE profile_id=? AND job=? AND name=?",
                    (opened, stamp, profile_id, job, name),
                )
            if not approved:
                c.execute(
                    "UPDATE step SET state=?, updated_at=? WHERE approval_id=? AND state=?",
                    (STATE_CANCELLED, stamp, approval_id, STATE_BLOCKED),
                )
        return state

    def approvals(self, *, state: str | None = None) -> tuple[Approval, ...]:
        if state is None:
            rows = self._conn().execute(f"SELECT {_APPROVAL_COLUMNS} FROM approval ORDER BY created_at, id")
        else:
            _check_member(state, APPROVAL_STATES, "approval state")
            rows = self._conn().execute(
                f"SELECT {_APPROVAL_COLUMNS} FROM approval WHERE state=? ORDER BY created_at, id", (state,)
            )
        return tuple(Approval(*row) for row in rows.fetchall())

    # the "new" anchor (one per install)

    def anchor(self) -> Anchor | None:
        row = self._conn().execute("SELECT last_checked_at, set_by FROM anchor WHERE scope='user'").fetchone()
        return None if row is None else Anchor(*row)

    def advance_anchor(self, at: str, *, set_by: str) -> Anchor:
        """Move the anchor to ``at`` (ISO UTC, ``...Z``); it never moves back. Returns the anchor now."""

        _check("timestamp", at, "anchor timestamp")
        _check_member(set_by, ANCHOR_SET_BY, "set_by")
        with self._write() as c:
            row = c.execute("SELECT last_checked_at, set_by FROM anchor WHERE scope='user'").fetchone()
            if row is not None and _instant(row[0]) >= _instant(at):
                return Anchor(*row)
            c.execute(
                "INSERT INTO anchor(scope, last_checked_at, set_by) VALUES ('user', ?, ?) "
                "ON CONFLICT(scope) DO UPDATE SET last_checked_at=excluded.last_checked_at, set_by=excluded.set_by",
                (at, set_by),
            )
        return Anchor(at, set_by)

    # daily caps

    def spend(self, cap: str, day: str, calls: int = 1, *, limit: int | None = None) -> bool:
        """Count ``calls`` against ``cap`` for ``day`` (local ``YYYY-MM-DD``); ``False`` (nothing counted) past ``limit``."""

        _check("code", cap, "cap")
        _check("day", day, "day")
        _check_count(calls, "calls")
        _check_count(limit, "limit", optional=True)
        with self._write() as c:
            row = c.execute("SELECT used FROM cap_counter WHERE cap=? AND day=?", (cap, day)).fetchone()
            used = 0 if row is None else row[0]
            if limit is not None and used + calls > limit:
                return False
            c.execute(
                "INSERT INTO cap_counter(cap, day, used) VALUES (?,?,?) ON CONFLICT(cap, day) DO UPDATE SET used=excluded.used",
                (cap, day, used + calls),
            )
        return True

    def used(self, cap: str, day: str) -> int:
        row = self._conn().execute("SELECT used FROM cap_counter WHERE cap=? AND day=?", (cap, day)).fetchone()
        return 0 if row is None else row[0]


def _instant(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp.replace("Z", "+00:00"))


__all__ = [
    "COLUMN_KINDS",
    "DEPS",
    "LANE_CAPS",
    "MODEL_STEPS",
    "STEPS",
    "Anchor",
    "Approval",
    "Claim",
    "LaneBackoff",
    "PipelineStore",
    "PipelineStoreError",
    "Step",
    "StepMetrics",
    "StepRun",
    "pipeline_path",
    "process_token",
]
