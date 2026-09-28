"""P6: rank a batch of postings against a resume with Jev, cached and cost-capped.

``rank_postings_report`` is the one ranking pass: acquire's selection
ordering, the ``/rank`` route and quick assess all go through it
(``rank_postings`` is the same pass as the three values its earlier callers
read). It never touches the network directly (that is ``jev_client.py``);
it owns the cache, the cost cap, the daily budget and the fail-open
contract: no key, or any per-call failure, degrades to an unscored row
rather than failing the caller.

Cache (operator decision 2026-09-28): ONE cache under the home, shared by
every project: ``<home>/cache/scout/jev/scores/<key>.json``, ``key =
sha256((content_sha256, resume_sha256, JEV_PROMPT_VERSION, model))``, where
``resume_sha256`` is the digest of the resume's TEXT. A posting scored
against a resume is never paid for again, whichever project, profile or
resume revision asks. An entry of the earlier per-project cache
(``<home>/scout/<project_id>/jev_cache/``, keyed by profile and resume
revision) is still read when the shared cache has none, and copied over.
Only a *successful* score is cached (an error is never memoized, so a
transient Jev outage doesn't permanently blank a posting's score).

Fit: the label is derived from the score, by ``FIT_THRESHOLDS``, wherever a
score is returned: a fresh answer and a cached one alike. Jev answers the
label and the score as two separate questions and the two can disagree; the
score is the one the ordering uses, so it decides the label too.

Cost cap: default $0.25 for one pass, overridable via the ``cost_cap_usd``
request field or ``GIGAI_JEV_COST_CAP_USD`` for the run path. Daily budget:
``jev_budget.daily_budget_usd`` (default $0.50 a day), across every pass
of the day: each paid call is written to ``jev_budget``'s ledger and a pass
stops once today's total reaches the budget. Both are checked before each
group of calls. Every row not scored is returned as an unscored
:class:`RankScore` (``fit=None, score=None``), never dropped from the
output -- so there is always one entry per input row.

uat-bug-021: what a ranking pass did is never silent. The pass returns a
:class:`RankReport`, and :class:`RankStatus` is the one sentence a caller
records, logs and shows: ``scored N of M`` or ``skipped: <reason>``.

A pass stops calling Jev when going on cannot help: at once on a key or
credit error (401, 402, 403), and after ``_MAX_CONSECUTIVE_FAILURES`` rows
in a row failed. A busy or unreachable answer (429, 5xx, transport) is
asked again after a pause when ``retries`` allows, and the pass then makes
half as many calls side by side for the rest of its rows.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import json
import logging
from pathlib import Path
import sys
import time
from typing import TYPE_CHECKING

from ...canonical import canonical_json_digest, digest_imported_bytes
from . import jev_budget
from .discovery.storage import atomic_write, project_id
from .jev_budget import format_cost, format_usd
from .jev_client import JevClient, JevClientError, JevScore
from .jev_contracts import RankScore

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    from .contracts import PostingRow

JEV_PROMPT_VERSION = "1"
DEFAULT_COST_CAP_USD = 0.25
# How a find-jobs run and ``POST /rank`` ask Jev (orchestrator decision
# 2026-09-28): 8 calls side by side, a busy answer asked again twice.
RUN_CONCURRENCY = 8
RUN_RETRIES = 2

# The one table the fit label comes from: the first row whose floor the
# score (0-100) reaches. 70 and up is strong, 40 to 69 maybe, below 40 no.
FIT_THRESHOLDS: tuple[tuple[str, int], ...] = (("strong", 70), ("maybe", 40), ("no", 0))

# A row is hidden by default in the UI once Jev is confident it's a poor
# fit (S28/S29 design intent: "no" fit at a high score-confidence gap is a
# clear miss) -- score threshold matches the plan's target schema
# (<=? not specified numerically; a conservative low-score "no" verdict).
_HIDDEN_FIT = "no"
_HIDDEN_SCORE_MAX = 30

# Going on after one of these cannot succeed: the key or the account is
# refused, so every later call gets the same answer.
_FATAL_CODES = frozenset({"jev_http_401", "jev_http_402", "jev_http_403"})
# Worth another try after a pause: Jev is busy or briefly unreachable.
_RETRY_CODES = frozenset({"jev_http_429", "jev_http_500", "jev_http_502", "jev_http_503", "jev_http_504", "jev_transport"})
_RETRY_PAUSE_SECONDS = (0.5, 1.5)
_MAX_CONSECUTIVE_FAILURES = 5

_logger = logging.getLogger("gigai.scout.server")


def ensure_log_handler(logger: logging.Logger = _logger) -> None:
    """Give ``logger`` a stderr handler when nothing would print its lines.

    A find-jobs run executes in a child process of the Scout server, where
    ``api/server.py``'s ``_configure_logging`` never ran: an INFO line
    there was dropped. The child's stderr is the server's log file, so a
    handler on stderr is the server log. No-op when any handler (the
    server's own, or a test's) already sees this logger.
    """

    if logger.hasHandlers():
        return
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    handler._gigai_scout_server = True  # type: ignore[attr-defined]
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


@dataclass(frozen=True)
class RankPreferences:
    """The small preference slice Jev's ``state`` needs (never the full config)."""

    target_titles: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    visa_sponsorship_required: bool = False


