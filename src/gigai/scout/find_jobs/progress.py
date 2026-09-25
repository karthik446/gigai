"""B4: non-authoritative live progress files for one find-jobs run.

The sealed ``outputs/*.json`` + ``node_receipts`` (written at step end) stay
the only authority for a run's result. This module owns a second, purely
additive set of files next to them -- ``runs/<run_id>/progress/`` -- written
*as work happens* so the UI can show a card the moment a posting is acquired
and fill it in as its assessment finishes, instead of waiting for the whole
run (operator: "we load as we get").

Layout, all under ``runs/<run_id>/progress/``:

- ``steps.json``: ``{"acquire": {"status": "running"|"done"|"failed",
  "started_at": iso, "finished_at": iso|null}, "assess": {...}, "present":
  {...}}``. Rewritten wholesale on every step transition (small, no benefit
  to append-only here).
- ``acquire.jsonl``: one JSON line per posting kept after the B1 acquire
  filter, appended as each source/board finishes. Each line is a full
  ``PostingRow.to_json()`` plus ``{"outcome": "new"|"edited"|"unchanged"}``.
- ``assess.jsonl``: one JSON line per assessment lifecycle event, appended as
  it happens: a ``"started"`` line when a posting is handed to the model, and
  a ``"finished"`` line (with ``"ok": true/false``) when it resolves. Also
  one ``"not_assessed"`` line per row acquire/assess decided not to assess,
  carrying its reason, so the UI can show "why" before the sealed
  ``outputs/assess.json`` exists at all.
- ``cap.json``: ``{"cap": <int>, "candidate_count": <int>}``, written once
  acquire knows the selection cap and how many candidates it saw. Optional;
  absent until acquire has that information.
- ``boards.json`` (Q2): ``{"total": <int>, "budget_seconds": <float>|null,
  "status": "running"|"done", ...totals}``; written once acquire has planned
  its ATS board fetches, replaced with the totals (requests, cache hits,
  skipped, elapsed) when the fetch pass ends. acquire-rotation adds
  ``"rotation": {"cycle", "cycle_started_at", "total", "first", "last",
  "page_size", "runs_per_rotation", "estimated", "providers": {<provider>:
  {"total", "page_size", "runs_per_rotation"}}}`` -- "boards first-last of
  total this run; full rotation every ~K runs". While the pass runs
  ``last``/``page_size``/K come from the previous run's page
  (``estimated: true``, ``null`` on the first run ever); the final write
  carries this run's measured page.
- ``boards.jsonl`` (Q2): one JSON line per watchlist board as its fetch
  finishes: ``{"provider", "board_token", "status": "fetched"|"cached"|
  "failed"|"skipped", "requests", "cache", "postings", "matched",
  "elapsed_ms", "code"}`` -- so a UI can show "board 212 of 3,000" and which
  boards a run-time budget left unfetched.
- ``watchlist-seed.json`` (Q2): what the catalog seeding pass did before the
  boards were listed (``watchlist.WatchlistSeedResult.to_json()`` plus a
  ``status``), or why it was skipped (``prefs_missing``); the explicit
  record of an otherwise invisible watchlist change.

Every write is append-only (jsonl) or whole-file replace (steps.json,
cap.json) via a write-to-temp-then-rename, so a reader never observes a
half-written line or file. fsync is not required (these are best-effort
progress, never replayed as authority) so this stays cheap to call once per
posting/assessment without slowing the run down.

Nothing here validates against the frozen contracts in ``contracts.py``: a
progress line is a plain dict, read back as plain dicts by
``read_progress``. The sealed outputs remain the single source of truth for
the final results view; ``present_api.py``'s ``/progress`` route folds these
files together with whatever sealed state already exists (B4's design
constraint: never change the sealed contracts, the run seam, or the
scheduler).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any

_STEPS_FILENAME = "steps.json"
_ACQUIRE_FILENAME = "acquire.jsonl"
_ASSESS_FILENAME = "assess.jsonl"
_CAP_FILENAME = "cap.json"
# Q2 (acquire at scale): per-board progress + the watchlist seeding record.
_BOARDS_FILENAME = "boards.jsonl"
_BOARDS_SUMMARY_FILENAME = "boards.json"
_WATCHLIST_SEED_FILENAME = "watchlist-seed.json"

BOARD_STATUSES = ("fetched", "cached", "failed", "skipped")

STEP_NAMES = ("acquire", "assess", "present")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def progress_dir(run_root: Path) -> Path:
    """``run_root`` is ``<workpad>/runs/<run_id>``; returns its ``progress/`` dir."""

    return run_root / "progress"


def _append_line(path: Path, record: Mapping[str, object]) -> None:
    """Append one JSON line to ``path``, creating parent dirs as needed.

    A single ``open(..., "a")`` write of one line (with its own trailing
    newline) is atomic at the OS level for writes under the platform's pipe
    buffer size, which every line written here is well under (postings/
    assessments are bounded well below that by the frozen contracts' own
    string caps) -- so concurrent-with-read appends never interleave a
    reader's line boundary.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _replace_json(path: Path, payload: Mapping[str, object]) -> None:
    """Write ``payload`` to ``path`` via a temp file + atomic rename.

    Never lets a reader observe a partially-written steps.json/cap.json: the
    rename is atomic on the platforms this runs on (POSIX same-filesystem
    rename), so a read either sees the old complete file or the new one.
    """

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> object | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_jsonl(path: Path) -> list[dict[str, object]]:
    """Read every complete line; a truncated last line (mid-write) is dropped.

    Mid-run reads are expected: the writer may be mid-``_append_line`` for
    the very last line when a reader's poll lands. Any line that fails to
    parse is treated as "not yet fully written" and simply skipped -- never
    raised -- since every earlier line is already a complete, durable record.
    """

    if not path.is_file():
        return []
    records: list[dict[str, object]] = []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return records
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            decoded = json.loads(line)
        except ValueError:
            continue
        if isinstance(decoded, dict):
            records.append(decoded)
    return records


