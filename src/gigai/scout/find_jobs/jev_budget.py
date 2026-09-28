"""What Jev may cost in a day, and what it has cost today.

Operator (UAT 2026-09-28): Jev had spent $1.00, too much. The per-run cost
cap bounds one ranking pass; nothing bounded a day of runs, of ``POST
/rank`` calls and of pages that ranked on their own. This module is the
bound: every Jev call that was paid for is written to a ledger under the
home, and a pass stops asking once today's total reaches the daily budget.

Ledger: ``<home>/cache/scout/jev/spend/<YYYY-MM-DD>.jsonl``, one line per
paid call, ``{"at", "cost_usd", "where", "run_id"}``; never a posting, a
resume or a key. It is the home's, not a project's: the budget holds
across every project that shares the key. A day is the machine's local
day. Lines are appended (one short write each), so a run's child process
and the server can both write; a line that cannot be read is skipped.

Budget: ``daily_budget_usd(home_root)`` is the ONE place it is read:
``GIGAI_JEV_DAILY_BUDGET_USD`` when set to a number that is not negative
(the environment wins, so an eval can run under its own cap), else the
operator's setting, else ``DEFAULT_DAILY_BUDGET_USD``.

On/off: ``rank_enabled(home_root)`` is the ONE place "Rank with Jev" is
read (uat-bug-021 decision a). Off: a run's rank pass, a "Score with Jev"
click and quick assess ask Jev nothing.

Per-run cap: ``run_cost_cap_usd(home_root)`` is the ONE place the per-run
cap is read (jev-disclosure-fixes, TARGET 4): ``GIGAI_JEV_COST_CAP_USD``
when set to a number that is not negative, else the operator's setting
(``jev_run_cap_usd``), else ``jev_rank.DEFAULT_COST_CAP_USD`` ($0.25). Both
the search's rank pass (``market_acquisition._rank_candidates``) and
``POST /rank`` read it; a request's own ``cost_cap_usd`` may only LOWER it,
never raise it above what the operator (or the environment) allows.

Settings (ui-pass, orchestrator decision B): all three live in ONE
home-wide file, ``<home>/local/scout/jev-settings.json``,
``{"schema_version", "jev_daily_budget_usd", "jev_rank_enabled",
"jev_run_cap_usd"}``, beside the home's other Scout state
(``target_resolution.earlier_project_notice_marker``). Not a project's
preferences: the ledger is the home's, so one budget holds against it. Not
under ``cache/``: clearing a cache never resets the limit or turns ranking
back on. A missing file, or a missing ``jev_run_cap_usd`` key in an older
file, is the defaults (on, $0.50/day, $0.25/run); a file that cannot be
read turns ranking OFF (a spend switch fails closed) and is logged by
exception type. Written only by ``write_settings`` (``PUT
/api/jev/settings``, Settings).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path

from .discovery.storage import atomic_write

DEFAULT_DAILY_BUDGET_USD = 0.50
DAILY_BUDGET_ENV = "GIGAI_JEV_DAILY_BUDGET_USD"
RUN_COST_CAP_ENV = "GIGAI_JEV_COST_CAP_USD"
SETTINGS_SCHEMA_VERSION = "scout-jev-settings:1"

_logger = logging.getLogger("gigai.scout.server")


class JevSettingsError(ValueError):
    """A settings value that cannot be stored; the message names the field only."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def format_usd(value: float) -> str:
    """A configured amount as it was set: ``0.25`` -> ``"0.25"``, ``0.125`` -> ``"0.125"``."""

    text = f"{value:.4f}".rstrip("0")
    whole, _, cents = text.partition(".")
    return f"{whole}.{cents.ljust(2, '0')}"


def format_cost(value: float) -> str:
    """A spent amount in cents; below one cent, to four places: ``"0.23"``, ``"0.0005"``."""

    if value <= 0:
        return "0.00"
    if value < 0.01:
        return format_usd(value)
    return f"{value:.2f}"


