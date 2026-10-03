"""0110-024 P3: fill the function tag of titles the rules could not place, with a model.

The rules tagger (``posting_tags``) always sets a level; it leaves
``function`` empty when no family word is in the title. :class:`TagQueue`
asks a model for those, in batches of :data:`BATCH_SIZE` titles, through the
SAME adapter path the ranker uses (``model_rank._resolve``: the configured
target for the adapter kind, the test transport installed first, the CLI
adapters' lean/low-effort copies), and writes each answer with
``TagStore.set_model_function`` (source ``model``, the model id, the prompt
version). Level stays the rules' (the spike: a model's level is worse).

**What is sent.** One line per title: ``id | title | location``, the
posting's own title and location as its board listed them (public data).
Nothing else: no resume, profile or contact text, no company name, no
description. The prompt is the spike's ``tag-v1``
(``research/posting-index-spike/scripts/models.py``), unchanged.

**Order.** The DEMAND SET first: titles whose rules level is the level of a
role of an active profile (only those can ever match a tag query, see
``title_query``), with the model the project is configured to use
(``find-jobs.json`` ``default_model_target``). Then the BACKFILL: every
other title, oldest first, with the model ``tagging.tag_backfill_model``
names (:data:`BACKFILL_CONFIGURED`: the same one; ``haiku``; ``openai``).

**Settings** (read only; ``<home>/scout/<project_id>/settings.json``, beside
``sources.auto_refresh``)::

    {"schema_version": "scout-settings:1",
     "tagging": {"model_enabled": true, "backfill_enabled": false, "tag_backfill_model": "configured"}}

``model_enabled`` is ON by default and covers the demand set, which is small
(the spike: about 3,700 titles for one profile) and is what makes a search
better. ``backfill_enabled`` is OFF by default: the backfill is about 3,400
calls for titles no profile can reach yet, so the operator turns it on. A
settings file that cannot be read turns both off (a background job that
spends model calls does not guess). :data:`MODEL_TAGS_ENV` overrides
``model_enabled`` either way.

**Resumable.** A batch is written when its answer returns; nothing else is
kept. A process killed mid-call loses that one batch, and the next drain
asks for it again.

**Never in the way.** One drain makes at most ``max_batches`` calls, one at
a time, and holds no lock while a call runs: a search reads the tag store,
and an update writes it, as if no drain were running. Before every batch the
drain looks at the stop event and at the sources update snapshot: it stops
for a shutdown and yields to a live MANUAL update (the operator asked for
that one, at full speed). It does not yield to a background check
(0110-028): a check is paced, a model call asks no job board, and yielding
to every check left the demand set untagged for as long as checks ran.

**Invalid answers** are never stored. An answer that is not the asked JSON
array is retried once with the error fed back, then the batch is split once
into halves (the ranker's rule). An item with an unknown function code, or a
title the answer left out, is skipped; a title skipped :data:`MAX_TITLE_FAILURES`
times is parked until the process restarts, so one odd title cannot hold
the queue.

**A model that cannot be called** (no configured target, an adapter error)
backs the lane off: :data:`BACKOFF_SECONDS` doubling to
:data:`BACKOFF_MAX_SECONDS`. :meth:`TagQueue.status` carries the count, the
last error and when the next try is.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
import json
import logging
import os
from pathlib import Path
import sqlite3
import threading
from typing import Any

from ...adapters.port import ModelInvocationError
from ..call_metrics import KIND_TAG, CallMeter
from . import model_rank
from .posting_tags import FUNCTIONS, normalize_title
from .tag_store import TagStore
from .title_query import open_tag_store, tag_query_for_roles

PROMPT_VERSION = "tag-v1"
BATCH_SIZE = model_rank.DEFAULT_BATCH_SIZE
#: Model calls' worth of batches one drain may start (retries and halves are extra calls of the same batch).
DEFAULT_BATCHES_PER_TICK = 4
MAX_TITLE_FAILURES = 2
BACKOFF_SECONDS = 300.0
BACKOFF_MAX_SECONDS = 6 * 3600.0
#: How long the active profiles' roles are kept before they are read again (a kick reads them at once).
DEMAND_TTL_SECONDS = 300.0
#: How many waiting titles one pass over the company index looks up a location for.
LOOKUP_WINDOW = 2000

#: ``0``/``false``/``off``/``no`` turns model tagging off, ``1``/``true``/``on``/``yes`` on, whatever the setting says.
MODEL_TAGS_ENV = "GIGAI_SCOUT_MODEL_TAGS"

BACKFILL_CONFIGURED = "configured"
BACKFILL_HAIKU = "haiku"
#: Wired as a provider id only: the accuracy check against the 300-title sample is pending (0110-024c).
BACKFILL_OPENAI = "openai"
BACKFILL_MODELS = (BACKFILL_CONFIGURED, BACKFILL_HAIKU, BACKFILL_OPENAI)

LANE_DEMAND = "demand"
LANE_BACKFILL = "backfill"

STATE_DISABLED = "disabled"
STATE_IDLE = "idle"  # nothing waits for a model
STATE_DRAINED = "drained"  # this drain wrote at least one batch
STATE_YIELDED = "yielded"  # a sources update is live
STATE_STOPPED = "stopped"
STATE_BACKOFF = "backoff"
STATE_NO_STORE = "no_store"

SOURCE_DEFAULT = "default"
SOURCE_SETTING = "setting"
SOURCE_ENVIRONMENT = "environment"
SOURCE_UNREADABLE = "settings_unreadable"

_OFF = frozenset({"0", "false", "off", "no"})
_ON = frozenset({"1", "true", "on", "yes"})
_ROLE = "reviewer"
_HAIKU_MODEL = "haiku"
#: ``gigai scout install`` writes this in place of a role until the operator sets one: not a role.
_ROLE_PLACEHOLDER = "REPLACE_WITH_"
_MAX_ERROR = 200

_logger = logging.getLogger("gigai.scout.refresh")

# The spike's prompt, byte for byte (the accuracy tables were measured with it).
# It still asks for a level: the answer's level is read and thrown away.
PROMPT = """Tag each job posting below with its level and job function. Judge from the title (and the excerpt, when one is given).