class ProgressWriter:
    """Bound to one run's ``progress/`` directory; every method is a single append/replace.

    r1 (coordinator review): the contract this module documents above --
    "progress must never break the sealed run" -- previously only covered
    *constructing* a writer (``_progress_writer``/``_assess_progress_writer``
    in the calling modules catch a resolution failure); an ``OSError`` from
    an actual write (disk full, permissions, the run dir removed mid-run)
    still propagated straight out of ``start_step``/``posting_acquired``/etc
    into ``acquire_node``/``assess_node``, failing the sealed run over a
    purely cosmetic write. Every public method here now routes through
    ``_guard``: it catches ``Exception`` (not ``BaseException`` -- a
    ``KeyboardInterrupt``/``SystemExit`` during a progress write must still
    propagate normally), logs the *first* failure once to stderr, and then
    permanently disables further writes on this instance (``self._disabled``)
    so one already-observed failure mode (e.g. a removed run dir) doesn't
    spend the rest of the run repeating the same doomed write and its
    warning on every single posting/assessment.
    """

    def __init__(self, run_root: Path) -> None:
        self._dir = progress_dir(run_root)
        self._disabled = False

    def _guard(self, operation: Callable[[], None]) -> None:
        if self._disabled:
            return
        try:
            operation()
        except Exception as exc:  # noqa: BLE001 - progress must never break the sealed run
            self._disabled = True
            print(
                f"warning: scout find-jobs progress writer disabled after a write failure "
                f"under {self._dir}: {type(exc).__name__}: {exc}",
                file=sys.stderr,
            )

    def start_step(self, step: str) -> None:
        self._guard(lambda: self._update_step(step, status="running", started_at=_now(), finished_at=None))

    def finish_step(self, step: str, *, ok: bool, message: str | None = None) -> None:
        self._guard(
            lambda: self._update_step(
                step, status="done" if ok else "failed", finished_at=_now(), message=message
            )
        )

    def _update_step(
        self,
        step: str,
        *,
        status: str,
        started_at: str | None = None,
        finished_at: str | None = None,
        message: str | None = None,
    ) -> None:
        path = self._dir / _STEPS_FILENAME
        raw_steps = _read_json(path)
        steps: dict[str, object] = raw_steps if isinstance(raw_steps, dict) else {}
        raw_existing = steps.get(step)
        entry: dict[str, object] = dict(raw_existing) if isinstance(raw_existing, dict) else {}
        entry["status"] = status
        if started_at is not None:
            entry["started_at"] = started_at
        entry.setdefault("started_at", _now())
        if finished_at is not None:
            entry["finished_at"] = finished_at
        elif "finished_at" not in entry:
            entry["finished_at"] = None
        # uat-bug-005 part 2: a failed step must carry *why* so /progress (and
        # any UI reading it) doesn't just show "failed" with nothing else --
        # only ever set on failure; a successful finish never adds/clears it
        # (there's nothing to say, and this step object is otherwise replaced
        # wholesale each write, so an omitted message here would silently
        # drop a still-relevant one from an earlier write, which never
        # happens today since each step finishes exactly once).
        if message is not None:
            entry["message"] = message
        steps[step] = entry
        _replace_json(path, steps)

    def posting_acquired(self, posting_json: Mapping[str, object], *, outcome: str) -> None:
        """Append one kept-after-filter posting (B1 filter already applied)."""

        record = {**posting_json, "outcome": outcome}
        self._guard(lambda: _append_line(self._dir / _ACQUIRE_FILENAME, record))

    def assessment_started(self, normalized_url: str) -> None:
        self._guard(
            lambda: _append_line(
                self._dir / _ASSESS_FILENAME,
                {"event": "started", "normalized_url": normalized_url, "at": _now()},
            )
        )

    def assessment_finished(
        self,
        normalized_url: str,
        *,
        ok: bool,
        assessment_json: Mapping[str, object] | None = None,
        reason: str | None = None,
    ) -> None:
        record: dict[str, object] = {
            "event": "finished",
            "normalized_url": normalized_url,
            "ok": ok,
            "at": _now(),
        }
        if assessment_json is not None:
            record["assessment"] = dict(assessment_json)
        if reason is not None:
            record["reason"] = reason
        self._guard(lambda: _append_line(self._dir / _ASSESS_FILENAME, record))

    def not_assessed(self, normalized_url: str, *, reason: str) -> None:
        self._guard(
            lambda: _append_line(
                self._dir / _ASSESS_FILENAME,
                {"event": "not_assessed", "normalized_url": normalized_url, "reason": reason, "at": _now()},
            )
        )

    def cap_known(self, *, cap: int, candidate_count: int) -> None:
        self._guard(
            lambda: _replace_json(self._dir / _CAP_FILENAME, {"cap": cap, "candidate_count": candidate_count})
        )

    # -- Q2: per-board progress + the seeding record ------------------------

    def watchlist_seeded(self, payload: Mapping[str, object]) -> None:
        """Replace ``watchlist-seed.json`` with what seeding did (or why not)."""

        self._guard(lambda: _replace_json(self._dir / _WATCHLIST_SEED_FILENAME, dict(payload)))

    def boards_planned(self, *, total: int, budget_seconds: float | None, rotation: Mapping[str, object] | None = None) -> None:
        """Written once acquire knows how many boards it will fetch this run.

        ``rotation`` (acquire-rotation, optional) is the cursor block: where
        in the watchlist this run's page starts and the estimated cadence.
        """

        payload: dict[str, object] = {"total": total, "budget_seconds": budget_seconds, "status": "running"}
        if rotation is not None:
            payload["rotation"] = dict(rotation)
        self._guard(lambda: _replace_json(self._dir / _BOARDS_SUMMARY_FILENAME, payload))

    def board_finished(
        self,
        *,
        provider: str,
        board_token: str,
        status: str,
        requests: int = 0,
        cache: str | None = None,
        postings: int = 0,
        matched: int = 0,
        elapsed_ms: int = 0,
        code: str | None = None,
    ) -> None:
        """Append one line per board the moment its fetch settles."""

        record: dict[str, object] = {
            "provider": provider,
            "board_token": board_token,
            "status": status,
            "requests": requests,
            "cache": cache,
            "postings": postings,
            "matched": matched,
            "elapsed_ms": elapsed_ms,
            "at": _now(),
        }
        if code is not None:
            record["code"] = code
        self._guard(lambda: _append_line(self._dir / _BOARDS_FILENAME, record))

    def boards_finished(self, summary: Mapping[str, object]) -> None:
        """Replace ``boards.json`` with the fetch pass's final totals."""

        self._guard(
            lambda: _replace_json(self._dir / _BOARDS_SUMMARY_FILENAME, {**dict(summary), "status": "done"})
        )


