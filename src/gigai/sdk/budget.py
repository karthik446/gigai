"""``ctx.budget``: a gig's spend caps, what it spent today, and its per-gig key (P12).

A gig declares its keys in ``gig.toml``::

    [[keys]]
    service = "openrouter"            # env defaults to the catalog's OPENROUTER_API_KEY
    alias = "scout"                   # the per-gig key: OPENROUTER_API_KEY_SCOUT, before the shared key
    spend_cap = { usd_per_day = 1.0, usd_per_run = 0.25, calls_per_day = 20 }

The first row with a ``spend_cap`` caps the gig. Core keeps what the gig charged today in
``<home>/gigs/<gig>/spend.json`` (one UTC day, reset on the next) and the current run's share in memory, on an
:class:`~gigai.model_execution.InvocationBudget` ledger. USD comes from the adapters' reported cost (``charge``); the
hard stop for USD is the per-gig key's own limit at the provider (:class:`KeyProvider`) -- when the provider says the
key is spent, ``check`` refuses with the provider's words and does not retry.

Fail closed: a ``spend.json`` that cannot be read as today's numbers (corrupt, negative, not a number) refuses every
``check``/``reserve``/``charge`` with :class:`SpendFileError` naming the file, and never resets a cap; ``status``
reports the error instead of raising.

Nothing here keeps or prints a secret: ``present`` in :meth:`GigBudget.status` comes from variable names only, and the
key value only passes through :attr:`GigBudget.resolver` to an adapter or to a provider's ``usage`` call.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol
from collections.abc import Callable, Iterator, Mapping

from .. import secrets_store
from ..credentials import CredentialReferenceError, CredentialUnavailableError, alias_env_name, resolve_reference_value
from ..gigs.home import ensure_gig_folder
from ..model_execution import InvocationBudget
from ..secrets_catalog import KNOWN_SERVICES

SPEND_FILENAME = "spend.json"
SPEND_SCHEMA = "gig-spend:1"
_UNBOUNDED = sys.maxsize

__all__ = [
    "BudgetExhausted",
    "GigBudget",
    "KeyExhausted",
    "KeyProvider",
    "KeyRow",
    "KeyUsage",
    "LoopbackKeyProvider",
    "OpenRouterKeyProvider",
    "Remaining",
    "SpendCap",
    "SpendFileError",
    "Spent",
    "key_rows",
    "status_fields",
]


class BudgetExhausted(Exception):
    """A cap is reached, a reservation does not fit, or the provider says the key is spent (its message verbatim)."""

    code = "budget_exhausted"


class SpendFileError(BudgetExhausted):
    """``spend.json`` cannot be read as today's spend; the gig is refused until the file is fixed or removed."""

    code = "spend_file_unreadable"


class KeyExhausted(Exception):
    """Raised by a :class:`KeyProvider` whose key has no limit left; ``str(error)`` is the provider's message."""


@dataclass(frozen=True)
class SpendCap:
    """A ``[[keys]] spend_cap``; ``None`` means that dimension is not capped."""

    usd_per_day: float | None = None
    usd_per_run: float | None = None
    calls_per_day: int | None = None

    @classmethod
    def from_manifest(cls, value: Mapping[str, Any] | None) -> SpendCap:
        if not value:
            return cls()
        return cls(
            usd_per_day=_optional_float(value.get("usd_per_day")),
            usd_per_run=_optional_float(value.get("usd_per_run")),
            calls_per_day=None if value.get("calls_per_day") is None else int(value["calls_per_day"]),
        )

    @property
    def capped(self) -> bool:
        return self.usd_per_day is not None or self.usd_per_run is not None or self.calls_per_day is not None

    def to_json(self) -> dict[str, float | int | None]:
        return {"usd_per_day": self.usd_per_day, "usd_per_run": self.usd_per_run, "calls_per_day": self.calls_per_day}


