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

Per click (BK3): a step's ``cap_usd`` (``[steps].<id>.cap_usd``, ``steps set <id> --cap-usd``, or the call argument)
bounds one call. ``estimate(request, target)`` is the worst case the call can cost from the price table
``<home>/prices.json`` (:class:`PriceTable`: OpenRouter's model list, fetched once by :func:`fetch_prices` and cached
with its ``fetched_at``; older than :data:`PRICES_MAX_AGE` warns once and is still used): ``ceil(chars / 4)`` input
tokens (:data:`CHARS_PER_TOKEN`) times the prompt price plus ``max_output_tokens`` times the completion price, rounded
to the microdollar. ``reserve(estimate, cap_usd=cap)`` refuses ``estimated $X > cap $Y`` (:class:`ClickCapExceeded`)
before the provider is asked, the lock is taken or any adapter is called; an equal estimate passes. After the call
``settle`` moves the ledger from the estimate to the cost the provider reported.

Fail closed: a ``spend.json`` that cannot be read as today's numbers (corrupt, negative, not a number) refuses every
``check``/``reserve``/``charge`` with :class:`SpendFileError` naming the file, and never resets a cap; ``status``
reports the error instead of raising. A price the table lacks (or no ``max_output_tokens``) refuses the estimate with
:class:`PriceUnknownError` naming the model and the file, never an unbounded guess.

Nothing here keeps or prints a secret: ``present`` in :meth:`GigBudget.status` comes from variable names only, and the
key value only passes through :attr:`GigBudget.resolver` to an adapter or to a provider's ``usage`` call.
"""

from __future__ import annotations

import fcntl
import json
import math
import os
import sys
import tempfile
import uuid
import warnings
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from functools import cached_property, partial
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
PRICES_FILENAME = "prices.json"
PRICES_SCHEMA = "gig-prices:1"
#: OpenRouter's model list; ``pricing.prompt``/``pricing.completion`` are USD per token as decimal strings.
#: NOT fetched from this repository's tests (a loopback fake serves the same shape); unverified live.
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
#: The input-length rule of ``estimate``: one token per four characters of prompt, system and prior turns, rounded up.
CHARS_PER_TOKEN = 4
PRICES_MAX_AGE = timedelta(days=7)
_UNBOUNDED = sys.maxsize

__all__ = [
    "BudgetExhausted",
    "CHARS_PER_TOKEN",
    "ClickCapExceeded",
    "GigBudget",
    "KeyExhausted",
    "KeyProvider",
    "KeyRow",
    "KeyUsage",
    "LoopbackKeyProvider",
    "OPENROUTER_MODELS_URL",
    "OpenRouterKeyProvider",
    "PRICES_MAX_AGE",
    "Price",
    "PriceTable",
    "PriceUnknownError",
    "Remaining",
    "SpendCap",
    "SpendFileError",
    "Spent",
    "StalePricesWarning",
    "cap_value",
    "fetch_prices",
    "key_rows",
    "status_fields",
]


class BudgetExhausted(Exception):
    """A cap is reached, a reservation does not fit, or the provider says the key is spent (its message verbatim)."""

    code = "budget_exhausted"


class SpendFileError(BudgetExhausted):
    """``spend.json`` cannot be read as today's spend; the gig is refused until the file is fixed or removed."""

    code = "spend_file_unreadable"


class ClickCapExceeded(BudgetExhausted):
    """One call's worst-case estimate is over the step's ``cap_usd``; refused before any adapter call.

    The text is ``estimated $X > cap $Y`` in cents; when both round to the same cents it shows four, then six, decimals.
    """

    code = "cap_usd_exceeded"

    def __init__(self, estimate: float, cap: float) -> None:
        decimals = next((n for n in (2, 4) if f"{estimate:.{n}f}" != f"{cap:.{n}f}"), 6)
        super().__init__(f"estimated ${estimate:.{decimals}f} > cap ${cap:.{decimals}f}")
        self.estimate, self.cap = estimate, cap


class PriceUnknownError(BudgetExhausted):
    """No worst case can be put on the call: the price table is missing or lacks the model, or the request has no
    ``max_output_tokens``; the call is refused rather than guessed unbounded."""

    code = "price_unknown"


class StalePricesWarning(UserWarning):
    """``prices.json`` is older than :data:`PRICES_MAX_AGE`; the estimate uses it anyway. Warned once per budget."""


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


# --- the price table ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Price:
    """USD per token of one model: what it charges for a prompt token and for a completion token."""

    prompt: float
    completion: float


