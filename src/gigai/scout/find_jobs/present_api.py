"""C-2: the localhost API server the Scout find-jobs UI talks to.

Stdlib-only (``http.server.ThreadingHTTPServer``); binds loopback only and
refuses any non-loopback peer with 403.  Every state-changing route (POST
/PUT/PATCH/DELETE) also runs a same-origin CSRF guard (``_check_csrf``):
loopback alone doesn't stop a malicious web page open in the operator's own
browser, so writes additionally require ``Content-Type: application/json``,
a matching ``Origin`` when present, and a matching ``Host``.  All business
logic is injected through the ``Backend`` protocol below; this module owns
routing, JSON (de)serialization against the frozen contracts, and both
guards.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import posixpath
import re
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Protocol
from urllib.parse import urlsplit

from ...canonical import canonical_json_bytes, parse_json_bytes
from ...run import RunError
from .contracts import (
    API_BIND,
    AggregateStatus,
    FindJobsConfig,
    FindJobsContractError,
    ModelTarget,
    PinnedResume,
    RunRequest,
    RunResultsResponse,
    RunStatusResponse,
    SourceToggles,
)

_TEST_HTTP_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP"
_TEST_MODEL_ENV = "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL"

# uat-bug-003: one stdlib logger for the whole Scout server. Never configured
# at import time and never touching the root logger -- a library caller that
# imports this module must not have its own logging config hijacked. The
# handler is attached only where the server actually starts (``serve()``),
# and points at stderr because ``run_supervisor.py`` already redirects the
# child process's stderr to the operator-visible log file; ``--foreground``
# gets the same lines on the terminal for free. Tests capture this logger
# via ``caplog`` (or raise its level) instead of relying on stderr, which is
# what keeps this suite's own output quiet without silencing production.
LOGGER_NAME = "gigai.scout.server"
_logger = logging.getLogger(LOGGER_NAME)


def _gigai_version() -> str:
    """Best-effort package version for the "server start" log line.

    Mirrors ``diagnostics.py``'s own ``importlib.metadata.version`` lookup;
    falls back to "unknown" for an editable/source checkout without
    distribution metadata rather than let a log line crash the server.
    """

    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("gigai")
    except PackageNotFoundError:
        return "unknown"


def _configure_logging(logger: logging.Logger = _logger) -> None:
    """Attach a stderr handler to ``logger`` if it doesn't already have one.

    Idempotent so repeated ``serve()`` calls in the same process (e.g. two
    tests, or a test harness that starts several servers) never accumulate
    duplicate handlers and log each line multiple times.
    """

    if any(getattr(handler, "_gigai_scout_server", False) for handler in logger.handlers):
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._gigai_scout_server = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class Backend(Protocol):
    """Injected dependencies; I-3 wires the real one to ``run.launch_find_jobs_run``."""

    def read_config(self) -> tuple[FindJobsConfig, bytes]: ...

    def resume_preview(self) -> PinnedResume | None: ...

    def start_run(
        self,
        run_request: RunRequest,
        config_bytes: bytes,
        on_run_allocated: Callable[[str], None],
    ) -> None:
        """Run the whole traversal; call ``on_run_allocated(run_id)`` once the run exists.

        Any exception raised before ``on_run_allocated`` is called is surfaced to the
        POST caller as an error response. Any exception raised after allocation cannot
        reach the (already-answered) POST caller; the backend must record the failure
        so it shows up in ``run_status``/``run_results`` instead.
        """
        ...

    def run_status(self, run_id: str) -> RunStatusResponse: ...

    def run_results(self, run_id: str) -> RunResultsResponse: ...

    def run_progress(self, run_id: str) -> dict[str, object]: ...

    # S2-B: the setup interview + "Discover companies" panel. These four
    # methods are a thin pass-through onto the S2-A discovery package
    # (``gigai.scout.find_jobs.discovery``), imported lazily inside each
    # method body -- see ``ScoutFindJobsBackend`` below for why (the module
    # may not exist yet, and the API must still start without it). Prefs and
    # results are always exchanged as plain JSON-able dicts across this
    # boundary (``.to_json()``/``.from_json()`` on S2-A's dataclasses), never
    # as the dataclass types themselves, so this module never needs an eager
    # import of a package that may not exist.

    def read_setup(self) -> dict[str, object] | None:
        """Return the saved prefs' JSON, or ``None`` if none are saved yet."""
        ...

    def write_setup(self, prefs_fields: dict[str, object]) -> dict[str, object]:
        """Validate, then ``save_prefs`` + update ``find-jobs.json`` atomically.

        Returns the saved prefs' JSON. ``prefs_fields`` is already validated
        (see ``_validate_setup_body``) by the time this is called.
        """
        ...

    def start_discovery(self, on_progress: Callable[[dict[str, object]], None]) -> str:
        """Start a background discovery run; return its ``discovery_id``."""
        ...

    def latest_discovery(self) -> dict[str, object] | None:
        """Return the latest ``DiscoveryResult``'s JSON, or ``None`` if none exists."""
        ...

    def discovery_running(self) -> bool: ...


class NotWiredBackend:
    """Explicit test fallback; ``__main__`` installs ``ScoutFindJobsBackend``."""

    def _not_wired(self) -> None:
        raise NotImplementedError("scout_present_api backend is not wired yet")

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        self._not_wired()
        raise AssertionError("unreachable")

    def resume_preview(self) -> PinnedResume | None:
        self._not_wired()
        raise AssertionError("unreachable")

    def start_run(
        self,
        run_request: RunRequest,
        config_bytes: bytes,
        on_run_allocated: Callable[[str], None],
    ) -> None:
        self._not_wired()

    def run_status(self, run_id: str) -> RunStatusResponse:
        self._not_wired()
        raise AssertionError("unreachable")

    def run_results(self, run_id: str) -> RunResultsResponse:
        self._not_wired()
        raise AssertionError("unreachable")

    def run_progress(self, run_id: str) -> dict[str, object]:
        self._not_wired()
        raise AssertionError("unreachable")

    def read_setup(self) -> dict[str, object] | None:
        self._not_wired()
        raise AssertionError("unreachable")

    def write_setup(self, prefs_fields: dict[str, object]) -> dict[str, object]:
        self._not_wired()
        raise AssertionError("unreachable")

    def start_discovery(self, on_progress: Callable[[dict[str, object]], None]) -> str:
        self._not_wired()
        raise AssertionError("unreachable")

    def latest_discovery(self) -> dict[str, object] | None:
        self._not_wired()
        raise AssertionError("unreachable")

    def discovery_running(self) -> bool:
        self._not_wired()
        raise AssertionError("unreachable")