@dataclass(frozen=True)
class ProgressSnapshot:
    """The plain-dict result of reading one run's progress directory."""

    steps: dict[str, dict[str, object]]
    postings: list[dict[str, object]]
    assessments: list[dict[str, object]]
    cap: int | None
    candidate_count: int | None
    not_assessed_counts: dict[str, int]
    # Q2: additive, defaulted so every existing constructor call still works.
    boards: dict[str, object] = field(default_factory=dict)
    watchlist_seed: dict[str, object] | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "steps": self.steps,
            "postings": self.postings,
            "assessments": self.assessments,
            "cap": self.cap,
            "candidate_count": self.candidate_count,
            "not_assessed_counts": self.not_assessed_counts,
            "boards": self.boards,
            "watchlist_seed": self.watchlist_seed,
        }


def read_progress(run_root: Path) -> ProgressSnapshot:
    """Read every progress file for one run, tolerating any subset missing.

    Called before acquire starts (nothing exists yet -> all-empty snapshot),
    mid-acquire (steps + a growing acquire.jsonl), mid-assess (all of the
    above plus a growing assess.jsonl), and after completion (everything
    present). Never raises on a missing/partial file -- a progress read is
    best-effort by design; the sealed outputs are the authority.
    """

    directory = progress_dir(run_root)
    steps_raw = _read_json(directory / _STEPS_FILENAME)
    steps = {k: v for k, v in steps_raw.items() if isinstance(v, dict)} if isinstance(steps_raw, dict) else {}

    postings = _read_jsonl(directory / _ACQUIRE_FILENAME)

    assess_events = _read_jsonl(directory / _ASSESS_FILENAME)
    # Fold the assess.jsonl event log into one entry per posting: the latest
    # "finished"/"not_assessed" event wins over an earlier "started" for the
    # same normalized_url, so a card shows "assessing…" only while genuinely
    # still in flight.
    assessments_by_url: dict[str, dict[str, object]] = {}
    order: list[str] = []
    not_assessed_counts: dict[str, int] = {}
    for event in assess_events:
        url = event.get("normalized_url")
        if not isinstance(url, str):
            continue
        kind = event.get("event")
        if kind == "started":
            entry = assessments_by_url.setdefault(url, {"normalized_url": url})
            entry["status"] = "assessing"
            if url not in order:
                order.append(url)
        elif kind == "finished":
            entry = assessments_by_url.setdefault(url, {"normalized_url": url})
            entry["status"] = "assessed" if event.get("ok") else "failed"
            if "assessment" in event:
                entry["assessment"] = event["assessment"]
            if event.get("reason") is not None:
                entry["reason"] = event["reason"]
            if url not in order:
                order.append(url)
        elif kind == "not_assessed":
            entry = assessments_by_url.setdefault(url, {"normalized_url": url})
            entry["status"] = "not_assessed"
            reason = event.get("reason")
            entry["reason"] = reason
            if isinstance(reason, str):
                not_assessed_counts[reason] = not_assessed_counts.get(reason, 0) + 1
            if url not in order:
                order.append(url)
    assessments = [assessments_by_url[url] for url in order]

    cap_payload = _read_json(directory / _CAP_FILENAME)
    cap = None
    candidate_count = None
    if isinstance(cap_payload, dict):
        raw_cap = cap_payload.get("cap")
        raw_candidates = cap_payload.get("candidate_count")
        if isinstance(raw_cap, int):
            cap = raw_cap
        if isinstance(raw_candidates, int):
            candidate_count = raw_candidates

    return ProgressSnapshot(
        steps=steps,
        postings=postings,
        assessments=assessments,
        cap=cap,
        candidate_count=candidate_count,
        not_assessed_counts=not_assessed_counts,
        boards=_read_boards(directory),
        watchlist_seed=_read_watchlist_seed(directory),
    )