level codes: I intern/co-op; J junior, entry, associate or assistant; M mid (no seniority word); S senior; T staff; P principal/distinguished; L lead (tech lead, team lead); G manager (including senior manager); D director (including senior/associate director); V vice president (VP, SVP, AVP); H "head of"; C chief / C-level.
fn codes: sw software engineering; ai AI/ML engineering and applied science; da data (analytics, data engineering, data science); pr product management; de design/creative; se security and IT; hw hardware, mechanical, electrical, manufacturing, civil and other non-software engineering; sa sales, business development, partnerships, account management; so solutions/sales engineering, technical consulting, professional services; ma marketing/communications; cu customer success/support; op operations, program/project management, strategy; fi finance/accounting; le legal/compliance; pe people/HR/recruiting; he healthcare/clinical; rs research/science (not AI); ot other.

Posting lines are: id | title | location{extra}

POSTINGS ({n}):
{lines}

Answer with ONLY a JSON array, no prose, no code fences, one object per posting, every id exactly once:
[{{"id":"s000","level":"D","fn":"sw"}}]"""

#: The prompt's function codes -> the store's families. ``ot`` (other) is ``None``: asked, and no family fits.
FN_CODES: dict[str, str | None] = {
    "sw": "software",
    "ai": "ai_ml",
    "da": "data",
    "pr": "product",
    "de": "design",
    "se": "security_it",
    "hw": "hardware",
    "sa": "sales",
    "so": "solutions",
    "ma": "marketing",
    "cu": "customer",
    "op": "operations",
    "fi": "finance",
    "le": "legal",
    "pe": "people",
    "he": "healthcare",
    "rs": "research",
    "ot": None,
}
assert {value for value in FN_CODES.values() if value is not None} == set(FUNCTIONS)


# ---------------------------------------------------------------------------
# The prompt and its answer
# ---------------------------------------------------------------------------


def _field(value: str | None) -> str:
    """One prompt field: a single line with no column separator in it."""

    return " ".join((value or "").replace("|", "/").split())


def tag_line(posting_id: str, title: str, location: str | None) -> str:
    return f"{posting_id} | {_field(title)} | {_field(location)}"


def render_tag_prompt(lines: Sequence[str], error: str | None = None) -> str:
    """The tag-v1 prompt for one batch of ``id | title | location`` lines."""

    text = PROMPT.format(n=len(lines), lines="\n".join(lines), extra="")
    if error:
        text += model_rank.RETRY.format(error=error[: model_rank._MAX_FED_BACK_ERROR])
    return text


class TagAnswerError(ValueError):
    """A model answer that is not the asked JSON array of ``{"id", "fn"}`` objects."""


@dataclass(frozen=True)
class TagAnswer:
    """What one answer is worth: the function per id, and why each other id was skipped."""

    functions: dict[str, str | None]
    rejected: dict[str, str]


def validate_tag_answer(text: str, ids: Sequence[str]) -> TagAnswer:
    """The functions of ``ids``, or :class:`TagAnswerError` when the answer is not usable at all.

    The whole answer is refused when it is not a JSON array of objects, names
    an id that was not asked, or names one twice. Inside a well-formed answer
    each item stands alone: a function code outside :data:`FN_CODES`, or an
    asked id with no item, skips that one title and keeps the rest.
    """

    try:
        data = model_rank._extract_array(text)
    except model_rank.RankAnswerError as exc:
        raise TagAnswerError(str(exc)) from None
    if not isinstance(data, list):
        raise TagAnswerError("top level is not an array")
    wanted = set(ids)
    functions: dict[str, str | None] = {}
    rejected: dict[str, str] = {}
    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise TagAnswerError(f"item {index} is not an object")
        posting_id = item.get("id")
        if not isinstance(posting_id, str) or posting_id not in wanted:
            raise TagAnswerError(f"item {index} has unknown id {posting_id!r}")
        if posting_id in functions or posting_id in rejected:
            raise TagAnswerError(f"id {posting_id} appears twice")
        code = item.get("fn")
        code = code.strip().lower() if isinstance(code, str) else code
        if not isinstance(code, str) or code not in FN_CODES:
            rejected[posting_id] = f"unknown fn code {str(code)[:20]!r}"
            continue
        functions[posting_id] = FN_CODES[code]
    for posting_id in ids:
        if posting_id not in functions and posting_id not in rejected:
            rejected[posting_id] = "left out of the answer"
    if not functions:
        raise TagAnswerError(f"no usable item for {len(ids)} ids (e.g. {next(iter(rejected.values()))})")
    return TagAnswer(functions, rejected)


# ---------------------------------------------------------------------------
# The settings (reader only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaggingSetting:
    """Whether a model may tag, whether the backfill may run, and with which model."""

    model_enabled: bool
    backfill_enabled: bool
    backfill_model: str
    source: str

    def to_json(self) -> dict[str, object]:
        return {
            "model_enabled": self.model_enabled,
            "backfill_enabled": self.backfill_enabled,
            "tag_backfill_model": self.backfill_model,
            "source": self.source,
        }


_SETTING_OFF = TaggingSetting(False, False, BACKFILL_CONFIGURED, SOURCE_UNREADABLE)


def tagging_setting(
    home_root: Path,
    target: Path | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> TaggingSetting:
    """The ``tagging`` block of the project's settings file: the environment, the file, then the defaults."""

    from .refresh_tick import SETTINGS_SCHEMA, settings_path

    def forced(setting: TaggingSetting) -> TaggingSetting:
        raw = ((os.environ if environ is None else environ).get(MODEL_TAGS_ENV) or "").strip().lower()
        if raw in _OFF:
            return replace(setting, model_enabled=False, source=SOURCE_ENVIRONMENT)
        if raw in _ON:
            return replace(setting, model_enabled=True, source=SOURCE_ENVIRONMENT)
        return setting

    default = TaggingSetting(True, False, BACKFILL_CONFIGURED, SOURCE_DEFAULT)
    if target is None:
        return forced(default)
    try:
        path = settings_path(home_root, target)
    except Exception:  # noqa: BLE001 - no bound project yet: there is no settings file to read
        return forced(default)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return forced(default)
    except (OSError, ValueError):
        return forced(_SETTING_OFF)
    if not isinstance(payload, dict) or payload.get("schema_version") != SETTINGS_SCHEMA:
        return forced(_SETTING_OFF)
    block = payload.get("tagging", {})
    if not isinstance(block, dict):
        return forced(_SETTING_OFF)
    model_enabled = block.get("model_enabled", True)
    backfill_enabled = block.get("backfill_enabled", False)
    backfill_model = block.get("tag_backfill_model", BACKFILL_CONFIGURED)
    if type(model_enabled) is not bool or type(backfill_enabled) is not bool or backfill_model not in BACKFILL_MODELS:
        return forced(_SETTING_OFF)
    return forced(TaggingSetting(model_enabled, backfill_enabled, backfill_model, SOURCE_SETTING if block else SOURCE_DEFAULT))