def settings_path(home_root: Path) -> Path:
    return Path(home_root) / "local" / "scout" / "jev-settings.json"


def _budget_value(value: object, *, field: str = "jev_daily_budget_usd") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value >= 0 or value == float("inf"):
        raise JevSettingsError("invalid_value", f"{field} must be a number, 0 or more")
    return float(value)


def _enabled_value(value: object) -> bool:
    if type(value) is not bool:
        raise JevSettingsError("wrong_type", "jev_rank_enabled must be true or false")
    return value


def _default_run_cap_usd() -> float:
    from .jev_rank import DEFAULT_COST_CAP_USD

    return DEFAULT_COST_CAP_USD


def read_settings(home_root: Path | None) -> dict[str, object]:
    """``{"jev_daily_budget_usd": float, "jev_rank_enabled": bool, "jev_run_cap_usd":
    float}`` as stored, defaults filled in.

    No home or no file: the defaults. A file that cannot be read (a
    symlink, not JSON, a wrong value): ranking OFF, the default budgets.
    """

    settings: dict[str, object] = {
        "jev_daily_budget_usd": DEFAULT_DAILY_BUDGET_USD,
        "jev_rank_enabled": True,
        "jev_run_cap_usd": _default_run_cap_usd(),
    }
    if home_root is None:
        return settings
    path = settings_path(home_root)
    if not path.is_symlink() and not path.exists():
        return settings
    try:
        if path.is_symlink() or not path.is_file():
            raise JevSettingsError("invalid_value", "the settings path is not a regular file")
        stored = json.loads(path.read_text(encoding="utf-8"))
        if type(stored) is not dict or stored.get("schema_version") != SETTINGS_SCHEMA_VERSION:
            raise JevSettingsError("bad_enum", "the settings file's schema_version is unsupported")
        if "jev_daily_budget_usd" in stored:
            settings["jev_daily_budget_usd"] = _budget_value(stored["jev_daily_budget_usd"])
        if "jev_rank_enabled" in stored:
            settings["jev_rank_enabled"] = _enabled_value(stored["jev_rank_enabled"])
        if "jev_run_cap_usd" in stored:
            settings["jev_run_cap_usd"] = _budget_value(stored["jev_run_cap_usd"], field="jev_run_cap_usd")
    except (OSError, ValueError) as exc:
        _logger.warning("jev settings: %s could not be read (%s); Rank with Jev is off", path.name, type(exc).__name__)
        return {
            "jev_daily_budget_usd": DEFAULT_DAILY_BUDGET_USD,
            "jev_rank_enabled": False,
            "jev_run_cap_usd": _default_run_cap_usd(),
        }
    return settings


def write_settings(
    home_root: Path, *, daily_budget_usd: object = None, rank_enabled: object = None, run_cap_usd: object = None
) -> dict[str, object]:
    """Store the settings given; one that is not given keeps its stored value. Returns what is stored."""

    current = read_settings(home_root)
    if daily_budget_usd is not None:
        current["jev_daily_budget_usd"] = _budget_value(daily_budget_usd)
    if rank_enabled is not None:
        current["jev_rank_enabled"] = _enabled_value(rank_enabled)
    if run_cap_usd is not None:
        current["jev_run_cap_usd"] = _budget_value(run_cap_usd, field="jev_run_cap_usd")
    path = settings_path(home_root)
    if path.is_symlink():
        raise JevSettingsError("invalid_value", "the settings path is a symlink")
    encoded = json.dumps({"schema_version": SETTINGS_SCHEMA_VERSION, **current}, indent=2, sort_keys=True) + "\n"
    atomic_write(path, encoded.encode("utf-8"))
    return current


def budget_env_override() -> float | None:
    """``GIGAI_JEV_DAILY_BUDGET_USD`` when it is set to a number that is not negative, else ``None``."""

    raw = os.environ.get(DAILY_BUDGET_ENV)
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return None
        if value >= 0:
            return value
    return None