def fit_for_score(score: int) -> str:
    """``strong`` / ``maybe`` / ``no`` for a 0-100 score (``FIT_THRESHOLDS``)."""

    for label, floor in FIT_THRESHOLDS:
        if score >= floor:
            return label
    return FIT_THRESHOLDS[-1][0]


def _hidden_by_default(fit: str, score: int) -> bool:
    return fit == _HIDDEN_FIT and score <= _HIDDEN_SCORE_MAX


# --- the cache ---------------------------------------------------------------------------


def resume_digest(resume_text: str) -> str:
    """The digest of a resume's text: what a cached score is keyed by."""

    return digest_imported_bytes(resume_text.encode("utf-8"))


def _hex(payload: dict[str, object]) -> str:
    return canonical_json_digest(payload).split(":", 1)[1]  # "sha256:<hex>"


def _cache_key(*, content_sha256: str, resume_sha256: str, model: str) -> str:
    return _hex({
        "content_sha256": content_sha256,
        "resume_sha256": resume_sha256,
        "prompt_version": JEV_PROMPT_VERSION,
        "model": model,
    })


def _legacy_cache_key(
    *, content_sha256: str, profile_id: str | None, resume_revision_id: str | None, model: str
) -> str:
    return _hex({
        "content_sha256": content_sha256,
        "profile_id": profile_id,
        "resume_revision_id": resume_revision_id,
        "prompt_version": JEV_PROMPT_VERSION,
        "model": model,
    })


def _cache_digest(row: "PostingRow") -> str:
    """The content digest a row's cache key is made of.

    A row with neither a digest nor a text (a posting Exa listed whose
    board is not stored yet) used to fall back to the digest of nothing,
    so every such row shared one cache entry and one score. Its company,
    title, location and URL -- what Jev is asked about -- stand in.
    """

    if row.content_sha256:
        return row.content_sha256
    if row.text:
        return digest_imported_bytes(row.text.encode("utf-8"))
    identity = "\n".join((row.normalized_url, row.company or "", row.title or "", row.location or ""))
    return digest_imported_bytes(identity.encode("utf-8"))


def cache_dir(home_root: Path) -> Path:
    """The shared score cache: the home's, not a project's."""

    return Path(home_root) / "cache" / "scout" / "jev" / "scores"


def _legacy_cache_dir(home_root: Path, target: Path) -> Path | None:
    """The earlier per-project cache, or ``None`` when ``target`` names no project."""

    try:
        return Path(home_root) / "scout" / project_id(home_root, target) / "jev_cache"
    except Exception:  # noqa: BLE001 - a fallback read: an unbound target has no earlier cache
        return None


def _read_cache_file(path: Path) -> RankScore | None:
    if path.is_symlink() or not path.is_file():
        return None
    try:
        return RankScore.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def _write_cache(path: Path, score: RankScore) -> None:
    atomic_write(path, json.dumps(score.to_json(), separators=(",", ":")).encode("utf-8"))