# ---------------------------------------------------------------------------
# Who is asking: the active profiles' roles and the configured model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Demand:
    """The roles the active profiles search for, and the adapter kind the project runs with."""

    roles: tuple[str, ...]
    model_target: str | None

    @property
    def levels(self) -> tuple[str, ...]:
        """The rules levels a tag query for these roles can match (a role with no function has none)."""

        return tuple(sorted({level for level, _function in tag_query_for_roles(self.roles).pairs}))


def load_demand(home_root: Path, target: Path) -> Demand:
    """Every active profile's titles (else the shared ``find-jobs.json`` roles) and its ``default_model_target``.

    Read only: no profile is created or migrated here. Anything that cannot
    be read leaves that part empty; with no model target nothing is asked.
    The install template's placeholder role is not a role, so a project
    nobody has set up yet has no demand set and makes no model call.
    """

    from ...canonical import parse_json_bytes
    from ...workpad import resolve_workpad
    from .. import profile_records
    from .contracts import FindJobsConfig

    roles: list[str] = []
    model_target: str | None = None
    path = Path(target) / "find-jobs.json"
    try:
        if not path.is_symlink() and path.is_file():
            config = FindJobsConfig.from_json(parse_json_bytes(path.read_bytes()))
            roles.extend(config.roles)
            model_target = str(config.default_model_target.value)
    except (OSError, ValueError):
        pass
    try:
        resolved = resolve_workpad(home_root=Path(home_root), requested_target=Path(target), gig_id=None, allow_semantic_state=True)
        for profile in profile_records.list_profiles(resolved):
            if profile.state == "active":
                roles.extend(profile.titles)
    except Exception:  # noqa: BLE001 - the shared roles are a fine fallback
        pass
    real = (role for role in roles if isinstance(role, str) and role.strip() and not role.startswith(_ROLE_PLACEHOLDER))
    return Demand(tuple(dict.fromkeys(real)), model_target)