def daily_budget_usd(home_root: Path | None = None) -> float:
    """The most Jev may cost in one day, in USD: the environment, else the stored setting, else $0.50."""

    override = budget_env_override()
    if override is not None:
        return override
    return float(read_settings(home_root)["jev_daily_budget_usd"])  # type: ignore[arg-type]


def rank_enabled(home_root: Path | None = None) -> bool:
    """"Rank with Jev": true unless the operator turned it off (or the settings file is unreadable)."""

    return bool(read_settings(home_root)["jev_rank_enabled"])


def run_cap_env_override() -> float | None:
    """``GIGAI_JEV_COST_CAP_USD`` when it is set to a number that is not negative, else ``None``."""

    raw = os.environ.get(RUN_COST_CAP_ENV)
    if raw:
        try:
            value = float(raw)
        except ValueError:
            return None
        if value >= 0:
            return value
    return None


def run_cost_cap_usd(home_root: Path | None = None) -> float:
    """The most one ranking pass may cost, in USD: the environment, else the stored
    setting, else ``jev_rank.DEFAULT_COST_CAP_USD`` ($0.25). The ONE place both the
    search's own rank pass and ``POST /rank`` read the per-run cap from."""

    override = run_cap_env_override()
    if override is not None:
        return override
    return float(read_settings(home_root)["jev_run_cap_usd"])  # type: ignore[arg-type]


def _today() -> date:
    return datetime.now().astimezone().date()


def spend_dir(home_root: Path) -> Path:
    return Path(home_root) / "cache" / "scout" / "jev" / "spend"


def _ledger_path(home_root: Path, day: date) -> Path:
    return spend_dir(home_root) / f"{day.isoformat()}.jsonl"


def record_spend(home_root: Path, cost_usd: float, *, where: str, run_id: str | None = None, day: date | None = None) -> None:
    """Append one paid call to today's ledger. A call that cost nothing is not written."""

    if not cost_usd > 0:
        return
    path = _ledger_path(home_root, day or _today())
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(
        {
            "at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "cost_usd": f"{cost_usd:.6f}",
            "where": where,
            "run_id": run_id,
        },
        separators=(",", ":"),
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def spent_today_usd(home_root: Path, *, day: date | None = None) -> float:
    """The sum of today's ledger; 0 when there is none."""

    path = _ledger_path(home_root, day or _today())
    if path.is_symlink() or not path.is_file():
        return 0.0
    total = 0.0
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return 0.0
    for line in raw.splitlines():
        try:
            entry = json.loads(line)
            total += float(entry["cost_usd"])
        except (ValueError, TypeError, KeyError):
            continue
    return total


def usage_line(spent_usd: float, budget_usd: float) -> str:
    """``Jev: $0.31 of $0.50 today``."""

    return f"Jev: ${format_cost(spent_usd)} of ${format_usd(budget_usd)} today"


def usage(home_root: Path) -> dict[str, object]:
    """Today's Jev spend and the daily budget, as an API answers it."""

    spent = spent_today_usd(home_root)
    budget = daily_budget_usd(home_root)
    return {
        "day": _today().isoformat(),
        "spent_today_usd": f"{spent:.6f}",
        "daily_budget_usd": format_usd(budget),
        "remaining_usd": f"{max(0.0, budget - spent):.6f}",
        "budget_reached": spent >= budget,
        "line": usage_line(spent, budget),
    }


__all__ = [
    "DAILY_BUDGET_ENV",
    "DEFAULT_DAILY_BUDGET_USD",
    "RUN_COST_CAP_ENV",
    "JevSettingsError",
    "SETTINGS_SCHEMA_VERSION",
    "budget_env_override",
    "daily_budget_usd",
    "rank_enabled",
    "read_settings",
    "run_cap_env_override",
    "run_cost_cap_usd",
    "settings_path",
    "write_settings",
    "format_cost",
    "format_usd",
    "record_spend",
    "spend_dir",
    "spent_today_usd",
    "usage",
    "usage_line",
]