@dataclass(frozen=True)
class PriceTable:
    """``<home>/prices.json``: per-token prices by model id, with when they were fetched and from where.

    The file is ``{"schema_version": "gig-prices:1", "fetched_at": "<UTC, ISO 8601>", "source": "<url>",
    "prices": {"<model id>": {"prompt": <usd/token>, "completion": <usd/token>}}}``. Lookups are by the target's
    ``model`` string as config.toml spells it (an OpenRouter id such as ``openai/gpt-4o-mini``); a CLI target's model
    (``sonnet``) is priced only when the table carries that id.
    """

    path: Path
    fetched_at: datetime
    source: str
    prices: Mapping[str, Price]

    @classmethod
    def load(cls, path: Path) -> PriceTable:
        """The table in ``path``; :class:`PriceUnknownError` names the file when it is missing or not a price table."""

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise PriceUnknownError(f"no price table at {path}: fetch one with gigai.sdk.budget.fetch_prices") from None
        except (OSError, ValueError) as error:
            raise PriceUnknownError(f"{path} is not a price table: {error}") from None
        try:
            if payload.get("schema_version") != PRICES_SCHEMA:
                raise ValueError(f"schema_version is not {PRICES_SCHEMA!r}")
            fetched_at = datetime.fromisoformat(str(payload["fetched_at"]).replace("Z", "+00:00"))
            if fetched_at.tzinfo is None:
                raise ValueError("fetched_at has no timezone")
            prices = {
                str(model): Price(_number(entry["prompt"], f"prices[{model}].prompt"), _number(entry["completion"], f"prices[{model}].completion"))
                for model, entry in payload["prices"].items()
            }
        except (KeyError, TypeError, ValueError, AttributeError) as error:
            raise PriceUnknownError(f"{path} is not a price table: {error}") from None
        return cls(path=path, fetched_at=fetched_at, source=str(payload.get("source", "")), prices=prices)

    @classmethod
    def from_models(cls, payload: Mapping[str, Any], *, path: Path, source: str, fetched_at: datetime) -> PriceTable:
        """A table from OpenRouter's ``/api/v1/models`` body (``data[].id``, ``data[].pricing.prompt/completion``,
        USD per token as decimal strings); a model whose two prices are not numbers is left out."""

        prices = {}
        for item in payload.get("data") or ():
            pricing = item.get("pricing") if isinstance(item, Mapping) else None
            if not isinstance(pricing, Mapping) or not isinstance(item.get("id"), str):
                continue
            try:
                prices[item["id"]] = Price(_number(float(pricing["prompt"]), "prompt"), _number(float(pricing["completion"]), "completion"))
            except (KeyError, TypeError, ValueError):
                continue
        return cls(path=path, fetched_at=fetched_at, source=source, prices=prices)

    def write(self) -> None:
        """Write the table to :attr:`path` atomically (the home must exist)."""

        _write_json(
            self.path,
            {
                "schema_version": PRICES_SCHEMA,
                "fetched_at": self.fetched_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "source": self.source,
                "prices": {model: {"prompt": price.prompt, "completion": price.completion} for model, price in sorted(self.prices.items())},
            },
        )

    def stale(self, now: datetime | None = None) -> bool:
        return (now or datetime.now(UTC)) - self.fetched_at > PRICES_MAX_AGE

    def price(self, model: str) -> Price:
        """The price of ``model``; :class:`PriceUnknownError` names the model and the file when the table lacks it."""

        try:
            return self.prices[model]
        except KeyError:
            raise PriceUnknownError(f"no price for model {model!r} in {self.path}: refetch it or bind the step to a listed model") from None


def prices_path(home: Path) -> Path:
    return Path(home) / PRICES_FILENAME