# ---------------------------------------------------------------------------
# The title and location of a title key
# ---------------------------------------------------------------------------


class IndexTitles:
    """``title key -> (a posting's own title, its location)``, read from the company index.

    The tag store keeps only the normalized title. The model was measured on
    the listed title and location, so both are looked up in the company
    files: one pass over the index answers a whole window of waiting titles
    and is kept until the queue has moved past it.
    """

    def __init__(self, home_root: Path) -> None:
        self._home = Path(home_root)
        self._found: dict[str, tuple[str, str]] = {}
        self._looked_for: set[str] = set()

    def lookup(self, keys: Sequence[str], *, should_stop: Callable[[], bool] = lambda: False) -> dict[str, tuple[str, str]]:
        """What the index lists for ``keys``. A key no live posting carries any more is left out."""

        missing = [key for key in keys if key not in self._looked_for]
        if missing:
            if len(self._looked_for) > 4 * LOOKUP_WINDOW:
                self._found.clear()
                self._looked_for.clear()
                missing = list(keys)
            self._scan(set(missing), should_stop)
        return {key: self._found[key] for key in keys if key in self._found}

    def _scan(self, wanted: set[str], should_stop: Callable[[], bool]) -> None:
        from .company_index import CompanyIndex

        index = CompanyIndex.for_home(self._home)
        left = set(wanted)
        for ats, slug in index.keys():
            if not left:
                break
            if should_stop():
                return  # not marked as looked for: the next drain scans again
            try:
                entry = index.read(ats, slug)
            except Exception:  # noqa: BLE001 - one unreadable company file does not stop the lookup
                continue
            if entry is None:
                continue
            for posting in entry.live():
                key = normalize_title(posting.title) if isinstance(posting.title, str) else ""
                if key in left:
                    self._found[key] = (posting.title, posting.location or "")
                    left.discard(key)
        self._looked_for.update(wanted)


# ---------------------------------------------------------------------------
# The queue
# ---------------------------------------------------------------------------


