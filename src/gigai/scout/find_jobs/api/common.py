"""R0: small helpers shared across the route modules.

Moved out of ``present_api.py`` verbatim (pure move; see that module's
docstring for the split rationale).
"""

from __future__ import annotations

from datetime import datetime, timezone


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


def _parse_iso(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


# r1: the only RunError prefix run.py's own docstrings/comments mark as "a
# transient, retryable refusal" (see run.read_run_details ->
# _read_committed_run_details): a concurrent journal writer -- the run's own
# child process, mid-commit -- can make one poll observe an uncommitted or
# in-flight run-details write. occurrence.py already treats this exact
# prefix the same way (still-running, not a real failure) at its own
# read_run_details call site; this mirrors that established convention
# rather than inventing a new one. Every other RunError message in run.py is
# either explicitly marked non-retryable (goal-level "retryable": False
# error entries, a different, non-RunError concept) or names a genuine
# setup/authority/validation refusal that must keep failing loudly.
_TRANSIENT_RUN_ERROR_PREFIX = "run_details_reconciliation_required:"


def _is_transient_run_error(exc: BaseException) -> bool:
    return str(exc).startswith(_TRANSIENT_RUN_ERROR_PREFIX)


def _receipt_span_ms(node_receipts: tuple) -> int | None:
    """Wall-clock milliseconds from the earliest receipt's ``started_at`` to
    the latest ``finished_at``, for the "run finished/failed" log line's
    ``duration_ms`` -- ``None`` (never a raise) if there are no receipts yet
    or their timestamps don't parse, since a display-only duration must
    never break the status route itself."""

    starts = [ts for ts in (_parse_iso(receipt.started_at) for receipt in node_receipts) if ts is not None]
    finishes = [ts for ts in (_parse_iso(receipt.finished_at) for receipt in node_receipts) if ts is not None]
    if not starts or not finishes:
        return None
    delta = max(finishes) - min(starts)
    return max(0, round(delta.total_seconds() * 1000))


def _failure_message(node_receipts: tuple) -> str:
    """The first failed node receipt's own failure message, for the "run
    failed" log line -- falls back to a generic message if no receipt
    carries one (for example, a run rejected before any node ran)."""

    for receipt in node_receipts:
        failure = getattr(receipt, "failure", None)
        message = getattr(failure, "message", None)
        if message:
            return str(message)
    return "no node failure detail available"


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