@dataclass(frozen=True)
class KeyRow:
    """One ``[[keys]]`` row; ``env`` falls back to the catalog's variable for the service, ``None`` when unknown."""

    service: str
    env: str | None
    required: bool = True
    alias: str | None = None
    spend_cap: SpendCap = field(default_factory=SpendCap)

    @property
    def alias_error(self) -> str | None:
        """Why the alias cannot name a variable (the manifest schema accepts any string), or ``None``."""

        if self.alias is None or self.env is None:
            return None
        try:
            alias_env_name(self.env, self.alias)
        except CredentialReferenceError as error:
            return str(error)
        return None

    @property
    def alias_env(self) -> str | None:
        """The per-gig key's variable (``<ENV>_<ALIAS>``), or ``None`` without an alias, a known variable or a usable alias."""

        if self.alias is None or self.env is None or self.alias_error is not None:
            return None
        return alias_env_name(self.env, self.alias)

    def present(self, home: Path) -> bool:
        """Whether the per-gig key or the shared one is set -- by name only, in the environment or the secrets store."""

        names = [name for name in (self.alias_env, self.env) if name is not None]
        if any(name in os.environ for name in names):
            return True
        return bool(names) and bool(set(names) & secrets_store.names_set(home_root=home))

    def to_json(self, home: Path) -> dict[str, Any]:
        return {
            "service": self.service,
            "env": self.env,
            "alias": self.alias,
            "alias_env": self.alias_env,
            "required": self.required,
            "present": self.present(home),
            "spend_cap": self.spend_cap.to_json() if self.spend_cap.capped else None,
            "error": self.alias_error,
        }


def key_rows(manifest: Mapping[str, Any]) -> tuple[KeyRow, ...]:
    """The manifest's ``[[keys]]`` rows as :class:`KeyRow`\\s (an empty tuple when the gig declares none)."""

    rows = []
    for raw in manifest.get("keys") or ():
        service = str(raw["service"])
        rows.append(
            KeyRow(
                service=service,
                env=raw.get("env") or KNOWN_SERVICES.get(service),
                required=bool(raw.get("required", True)),
                alias=raw.get("alias"),
                spend_cap=SpendCap.from_manifest(raw.get("spend_cap")),
            )
        )
    return tuple(rows)


@dataclass(frozen=True)
class Remaining:
    """What is left before a cap stops the gig; ``None`` means uncapped in that dimension."""

    usd: float | None
    calls: int | None

    def to_json(self) -> dict[str, float | int | None]:
        return {"usd": self.usd, "calls": self.calls}


@dataclass(frozen=True)
class Spent:
    """What the gig charged on one UTC day."""

    day: str
    usd: float = 0.0
    calls: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"schema_version": SPEND_SCHEMA, "day": self.day, "usd": self.usd, "calls": self.calls}


@dataclass(frozen=True)
class KeyUsage:
    """What a provider reports for one key; ``None`` limits mean the key is not limited there."""

    usage_usd: float
    limit_usd: float | None
    limit_remaining_usd: float | None


class KeyProvider(Protocol):
    """Where a per-gig key lives: created with a USD limit, limited later, asked for its usage.

    ``usage`` raises :class:`KeyExhausted` with the provider's own message when the key has nothing left; callers
    pass that message on as-is and never retry.
    """

    def create(self, alias: str, *, limit_usd: float | None) -> str: ...

    def limit(self, key: str, *, limit_usd: float | None) -> None: ...

    def usage(self, key: str) -> KeyUsage: ...


class LoopbackKeyProvider:
    """An in-memory :class:`KeyProvider` for tests and dry runs: keys are ``loop-<alias>-<n>``, usage is what ``spend`` says."""

    def __init__(self) -> None:
        self._limits: dict[str, float | None] = {}
        self._usage: dict[str, float] = {}
        self.usage_calls = 0

    def create(self, alias: str, *, limit_usd: float | None) -> str:
        key = f"loop-{alias}-{len(self._limits) + 1}"
        self._limits[key] = limit_usd
        self._usage[key] = 0.0
        return key

    def limit(self, key: str, *, limit_usd: float | None) -> None:
        self._limits[key] = limit_usd

    def spend(self, key: str, usd: float) -> None:
        self._usage[key] = self._usage.get(key, 0.0) + usd

    def usage(self, key: str) -> KeyUsage:
        self.usage_calls += 1
        if key not in self._limits:
            raise KeyExhausted("loopback: unknown key")
        limit, used = self._limits[key], self._usage[key]
        remaining = None if limit is None else max(0.0, limit - used)
        if remaining is not None and remaining <= 0:
            raise KeyExhausted("Key limit exceeded")
        return KeyUsage(usage_usd=used, limit_usd=limit, limit_remaining_usd=remaining)