class _RunBoundaryError(FindJobsContractError):
    """A typed pre-allocation error with its route-level HTTP status."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(code, message)
        self.status = status


class ConfigMissingError(LookupError):
    """``find-jobs.json`` doesn't exist for the target yet.

    Distinct from a generic ``LookupError`` (used elsewhere for "not found"
    routes like an unknown run id) so ``/api/config`` can return a message
    that names the actual missing file and the commands that create it,
    instead of the generic "not found" text the UI used to show for every
    404 (see api.js's old blanket "That run could not be found.").
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        super().__init__(str(path))


class DiscoveryUnavailableError(RuntimeError):
    """``gigai.scout.find_jobs.discovery`` (S2-A) is not importable yet.

    Raised by ``ScoutFindJobsBackend`` so the HTTP layer can return 503
    ``discovery_unavailable`` instead of a raw import error -- the setup/
    discover routes must not prevent the rest of the API from starting or
    serving while S2-A hasn't landed (see the CHANGE #3 packet note).
    """


class SetupPrefsMissingError(LookupError):
    """No discovery prefs have been saved for this target yet."""


class SetupValidationError(FindJobsContractError):
    """A ``PUT /api/setup`` body failed field-level validation.

    ``field_errors`` maps a field name to a human-readable message so the
    UI can show per-field errors instead of one opaque string (CHANGE #1:
    "field errors as 400 with per-field messages").
    """

    def __init__(self, field_errors: dict[str, str]) -> None:
        self.field_errors = dict(field_errors)
        joined = "; ".join(f"{field}: {message}" for field, message in self.field_errors.items())
        super().__init__("invalid_value", joined or "invalid setup")


class DiscoveryConflictError(RuntimeError):
    """A discovery run is already in progress (one-at-a-time)."""


_WORK_MODES = ("remote", "hybrid", "onsite", "any")
_COUNTRY_CODE = re.compile(r"\A[A-Z]{2}\Z")


def _setup_field_string_list(body: dict[str, object], field: str, errors: dict[str, str]) -> tuple[str, ...]:
    value = body.get(field, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors[field] = f"{field} must be an array of strings"
        return ()
    return tuple(value)


def _setup_field_optional_string(body: dict[str, object], field: str, errors: dict[str, str]) -> str | None:
    value = body.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        errors[field] = f"{field} must be a non-empty string or null"
        return None
    return value


def _setup_field_bool(body: dict[str, object], field: str, errors: dict[str, str], *, default: bool) -> bool:
    if field not in body:
        return default
    value = body[field]
    if not isinstance(value, bool):
        errors[field] = f"{field} must be a boolean"
        return default
    return value


def _setup_countries(body: dict[str, object], errors: dict[str, str]) -> tuple[str, ...]:
    codes = _setup_field_string_list(body, "countries", errors)
    if "countries" in errors:
        return ()
    for index, code in enumerate(codes):
        if not _COUNTRY_CODE.fullmatch(code):
            errors["countries"] = f"countries[{index}] must be an ISO-3166 alpha-2 code"
            return ()
    return codes


def _setup_work_mode(body: dict[str, object], errors: dict[str, str]) -> str:
    value = body.get("work_mode")
    if value not in _WORK_MODES:
        errors["work_mode"] = f"work_mode must be one of {', '.join(_WORK_MODES)}"
        return "any"
    return value


def _setup_cadence_days(body: dict[str, object], errors: dict[str, str]) -> int:
    if "cadence_days" not in body:
        return 7
    value = body["cadence_days"]
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        errors["cadence_days"] = "cadence_days must be a positive integer"
        return 7
    return value


def _setup_budget_usd_per_session(body: dict[str, object], errors: dict[str, str]) -> float:
    if "budget_usd_per_session" not in body:
        return 0.50
    value = body["budget_usd_per_session"]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        errors["budget_usd_per_session"] = "budget_usd_per_session must be a positive number"
        return 0.50
    return float(value)


def _validate_setup_body(body: object) -> dict[str, object]:
    """Validate a ``PUT /api/setup`` body against the 11 S23 interview fields.

    Returns a plain dict shaped exactly like ``DiscoveryPrefs`` fields
    (S2-A's frozen dataclass); the caller constructs the real dataclass so
    this module never has to import it eagerly (see CHANGE #3). Raises
    ``SetupValidationError`` (-> 400 with per-field messages) on any bad
    field; unknown top-level keys are also rejected to fail closed on typos.
    """

    if not isinstance(body, dict):
        raise SetupValidationError({"_": "request body must be a JSON object"})
    known_keys = {
        "roles",
        "titles_to_avoid",
        "countries",
        "work_mode",
        "city",
        "visa_sponsorship_required",
        "exclude_companies",
        "watch_companies",
        "company_stage_size",
        "industries_include",
        "industries_exclude",
        "must_have_stack",
        "dealbreaker_stack",
        "cadence_days",
        "budget_usd_per_session",
    }
    errors: dict[str, str] = {}
    unknown = set(body) - known_keys
    if unknown:
        errors["_"] = f"unknown field(s): {sorted(unknown)}"

    roles = _setup_field_string_list(body, "roles", errors)
    if "roles" not in errors and not roles:
        errors["roles"] = "roles must not be empty"
    titles_to_avoid = _setup_field_string_list(body, "titles_to_avoid", errors)
    countries = _setup_countries(body, errors)
    work_mode = _setup_work_mode(body, errors)
    city = _setup_field_optional_string(body, "city", errors)
    visa_sponsorship_required = _setup_field_bool(body, "visa_sponsorship_required", errors, default=False)
    exclude_companies = _setup_field_string_list(body, "exclude_companies", errors)
    watch_companies = _setup_field_string_list(body, "watch_companies", errors)
    company_stage_size = _setup_field_optional_string(body, "company_stage_size", errors)
    industries_include = _setup_field_string_list(body, "industries_include", errors)
    industries_exclude = _setup_field_string_list(body, "industries_exclude", errors)
    must_have_stack = _setup_field_string_list(body, "must_have_stack", errors)
    dealbreaker_stack = _setup_field_string_list(body, "dealbreaker_stack", errors)
    cadence_days = _setup_cadence_days(body, errors)
    budget_usd_per_session = _setup_budget_usd_per_session(body, errors)

    if errors:
        raise SetupValidationError(errors)

    return {
        "roles": roles,
        "titles_to_avoid": titles_to_avoid,
        "countries": countries,
        "work_mode": work_mode,
        "city": city,
        "visa_sponsorship_required": visa_sponsorship_required,
        "exclude_companies": exclude_companies,
        "watch_companies": watch_companies,
        "company_stage_size": company_stage_size,
        "industries_include": industries_include,
        "industries_exclude": industries_exclude,
        "must_have_stack": must_have_stack,
        "dealbreaker_stack": dealbreaker_stack,
        "cadence_days": cadence_days,
        "budget_usd_per_session": budget_usd_per_session,
    }


def _prefs_prefill_from_config(config: FindJobsConfig) -> dict[str, object]:
    """Derive a ``PUT /api/setup``-shaped pre-fill from the current find-jobs.json.

    Used for the 404 ``prefs_missing`` response's pre-fill payload (CHANGE
    #1) -- only the fields ``FindJobsConfig`` actually carries are filled;
    the rest default the same way ``_validate_setup_body`` would.
    """

    work_mode = "remote" if config.remote else ("any" if config.location is None else "onsite")
    return {
        "roles": list(config.roles),
        "titles_to_avoid": [],
        "countries": list(config.countries),
        "work_mode": work_mode,
        "city": config.location,
        "visa_sponsorship_required": config.visa_sponsorship_required,
        "exclude_companies": [],
        "watch_companies": [],
        "company_stage_size": None,
        "industries_include": [],
        "industries_exclude": [],
        "must_have_stack": [],
        "dealbreaker_stack": [],
        "cadence_days": 7,
        "budget_usd_per_session": 0.50,
    }


def _atomic_write_json(path: Path, payload: dict[str, object]) -> None:
    """Write ``payload`` to ``path`` atomically (write-temp, fsync, replace)."""

    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json_bytes(payload)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class ScoutFindJobsBackend:
    """The production localhost backend for the Scout find-jobs API."""

    def __init__(self, *, home_root: Path, target: Path | None) -> None:
        self.home_root = Path(home_root).expanduser().resolve(strict=False)
        self.target = (
            Path(target).expanduser().resolve(strict=False)
            if target is not None
            else None
        )
        # CHANGE #1: "one at a time -> 409 if running". A single process-wide
        # lock plus an in-memory progress snapshot for the currently (or most
        # recently) running discovery -- ``run_discovery`` itself is
        # synchronous, so the handler thread that calls it holds this lock
        # for the run's whole duration; a second POST while held is refused
        # before a second background thread is even started.
        self._discovery_lock = threading.Lock()
        self._discovery_running = False
        self._discovery_progress: dict[str, object] | None = None

    def _target_root(self) -> Path:
        if self.target is None:
            raise LookupError("a target path is required")
        return self.target

    def _resolved_run(self, run_id: str):
        from ...workpad import resolve_workpad

        return resolve_workpad(
            home_root=self.home_root,
            requested_target=self._target_root(),
            gig_id=None,
            allow_semantic_state=True,
        )

    def _require_run(self, run_id: str):
        resolved = self._resolved_run(run_id)
        run_path = resolved.path / "runs" / run_id / "run-details.json"
        if run_path.is_symlink() or not run_path.is_file():
            raise LookupError(run_id)
        return resolved

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        path = self._target_root() / "find-jobs.json"
        if path.is_symlink() or not path.is_file():
            raise ConfigMissingError(path)
        try:
            from ...canonical import parse_json_bytes

            config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
        except FindJobsContractError:
            raise
        except (OSError, ValueError) as exc:
            raise FindJobsContractError("invalid_value", "find-jobs.json is not valid JSON") from exc
        return config, canonical_json_bytes(config.to_json())

    def resume_preview(self) -> PinnedResume | None:
        from ... import run

        try:
            return run.resolve_newest_resume(self.home_root, self._target_root())
        except RunError as exc:
            if str(exc).startswith("find_jobs_resume_required:"):
                return None
            raise

    def start_run(
        self,
        run_request: RunRequest,
        config_bytes: bytes,
        on_run_allocated: Callable[[str], None],
    ) -> None:
        from ... import run
        from .bindings import register_find_jobs_nodes

        target = self._target_root()
        # This call binds the parent (for the launch hook) and causes the
        # spawned child to bind itself before executing any Goal.
        register_find_jobs_nodes(home_root=self.home_root, target=target)
        try:
            run_id = run.launch_find_jobs_run(
                home_root=self.home_root,
                target=target,
                run_request=run_request,
                config_bytes=config_bytes,
                ui_loopback_verified=True,
            )
        except RunError as exc:
            message = str(exc)
            lowered = message.casefold()
            if "digest_mismatch" in lowered:
                raise _RunBoundaryError(HTTPStatus.CONFLICT, "config_digest_mismatch", message) from exc
            if "consent" in lowered or "loopback" in lowered:
                raise _RunBoundaryError(HTTPStatus.FORBIDDEN, "consent_required", message) from exc
            raise _RunBoundaryError(HTTPStatus.UNPROCESSABLE_ENTITY, "run_start_failed", message) from exc
        on_run_allocated(run_id)

    def _payload(self, run_id: str):
        from ..projection import build_present_payload

        self._require_run(run_id)
        return build_present_payload(
            home_root=self.home_root,
            target=self._target_root(),
            run_id=run_id,
        )

    def run_status(self, run_id: str) -> RunStatusResponse:
        from ... import run

        self._require_run(run_id)
        details = run.read_run_details(
            home_root=self.home_root,
            requested_target=self._target_root(),
            run_id=run_id,
        )
        payload = self._payload(run_id)
        detail_status = str(details.get("status", "running"))
        if detail_status == "preparing":
            status = AggregateStatus.PENDING
        elif detail_status in {"running", "verifying"}:
            status = AggregateStatus.RUNNING
        else:
            try:
                status = AggregateStatus(detail_status)
            except ValueError:
                status = payload.status
        receipts = payload.node_receipts
        # A partial receipt set is not enough to satisfy the DTO invariant
        # while the scheduler is still running; terminal reads expose all
        # receipts and the C-1 payload remains the result authority.
        if status in {AggregateStatus.PENDING, AggregateStatus.RUNNING}:
            receipts = ()
        return RunStatusResponse(run_id, status, receipts)

    def run_results(self, run_id: str) -> RunResultsResponse:
        payload = self._payload(run_id)
        return RunResultsResponse(run_id, payload)

    def run_progress(self, run_id: str) -> dict[str, object]:
        """B4: the non-authoritative live-progress view for one run.

        Reads the ``progress/`` files (``.progress.read_progress``) plus
        whatever sealed outputs already exist (via ``_payload``, which
        degrades to empty when a node hasn't produced its output yet) so a
        posting that's already in the sealed ``outputs/acquire.json`` is
        never missing here just because acquire finished between two polls
        and its progress line predates this read. The sealed payload is
        folded in additively -- it never overrides a progress-only field
        (e.g. an in-flight "assessing" status) with a stale/absent one.
        """
        from .progress import read_progress

        resolved = self._require_run(run_id)
        run_root = resolved.path / "runs" / run_id
        snapshot = read_progress(run_root)

        payload = self._payload(run_id)

        already_recorded: set[object] = {
            item.get("normalized_url") for item in snapshot.assessments if item.get("status") == "not_assessed"
        }

        postings_by_url: dict[str, dict[str, object]] = {}
        for item in snapshot.postings:
            url = item.get("normalized_url")
            if isinstance(url, str):
                postings_by_url[url] = item
        for row in payload.rows:
            url = row.posting.normalized_url
            if url not in postings_by_url:
                postings_by_url[url] = {**row.posting.to_json(), "outcome": row.outcome.value}

        assessments_by_url: dict[str, dict[str, object]] = {}
        for item in snapshot.assessments:
            url = item.get("normalized_url")
            if isinstance(url, str):
                assessments_by_url[url] = dict(item)
        for assessment in payload.assessments:
            url = assessment.posting.normalized_url
            entry = assessments_by_url.setdefault(url, {"normalized_url": url})
            entry["status"] = "assessed"
            entry["assessment"] = assessment.to_json()
        not_assessed_counts = dict(snapshot.not_assessed_counts)
        for row in payload.not_assessed:
            url = row.posting.normalized_url
            entry = assessments_by_url.setdefault(url, {"normalized_url": url})
            if entry.get("status") != "assessed":
                entry["status"] = "not_assessed"
                entry["reason"] = row.reason.value
            # Only count a sealed not-assessed row that assess.jsonl never
            # recorded (e.g. an older run dir, or a race where the sealed
            # output landed between two polls) -- otherwise this would
            # double-count against snapshot.not_assessed_counts.
            if url not in already_recorded:
                reason_value = row.reason.value
                not_assessed_counts[reason_value] = not_assessed_counts.get(reason_value, 0) + 1

        cap = snapshot.cap if snapshot.cap is not None else self._sealed_selection_cap(run_id)

        return {
            "schema_version": "scout-find-jobs-progress:1",
            "run_id": run_id,
            "steps": dict(snapshot.steps),
            "postings": list(postings_by_url.values()),
            "assessments": list(assessments_by_url.values()),
            "cap": cap,
            "candidate_count": snapshot.candidate_count,
            "not_assessed_counts": not_assessed_counts,
        }

    def _sealed_selection_cap(self, run_id: str) -> int | None:
        """Fall back to the sealed run input's cap if acquire hasn't written cap.json yet."""

        from ...canonical import parse_json_bytes

        path = self._target_root() / "runs" / run_id / "sealed" / "find-jobs-run-input.json"
        if path.is_symlink() or not path.is_file():
            return None
        try:
            payload = parse_json_bytes(path.read_bytes())
        except (OSError, ValueError):
            return None
        cap = payload.get("selection_cap") if isinstance(payload, dict) else None
        return cap if isinstance(cap, int) else None

    @staticmethod
    def _discovery_module():
        """Lazily import S2-A's discovery package.

        CHANGE #3: "Import it as ``from gigai.scout.find_jobs import
        discovery`` lazily inside the handlers so the API still starts if the
        module is absent." Every setup/discover backend method routes
        through this so a missing package degrades to
        ``DiscoveryUnavailableError`` (-> 503 ``discovery_unavailable``)
        rather than an import error at module load time.
        """

        try:
            from gigai.scout.find_jobs import discovery
        except ModuleNotFoundError as exc:
            raise DiscoveryUnavailableError("the discovery module is not available yet") from exc
        return discovery

    def read_setup(self) -> dict[str, object] | None:
        discovery = self._discovery_module()
        prefs = discovery.load_prefs(home_root=self.home_root, target=self._target_root())
        return prefs.to_json() if prefs is not None else None

    def write_setup(self, prefs_fields: dict[str, object]) -> dict[str, object]:
        discovery = self._discovery_module()
        prefs = discovery.DiscoveryPrefs(**prefs_fields)
        discovery.save_prefs(home_root=self.home_root, target=self._target_root(), prefs=prefs)
        self._update_find_jobs_config(prefs_fields)
        return prefs.to_json()

    def _update_find_jobs_config(self, prefs_fields: dict[str, object]) -> None:
        """Apply the setup answers onto ``find-jobs.json``, keeping every other field.

        CHANGE #1: writes ``roles``, ``merged_queries`` (mirrored from
        ``roles``, matching the starter config's own convention -- see
        ``scout_cli.STARTER_FIND_JOBS_CONFIG``), ``location`` (from
        ``city``), ``remote`` (derived from ``work_mode``), ``countries``,
        and ``visa_sponsorship_required``. Every other ``FindJobsConfig``
        field (``published_after``, ``sources``, ``default_assess_cap``,
        ``default_model_target``) is read from the existing file and kept
        unchanged. If no ``find-jobs.json`` exists yet, one is written from
        ``FindJobsConfig``'s own defaults for the untouched fields plus the
        interview answers -- the interview is allowed to run before a target
        has ever been configured.
        """

        path = self._target_root() / "find-jobs.json"
        roles: tuple[str, ...] = tuple(prefs_fields["roles"])  # type: ignore[arg-type]
        work_mode = prefs_fields["work_mode"]
        remote = work_mode in ("remote", "any")
        city: str | None = prefs_fields["city"]  # type: ignore[assignment]
        countries: tuple[str, ...] = tuple(prefs_fields["countries"])  # type: ignore[arg-type]
        visa_sponsorship_required = bool(prefs_fields["visa_sponsorship_required"])

        if path.is_symlink() or not path.is_file():
            config = FindJobsConfig(
                roles=roles,
                merged_queries=roles,
                location=city,
                remote=remote,
                published_after=None,
                sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
                default_assess_cap=10,
                default_model_target=ModelTarget.OLLAMA_LOCAL,
                countries=countries,
                visa_sponsorship_required=visa_sponsorship_required,
            )
        else:
            existing = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
            config = FindJobsConfig(
                roles=roles,
                merged_queries=roles,
                location=city,
                remote=remote,
                published_after=existing.published_after,
                sources=existing.sources,
                default_assess_cap=existing.default_assess_cap,
                default_model_target=existing.default_model_target,
                countries=countries,
                visa_sponsorship_required=visa_sponsorship_required,
            )
        _atomic_write_json(path, config.to_json())

    def start_discovery(self, on_progress: Callable[[dict[str, object]], None]) -> str:
        """Start ``run_discovery`` on a background thread; return a request id.

        ``run_discovery`` is synchronous and may run 5-30 minutes (contract),
        so this cannot wait for it to return a real ``discovery_id`` before
        answering the POST (CHANGE #1: "202 {discovery_id}"). The id handed
        back here is a request-scoped tracking token, not
        ``DiscoveryResult.discovery_id`` (which the contract only produces
        once the run finishes) -- ``GET /api/discover/latest`` is the
        authority for the real id and the run's live progress, exactly like
        ``/api/run``'s ``run_id`` vs. ``/api/runs/{id}`` status pattern this
        mirrors. Refuses with ``DiscoveryConflictError`` (-> 409) if a run is
        already in progress, and ``SetupPrefsMissingError`` (-> 404-ish) if
        no prefs have been saved yet.
        """

        discovery = self._discovery_module()
        acquired = self._discovery_lock.acquire(blocking=False)
        if not acquired:
            raise DiscoveryConflictError("a discovery run is already in progress")
        try:
            prefs = discovery.load_prefs(home_root=self.home_root, target=self._target_root())
            if prefs is None:
                raise SetupPrefsMissingError("no discovery prefs saved yet")
        except BaseException:
            self._discovery_lock.release()
            raise

        request_id = f"discovery_req_{uuid.uuid4().hex}"
        started_at = datetime.now(timezone.utc).isoformat()
        self._discovery_running = True
        self._discovery_progress = {
            "discovery_id": request_id,
            "status": "running",
            "started_at": started_at,
            "finished_at": None,
            "cost_usd": 0.0,
            "sources": [],
            "new_boards": [],
            "skipped": {},
        }

        def _capture_progress(event: dict[str, object]) -> None:
            # P0-5: `event` is the raw, small progress-step dict
            # run_discovery emits (e.g. {"stage": "discovery_start", ...}),
            # not a DiscoveryResult -- overwriting the snapshot with it wipes
            # cost_usd/sources/new_boards that GET /api/discover/latest
            # promises mid-run, so keep the DiscoveryResult-shaped snapshot
            # as the source of truth and nest the raw event under "progress".
            snapshot = dict(self._discovery_progress or {})
            snapshot["progress"] = event
            self._discovery_progress = snapshot
            on_progress(event)

        def _run() -> None:
            try:
                result = discovery.run_discovery(
                    home_root=self.home_root,
                    target=self._target_root(),
                    prefs=prefs,
                    on_progress=_capture_progress,
                )
                self._discovery_progress = result.to_json()
            except Exception as exc:
                # run_discovery's own contract is "never raises for a
                # provider error"; anything that does escape it (e.g. S2-A's
                # DiscoveryBudgetExceeded, raised before any spend) is a
                # pre-flight failure this thread must still record instead
                # of silently dropping -- nothing else observes this thread,
                # so an uncaught exception here would otherwise vanish and
                # leave the UI's Discover panel stuck on "running" forever.
                self._discovery_progress = {
                    "discovery_id": request_id,
                    "status": "failed",
                    "started_at": started_at,
                    "finished_at": None,
                    "cost_usd": 0.0,
                    "sources": [],
                    "new_boards": [],
                    "skipped": {},
                    "error": str(exc),
                }
            finally:
                self._discovery_running = False
                self._discovery_lock.release()

        threading.Thread(target=_run, daemon=True).start()
        return request_id

    def latest_discovery(self) -> dict[str, object] | None:
        discovery = self._discovery_module()
        if self._discovery_running and self._discovery_progress is not None:
            return dict(self._discovery_progress)
        result = discovery.latest_discovery(home_root=self.home_root, target=self._target_root())
        return result.to_json() if result is not None else None

    def discovery_running(self) -> bool:
        return self._discovery_running


RUN_START_TIMEOUT_SECONDS = 30.0


def _error_body(code: str, message: str) -> dict[str, object]:
    return {"error": {"code": code, "message": message}}


def _days_ago(iso_timestamp: str) -> int | None:
    """Whole days between ``iso_timestamp`` and now, for the Discover panel's

    "last run N days ago". Returns ``None`` (rather than raising) for a
    timestamp this process can't parse -- a display-only convenience field
    should never break the route over a malformed/foreign timestamp.
    """

    try:
        parsed = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - parsed
    return max(0, delta.days)


# The packaged UI: built by `yarn build` in src/gigai/scout/ui and committed
# under ui/dist (pyproject.toml package-data ships it in the wheel/sdist).
# Read via importlib.resources, anchored on the real "gigai.scout" package
# and joined onto "ui/dist" (ui/ itself has no __init__.py, so it isn't an
# importable package/resource anchor of its own) so this works from an
# installed wheel, not just a source checkout.
_UI_DIST_ANCHOR_PACKAGE = "gigai.scout"
_UI_DIST_RELATIVE_PARTS = ("ui", "dist")
_UI_MISSING_MESSAGE = (
    "the Scout UI is not built: run `yarn build` in src/gigai/scout/ui "
    "(this is a source checkout without a built UI)"
)

_STATIC_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".ico": "image/x-icon",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".txt": "text/plain; charset=utf-8",
}


def _static_content_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return _STATIC_CONTENT_TYPES.get(suffix, "application/octet-stream")


def _ui_dist_root():
    """Return the packaged ``ui/dist`` resource root, or ``None`` if unbuilt.

    A source checkout without a build has no ``ui/dist`` directory at all
    (it's gitignored-turned-tracked only once built); an installed wheel
    always has it because package-data ships it. Either way, a missing or
    empty root is treated the same: not built.
    """

    try:
        root = resources.files(_UI_DIST_ANCHOR_PACKAGE)
    except ModuleNotFoundError:
        return None
    for part in _UI_DIST_RELATIVE_PARTS:
        root = root.joinpath(part)
    if not root.is_dir():
        return None
    return root


def _resolve_static_resource(path: str):
    """Resolve a URL path to a traversal-safe resource under ``ui/dist``.

    Returns ``None`` if the dist root is missing, the path escapes the
    dist root, or the resource doesn't exist as a file. ``path`` is the
    already-percent-decoded, query-stripped request path (e.g. ``/`` or
    ``/assets/index-abc123.js``).
    """

    root = _ui_dist_root()
    if root is None:
        return None
    normalized = posixpath.normpath(path)
    if normalized in ("/", ".", ""):
        relative = "index.html"
    else:
        relative = normalized.lstrip("/")
    # normpath collapses ".." segments together, so any remaining ".."
    # component means the request tried to climb out of dist/; reject it
    # rather than resolve it away, to fail closed on traversal attempts.
    parts = relative.split("/")
    if ".." in parts or any(not part for part in parts):
        return None
    resource = root
    for part in parts:
        resource = resource.joinpath(part)
    if not resource.is_file():
        return None
    return resource


def _make_handler(
    backend: Backend,
    *,
    run_start_timeout_seconds: float = RUN_START_TIMEOUT_SECONDS,
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def handle_one_request(self) -> None:
            self._gigai_request_started_at = time.monotonic()
            super().handle_one_request()

        def log_message(self, _format: str, *args: object) -> None:
            """Replace stdlib's stderr-only request log with one via ``_logger``.

            Never uses ``_format``/``self.requestline``/``args`` directly for
            the path -- ``log_request`` (the only real caller, from
            ``send_response``) passes ``(self.requestline, code, size)``, and
            ``requestline`` is the raw ``"GET /api/health?x=y HTTP/1.1"``
            line built straight from the wire before any of this module's
            own parsing runs. This rebuilds the line from ``self.command``
            and a query-stripped ``self.path`` instead, and reads the status
            out of ``args[1]`` (the one field ``log_request`` guarantees),
            never out of the free-form ``requestline``.
            """

            status = args[1] if len(args) > 1 else "-"
            path = urlsplit(self.path).path if self.path else "-"
            started_at = getattr(self, "_gigai_request_started_at", None)
            duration_ms = (time.monotonic() - started_at) * 1000 if started_at is not None else -1.0
            _logger.info(
                "%s %s %s %.1fms",
                self.command or "-",
                path,
                status,
                duration_ms,
            )

        def _write_json(self, status: int, payload: dict[str, object]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _error(self, status: int, code: str, message: str) -> None:
            self._write_json(status, _error_body(code, message))

        def _error_with_extra(self, status: int, code: str, message: str, extra: dict[str, object]) -> None:
            body = _error_body(code, message)
            body["error"] = {**body["error"], **extra}  # type: ignore[dict-item]
            self._write_json(status, body)

        def _log_rejection(self, reason: str) -> None:
            """Log a loopback/CSRF/Origin rejection: route + reason, never headers."""

            path = urlsplit(self.path).path if self.path else "-"
            _logger.warning("rejected %s %s: %s", self.command or "-", path, reason)

        def _check_loopback(self) -> bool:
            peer_host = self.client_address[0]
            if peer_host not in {"127.0.0.1", "::1"}:
                self._log_rejection("forbidden: peer is not loopback")
                self._error(HTTPStatus.FORBIDDEN, "forbidden", "peer must be loopback")
                return False
            return True

        def _bound_port(self) -> int:
            return self.server.server_address[1]  # type: ignore[attr-defined]

        def _check_csrf(self) -> bool:
            """CSRF guard for every state-changing route (POST/PUT/PATCH/DELETE).

            Loopback alone (``_check_loopback``) checks *who* the peer is, not
            *where the request came from*: a malicious web page open in the
            operator's own browser is also loopback. A "simple" cross-origin
            request (the shape ``fetch(url, {mode: "no-cors"})`` is allowed to
            send) cannot set ``Content-Type: application/json`` without
            triggering a CORS preflight, and this server never answers a
            preflight with ``Access-Control-Allow-Origin`` -- so requiring JSON
            here is what actually blocks the browser, not the check itself.

            Three checks, in order:
            1. ``Content-Type`` must be ``application/json`` (ignoring any
               ``; charset=...`` suffix) -> 415 ``unsupported_media_type`` if not.
            2. ``Origin``, when the header is present at all, must equal this
               server's own served origin for the bound port (``http://
               127.0.0.1:<port>`` or ``http://localhost:<port>``) -> 403
               ``forbidden_origin`` if not. No ``Origin`` header at all (a
               same-origin fetch, curl, or the CLI) is allowed through --
               browsers always send Origin on cross-origin writes, so its
               absence here is not the attack this guards against.
            3. ``Host`` must match the bound host:port (DNS-rebinding guard:
               without this, a DNS name an attacker controls but that resolves
               to 127.0.0.1 lets a real browser send a same-origin-looking,
               honest ``Origin`` header that would otherwise pass check 2)
               -> 403 ``forbidden_origin`` if not.

            Never sets ``Access-Control-Allow-Origin`` -- there is no
            cross-origin caller this API is meant to serve.
            """

            content_type = self.headers.get("Content-Type", "")
            media_type = content_type.split(";", 1)[0].strip().lower()
            if media_type != "application/json":
                self._log_rejection("unsupported_media_type: Content-Type must be application/json")
                self._error(
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                    "unsupported_media_type",
                    "Content-Type must be application/json",
                )
                return False

            port = self._bound_port()
            allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
            origin = self.headers.get("Origin")
            if origin is not None and origin not in allowed_origins:
                self._log_rejection("forbidden_origin: request Origin is not allowed")
                self._error(HTTPStatus.FORBIDDEN, "forbidden_origin", "request Origin is not allowed")
                return False

            allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
            host = self.headers.get("Host")
            if host not in allowed_hosts:
                self._log_rejection("forbidden_origin: request Host does not match the bound server")
                self._error(HTTPStatus.FORBIDDEN, "forbidden_origin", "request Host does not match the bound server")
                return False

            return True

        def _read_json_body(self) -> object | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "Content-Length is invalid")
                return None
            if length < 0:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "invalid_value", "Content-Length is invalid")
                return None
            raw = self.rfile.read(length) if length > 0 else b""
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, "wrong_type", "request body is not valid JSON")
                return None

        def do_GET(self) -> None:  # noqa: N802
            if not self._check_loopback():
                return
            path = urlsplit(self.path).path
            try:
                if path.startswith("/api/"):
                    if path == "/api/health":
                        self._write_json(HTTPStatus.OK, {"status": "ok"})
                        return
                    if path == "/api/config":
                        self._handle_get_config()
                        return
                    if path == "/api/setup":
                        self._handle_get_setup()
                        return
                    if path == "/api/discover/latest":
                        self._handle_get_discover_latest()
                        return
                    run_id = _match_run_id(path, suffix="/results")
                    if run_id is not None:
                        self._handle_get_run_results(run_id)
                        return
                    run_id = _match_run_id(path, suffix="/progress")
                    if run_id is not None:
                        self._handle_get_run_progress(run_id)
                        return
                    run_id = _match_run_id(path, suffix="")
                    if run_id is not None:
                        self._handle_get_run_status(run_id)
                        return
                    self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
                    return
                self._handle_get_static(path)
            except Exception:  # noqa: BLE001 - last-resort boundary so the connection never just drops
                _logger.exception("unhandled exception in GET %s", path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "an internal error occurred")

        def _handle_get_static(self, path: str) -> None:
            if _ui_dist_root() is None:
                if path in ("/", ""):
                    self._error(HTTPStatus.SERVICE_UNAVAILABLE, "ui_not_built", _UI_MISSING_MESSAGE)
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
                return
            resource = _resolve_static_resource(path)
            if resource is None:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
                return
            body = resource.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", _static_content_type(resource.name))
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if not self._check_loopback():
                return
            if not self._check_csrf():
                return
            path = urlsplit(self.path).path
            try:
                if path == "/api/run":
                    self._handle_post_run()
                    return
                if path == "/api/discover":
                    self._handle_post_discover()
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
            except Exception:  # noqa: BLE001 - same last-resort boundary as do_GET
                _logger.exception("unhandled exception in POST %s", path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "an internal error occurred")

        def do_PUT(self) -> None:  # noqa: N802
            if not self._check_loopback():
                return
            if not self._check_csrf():
                return
            path = urlsplit(self.path).path
            try:
                if path == "/api/setup":
                    self._handle_put_setup()
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
            except Exception:  # noqa: BLE001 - same last-resort boundary as do_GET
                _logger.exception("unhandled exception in PUT %s", path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "an internal error occurred")

        def _handle_get_config(self) -> None:
            try:
                config, _config_bytes = backend.read_config()
            except ConfigMissingError as exc:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "config_missing",
                    f"{exc.path} does not exist yet. Run `gigai scout install` or "
                    "`gigai scout run` to write a starter find-jobs.json, then edit it.",
                )
                return
            except FindJobsContractError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
                return
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "config not found")
                return
            resume_preview = backend.resume_preview()
            config_digest = config.digest()
            payload = {
                "schema_version": "scout-find-jobs-config-response:1",
                "config": config.to_json(),
                "resume_preview": resume_preview.to_json() if resume_preview is not None else None,
                "resume_missing_hint": (
                    None if resume_preview is not None else "gigai scout resume add <file>"
                ),
                "config_digest": config_digest,
            }
            self._write_json(HTTPStatus.OK, payload)

        def _handle_get_setup(self) -> None:
            try:
                prefs_json = backend.read_setup()
            except DiscoveryUnavailableError as exc:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
                return
            if prefs_json is not None:
                self._write_json(
                    HTTPStatus.OK,
                    {"schema_version": "scout-find-jobs-setup-response:1", "prefs": prefs_json},
                )
                return
            # CHANGE #1: 404 prefs_missing carries a pre-fill derived from the
            # current find-jobs.json where possible, so the UI's first-run
            # interview starts from the operator's existing config instead of
            # a blank form.
            try:
                config, _config_bytes = backend.read_config()
                prefill = _prefs_prefill_from_config(config)
            except (ConfigMissingError, FindJobsContractError, LookupError):
                prefill = _prefs_prefill_from_config(
                    FindJobsConfig(
                        roles=(),
                        merged_queries=(),
                        location=None,
                        remote=True,
                        published_after=None,
                        sources=SourceToggles(exa=True, ats=True, hiringcafe=False),
                    )
                )
            self._error_with_extra(
                HTTPStatus.NOT_FOUND,
                "prefs_missing",
                "no discovery preferences saved yet; complete the setup interview",
                {"prefill": prefill},
            )

        def _handle_put_setup(self) -> None:
            body = self._read_json_body()
            if body is None:
                return
            try:
                prefs_fields = _validate_setup_body(body)
            except SetupValidationError as exc:
                self._error_with_extra(
                    HTTPStatus.BAD_REQUEST,
                    exc.code,
                    str(exc),
                    {"field_errors": exc.field_errors},
                )
                return
            try:
                prefs_json = backend.write_setup(prefs_fields)
            except DiscoveryUnavailableError as exc:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
                return
            # Field *names* only -- prefs_fields carries the operator's actual
            # roles/companies/etc, which must never reach the log (ticket:
            # "setup saved (which fields changed, not values)").
            _logger.info("setup saved: fields=%s", sorted(prefs_fields))
            self._write_json(
                HTTPStatus.OK,
                {"schema_version": "scout-find-jobs-setup-response:1", "prefs": prefs_json},
            )

        def _handle_post_discover(self) -> None:
            def _on_progress(event: dict[str, object]) -> None:
                # ``run_discovery`` (gigai.scout.find_jobs.discovery) never
                # raises for a provider error; it reports success/partial/
                # failure through the "discovery_done" progress event's
                # "status" field instead of an exception, so "finished" and
                # "failed" are both observed here rather than as a caught
                # exception around start_discovery.
                if event.get("stage") == "discovery_done":
                    status = event.get("status")
                    new_boards = event.get("new_boards")
                    if status == "failed":
                        _logger.warning("discover failed: status=%s new_boards=%s", status, new_boards)
                    else:
                        _logger.info("discover finished: status=%s new_boards=%s", status, new_boards)

            try:
                request_id = backend.start_discovery(_on_progress)
            except DiscoveryUnavailableError as exc:
                _logger.warning("discover failed to start: discovery_unavailable (%s)", exc)
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
                return
            except DiscoveryConflictError as exc:
                _logger.warning("discover failed to start: discovery_running (%s)", exc)
                self._error(HTTPStatus.CONFLICT, "discovery_running", str(exc))
                return
            except SetupPrefsMissingError as exc:
                _logger.warning("discover failed to start: prefs_missing (%s)", exc)
                self._error(HTTPStatus.NOT_FOUND, "prefs_missing", str(exc))
                return
            _logger.info("discover started: discovery_id=%s", request_id)
            self._write_json(HTTPStatus.ACCEPTED, {"discovery_id": request_id})

        def _handle_get_discover_latest(self) -> None:
            try:
                result_json = backend.latest_discovery()
                running = backend.discovery_running()
            except DiscoveryUnavailableError as exc:
                self._error(HTTPStatus.SERVICE_UNAVAILABLE, "discovery_unavailable", str(exc))
                return
            days_ago = None
            if result_json is not None:
                finished_at = result_json.get("finished_at")
                if isinstance(finished_at, str):
                    days_ago = _days_ago(finished_at)
            self._write_json(
                HTTPStatus.OK,
                {
                    "schema_version": "scout-find-jobs-discover-latest-response:1",
                    "result": result_json,
                    "running": running,
                    "days_ago": days_ago,
                },
            )

        def _handle_post_run(self) -> None:
            body = self._read_json_body()
            if body is None:
                return
            try:
                run_request = RunRequest.from_json(body)
            except FindJobsContractError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
                return
            try:
                config, config_bytes = backend.read_config()
            except ConfigMissingError as exc:
                self._error(
                    HTTPStatus.NOT_FOUND,
                    "config_missing",
                    f"{exc.path} does not exist yet. Run `gigai scout install` or "
                    "`gigai scout run` to write a starter find-jobs.json, then edit it.",
                )
                return
            except FindJobsContractError as exc:
                self._error(HTTPStatus.UNPROCESSABLE_ENTITY, exc.code, str(exc))
                return
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "config not found")
                return
            if run_request.config_digest != config.digest():
                self._error(HTTPStatus.CONFLICT, "config_digest_mismatch", "config_digest does not match the current config")
                return

            allocated = threading.Event()
            allocation: dict[str, str] = {}
            pre_allocation_error: BaseException | None = None
            post_allocation_error: BaseException | None = None

            def _on_run_allocated(run_id: str) -> None:
                allocation["run_id"] = run_id
                allocated.set()

            def _run() -> None:
                nonlocal pre_allocation_error, post_allocation_error
                try:
                    backend.start_run(run_request, config_bytes, _on_run_allocated)
                except BaseException as exc:  # noqa: BLE001 - routed to the right side of allocation, not swallowed
                    if allocated.is_set():
                        post_allocation_error = exc
                    else:
                        pre_allocation_error = exc
                        allocated.set()

            thread = threading.Thread(target=_run, daemon=True)
            thread.start()
            reached = allocated.wait(run_start_timeout_seconds)
            if not reached:
                self._error(HTTPStatus.GATEWAY_TIMEOUT, "run_start_timeout", "run did not allocate a run_id in time")
                return
            if pre_allocation_error is not None:
                if isinstance(pre_allocation_error, _RunBoundaryError):
                    _logger.warning(
                        "find-jobs run failed to start: %s (%s)",
                        pre_allocation_error.code,
                        pre_allocation_error,
                    )
                    self._error(
                        pre_allocation_error.status,
                        pre_allocation_error.code,
                        str(pre_allocation_error),
                    )
                    return
                if isinstance(pre_allocation_error, FindJobsContractError):
                    _logger.warning(
                        "find-jobs run failed to start: %s (%s)",
                        pre_allocation_error.code,
                        pre_allocation_error,
                    )
                    self._error(HTTPStatus.UNPROCESSABLE_ENTITY, pre_allocation_error.code, str(pre_allocation_error))
                    return
                _logger.exception("find-jobs run failed to start", exc_info=pre_allocation_error)
                raise pre_allocation_error
            # post_allocation_error (if any) surfaces via run_status, not here: the POST
            # response is already committed to a run_id once allocation happened. A
            # "run finished" event belongs at that same layer (this handler only ever
            # observes allocation, not completion) -- out of scope for this module.
            run_id = allocation["run_id"]
            _logger.info("find-jobs run started: run_id=%s", run_id)
            payload = {
                "schema_version": "scout-find-jobs-run-response:1",
                "run_id": run_id,
                "status": "pending",
                "node_receipts": [],
            }
            self._write_json(HTTPStatus.ACCEPTED, payload)

        def _handle_get_run_status(self, run_id: str) -> None:
            try:
                status_response = backend.run_status(run_id)
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
                return
            self._write_json(HTTPStatus.OK, status_response.to_json())

        def _handle_get_run_results(self, run_id: str) -> None:
            try:
                results_response = backend.run_results(run_id)
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
                return
            self._write_json(HTTPStatus.OK, results_response.to_json())

        def _handle_get_run_progress(self, run_id: str) -> None:
            try:
                progress_response = backend.run_progress(run_id)
            except LookupError:
                self._error(HTTPStatus.NOT_FOUND, "not_found", "run not found")
                return
            self._write_json(HTTPStatus.OK, progress_response)

    return Handler