@dataclass(frozen=True)
class _Cache:
    """Where one pass reads and writes its scores."""

    shared: Path
    legacy: Path | None
    resume_sha256: str
    profile_id: str | None
    resume_revision_id: str | None
    model: str

    def path(self, row: "PostingRow") -> Path:
        key = _cache_key(content_sha256=_cache_digest(row), resume_sha256=self.resume_sha256, model=self.model)
        return self.shared / f"{key}.json"

    def read(self, row: "PostingRow") -> RankScore | None:
        path = self.path(row)
        found = _read_cache_file(path)
        if found is None and self.legacy is not None:
            legacy_key = _legacy_cache_key(
                content_sha256=row.content_sha256 or digest_imported_bytes((row.text or "").encode("utf-8")),
                profile_id=self.profile_id,
                resume_revision_id=self.resume_revision_id,
                model=self.model,
            )
            found = _read_cache_file(self.legacy / f"{legacy_key}.json")
            if found is not None and found.score is not None:
                try:
                    _write_cache(path, found)
                except OSError:
                    pass
        if found is None or found.score is None:
            return None
        # The cache is keyed by content, not URL: the same posting under
        # another URL carries THIS row's URL, so a caller that looks a score
        # up by URL finds it. The label is the score's (an entry written
        # before FIT_THRESHOLDS carries Jev's own).
        fit = fit_for_score(found.score)
        return replace(
            found,
            normalized_url=row.normalized_url,
            fit=fit,
            hidden_by_default=_hidden_by_default(fit, found.score),
            cached=True,
        )