class OpenRouterKeyProvider:
    """OpenRouter runtime keys (``/api/v1/keys`` with a provisioning key; ``/api/v1/key`` with the key itself).

    NOT exercised live from this repository: the endpoints and response shapes follow OpenRouter's published API
    reference at the time of writing and are checked only through :class:`LoopbackKeyProvider`-shaped tests.
    ``provisioning_key`` is needed for ``create``/``limit``; ``usage`` needs only the key being asked about.
    """

    base_url = "https://openrouter.ai/api/v1"

    def __init__(self, *, provisioning_key: str | None = None, timeout: float = 10.0) -> None:
        self._provisioning_key = provisioning_key
        self._timeout = timeout

    def create(self, alias: str, *, limit_usd: float | None) -> str:
        body = {"name": alias, "limit": limit_usd}
        return str(self._call("POST", "/keys", body, bearer=self._provisioning())["key"])

    def limit(self, key: str, *, limit_usd: float | None) -> None:
        key_hash = self._call("GET", "/key", None, bearer=key)["data"].get("hash")
        if not key_hash:
            raise KeyExhausted("OpenRouter did not return the key's hash")
        self._call("PATCH", f"/keys/{key_hash}", {"limit": limit_usd}, bearer=self._provisioning())

    def usage(self, key: str) -> KeyUsage:
        data = self._call("GET", "/key", None, bearer=key)["data"]
        remaining = _optional_float(data.get("limit_remaining"))
        if remaining is not None and remaining <= 0:
            raise KeyExhausted("Key limit exceeded")
        return KeyUsage(
            usage_usd=float(data.get("usage") or 0.0),
            limit_usd=_optional_float(data.get("limit")),
            limit_remaining_usd=remaining,
        )

    def _provisioning(self) -> str:
        if not self._provisioning_key:
            raise KeyExhausted("OpenRouter key management needs a provisioning key")
        return self._provisioning_key

    def _call(self, method: str, path: str, body: Mapping[str, Any] | None, *, bearer: str) -> dict[str, Any]:
        import urllib.error
        import urllib.request

        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=None if body is None else json.dumps(body).encode("utf-8"),
            method=method,
            headers={"Authorization": f"Bearer {bearer}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310 - fixed https host
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise KeyExhausted(_provider_message(error)) from None


def _provider_message(error: Any) -> str:
    """The provider's own words for a failed key call (``error.message`` of the JSON body, else the HTTP reason)."""

    try:
        payload = json.loads(error.read().decode("utf-8"))
        message = payload.get("error", {}).get("message")
    except Exception:  # noqa: BLE001 - any unreadable body falls back to the status line
        message = None
    return str(message) if message else f"HTTP {error.code}: {error.reason}"


@dataclass(frozen=True)
class GigBudget:
    """One gig's budget: its caps, today's spend, the current run's ledger and its per-gig key.

    ``remaining``/``check``/``status`` read; ``charge`` records a call that happened; ``reserve`` is the fail-closed
    pre-charge (check, fit, charge). ``with_provider`` attaches the :class:`KeyProvider` that ``check`` consults;
    without one only the local caps apply (no network).
    """

    home: Path
    gig: str
    keys: tuple[KeyRow, ...]
    provider: KeyProvider | None = None
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    run: InvocationBudget = field(default=None, repr=False)  # type: ignore[assignment]  # built in __post_init__

    def __post_init__(self) -> None:
        if self.run is None:
            caps = self.caps
            ledger = InvocationBudget(
                max_model_calls=_UNBOUNDED if caps.calls_per_day is None else caps.calls_per_day,
                max_tokens=_UNBOUNDED,
                max_usd=caps.usd_per_run,
            )
            object.__setattr__(self, "run", ledger)

    @classmethod
    def for_gig(cls, home: Path, gig: str, manifest: Mapping[str, Any]) -> GigBudget:
        return cls(home=Path(home), gig=gig, keys=key_rows(manifest))

    def with_provider(self, provider: KeyProvider | None) -> GigBudget:
        return replace(self, provider=provider)

    @property
    def key(self) -> KeyRow | None:
        """The row whose ``spend_cap`` caps the gig (the first capped row), else the first row, else ``None``."""

        for row in self.keys:
            if row.spend_cap.capped:
                return row
        return self.keys[0] if self.keys else None

    @property
    def caps(self) -> SpendCap:
        row = self.key
        return SpendCap() if row is None else row.spend_cap

    @property
    def resolver(self) -> Callable[..., str]:
        """A ``CredentialResolver`` for adapters: the per-gig key (alias) before the shared one, in this home.

        An alias the manifest accepts but no variable can carry (:attr:`KeyRow.alias_error`) makes every call raise
        ``CredentialReferenceError``; ``check``/``reserve`` refuse it first.
        """

        row = self.key
        return partial(resolve_reference_value, home_root=self.home, alias=None if row is None else row.alias)

    def spent(self) -> Spent:
        """Today's charges (UTC); a file from another day counts as nothing; no file is zero.

        Raises :class:`SpendFileError` (fail closed) when the file exists but is not today's numbers.
        """

        today, path = _today(), self._path()
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return Spent(day=today)
        except OSError as error:
            raise SpendFileError(f"{self.gig}: cannot read {path}: {error.strerror or error}; fix or remove the file") from None
        try:
            raw = json.loads(text)
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
            if raw.get("day") != today:
                return Spent(day=today)
            usd, calls = _number(raw.get("usd", 0.0), "usd"), _number(raw.get("calls", 0), "calls")
            if calls != int(calls):
                raise ValueError("calls is not a whole number")
        except ValueError as error:
            raise SpendFileError(f"{self.gig}: cannot read {path}: {error}; fix or remove the file") from None
        return Spent(day=today, usd=usd, calls=int(calls))

    def remaining(self) -> Remaining:
        return self._remaining(self.spent())

    def _remaining(self, spent: Spent) -> Remaining:
        caps = self.caps
        usd: float | None = None
        if caps.usd_per_day is not None:
            usd = max(0.0, caps.usd_per_day - spent.usd)
        if caps.usd_per_run is not None:
            run_left = max(0.0, caps.usd_per_run - self.run.usd)
            usd = run_left if usd is None else min(usd, run_left)
        calls: int | None = None
        if caps.calls_per_day is not None:
            calls = max(0, caps.calls_per_day - spent.calls)
        return Remaining(usd=usd, calls=calls)

    def _refuse_caps(self, left: Remaining, spent: Spent) -> None:
        if left.calls is not None and left.calls <= 0:
            raise BudgetExhausted(f"{self.gig}: calls_per_day {self.caps.calls_per_day} reached for {spent.day}")
        if left.usd is not None and left.usd <= 0:
            raise BudgetExhausted(f"{self.gig}: usd cap reached (usd_per_day {self.caps.usd_per_day}, usd_per_run {self.caps.usd_per_run})")

    def _ask_provider(self) -> None:
        """One ``usage`` call to the attached provider (never under the lock); its refusal verbatim, no retry."""

        if self.provider is None or self.key is None:
            return
        try:
            key = self.resolver(_reference(self.key))
        except CredentialUnavailableError as error:
            raise BudgetExhausted(f"{self.gig}: no key for {self.key.service}: {error}") from None
        try:
            self.provider.usage(key)
        except KeyExhausted as error:
            raise BudgetExhausted(str(error)) from None

    def check(self) -> Remaining:
        """Refuse (``BudgetExhausted``) when a cap is reached, the key is missing or unusable, the spend file is
        unreadable, or the provider says the key is spent; else what is left."""

        spent = self.spent()
        left = self._remaining(spent)
        self._refuse_caps(left, spent)
        self._ask_provider()
        return left

    def charge(self, usd: float, *, calls: int = 1) -> Spent:
        """Record ``usd`` and ``calls`` that already happened: today's file and the run ledger. Never refuses a cap
        (an unreadable spend file still raises :class:`SpendFileError`)."""

        if usd < 0 or calls < 0:
            raise ValueError("charges are non-negative")
        with self._locked():
            return self._charge_locked(self.spent(), usd, calls)

    def _charge_locked(self, spent: Spent, usd: float, calls: int) -> Spent:
        spent = Spent(day=spent.day, usd=spent.usd + usd, calls=spent.calls + calls)
        _write_json(self._path(), spent.to_json())
        self.run.model_calls += calls
        self.run.usd += usd
        return spent

    def reserve(self, usd: float) -> Remaining:
        """Fail-closed pre-charge of one call costing up to ``usd``: check the caps, fit, charge -- read, decision
        and write under one lock, so concurrent reservations never pass a cap together."""

        if usd < 0:
            raise ValueError("reservations are non-negative")
        self._ask_provider()
        with self._locked():
            spent = self.spent()
            left = self._remaining(spent)
            self._refuse_caps(left, spent)
            if left.usd is not None and usd > left.usd:
                raise BudgetExhausted(f"{self.gig}: ${usd:.4f} does not fit the remaining ${left.usd:.4f}")
            return self._remaining(self._charge_locked(spent, usd, 1))

    def status(self) -> dict[str, Any]:
        """The ``budget`` object of ``status --json``: caps, spent, remaining and the UTC day window; an unreadable
        spend file is reported in ``error`` (``spent``/``remaining`` null) instead of raised."""

        row, error = self.key, None
        try:
            spent: Spent | None = self.spent()
        except SpendFileError as refused:
            spent, error = None, str(refused)
        day = datetime.strptime(_today() if spent is None else spent.day, "%Y-%m-%d").replace(tzinfo=UTC)
        return {
            "key": None if row is None else {"service": row.service, "alias": row.alias},
            "caps": self.caps.to_json(),
            "spent": None if spent is None else {"usd": spent.usd, "calls": spent.calls},
            "remaining": None if spent is None else self._remaining(spent).to_json(),
            "window": {
                "day": day.strftime("%Y-%m-%d"),
                "timezone": "UTC",
                "resets_at": (day + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            "error": error,
        }

    def _path(self) -> Path:
        return Path(self.home) / "gigs" / self.gig / SPEND_FILENAME

    @contextmanager
    def _locked(self) -> Iterator[None]:
        folder = ensure_gig_folder(self.home, self.gig)
        descriptor = os.open(folder / f"{SPEND_FILENAME}.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            os.close(descriptor)


def status_fields(home: Path, gig: str, manifest: Mapping[str, Any]) -> dict[str, Any]:
    """``{"budget": ..., "keys": [...]}`` for a ``status --json`` payload; ``None``/``[]`` for a gig without keys."""

    budget = GigBudget.for_gig(home, gig, manifest)
    if not budget.keys:
        return {"budget": None, "keys": []}
    return {"budget": budget.status(), "keys": [row.to_json(budget.home) for row in budget.keys]}


def _reference(row: KeyRow) -> Any:
    from ..config import CredentialReference

    if row.env is None:
        raise BudgetExhausted(f"key {row.service!r} names no environment variable")
    if row.alias_error is not None:
        raise BudgetExhausted(f"key {row.service!r} alias {row.alias!r} is not usable: {row.alias_error}")
    return CredentialReference(name=row.service, kind="environment", reference=row.env)


def _number(value: Any, name: str) -> float:
    """A finite, non-negative JSON number; anything else is a ``ValueError`` naming the field."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} is not a number")
    if value != value or value in (float("inf"), float("-inf")):
        raise ValueError(f"{name} is not finite")
    if value < 0:
        raise ValueError(f"{name} is negative")
    return float(value)


def _today() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