def _match_run_id(path: str, *, suffix: str) -> str | None:
    prefix = "/api/runs/"
    if not path.startswith(prefix):
        return None
    remainder = path[len(prefix) :]
    if suffix:
        if not remainder.endswith(suffix):
            return None
        run_id = remainder[: -len(suffix)]
    else:
        run_id = remainder
    if not run_id or "/" in run_id:
        return None
    return run_id


def serve(
    *,
    backend: Backend | None = None,
    bind: tuple[str, int] = API_BIND,
    run_start_timeout_seconds: float = RUN_START_TIMEOUT_SECONDS,
) -> ThreadingHTTPServer:
    """Build and start a ``ThreadingHTTPServer`` bound to ``bind``.

    Callers (including tests) own the returned server's lifecycle: start its
    serve loop on a thread and call ``shutdown()``/``server_close()`` when done.
    ``run_start_timeout_seconds`` is exposed for tests that need a short
    ``POST /api/run`` allocation timeout; production callers should leave it
    at the default.
    """

    if backend is None:
        backend = NotWiredBackend()
    _configure_logging()
    handler = _make_handler(backend, run_start_timeout_seconds=run_start_timeout_seconds)
    server = ThreadingHTTPServer(bind, handler)
    server.daemon_threads = True
    host, port = server.server_address[0], server.server_address[1]
    # "project id" here is the target root path -- the closest thing this
    # module has to a project identifier (see ``Backend``/``ScoutFindJobsBackend``:
    # there's no separate project-id concept, only home_root + target).
    project = getattr(backend, "target", None)
    _logger.info(
        "scout server starting: host=%s port=%s gigai_version=%s project=%s pid=%s",
        host,
        port,
        _gigai_version(),
        project if project is not None else "-",
        os.getpid(),
    )
    return server