@dataclass
class LaneStatus:
    """One lane's counters since the process started, and its backoff."""

    model: str | None = None
    batches: int = 0
    calls: int = 0
    tagged: int = 0
    rejected: int = 0
    failures: int = 0
    consecutive_failures: int = 0
    last_error: str | None = None
    last_error_at: datetime | None = None
    retry_after: datetime | None = None

    def to_json(self) -> dict[str, object]:
        def stamp(moment: datetime | None) -> str | None:
            return None if moment is None else moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        return {
            "model": self.model,
            "batches": self.batches,
            "calls": self.calls,
            "tagged": self.tagged,
            "rejected": self.rejected,
            "failures": self.failures,
            "consecutive_failures": self.consecutive_failures,
            "last_error": self.last_error,
            "last_error_at": stamp(self.last_error_at),
            "retry_after": stamp(self.retry_after),
        }


@dataclass(frozen=True)
class DrainResult:
    """What one drain did."""

    state: str
    batches: int = 0
    tagged: int = 0
    rejected: int = 0
    calls: int = 0

    def to_json(self) -> dict[str, object]:
        return {"state": self.state, "batches": self.batches, "tagged": self.tagged, "rejected": self.rejected, "calls": self.calls}


class _ModelUnavailable(Exception):
    """The lane's model cannot be called now: back off."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class _Caller:
    """One lane's resolved model, for the length of one drain."""

    resolved: Any
    adapter_kind: str
    model_override: str | None
    #: 0.1.10.7 E: every call of the drain is recorded in the project's metrics.
    meter: CallMeter

    @property
    def model_id(self) -> str:
        return f"{self.adapter_kind}:{self.model_override or self.resolved.model}"

    def invoke(self, prompt: str, *, items: int = 1) -> Any:
        request = self.resolved.binding.request(role=_ROLE, prompt=prompt)
        if self.model_override is not None:
            request = replace(request, model=self.model_override)
        if self.resolved.effort_applied is not None:
            request = replace(request, reasoning_effort=self.resolved.effort_applied)
        return self.meter.invoke(self.resolved.port, request, items=items)

    def close(self) -> None:
        close = getattr(self.resolved.binding, "close", None)
        if callable(close):
            close()


@dataclass
class _Asked:
    """One batch's outcome: the function per title key, the skip reason per title key, the calls made."""

    functions: dict[str, str | None] = field(default_factory=dict)
    rejected: dict[str, str] = field(default_factory=dict)
    calls: int = 0
    #: Set when the model could not be called (or refused the caller): a lane failure, not the titles'.
    unavailable: str | None = None


@dataclass
class _Totals:
    batches: int = 0
    tagged: int = 0
    rejected: int = 0
    calls: int = 0
    states: list[str] = field(default_factory=list)


def backfill_adapter(setting_value: str, configured: str | None) -> tuple[str | None, str | None]:
    """``(adapter kind, model override)`` for a ``tag_backfill_model`` value."""

    if setting_value == BACKFILL_HAIKU:
        return "claude_cli", _HAIKU_MODEL
    if setting_value == BACKFILL_OPENAI:
        return "openai_api", None  # the operator's configured openai_api target and its credential reference
    return configured, None