def _cache_for(
    *, resume_text: str, profile_id: str | None, resume_revision_id: str | None, home_root: Path, target: Path, model: str
) -> _Cache:
    return _Cache(
        shared=cache_dir(home_root),
        legacy=_legacy_cache_dir(home_root, target),
        resume_sha256=resume_digest(resume_text),
        profile_id=profile_id,
        resume_revision_id=resume_revision_id,
        model=model,
    )


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
    fit = fit_for_score(jev.score)
    return RankScore(
        normalized_url=row.normalized_url,
        content_sha256=row.content_sha256 or digest_imported_bytes((row.text or "").encode("utf-8")),
        fit=fit,
        score=jev.score,
        reasons=jev.reasons,
        mismatch_flags=jev.mismatch_flags,
        hidden_by_default=_hidden_by_default(fit, jev.score),
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


def unscored(row: "PostingRow") -> RankScore:
    """The entry of a row Jev did not score (``fit=None, score=None``)."""

    return _unscored(row)


def read_cached_scores(
    rows: "Sequence[PostingRow]",
    *,
    resume_text: str,
    profile_id: str | None,
    resume_revision_id: str | None,
    home_root: Path,
    target: Path,
    model: str = "jev-latest",
) -> tuple[RankScore, ...]:
    """The scores already paid for, one entry per row; Jev is never asked.

    What a page reads: a row with no cached score is an unscored entry.
    """

    cache = _cache_for(
        resume_text=resume_text, profile_id=profile_id, resume_revision_id=resume_revision_id,
        home_root=home_root, target=target, model=model,
    )
    return tuple(cache.read(row) or _unscored(row) for row in rows)


# --- what a pass did ---------------------------------------------------------------------


@dataclass(frozen=True)
class RankReport:
    """What one ranking pass did.

    ``scores`` has one entry per input row, in the rows' own order.
    ``calls`` counts the rows Jev was asked about (a retried row counts
    once); ``cache_hits`` the rows read from the cache; ``unscored_by_cap``
    and ``unscored_by_budget`` the rows the cost cap and the daily budget
    left out; ``errors`` the Jev error codes of the rows whose call failed,
    most frequent first, as ``(code, rows)``; ``stopped_on`` the code that
    ended the pass early, if one did; ``throttled`` how the pass slowed
    down after a busy answer (``"8 -> 4 after 429"``). ``capped`` is true
    when the cap or the budget was reached.
    """

    scores: tuple[RankScore, ...]
    total_cost_usd: float
    capped: bool
    calls: int = 0
    cache_hits: int = 0
    unscored_by_cap: int = 0
    unscored_by_budget: int = 0
    errors: tuple[tuple[str, int], ...] = ()
    stopped_on: str | None = None
    throttled: str | None = None
    spent_today_usd: float | None = None
    daily_budget_usd: float | None = None

    @property
    def scored(self) -> int:
        return sum(1 for item in self.scores if item.score is not None)


_SKIP_WORDS = {
    "no_candidates": "no new postings to score",
    "no_home": "no GigAI home",
    "no_profile": "no profile selected",
    "no_run_output": "the run's postings could not be read",
    "no_key": "no Jev key",
    "no_run_input": "the run's input could not be read",
    "no_resume": "no resume",
    "not_requested": "not asked yet",
}


@dataclass(frozen=True)
class RankStatus:
    """uat-bug-021: what Jev did for one run, in one sentence.

    ``status`` is ``scored``, ``skipped``, or ``running`` (a ``POST /rank``
    pass still at work). ``text`` is ``scored N of M``, ``skipped:
    <reason>`` or ``scoring: N of M``. ``reason`` is one of
    ``no_candidates``, ``no_home``, ``no_key``, ``no_run_input``,
    ``no_run_output``, ``no_profile``, ``no_resume``, ``not_requested``
    (a read: postings are unscored and no pass was asked for),
    ``cost_cap_reached``,
    ``daily_budget_reached``, ``jev_error:<code>`` and
    ``error:<ExceptionType>``; a pass that scored some rows and then met
    the cap, the budget or an error is ``scored`` and carries that reason
    too. Never resume text, never a key.
    """

    status: str
    scored: int = 0
    total: int = 0
    reason: str | None = None
    cost_cap_usd: float | None = None
    cost_usd: float = 0.0
    throttled: str | None = None
    spent_today_usd: float | None = None
    daily_budget_usd: float | None = None

    @classmethod
    def skipped(
        cls, reason: str, *, total: int = 0, cost_cap_usd: float | None = None, home_root: Path | None = None
    ) -> "RankStatus":
        spent, budget = _usage_fields(home_root)
        return cls("skipped", 0, total, reason, cost_cap_usd, 0.0, None, spent, budget)

    @classmethod
    def running(cls, *, scored: int, total: int, cost_cap_usd: float | None = None, home_root: Path | None = None) -> "RankStatus":
        spent, budget = _usage_fields(home_root)
        return cls("running", scored, total, None, cost_cap_usd, 0.0, None, spent, budget)

    @classmethod
    def from_report(cls, report: RankReport, *, cost_cap_usd: float) -> "RankStatus":
        total = len(report.scores)
        scored = report.scored
        reason: str | None = None
        if report.unscored_by_budget:
            reason = "daily_budget_reached"
        elif report.unscored_by_cap:
            reason = "cost_cap_reached"
        elif scored < total and (report.stopped_on or report.errors):
            reason = f"jev_error:{report.stopped_on or report.errors[0][0]}"
        return cls(
            "scored" if scored else "skipped", scored, total, reason, cost_cap_usd, report.total_cost_usd,
            report.throttled, report.spent_today_usd, report.daily_budget_usd,
        )

    @property
    def text(self) -> str:
        if self.status == "scored":
            return f"scored {self.scored:,} of {self.total:,}"
        if self.status == "running":
            return f"scoring: {self.scored:,} of {self.total:,}"
        return f"skipped: {self.reason}"

    @property
    def reason_words(self) -> str:
        """The reason in words; empty when there is none."""

        reason = self.reason or ""
        if reason == "cost_cap_reached":
            cap = "" if self.cost_cap_usd is None else f" ${format_usd(self.cost_cap_usd)}"
            return f"cost cap{cap}" if self.scored else f"cost cap{cap} reached before any score"
        if reason == "daily_budget_reached":
            budget = "" if self.daily_budget_usd is None else f" ${format_usd(self.daily_budget_usd)}"
            return f"daily budget{budget} reached"
        if reason.startswith("jev_error:"):
            return f"Jev error: {reason.removeprefix('jev_error:')}"
        if reason.startswith("error:"):
            return f"error: {reason.removeprefix('error:')}"
        return _SKIP_WORDS.get(reason, reason)

    @property
    def line(self) -> str:
        """The run's progress line: ``Jev: scored 412 of 1,458 (cost $0.23, cost cap $0.25)``."""

        if self.status == "running":
            return f"Jev: scoring, {self.scored:,} of {self.total:,} so far"
        words = self.reason_words
        throttled = f"throttled: {self.throttled}" if self.throttled else ""
        if self.status == "scored":
            details = [f"cost ${format_cost(self.cost_usd)}", words, throttled]
            return f"Jev: {self.text} ({', '.join(item for item in details if item)})"
        return f"Jev: skipped ({', '.join(item for item in (words, throttled) if item)})"

    @property
    def usage_line(self) -> str | None:
        """``Jev: $0.31 of $0.50 today``; ``None`` when the day's spend was not read."""

        if self.spent_today_usd is None or self.daily_budget_usd is None:
            return None
        return jev_budget.usage_line(self.spent_today_usd, self.daily_budget_usd)

    def to_json(self) -> dict[str, object]:
        return {
            "status": self.status,
            "scored": self.scored,
            "total": self.total,
            "reason": self.reason,
            "cost_cap_usd": None if self.cost_cap_usd is None else format_usd(self.cost_cap_usd),
            "cost_usd": f"{self.cost_usd:.6f}",
            "throttled": self.throttled,
            "spent_today_usd": None if self.spent_today_usd is None else f"{self.spent_today_usd:.6f}",
            "daily_budget_usd": None if self.daily_budget_usd is None else format_usd(self.daily_budget_usd),
            "text": self.text,
            "line": self.line,
            "usage_line": self.usage_line,
        }


def _usage_fields(home_root: Path | None) -> tuple[float | None, float | None]:
    """``(spent today, daily budget)``; ``(None, None)`` when there is no home to read them from."""

    if home_root is None:
        return None, None
    try:
        return jev_budget.spent_today_usd(home_root), jev_budget.daily_budget_usd(home_root)
    except Exception:  # noqa: BLE001 - the day's usage is an extra on a status, never its failure
        return None, None


def log_rank_status(status: RankStatus, *, run_id: str, where: str) -> None:
    """One server-log line per ranking pass: INFO when scored, WARNING for a skip."""

    ensure_log_handler()
    quiet = status.status in {"scored", "running"} and status.reason is None and status.throttled is None
    _logger.log(
        logging.INFO if quiet else logging.WARNING,
        "scout %s: %s [run_id=%s reason=%s scored=%d total=%d cost_usd=%.6f cost_cap_usd=%s spent_today_usd=%s daily_budget_usd=%s]",
        where,
        status.line,
        run_id,
        status.reason or "-",
        status.scored,
        status.total,
        status.cost_usd,
        "-" if status.cost_cap_usd is None else format_usd(status.cost_cap_usd),
        "-" if status.spent_today_usd is None else f"{status.spent_today_usd:.6f}",
        "-" if status.daily_budget_usd is None else format_usd(status.daily_budget_usd),
    )


# --- the pass ----------------------------------------------------------------------------


def _short_code(code: str) -> str:
    return code.removeprefix("jev_http_").removeprefix("jev_")


def _score_with_retries(
    client: JevClient,
    state: dict[str, object],
    *,
    retries: int,
    sleep: Callable[[float], None],
) -> tuple[JevScore | JevClientError, str | None]:
    """One row's answer, or the error of its last attempt (never raised).

    The second value is the first busy or unreachable code this row met,
    whether or not a later attempt then succeeded.
    """

    attempt = 0
    busy: str | None = None
    while True:
        try:
            return client.score(state), busy
        except JevClientError as exc:
            if exc.code in _RETRY_CODES:
                busy = busy or exc.code
            if attempt >= retries or exc.code not in _RETRY_CODES:
                return exc, busy
            sleep(_RETRY_PAUSE_SECONDS[min(attempt, len(_RETRY_PAUSE_SECONDS) - 1)])
            attempt += 1


def rank_postings_report(
    rows: "Sequence[PostingRow]",
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
    concurrency: int = 1,
    retries: int = 0,
    sleep: Callable[[float], None] = time.sleep,
    where: str = "rank",
    run_id: str | None = None,
) -> RankReport:
    """Score every row, cache-first, stopping at the cap or the budget; say what happened.

    ``scores`` has exactly one entry per input row, in ``rows``' own order
    (never re-sorted here; ordering by score is the caller's job). A row
    past the cap or the budget, or one whose call failed, is returned
    unscored rather than dropped or raising -- Jev failures never fail
    acquire.

    Rows are asked in ``rows`` order, ``concurrency`` at a time (1: one
    after another). The cap and the day's budget are checked before each
    group of calls, so a pass spends at most ``concurrency - 1`` calls past
    either. ``retries`` is how many more times a row is asked after a busy
    or unreachable answer (``_RETRY_CODES``), with a pause before each; a
    group that met one halves the size of the groups after it (floor 1).
    ``where`` and ``run_id`` name the pass in the spend ledger.
    """

    rows = tuple(rows)
    cache = _cache_for(
        resume_text=resume_text, profile_id=profile_id, resume_revision_id=resume_revision_id,
        home_root=home_root, target=target, model=model,
    )
    scores: list[RankScore | None] = [None] * len(rows)
    pending: list[tuple[int, "PostingRow"]] = []
    cache_hits = 0
    for index, row in enumerate(rows):
        cached_score = cache.read(row)
        if cached_score is not None:
            scores[index] = cached_score
            cache_hits += 1
        else:
            pending.append((index, row))

    budget = jev_budget.daily_budget_usd(home_root)
    total_cost = 0.0
    capped = False
    calls = 0
    unscored_by_cap = 0
    unscored_by_budget = 0
    errors: dict[str, int] = {}
    consecutive_failures = 0
    stopped_on: str | None = None
    throttled: str | None = None
    first_size = max(1, concurrency)
    group_size = first_size
    executor = ThreadPoolExecutor(max_workers=first_size) if first_size > 1 else None

    def ask(item: tuple[int, "PostingRow"]) -> tuple[JevScore | JevClientError, str | None]:
        return _score_with_retries(
            client, _state_for(item[1], resume_text=resume_text, prefs=prefs), retries=retries, sleep=sleep
        )

    try:
        start = 0
        while start < len(pending) and stopped_on is None:
            if jev_budget.spent_today_usd(home_root) >= budget:
                capped = True
                unscored_by_budget = len(pending) - start
                break
            if total_cost >= cost_cap_usd:
                capped = True
                unscored_by_cap = len(pending) - start
                break
            group = pending[start : start + group_size]
            start += len(group)
            answers = list(executor.map(ask, group)) if executor is not None and len(group) > 1 else [ask(item) for item in group]
            busy: str | None = None
            for (index, row), (answer, met) in zip(group, answers):
                calls += 1
                busy = busy or met
                if isinstance(answer, JevClientError):
                    # Never the key, never the response body -- the code only
                    # (JevClientError's own message is already redacted).
                    _logger.warning("jev rank call failed for %s: %s", row.normalized_url, answer.code)
                    errors[answer.code] = errors.get(answer.code, 0) + 1
                    consecutive_failures += 1
                    if answer.code in _FATAL_CODES or consecutive_failures >= _MAX_CONSECUTIVE_FAILURES:
                        stopped_on = stopped_on or answer.code
                    continue
                consecutive_failures = 0
                cost = float(answer.cost_usd)
                total_cost += cost
                jev_budget.record_spend(home_root, cost, where=where, run_id=run_id)
                rank_score = _score_to_rank_score(row, answer, cached=False)
                scores[index] = rank_score
                # Cached copy is stored with cached=True: the file on disk always
                # represents "what a cache hit returns", so a later run that reads
                # it back sees cached=True (this call's own in-memory result
                # correctly reports cached=False -- it just paid for the call).
                _write_cache(cache.path(row), _score_to_rank_score(row, answer, cached=True))
                _logger.info(
                    "jev rank: url=%s fit=%s score=%s cost_usd=%.6f running_total_usd=%.6f",
                    row.normalized_url,
                    rank_score.fit,
                    rank_score.score,
                    cost,
                    total_cost,
                )
            if busy is not None and group_size > 1:
                group_size = max(1, group_size // 2)
                throttled = f"{first_size} -> {group_size} after {_short_code(busy)}"
                _logger.warning("jev rank throttled: %s", throttled)
        if total_cost >= cost_cap_usd:
            capped = True
    finally:
        if executor is not None:
            executor.shutdown(wait=True)
    if stopped_on is not None:
        _logger.warning("jev rank stopped early: %s", stopped_on)

    return RankReport(
        scores=tuple(score if score is not None else _unscored(rows[index]) for index, score in enumerate(scores)),
        total_cost_usd=total_cost,
        capped=capped,
        calls=calls,
        cache_hits=cache_hits,
        unscored_by_cap=unscored_by_cap,
        unscored_by_budget=unscored_by_budget,
        errors=tuple(sorted(errors.items(), key=lambda item: (-item[1], item[0]))),
        stopped_on=stopped_on,
        throttled=throttled,
        spent_today_usd=jev_budget.spent_today_usd(home_root),
        daily_budget_usd=budget,
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
    """``rank_postings_report`` as ``(scores, total_cost_usd, capped)``, one call after another."""

    report = rank_postings_report(
        rows,
        client=client,
        resume_text=resume_text,
        prefs=prefs,
        profile_id=profile_id,
        resume_revision_id=resume_revision_id,
        home_root=home_root,
        target=target,
        cost_cap_usd=cost_cap_usd,
        model=model,
    )
    return report.scores, report.total_cost_usd, report.capped


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
    "FIT_THRESHOLDS",
    "JEV_PROMPT_VERSION",
    "RUN_CONCURRENCY",
    "RUN_RETRIES",
    "RankPreferences",
    "RankReport",
    "RankStatus",
    "cache_dir",
    "ensure_log_handler",
    "fit_for_score",
    "format_cost",
    "format_usd",
    "log_rank_status",
    "order_by_rank",
    "rank_postings",
    "rank_postings_report",
    "read_cached_scores",
    "resume_digest",
    "unscored",
]