def _run_forever(bind: tuple[str, int], *, backend: Backend | None = None) -> None:
    server = serve(backend=backend, bind=bind)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m gigai.scout.find_jobs.present_api")
    parser.add_argument("--target", dest="target", default=None, help="target root path")
    parser.add_argument("--home", dest="home", default=None, help="GigAI home root path")
    parser.add_argument(
        "--port",
        dest="port",
        type=int,
        default=None,
        help=f"loopback port to bind (default: {API_BIND[1]})",
    )
    parser.add_argument(
        "--allow-test-seams",
        dest="allow_test_seams",
        action="store_true",
        default=False,
        help="allow starting with GIGAI_SCOUT_FIND_JOBS_TEST_HTTP/_MODEL test seams active",
    )
    args = parser.parse_args(argv)

    active_seam_vars = [name for name in (_TEST_HTTP_ENV, _TEST_MODEL_ENV) if os.environ.get(name)]
    if active_seam_vars and not args.allow_test_seams:
        joined = " and ".join(active_seam_vars)
        print(
            f"refusing to start: {joined} is set in the environment; pass --allow-test-seams to start anyway",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if active_seam_vars:
        print("TEST SEAMS ACTIVE: results are fixture data", file=sys.stderr)

    from ...setup import default_home_root

    target = Path(args.target).expanduser().resolve(strict=False) if args.target else None
    home_root = Path(args.home).expanduser().resolve(strict=False) if args.home else default_home_root()
    bind = (API_BIND[0], args.port) if args.port is not None else API_BIND
    backend = ScoutFindJobsBackend(home_root=home_root, target=target)
    _run_forever(bind, backend=backend)


if __name__ == "__main__":
    main()
