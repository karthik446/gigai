"""R0: the ``Backend`` protocol, ``ScoutFindJobsBackend``, the HTTP
``Handler``'s shared plumbing (loopback/CSRF/dispatch), ``serve()``,
``_run_forever``, and ``main()`` -- moved out of ``present_api.py`` verbatim.

The route-specific ``_handle_*`` methods live in sibling modules
(``config.py``, ``setup.py``, ``discover.py``, ``runs.py``,
``static.py``); ``_make_handler`` below composes all of their mixins onto
one ``Handler`` class per call, exactly as ``present_api.py`` built one
``Handler`` class per call before this split. See ``present_api.py``'s own
docstring for why: F1-c/F2/F3/F4 all add routes, and a single ~1,900-line
file would serialize them.

MONKEYPATCH TRAP: a moved handler method that used to read the free
variable ``backend`` (or ``run_start_timeout_seconds``) closed over from
``_make_handler``'s own parameters now reads ``self._backend`` (resp.
``self._run_start_timeout_seconds``) instead -- a closure can't be shared
across independently-defined top-level mixin classes. ``_make_handler``
sets both as class attributes on the composed ``Handler`` once per call,
so every instance and request still sees exactly the object it saw before;
this is the only change to any moved method's body (approved: see
R0-present-api-split's report).
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Protocol
from urllib.parse import urlsplit

from ....canonical import canonical_json_bytes, parse_json_bytes
from ....run import ResumeDetails, RunError
from ..contracts import (
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
from .common import (
    RUN_START_TIMEOUT_SECONDS,
    _error_body,
    _failure_message,
    _is_transient_run_error,
    _match_run_id,
    _receipt_span_ms,
)
from .profiles import _match_profile_id

# _TEST_HTTP_ENV/_TEST_MODEL_ENV live in present_api.py now -- they're only
# read by main(), which moved there too (see the MONKEYPATCH TRAP note near
# the bottom of this module).

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

    def resume_metadata(self) -> tuple[str | None, str | None] | None:
        """``(label, created_at)`` for the resume ``resume_preview`` picked.

        ``None`` when there is no resume (mirrors ``resume_preview()``
        returning ``None``); the tuple's members can themselves be ``None``
        if the reference is missing that field. uat-bug-004: additive to
        ``resume_preview`` so the Configuration card can show
        "<label> · added <date>" instead of raw record/revision ids,
        without changing the sealed ``PinnedResume`` shape.
        """
        ...

    def resume_details(self) -> "ResumeDetails | None":
        """One resolution for both ``resume_preview()`` and ``resume_metadata()``.

        uat-bug-008: a caller that needs both no longer resolves the newest
        resume twice per request; see ``ScoutFindJobsBackend.resume_details``.
        """
        ...

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

    def carried_forward_assessments(self, run_id: str) -> tuple:
        """uat-bug-009: this run's postings skipped as unchanged, plus their earlier result.

        Additive to ``run_results``: read from acquire's own sealed output
        (``AcquireOutput.carried_forward_assessments``), never through the
        assessed/not-assessed partition ``PresentPayload`` enforces. Returns
        ``()`` for a run with no carried-forward postings.
        """
        ...

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

    def resume_metadata(self) -> tuple[str | None, str | None] | None:
        self._not_wired()
        raise AssertionError("unreachable")

    def resume_details(self) -> ResumeDetails | None:
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

    def carried_forward_assessments(self, run_id: str) -> tuple:
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


_SELECTED_PROFILE_CACHE_LOCK = threading.Lock()
# F1-b1-r1 (coordinator's lane, uat-bug-008 regression): resolving the
# selected profile (``profile_records.selected_profile``, migrate-on-first-
# read + a journal snapshot) replays the whole committed journal exactly
# like the resume resolution uat-bug-008 already fixed for -- and F1-b's
# ``read_config()``/``start_run()`` call it on every request, undoing that
# fix's latency bound. Reuse ``run.py``'s own cache MECHANISM (its cheap
# ``_cheap_workpad_head`` git-rev-parse helper, never its resume-specific
# dict) rather than inventing a second one: cache the resolved selected
# profile per workpad, keyed by the workpad's exact git HEAD. Any new
# commit (a profile edit, a selection switch, a resume add, a migration)
# changes HEAD and misses the cache, so a cached entry can never serve a
# stale selection or profile content.
_UNSET = object()  # distinguishes "not cached" from a cached "no profile" (None)
_selected_profile_cache: dict[tuple[str, str], object] = {}


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
        # uat-bug-003 follow-up: find-jobs runs execute in a spawned child
        # process (run.py's own worker), so this API only ever *observes*
        # a run's terminal status through polling -- there is no single
        # in-process call that "is" the run to log around. Track which
        # run_ids have already had their terminal status logged so a
        # multi-poll UI (or several concurrent pollers) logs "run
        # finished"/"run failed" exactly once per run, the first time this
        # process sees it go terminal, never once per poll.
        self._logged_terminal_runs: set[str] = set()
        self._logged_terminal_runs_lock = threading.Lock()

    def _target_root(self) -> Path:
        if self.target is None:
            raise LookupError("a target path is required")
        return self.target

    def _resolved_run(self, run_id: str):
        from ....workpad import resolve_workpad

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

    def _resolved_gig(self):
        """Resolve this target's active gig, once, for a profile-aware read.

        S25 F1-b: the ONLY two callers that need the gig's selected profile
        (``read_config``, ``start_run``) share this one resolution helper --
        never re-derived per field, and never imported into ``run.py``/core
        (``profile_records`` stays a Scout-only import, confined to this
        server module and ``effective_config.py``).
        """

        from ....workpad import resolve_workpad

        return resolve_workpad(
            home_root=self.home_root,
            requested_target=self._target_root(),
            gig_id=None,
            allow_semantic_state=True,
        )

    def _selected_profile(self):
        """The active gig's selected profile, or ``None`` (see ``selected_profile``'s

        own docstring for the only cases that legitimately return ``None``:
        no ``find-jobs.json`` yet, or no committed resume for this gig).
        Migrates on first read (``ensure_default_profile``); never raises for
        a not-yet-migrated gig.

        F1-b1-r1: cached per workpad, keyed by the workpad's exact git HEAD
        (``_selected_profile_cache``) -- a repeat call against an unchanged
        workpad does no journal replay at all, matching uat-bug-008's own
        resume-cache bound; any new commit misses the cache. The HEAD read
        itself reuses ``run._cheap_workpad_head`` (one cheap ``git rev-parse``,
        never the expensive snapshot walk) -- the SAME mechanism ``run.py``'s
        resume cache already uses, not a second one.

        Unlike ``run.py``'s own resume cache (a pure read, so its pre-call
        HEAD is always also its post-call HEAD), this call can ITSELF
        commit -- the first-ever call migrates a default profile into
        existence. Keying on the pre-call HEAD would cache the result under
        a head that's already stale the instant the migration lands,
        guaranteeing a miss (and a second, needlessly expensive
        idempotent-migration replay) on the very next call. The cache key is
        therefore always the POST-call HEAD.

        On an ALREADY-migrated gig (the common case, every request after
        the first ever), ``ensure_default_profile``'s own existence
        pre-check short-circuits before resolving any resume at all (see
        that function's docstring) -- this path never pre-resolves a resume
        here, so it never pays for a resolution ``resume_details()`` would
        otherwise duplicate. Only the RARE first-ever migration pays for a
        second resume lookup (bounded separately -- see
        ``test_config_latency.py``'s split between an already-migrated and
        a first-ever-migration cold call).
        """

        from .... import run
        from ... import profile_records

        resolved = self._resolved_gig()
        pre_head = run._cheap_workpad_head(resolved.path)
        if pre_head is not None:
            with _SELECTED_PROFILE_CACHE_LOCK:
                cached = _selected_profile_cache.get((str(resolved.path), pre_head), _UNSET)
            if cached is not _UNSET:
                return cached

        profile = profile_records.selected_profile(
            resolved, home_root=self.home_root, target=self._target_root()
        )
        post_head = run._cheap_workpad_head(resolved.path)
        if post_head is not None:
            with _SELECTED_PROFILE_CACHE_LOCK:
                _selected_profile_cache[(str(resolved.path), post_head)] = profile
        return profile

    def read_config(self) -> tuple[FindJobsConfig, bytes]:
        """The EFFECTIVE config: the shared ``find-jobs.json`` with the

        selected profile's ``titles``/``queries`` overlaid onto ``roles``/
        ``merged_queries`` (S25 F1-b, coordinator decision: "the config the
        UI sees IS the effective config" -- see ``effective_config.py``).
        This is what ``GET /api/config``'s ``config_digest`` and every
        ``RunRequest.config_digest`` staleness check are computed over.
        """

        from ....canonical import parse_json_bytes
        from ..effective_config import overlay_selected_profile

        path = self._target_root() / "find-jobs.json"
        if path.is_symlink() or not path.is_file():
            raise ConfigMissingError(path)
        try:
            shared_config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
        except FindJobsContractError:
            raise
        except (OSError, ValueError) as exc:
            raise FindJobsContractError("invalid_value", "find-jobs.json is not valid JSON") from exc
        profile = self._selected_profile()
        config = overlay_selected_profile(shared_config, profile)
        return config, canonical_json_bytes(config.to_json())

    def resume_preview(self) -> PinnedResume | None:
        from .... import run

        try:
            return run.resolve_newest_resume(self.home_root, self._target_root())
        except RunError as exc:
            if str(exc).startswith("find_jobs_resume_required:"):
                return None
            raise

    def resume_metadata(self) -> tuple[str | None, str | None] | None:
        from .... import run

        try:
            details = run.resolve_newest_resume_details(self.home_root, self._target_root())
        except RunError as exc:
            if str(exc).startswith("find_jobs_resume_required:"):
                return None
            raise
        return (details.label, details.created_at)

    def resume_details(self) -> ResumeDetails | None:
        """One resolution for both the pinned preview and its display fields.

        uat-bug-008: ``resume_preview()`` and ``resume_metadata()`` both
        call ``run.resolve_newest_resume_details`` -- calling it twice per
        request is wasteful even with that resolver's own per-journal-head
        cache (present_api.py:1371 GET /api/config). Callers that need both
        ``pinned`` and the label/date use this instead of calling both.
        """
        from .... import run

        try:
            return run.resolve_newest_resume_details(self.home_root, self._target_root())
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
        """Seal and launch a run against the SELECTED profile (S25 F1-b).

        ``config_bytes`` (the caller's shared-config snapshot, from the
        route's own ``read_config()``/digest check -- unowned here, F1-c's
        ``runs.py``) is never trusted for the actual launch: this method
        re-resolves the gig and its selected profile fresh, at launch time,
        and rebuilds the EFFECTIVE config from scratch the same way
        ``read_config()`` does (never the client's bytes) -- a profile
        switch between "load the form" and "click Run" is then a genuine,
        detectable staleness: the effective digest ``run.launch_find_jobs_
        run`` recomputes will differ from ``run_request.config_digest`` (the
        form's snapshot), and that call's own unchanged digest guard raises
        ``find_jobs_config_digest_mismatch`` -> 409, exactly like any other
        stale-form edit.
        """

        from .... import run
        from ....canonical import canonical_json_bytes
        from ..bindings import register_find_jobs_nodes
        from ..effective_config import overlay_selected_profile

        target = self._target_root()
        # This call binds the parent (for the launch hook) and causes the
        # spawned child to bind itself before executing any Goal.
        register_find_jobs_nodes(home_root=self.home_root, target=target)

        resolved = self._resolved_gig()
        from ... import profile_records
        from ....canonical import parse_json_bytes

        profile = profile_records.selected_profile(resolved, home_root=self.home_root, target=target)
        # read_config()'s return value IS the effective config (F1-b), so it
        # cannot be reused here -- re-derive the SHARED find-jobs.json
        # directly (same read read_config() itself does) to avoid
        # double-overlaying a profile onto an already-overlaid config.
        path = target / "find-jobs.json"
        if path.is_symlink() or not path.is_file():
            raise ConfigMissingError(path)
        shared_config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
        effective_config = overlay_selected_profile(shared_config, profile)
        effective_config_bytes = canonical_json_bytes(effective_config.to_json())

        profile_ref = (
            None
            if profile is None
            else {
                "profile_id": profile.profile_id,
                "revision": profile.revision,
                "content_digest": profile.content_digest,
            }
        )
        pinned_resume_ref = (
            None
            if profile is None
            else {"record_id": profile.resume_ref.record_id, "revision_id": profile.resume_ref.revision_id}
        )
        try:
            run_id = run.launch_find_jobs_run(
                home_root=self.home_root,
                target=target,
                run_request=run_request,
                config_bytes=effective_config_bytes,
                ui_loopback_verified=True,
                profile_ref=profile_ref,
                pinned_resume_ref=pinned_resume_ref,
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
        from ...projection import build_present_payload

        self._require_run(run_id)
        return build_present_payload(
            home_root=self.home_root,
            target=self._target_root(),
            run_id=run_id,
        )

    def carried_forward_assessments(self, run_id: str) -> tuple:
        """uat-bug-009: postings this run skipped as unchanged, plus their earlier result.

        Read straight from the run's own sealed ``outputs/acquire.json``
        (``AcquireOutput.carried_forward_assessments``, additive) rather than
        through ``build_present_payload``/``projection.py`` -- that module's
        ``PresentPayload`` enforces the assessed/not-assessed partition
        invariant (T4: a posting cannot be both), so a carried-forward result
        must stay outside it. Degrades to ``()`` for a run whose acquire
        output hasn't landed yet, or predates this field.
        """
        from ....journal import JournalArtifactMissingError, read_committed_artifact
        from ..contracts import AcquireOutput

        resolved = self._resolved_run(run_id)
        try:
            raw, _commit = read_committed_artifact(
                workpad=resolved.path,
                project_id=resolved.project_id,
                gig_id=resolved.gig_id,
                path=f"runs/{run_id}/outputs/acquire.json",
            )
        except JournalArtifactMissingError:
            return ()
        try:
            from ....canonical import parse_json_bytes

            output = AcquireOutput.from_json(parse_json_bytes(raw))
        except (ValueError, FindJobsContractError):
            return ()
        return output.carried_forward_assessments

    def run_status(self, run_id: str) -> RunStatusResponse:
        from .... import run

        self._require_run(run_id)
        try:
            details = run.read_run_details(
                home_root=self.home_root,
                requested_target=self._target_root(),
                run_id=run_id,
            )
        except run.RunError as exc:
            if _is_transient_run_error(exc):
                # r1 (uat-bug-003/005 follow-up): run.read_run_details's own
                # docstring/comment calls this "a transient, retryable
                # refusal" -- a concurrent journal writer (the run's own
                # child process, mid-commit) can make one poll observe an
                # uncommitted or in-flight write. This is not a real error
                # the UI should ever see: report the same "running" the UI
                # already polls through for an ordinary in-flight run,
                # rather than a 500 that kills App.jsx's pollStatus loop
                # outright (its own comment: "must not spin forever... stop
                # rather than silently retrying" -- correct for a *real*
                # failure, wrong for a transient race this process caused).
                # Logged at info, not as an unhandled exception: this is an
                # expected, self-healing race, not a bug to alarm on.
                _logger.info(
                    "run status transiently unavailable, reporting running: run_id=%s (%s)",
                    run_id,
                    exc,
                )
                return RunStatusResponse(run_id, AggregateStatus.RUNNING, ())
            raise
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
        else:
            self._log_run_terminal_once(run_id, status, payload)
        return RunStatusResponse(run_id, status, receipts)

    def _log_run_terminal_once(
        self, run_id: str, status: "AggregateStatus", payload
    ) -> None:
        """Log "find-jobs run finished/failed" the first time this process
        observes ``run_id`` go terminal (uat-bug-003 follow-up).

        The run itself executes in a spawned child process (``run.py``'s own
        worker) -- this API only ever *polls* ``run.read_run_details`` and
        never calls or awaits the run directly, so there is no single
        in-process call to log around the way ``start_run``'s allocation is.
        A ``set`` guarded by a lock makes this idempotent across repeated
        polls (and concurrent pollers) without a durable write of its own;
        it resets on server restart, which only means a run whose terminal
        status was already logged before a restart may log once more after
        one -- never a duplicate within one server's lifetime, and never a
        silent drop.
        """

        with self._logged_terminal_runs_lock:
            if run_id in self._logged_terminal_runs:
                return
            self._logged_terminal_runs.add(run_id)
        posting_count = len(payload.rows)
        assessed_count = len(payload.assessments)
        duration_ms = _receipt_span_ms(payload.node_receipts)
        if status == AggregateStatus.FAILED:
            message = _failure_message(payload.node_receipts)
            _logger.warning(
                "find-jobs run failed: run_id=%s postings=%d assessed=%d duration_ms=%s error=%s",
                run_id,
                posting_count,
                assessed_count,
                duration_ms if duration_ms is not None else "unknown",
                message,
            )
        else:
            _logger.info(
                "find-jobs run finished: run_id=%s status=%s postings=%d assessed=%d duration_ms=%s",
                run_id,
                status.value,
                posting_count,
                assessed_count,
                duration_ms if duration_ms is not None else "unknown",
            )

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
        from ..progress import read_progress

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

        from ....canonical import parse_json_bytes

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
        """Apply the setup answers onto the SELECTED profile + ``find-jobs.json``.

        S25 F1-b (coordinator decision): ``roles``/``titles_to_avoid`` (->
        ``titles``/``titles_to_avoid``) and ``merged_queries`` (mirrored from
        ``roles``, matching the starter config's own convention -- see
        ``scout_cli.STARTER_FIND_JOBS_CONFIG``) now go to the gig's SELECTED
        profile via ``profile_records.write_profile`` (a revision bump when
        they actually changed, per F1-a's bump rule) -- never into the
        shared ``find-jobs.json``'s ``roles``/``merged_queries`` once a
        profile exists, so acquire's post-migration precedence test (profile
        titles win over the shared file) stays true after a setup-interview
        save too. ``location`` (from ``city``), ``remote`` (derived from
        ``work_mode``), ``countries``, and ``visa_sponsorship_required``
        still go to the shared file, unchanged from before F1-b; every other
        ``FindJobsConfig`` field (``published_after``, ``sources``,
        ``default_assess_cap``, ``default_model_target``) is read from the
        existing file and kept unchanged.

        No profile exists yet (no committed resume for this gig, so
        ``ensure_default_profile`` no-ops) -- the interview is allowed to run
        before a target has ever been configured, and before any resume is
        added: titles/queries fall back to the shared file exactly as
        pre-F1-b, so a later resume add's migration still picks up what the
        operator entered here as the default profile's initial titles.
        """

        path = self._target_root() / "find-jobs.json"
        roles: tuple[str, ...] = tuple(prefs_fields["roles"])  # type: ignore[arg-type]
        titles_to_avoid: tuple[str, ...] = tuple(prefs_fields.get("titles_to_avoid", ()))  # type: ignore[arg-type]
        work_mode = prefs_fields["work_mode"]
        remote = work_mode in ("remote", "any")
        city: str | None = prefs_fields["city"]  # type: ignore[assignment]
        countries: tuple[str, ...] = tuple(prefs_fields["countries"])  # type: ignore[arg-type]
        visa_sponsorship_required = bool(prefs_fields["visa_sponsorship_required"])

        from ... import profile_records

        # F1-b1-r1 (coordinator's lane): the setup interview is allowed to
        # run before a target has ever been configured -- no `gigai setup`
        # yet, no workpad, nothing to resolve a gig against at all. That
        # is a WorkpadUnavailableError (or any other resolution failure),
        # never a crash: fall back to `profile = None` (today's, pre-F1-b
        # behaviour -- write straight to the shared file below) exactly as
        # if no profile could ever exist yet.
        profile = None
        try:
            resolved = self._resolved_gig()
        except Exception:
            resolved = None
        if resolved is not None:
            profile = profile_records.selected_profile(
                resolved, home_root=self.home_root, target=self._target_root()
            )
        if profile is not None:
            profile_records.write_profile(
                resolved,
                profile_id=profile.profile_id,
                titles=roles,
                titles_to_avoid=titles_to_avoid,
                queries=roles,
            )

        if path.is_symlink() or not path.is_file():
            config = FindJobsConfig(
                roles=() if profile is not None else roles,
                merged_queries=() if profile is not None else roles,
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
                roles=existing.roles if profile is not None else roles,
                merged_queries=existing.merged_queries if profile is not None else roles,
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
                # uat-bug-003 follow-up: logged here (after run_discovery
                # returns), not in _on_progress's "discovery_done" handling
                # above -- that raw progress event has no cost_usd (see
                # discovery.run_discovery's own _progress call), while the
                # returned DiscoveryResult does.
                if result.status == "failed":
                    _logger.warning(
                        "discover failed: discovery_id=%s new_boards=%d cost_usd=%.6f",
                        result.discovery_id,
                        len(result.new_boards),
                        result.cost_usd,
                    )
                else:
                    _logger.info(
                        "discover finished: discovery_id=%s status=%s new_boards=%d cost_usd=%.6f",
                        result.discovery_id,
                        result.status,
                        len(result.new_boards),
                        result.cost_usd,
                    )
            except Exception as exc:
                # run_discovery's own contract is "never raises for a
                # provider error"; anything that does escape it (e.g. S2-A's
                # DiscoveryBudgetExceeded, raised before any spend) is a
                # pre-flight failure this thread must still record instead
                # of silently dropping -- nothing else observes this thread,
                # so an uncaught exception here would otherwise vanish and
                # leave the UI's Discover panel stuck on "running" forever.
                _logger.warning("discover failed: discovery_id=%s cost_usd=0.0 error=%s", request_id, exc)
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


def _make_handler(
    backend: Backend,
    *,
    run_start_timeout_seconds: float = RUN_START_TIMEOUT_SECONDS,
) -> type[BaseHTTPRequestHandler]:
    from .config import ConfigRoutesMixin
    from .discover import DiscoverRoutesMixin
    from .profiles import ProfilesRoutesMixin
    from .runs import RunRoutesMixin
    from .setup import SetupRoutesMixin
    from .static import StaticRoutesMixin

    class Handler(
        ConfigRoutesMixin,
        SetupRoutesMixin,
        DiscoverRoutesMixin,
        RunRoutesMixin,
        ProfilesRoutesMixin,
        StaticRoutesMixin,
        BaseHTTPRequestHandler,
    ):
        # MONKEYPATCH TRAP (see module docstring): backend/run_start_timeout_seconds
        # used to be free variables closed over from this function's own
        # parameters; every mixin method above now reads them as
        # self._backend/self._run_start_timeout_seconds instead, so they're
        # set once here, per _make_handler call, exactly as before.
        _backend = backend
        _run_start_timeout_seconds = run_start_timeout_seconds

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
                    if path == "/api/profiles":
                        self._handle_get_profiles()
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
                if path == "/api/profiles":
                    self._handle_post_profiles()
                    return
                if path == "/api/profiles/selection":
                    self._handle_post_profiles_selection()
                    return
                profile_id = _match_profile_id(path, suffix="/archive")
                if profile_id is not None:
                    self._handle_post_profile_archive(profile_id)
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
                profile_id = _match_profile_id(path, suffix="")
                if profile_id is not None:
                    self._handle_put_profile(profile_id)
                    return
                self._error(HTTPStatus.NOT_FOUND, "not_found", "no such route")
            except Exception:  # noqa: BLE001 - same last-resort boundary as do_GET
                _logger.exception("unhandled exception in PUT %s", path)
                self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", "an internal error occurred")

    return Handler


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


# MONKEYPATCH TRAP: `_run_forever` and `main` live in present_api.py (the
# shim), not here -- test_present_ui.py patches
# "gigai.scout.find_jobs.present_api._run_forever", and main() must read
# that same bare name back off its own module's globals (not this one's)
# for the patch to take effect. See present_api.py.
