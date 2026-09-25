"""``DiscoveryPrefs`` — the 11 S23 setup-interview answers, stored per project.

Read from ``<home>/scout/<project_id>/discovery/prefs.json`` (S23
Investigate §2's question list; S24's operator decisions on top: OpenAI
``web_search`` + the H-1B baseline as the two sources, budget guarded per
session). Not a versioned journal contract (Scout's ``find-jobs.json``
pattern, not the watchlist's journal-authoritative one) -- this is a plain,
operator-editable local file, atomically written, matching
``run_supervisor.py``'s ``<home>/run/scout/<project_id>.json`` storage
convention (state keyed by project id, not the target path, so it survives
a target rename and never lives inside the operator's project directory).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import json

from .storage import atomic_write, discovery_dir

_SCHEMA_VERSION = "scout-discovery-prefs:1"

_WORK_MODES = ("remote", "hybrid", "onsite", "any")


class DiscoveryPrefsError(ValueError):
    """Raised for a malformed ``prefs.json``; message never carries secrets."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise DiscoveryPrefsError(code, message)


def _strings(value: object, name: str) -> tuple[str, ...]:
    if type(value) is not list or not all(type(item) is str for item in value):
        _fail("invalid_value", f"{name} must be a list of strings")
    return tuple(value)  # type: ignore[arg-type]


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    if type(value) is not str:
        _fail("invalid_value", f"{name} must be a string or null")
    return value


def _bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        _fail("invalid_value", f"{name} must be a boolean")
    return value


def _positive_int(value: object, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        _fail("invalid_value", f"{name} must be a positive integer")
    return value


def _positive_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        _fail("invalid_value", f"{name} must be a positive number")
    return float(value)


@dataclass(frozen=True)
class DiscoveryPrefs:
    """The 11 setup-interview answers (S23 Investigate §2) that drive discovery."""

    schema_version: str = field(default=_SCHEMA_VERSION, init=False)
    roles: tuple[str, ...] = ()
    titles_to_avoid: tuple[str, ...] = ()
    countries: tuple[str, ...] = ()
    work_mode: str = "any"
    city: str | None = None
    visa_sponsorship_required: bool = False
    exclude_companies: tuple[str, ...] = ()
    watch_companies: tuple[str, ...] = ()
    company_stage_size: str | None = None
    industries_include: tuple[str, ...] = ()
    industries_exclude: tuple[str, ...] = ()
    must_have_stack: tuple[str, ...] = ()
    dealbreaker_stack: tuple[str, ...] = ()
    cadence_days: int = 7
    budget_usd_per_session: float = 0.50

    def __post_init__(self) -> None:
        if self.work_mode not in _WORK_MODES:
            _fail("invalid_value", f"work_mode must be one of {_WORK_MODES}")
        for code in self.countries:
            if len(code) != 2 or not code.isalpha() or not code.isupper():
                _fail("invalid_value", f"countries entry {code!r} must be an ISO-3166 alpha-2 code")
        if self.cadence_days <= 0:
            _fail("invalid_value", "cadence_days must be positive")
        if self.budget_usd_per_session <= 0:
            _fail("invalid_value", "budget_usd_per_session must be positive")

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "roles": list(self.roles),
            "titles_to_avoid": list(self.titles_to_avoid),
            "countries": list(self.countries),
            "work_mode": self.work_mode,
            "city": self.city,
            "visa_sponsorship_required": self.visa_sponsorship_required,
            "exclude_companies": list(self.exclude_companies),
            "watch_companies": list(self.watch_companies),
            "company_stage_size": self.company_stage_size,
            "industries_include": list(self.industries_include),
            "industries_exclude": list(self.industries_exclude),
            "must_have_stack": list(self.must_have_stack),
            "dealbreaker_stack": list(self.dealbreaker_stack),
            "cadence_days": self.cadence_days,
            "budget_usd_per_session": self.budget_usd_per_session,
        }

    @classmethod
    def from_json(cls, obj: object) -> "DiscoveryPrefs":
        if type(obj) is not dict:
            _fail("invalid_value", "prefs must be a JSON object")
        value: dict[str, object] = obj  # type: ignore[assignment]
        if value.get("schema_version") != _SCHEMA_VERSION:
            _fail("bad_enum", "prefs.schema_version is unsupported")
        return cls(
            roles=_strings(value.get("roles", []), "roles"),
            titles_to_avoid=_strings(value.get("titles_to_avoid", []), "titles_to_avoid"),
            countries=_strings(value.get("countries", []), "countries"),
            work_mode=value.get("work_mode", "any"),  # type: ignore[arg-type]
            city=_optional_string(value.get("city"), "city"),
            visa_sponsorship_required=_bool(value.get("visa_sponsorship_required", False), "visa_sponsorship_required"),
            exclude_companies=_strings(value.get("exclude_companies", []), "exclude_companies"),
            watch_companies=_strings(value.get("watch_companies", []), "watch_companies"),
            company_stage_size=_optional_string(value.get("company_stage_size"), "company_stage_size"),
            industries_include=_strings(value.get("industries_include", []), "industries_include"),
            industries_exclude=_strings(value.get("industries_exclude", []), "industries_exclude"),
            must_have_stack=_strings(value.get("must_have_stack", []), "must_have_stack"),
            dealbreaker_stack=_strings(value.get("dealbreaker_stack", []), "dealbreaker_stack"),
            cadence_days=_positive_int(value.get("cadence_days", 7), "cadence_days"),
            budget_usd_per_session=_positive_float(value.get("budget_usd_per_session", 0.50), "budget_usd_per_session"),
        )


def _prefs_path(home_root: Path, target: Path) -> Path:
    return discovery_dir(home_root, target) / "prefs.json"


def load_prefs(*, home_root: Path, target: Path) -> DiscoveryPrefs | None:
    """Return the stored prefs for this project, or ``None`` if never set."""

    path = _prefs_path(home_root, target)
    if not path.is_file():
        return None
    return DiscoveryPrefs.from_json(json.loads(path.read_text(encoding="utf-8")))


def save_prefs(*, home_root: Path, target: Path, prefs: DiscoveryPrefs) -> None:
    """Atomically persist ``prefs`` for this project.

    Plain JSON, not ``canonical_json_bytes`` -- prefs aren't identity-bearing
    journal content (like ``find-jobs.json``), and ``budget_usd_per_session``
    is a float, which GigAI's canonical JSON forbids
    (``InvalidCanonicalValueError``, C0's own identity-bearing-JSON rule).
    """

    path = _prefs_path(home_root, target)
    encoded = json.dumps(prefs.to_json(), indent=2, sort_keys=True).encode("utf-8")
    atomic_write(path, encoded)


__all__ = ["DiscoveryPrefs", "DiscoveryPrefsError", "load_prefs", "save_prefs"]