def fetch_prices(home: Path, *, url: str = OPENROUTER_MODELS_URL, opener: Callable[..., Any] | None = None, timeout: float = 10.0) -> PriceTable:
    """Fetch the model list at ``url`` (OpenRouter's shape), write ``<home>/prices.json`` and return the table.

    ``opener`` is ``urllib.request.urlopen`` by default; tests pass a loopback server's URL. The real
    :data:`OPENROUTER_MODELS_URL` is never fetched by this repository's tests (unverified live); a response that is
    not that shape is a :class:`PriceUnknownError`.
    """

    import urllib.error
    import urllib.request

    open_url = opener or urllib.request.urlopen
    try:
        with open_url(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise PriceUnknownError(f"could not fetch prices from {url}: {error}") from None
    if not isinstance(payload, Mapping):
        raise PriceUnknownError(f"could not fetch prices from {url}: not a model list")
    table = PriceTable.from_models(payload, path=prices_path(home), source=url, fetched_at=datetime.now(UTC))
    table.write()
    return table


def cap_value(value: Any, where: str) -> float:
    """A ``cap_usd``: a finite, non-negative number (``0`` refuses every priced call); anything else is a
    ``ValueError`` naming ``where``."""

    if isinstance(value, str):
        try:
            value = float(value.strip())
        except ValueError:
            raise ValueError(f"{where}: {value!r} is not a USD amount (a cap_usd is a non-negative USD amount)") from None
    try:
        return _number(value, where)
    except ValueError as error:
        raise ValueError(f"{error} (a cap_usd is a non-negative USD amount)") from None


def _request_chars(request: Any) -> int:
    """The characters a request sends: prompt, system and the content of prior turns."""

    chars = len(getattr(request, "prompt", "") or "") + len(getattr(request, "system", None) or "")
    for turn in getattr(request, "messages", None) or ():
        content = turn.get("content") if isinstance(turn, Mapping) else None
        chars += len(content) if isinstance(content, str) else len(json.dumps(content, default=str)) if content is not None else 0
    return chars



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
    _warned: list[str] = field(default_factory=list, repr=False, compare=False)  # ``["stale"]`` once the stale-prices warning went out

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
        spent = Spent(day=spent.day, usd=round(spent.usd + usd, 6), calls=spent.calls + calls)
        _write_json(self._path(), spent.to_json())
        self.run.model_calls += calls
        self.run.usd = round(self.run.usd + usd, 6)
        return spent

    def reserve(self, usd: float, *, cap_usd: float | None = None) -> Remaining:
        """Fail-closed pre-charge of one call costing up to ``usd``: the per-click cap, then the caps, fit, charge --
        read, decision and write under one lock, so concurrent reservations never pass a cap together.

        ``cap_usd`` is the step's per-click cap: ``usd`` over it is :class:`ClickCapExceeded` (``estimated $X > cap
        $Y``) before the provider is asked or the lock taken; equal passes.
        """

        if usd < 0:
            raise ValueError("reservations are non-negative")
        if cap_usd is not None and usd > cap_usd:
            raise ClickCapExceeded(usd, cap_usd)
        self._ask_provider()
        with self._locked():
            spent = self.spent()
            left = self._remaining(spent)
            self._refuse_caps(left, spent)
            if left.usd is not None and usd > left.usd:
                raise BudgetExhausted(f"{self.gig}: ${usd:.4f} does not fit the remaining ${left.usd:.4f}")
            return self._remaining(self._charge_locked(spent, usd, 1))

    def settle(self, reserved: float, actual: float | None) -> Spent:
        """After the call: move today's file and the run ledger from the ``reserved`` estimate to the ``actual`` cost
        the provider reported (``None``: the estimate stands). Never refuses; the ledger never goes below zero."""

        if actual is None or actual < 0:
            return self.spent()
        with self._locked():
            spent = self.spent()
            usd = max(0.0, round(spent.usd + actual - reserved, 6))
            spent = Spent(day=spent.day, usd=usd, calls=spent.calls)
            _write_json(self._path(), spent.to_json())
            self.run.usd = max(0.0, round(self.run.usd + actual - reserved, 6))
            return spent

    @cached_property
    def prices(self) -> PriceTable:
        """``<home>/prices.json``, fetched once from :data:`OPENROUTER_MODELS_URL` when the home has none. A stale
        table is used; :meth:`estimate` warns about it."""

        path = prices_path(self.home)
        return PriceTable.load(path) if path.is_file() else fetch_prices(self.home)

    def _priced(self) -> PriceTable:
        """:attr:`prices`, warning once per budget (:class:`StalePricesWarning`, attributed to :meth:`estimate`'s
        caller) when the table is older than :data:`PRICES_MAX_AGE`."""

        table = self.prices
        if table.stale() and "stale" not in self._warned:
            self._warned.append("stale")
            age = (datetime.now(UTC) - table.fetched_at).days
            warnings.warn(
                f"{prices_path(self.home)} was fetched {age} days ago; estimates use these prices until it is refetched",
                StalePricesWarning,
                stacklevel=3,
            )
        return table

    def estimate(self, request: Any, target: Any) -> float:
        """The worst case ``request`` on ``target`` can cost, in USD, rounded to the microdollar.

        Input tokens are ``ceil(chars / CHARS_PER_TOKEN)`` over the prompt, the system text and prior turns; output
        tokens are the request's ``max_output_tokens`` (else the target's). The price is :attr:`prices` at
        ``target.model``. No positive ``max_output_tokens``, no table or no price for the model is
        :class:`PriceUnknownError`; nothing is guessed.
        """

        max_output = getattr(request, "max_output_tokens", None) or getattr(target, "max_output_tokens", None)
        if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output <= 0:
            raise PriceUnknownError(f"cannot estimate a call of {getattr(target, 'model', target)!r}: no max_output_tokens on the request or the target")
        price = self._priced().price(str(getattr(target, "model", target)))
        input_tokens = math.ceil(_request_chars(request) / CHARS_PER_TOKEN)
        return round(input_tokens * price.prompt + max_output * price.completion, 6)

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