def _read_watchlist_seed(directory: Path) -> dict[str, object] | None:
    payload = _read_json(directory / _WATCHLIST_SEED_FILENAME)
    return dict(payload) if isinstance(payload, dict) else None


def _read_boards(directory: Path) -> dict[str, object]:
    """Fold ``boards.json`` + ``boards.jsonl`` into one live per-board view.

    ``{}`` when acquire never planned a board fetch (no ATS source, or an
    older run). Otherwise the planned ``total``/``budget_seconds``/``status``
    plus counts derived from the per-board lines -- ``done`` (lines seen),
    one count per :data:`BOARD_STATUSES` value, ``requests`` and
    ``cache_hits`` summed, and ``skipped_boards`` (the ``provider:token``
    of every board a run-time budget left unfetched) so a UI can name them.
    The final totals ``boards_finished`` writes win over the derived counts
    where both exist. The ``rotation`` block (acquire-rotation) passes
    through from whichever ``boards.json`` write is current.
    """

    summary_raw = _read_json(directory / _BOARDS_SUMMARY_FILENAME)
    lines = _read_jsonl(directory / _BOARDS_FILENAME)
    if not isinstance(summary_raw, dict) and not lines:
        return {}
    summary: dict[str, object] = dict(summary_raw) if isinstance(summary_raw, dict) else {}
    counts = {status: 0 for status in BOARD_STATUSES}
    requests = 0
    cache_hits = 0
    skipped: list[str] = []
    for line in lines:
        status = line.get("status")
        if isinstance(status, str) and status in counts:
            counts[status] += 1
        raw_requests = line.get("requests")
        if isinstance(raw_requests, int) and not isinstance(raw_requests, bool):
            requests += raw_requests
        if line.get("cache") in {"hit", "revalidated"}:
            cache_hits += 1
        if status == "skipped":
            skipped.append(f"{line.get('provider')}:{line.get('board_token')}")
    derived: dict[str, object] = {
        "done": len(lines),
        **counts,
        "requests": requests,
        "cache_hits": cache_hits,
        "skipped_boards": skipped,
    }
    return {**derived, **summary}


__all__ = [
    "BOARD_STATUSES",
    "STEP_NAMES",
    "ProgressSnapshot",
    "ProgressWriter",
    "progress_dir",
    "read_progress",
]