class TagQueue:
    """The model tag queue of one project. :meth:`drain` is one bounded pass, on the calling thread.

    ``clock``, ``demand_loader``, ``titles``, ``live_update``, ``config`` and
    ``setting`` are seams for tests; production passes none of them.
    """

    def __init__(
        self,
        *,
        home_root: Path,
        target: Path,
        clock: Callable[[], datetime] | None = None,
        demand_loader: Callable[[Path, Path], Demand] = load_demand,
        titles: Any | None = None,
        live_update: Callable[[], bool] | None = None,
        config: Any | None = None,
        environ: Mapping[str, str] | None = None,
        setting: Callable[[], TaggingSetting] | None = None,
        batch_size: int = BATCH_SIZE,
        max_batches: int = DEFAULT_BATCHES_PER_TICK,
        logger: logging.Logger | None = None,
    ) -> None:
        if batch_size < 1 or max_batches < 0:
            raise ValueError("batch_size must be positive; max_batches must not be negative")
        self.home_root = Path(home_root)
        self.target = Path(target)
        self.batch_size = int(batch_size)
        self.max_batches = int(max_batches)
        self._clock = clock if clock is not None else (lambda: datetime.now(timezone.utc))
        self._demand_loader = demand_loader
        self._titles = titles if titles is not None else IndexTitles(self.home_root)
        self._live_update = live_update if live_update is not None else self._update_is_live
        self._config = config
        self._environ = environ
        self._setting = setting
        self._logger = logger if logger is not None else _logger
        self._lock = threading.Lock()  # one drain at a time; status reads take no lock
        self._lanes = {LANE_DEMAND: LaneStatus(), LANE_BACKFILL: LaneStatus()}
        self._failed: dict[str, int] = {}
        self._demand: Demand | None = None
        self._demand_at: datetime | None = None
        self._last: DrainResult | None = None
        self._last_at: datetime | None = None

    # -- what the outside sees ---------------------------------------------

    def setting(self) -> TaggingSetting:
        if self._setting is not None:
            return self._setting()
        return tagging_setting(self.home_root, self.target, environ=self._environ)

    def kick(self, *, reset_backoff: bool = False) -> None:
        """Forget the cached roles (a profile changed); ``reset_backoff`` also lets a failed lane try now."""

        self._demand = None
        self._demand_at = None
        if reset_backoff:
            for lane in self._lanes.values():
                lane.retry_after = None
                lane.consecutive_failures = 0

    @property
    def parked(self) -> int:
        return sum(count >= MAX_TITLE_FAILURES for count in self._failed.values())

    def status(self) -> dict[str, object]:
        """The queue's block for the sources status (no store read, no model call)."""

        last = self._last
        return {
            "setting": self.setting().to_json(),
            "prompt_version": PROMPT_VERSION,
            "batch_size": self.batch_size,
            "batches_per_tick": self.max_batches,
            "state": last.state if last is not None else None,
            "last_drain_at": None if self._last_at is None else self._last_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "last_drain": None if last is None else last.to_json(),
            "parked": self.parked,
            LANE_DEMAND: self._lanes[LANE_DEMAND].to_json(),
            LANE_BACKFILL: self._lanes[LANE_BACKFILL].to_json(),
        }

    # -- one pass ----------------------------------------------------------

    def drain(self, *, stop: threading.Event | None = None, max_batches: int | None = None) -> DrainResult:
        """Ask for at most ``max_batches`` batches: the demand set first, then (when enabled) the backfill."""

        with self._lock:
            result = self._drain(stop if stop is not None else threading.Event(), self.max_batches if max_batches is None else max_batches)
            self._last, self._last_at = result, self._clock()
            return result

    def _update_is_live(self) -> bool:
        """A manual update is running (the queue waits for it); a background check is not waited for."""

        from .company_index import CompanyIndex
        from .sources_update import TRIGGER_AUTO, snapshot_is_live

        snapshot = CompanyIndex.for_home(self.home_root).read_update_summary()
        return snapshot_is_live(snapshot, now=self._clock()) and (snapshot or {}).get("trigger") != TRIGGER_AUTO

    def demand_levels(self) -> tuple[str, ...]:
        """The rules levels of the demand set, from the roles this queue last read (read again when they are stale).

        For the status: which waiting titles the demand lane will tag. Empty
        when the roles cannot be read or name no level.
        """

        try:
            return self._current_demand(self._clock()).levels
        except Exception:  # noqa: BLE001 - a status read never fails on the roles
            return ()

    def _current_demand(self, now: datetime) -> Demand:
        fresh = self._demand is not None and self._demand_at is not None and (now - self._demand_at).total_seconds() < DEMAND_TTL_SECONDS
        if not fresh:
            self._demand, self._demand_at = self._demand_loader(self.home_root, self.target), now
        assert self._demand is not None
        return self._demand

    def _waiting(self, store: TagStore, limit: int, *, levels: Sequence[str], demand: bool) -> list[str]:
        """Up to ``limit`` titles of one lane still waiting for a model, oldest first, parked ones left out."""

        if demand and not levels:
            return []
        parked = {key for key, count in self._failed.items() if count >= MAX_TITLE_FAILURES}
        listed = store.titles_awaiting_model(
            limit + len(parked),
            levels=levels if demand else None,
            exclude_levels=None if demand else levels,
        )
        return [key for key in listed if key not in parked][:limit]

    def _drain(self, stop: threading.Event, budget: int) -> DrainResult:
        setting = self.setting()
        if not setting.model_enabled:
            return DrainResult(STATE_DISABLED)
        if stop.is_set():
            return DrainResult(STATE_STOPPED)
        if self._live_update():
            # Before the store is even opened: an update may be creating it right now.
            return DrainResult(STATE_YIELDED)
        store = open_tag_store(self.home_root)
        if store is None:
            return DrainResult(STATE_NO_STORE)
        totals = _Totals()
        try:
            if budget < 1 or store.count_awaiting_model() == 0:
                return DrainResult(STATE_IDLE)
            now = self._clock()
            demand = self._current_demand(now)
            levels = demand.levels
            lanes: list[tuple[str, str | None, str | None]] = [(LANE_DEMAND, demand.model_target, None)]
            if setting.backfill_enabled:
                lanes.append((LANE_BACKFILL, *backfill_adapter(setting.backfill_model, demand.model_target)))
            for name, adapter_kind, model_override in lanes:
                left = budget - totals.batches
                if left < 1:
                    break
                waiting = self._waiting(store, left * self.batch_size, levels=levels, demand=name == LANE_DEMAND)
                if not waiting:
                    continue
                state = self._drain_lane(store, name, adapter_kind, model_override, waiting, stop, totals)
                totals.states.append(state)
                if state in (STATE_STOPPED, STATE_YIELDED):
                    break
        except (sqlite3.Error, OSError) as exc:  # the tag store is a cache: an unreadable one is not this pass's to repair
            self._logger.warning("model tags: the tag store could not be used (%s)", type(exc).__name__)
            totals.states.append(STATE_NO_STORE)
        finally:
            store.close()
        for state in (STATE_STOPPED, STATE_YIELDED):
            if state in totals.states:
                return DrainResult(state, totals.batches, totals.tagged, totals.rejected, totals.calls)
        if totals.batches:
            state = STATE_DRAINED
        elif totals.states:
            state = totals.states[0]
        else:
            state = STATE_IDLE
        return DrainResult(state, totals.batches, totals.tagged, totals.rejected, totals.calls)

    def _resolve(self, adapter_kind: str | None, model_override: str | None) -> _Caller:
        if adapter_kind is None:
            raise _ModelUnavailable("no model target is configured for this project")
        try:
            from ...config import load_config

            active = self._config if self._config is not None else load_config(self.home_root)
            resolved = model_rank._resolve(active, adapter_kind, home_root=self.home_root)
        # The same errors the ranker skips a pass for (model_rank.rank_postings).
        except (ValueError, KeyError, OSError, ModelInvocationError) as exc:
            raise _ModelUnavailable(f"model_target_unavailable: {str(exc)[:_MAX_ERROR] or type(exc).__name__}") from None
        meter = CallMeter(
            KIND_TAG, adapter_kind, self.home_root, self.target, target_name=getattr(resolved, "configured_target", None)
        )
        return _Caller(resolved, adapter_kind, model_override, meter)

    def _failed_lane(self, lane: LaneStatus, error: str) -> None:
        now = self._clock()
        lane.failures += 1
        lane.consecutive_failures += 1
        lane.last_error, lane.last_error_at = error[:_MAX_ERROR], now
        wait = min(BACKOFF_SECONDS * 2 ** (lane.consecutive_failures - 1), BACKOFF_MAX_SECONDS)
        lane.retry_after = now + timedelta(seconds=wait)
        self._logger.warning("model tags (%s): %s; next try in %.0f s", lane.model or "no model", lane.last_error, wait)

    def _drain_lane(
        self,
        store: TagStore,
        name: str,
        adapter_kind: str | None,
        model_override: str | None,
        waiting: Sequence[str],
        stop: threading.Event,
        totals: _Totals,
    ) -> str:
        lane = self._lanes[name]
        if lane.retry_after is not None and self._clock() < lane.retry_after:
            return STATE_BACKOFF
        if stop.is_set():
            return STATE_STOPPED
        if self._live_update():
            return STATE_YIELDED
        try:
            caller = self._resolve(adapter_kind, model_override)
        except _ModelUnavailable as exc:
            self._failed_lane(lane, exc.reason)
            return STATE_BACKOFF
        lane.model = caller.model_id
        try:
            listed = self._titles.lookup(waiting, should_stop=stop.is_set)
            for start in range(0, len(waiting), self.batch_size):
                if stop.is_set():
                    return STATE_STOPPED
                if self._live_update():
                    return STATE_YIELDED
                keys = waiting[start : start + self.batch_size]
                asked = self._ask(caller, keys, listed)
                # The batch is durable from here: each title is one committed row.
                written = 0
                for key, function in asked.functions.items():
                    if store.set_model_function(key, function, model=caller.model_id, prompt_version=PROMPT_VERSION):
                        written += 1
                    self._failed.pop(key, None)
                for key in asked.rejected:
                    self._failed[key] = self._failed.get(key, 0) + 1
                lane.calls += asked.calls
                lane.tagged += written
                lane.rejected += len(asked.rejected)
                totals.calls += asked.calls
                totals.tagged += written
                totals.rejected += len(asked.rejected)
                if asked.functions:
                    lane.batches += 1
                    totals.batches += 1
                if asked.unavailable is not None or not asked.functions:
                    # The model could not be called, or nothing it said was usable: stop asking for a while.
                    self._failed_lane(lane, asked.unavailable or f"no usable answer: {next(iter(asked.rejected.values()), 'invalid')}")
                    return STATE_BACKOFF
                lane.consecutive_failures, lane.retry_after = 0, None
            return STATE_DRAINED
        finally:
            caller.close()

    def _ask(self, caller: _Caller, keys: Sequence[str], listed: Mapping[str, tuple[str, str]]) -> "_Asked":
        """One batch: an attempt and one retry, then ONE split into halves (one attempt each).

        A call that fails in transport is an attempt like any other (the
        ranker's rule), except an authentication/denied error, which ends the
        batch at once. When no call of the batch got an answer at all the
        model is unavailable; titles are only held against (``rejected``) for
        answers that did come back.
        """

        asked = _Asked()
        answered = False

        def attempt(part: Sequence[str], tries: int) -> str | None:
            """Ask for ``part``; ``None`` when an answer was usable, else the last error."""

            nonlocal answered
            ids = [f"s{number:03d}" for number in range(len(part))]
            lines = [tag_line(pid, *listed.get(key, (key, ""))) for pid, key in zip(ids, part)]
            error: str | None = None
            for _ in range(tries):
                asked.calls += 1
                try:
                    result = caller.invoke(render_tag_prompt(lines, error), items=len(part))
                except (ModelInvocationError, OSError, TimeoutError) as exc:
                    code = getattr(exc, "code", "")
                    error = f"model call failed ({code or type(exc).__name__}): {str(exc)[:_MAX_ERROR]}"
                    if code in model_rank._ABORT_CODES:
                        raise _ModelUnavailable(f"model_unavailable: {error}") from None
                    continue
                answered = True
                try:
                    answer = validate_tag_answer(result.output_text, ids)
                except TagAnswerError as exc:
                    error = str(exc)
                    caller.meter.invalid_output()
                    continue
                by_id = dict(zip(ids, part))
                asked.functions.update({by_id[pid]: function for pid, function in answer.functions.items()})
                asked.rejected.update({by_id[pid]: reason for pid, reason in answer.rejected.items()})
                return None
            return error or "model call failed"

        try:
            error = attempt(keys, 2)
            if error is None:
                return asked
            if not answered:
                asked.unavailable = error
                return asked
            if len(keys) < 2:
                asked.rejected[keys[0]] = f"invalid: {error}"
                return asked
            middle = len(keys) // 2
            for half in (keys[:middle], keys[middle:]):
                half_error = attempt(half, 1)
                if half_error is not None:
                    asked.rejected.update({key: f"invalid: {half_error}" for key in half})
        except _ModelUnavailable as exc:
            asked.unavailable = exc.reason
        return asked


__all__ = [
    "BACKFILL_CONFIGURED",
    "BACKFILL_HAIKU",
    "BACKFILL_MODELS",
    "BACKFILL_OPENAI",
    "BACKOFF_MAX_SECONDS",
    "BACKOFF_SECONDS",
    "BATCH_SIZE",
    "DEFAULT_BATCHES_PER_TICK",
    "FN_CODES",
    "LANE_BACKFILL",
    "LANE_DEMAND",
    "MAX_TITLE_FAILURES",
    "MODEL_TAGS_ENV",
    "PROMPT",
    "PROMPT_VERSION",
    "Demand",
    "DrainResult",
    "IndexTitles",
    "TagAnswer",
    "TagAnswerError",
    "TagQueue",
    "TaggingSetting",
    "backfill_adapter",
    "load_demand",
    "render_tag_prompt",
    "tag_line",
    "validate_tag_answer",
    "tagging_setting",
]
