"""Shared contracts for the Scout ``find-jobs`` graph.

This module is deliberately a small boundary module.  It contains immutable
JSON DTOs and typing-only seams; it does not fetch a provider, invoke a model,
write a workpad, or start the local API.  The DTOs are the normative wave-1a
freeze consumed by the acquisition, assessment, runner, and presentation
packets.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
import re
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Callable,
    Iterable,
    Mapping,
    NamedTuple,
    Protocol,
)
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ...canonical import (
    EntityPrefix,
    canonical_json_digest,
    digest_imported_bytes,
    validate_entity_id,
)

if TYPE_CHECKING:  # pragma: no cover - imported only by static type checkers
    from pathlib import Path

    import httpx

    # P6: type-only, to avoid a real import cycle with jev_contracts.py
    # (which imports from this module); see AcquireOutput.rank_scores.
    from .jev_contracts import RankScore


class FindJobsContractError(ValueError):
    """A serialized find-jobs contract failed closed.

    All DTO parsing failures intentionally use this one error type.  Callers
    can branch on the stable ``code`` while keeping the message suitable for a
    local operator and free of provider response bodies or credentials.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ModelTarget(StrEnum):
    OLLAMA_LOCAL = "ollama_local"
    CODEX_CLI = "codex_cli"
    OPENROUTER_API = "openrouter_api"


class SourceKind(StrEnum):
    EXA = "exa"
    ATS = "ats"
    HIRINGCAFE = "hiringcafe"


class ATSProvider(StrEnum):
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


class RowOutcome(StrEnum):
    NEW = "new"
    EDITED = "edited"
    UNCHANGED = "unchanged"
    DUPLICATE = "duplicate"
    FAILED = "failed"


class SelectionRule(StrEnum):
    """The only D6 candidate-selection rule admitted by this graph."""

    NEW_OR_EDITED_ROLE_MATCH = "new_or_edited_role_match"


class SelectionReasonCode(StrEnum):
    NEW = "new"
    EDITED = "edited"


class ProgressStatus(StrEnum):
    """Acquire progress is separate from row outcome (not a row ``pending``)."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"


class NodeStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_GATE = "waiting_for_gate"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class GoalStatus(StrEnum):
    """The exact status enum accepted by run-details ``goal_details``."""

    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    WAITING_FOR_GATE = "waiting_for_gate"
    VERIFYING = "verifying"
    COMPLETE = "complete"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class AggregateStatus(StrEnum):
    """Run/presentation aggregate status; unlike goals it includes interrupted."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


class MatrixStatus(StrEnum):
    MET = "met"
    UNMET = "unmet"
    UNCLEAR = "unclear"
    # P2 (v0.1.9): the S29 r1 assess prompt only ever emits met/unmet/unclear.
    # PARTIAL/GAP are kept solely so an OLD serialized assessment result (the
    # pre-P2 prompt's met/partial/gap vocabulary) still parses; the P2
    # normalizer maps a new model's partial->unclear and gap->unmet before
    # validation, so no live code path emits these two anymore (operator
    # decision #10, plan section 8).
    PARTIAL = "partial"
    GAP = "gap"


class Verdict(StrEnum):
    """P2 (v0.1.9): the workflow-state verdict from the S29 r1 assess prompt."""

    MATCHED_ABOVE_THRESHOLD = "matched_above_threshold"
    PENDING_USER_ANSWERS = "pending_user_answers"
    NOT_A_MATCH = "not_a_match"


class RequirementClass(StrEnum):
    """P2 (v0.1.9): per-matrix-row classification the S29 r1 prompt assigns."""

    HARD = "hard"
    ASKABLE = "askable"
    NICE_TO_HAVE = "nice_to_have"


class SponsorshipStatus(StrEnum):
    """Visa sponsorship read for one posting; C0 adds this for U12/U25.

    ``PostingRow.sponsorship`` is derived from the posting text at acquire
    time; ``AssessmentResult.sponsorship`` is the model's read at assess
    time.  Both are optional and default to ``None`` (unknown / not derived)
    so old serialized rows without this field keep parsing.
    """

    OFFERED = "offered"
    NOT_OFFERED = "not_offered"
    UNKNOWN = "unknown"


class NotAssessedReason(StrEnum):
    UNCHANGED = "unchanged"
    DUPLICATE = "duplicate"
    FAILED = "failed"
    OVER_CAP = "over_cap"
    ROLE_MISMATCH = "role_mismatch"
    NO_RESUME = "no_resume"
    MODEL_UNAVAILABLE = "model_unavailable"
    MODEL_DENIED = "model_denied"
    LOCATION_MISMATCH = "location_mismatch"
    SPONSORSHIP_EXCLUDED = "sponsorship_excluded"
    MODEL_OUTPUT_INVALID = "model_output_invalid"
    # 0.1.8.1 r1 (B1 coordinator review): a finer-grained sub-case of
    # LOCATION_MISMATCH, used only for AcquireOutput.dropped_counts'
    # per-reason auditability -- a location that resolves to *only* region
    # tokens ("AMER"/"EMEA"/"APAC"/"LATAM"/"Remote - Americas") rather than
    # a recognized-but-wrong country. `exclusion_reason` itself keeps
    # returning LOCATION_MISMATCH for this case (its existing, stable
    # contract that proposal_execution.py's not-assessed labeling relies
    # on); only market_acquisition.py's drop-count bucketing distinguishes
    # the two, so a coordinator/operator reading dropped_counts can tell
    # "wrong country" apart from "region label, no country at all" without
    # changing exclusion_reason's public return value.
    REGION_ONLY = "region_only"
    # Q1 (v0.1.9, SCOPE-ADD-2): the posting's own `published_at` is older
    # than the rolling window (`FindJobsConfig.max_age_days`, or the fixed
    # `published_after` when set) -- see `filters.published_cutoff`. Applied
    # to EVERY source (Exa already asked for `startPublishedDate`; the ATS
    # boards never had any date filter, which is how a 2026-08-11 Kong
    # posting could still slip through or be missed unpredictably). A row
    # with NO `published_at` at all is never given this reason (kept).
    PUBLISHED_TOO_OLD = "published_too_old"


class _Contract:
    """Common canonical serialization behavior for every frozen DTO."""

    schema_version: ClassVar[str]

    def digest(self) -> str:
        return canonical_json_digest(self.to_json())  # type: ignore[attr-defined]


_DIGEST = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_URL_TRACKING_KEYS = frozenset(
    {
        "fbclid",
        "gclid",
        "gh_src",
        "lever-source",
        "ref",
        "source",
    }
)


def _fail(code: str, message: str) -> None:
    raise FindJobsContractError(code, message)


def _object(value: object, keys: Iterable[str], name: str) -> dict[str, object]:
    if type(value) is not dict:
        _fail("wrong_type", f"{name} must be an object")
    result = value
    expected = frozenset(keys)
    unknown = set(result) - expected
    if unknown:
        _fail("unknown_key", f"{name} contains unknown key(s): {sorted(unknown)}")
    missing = expected - set(result)
    if missing:
        _fail("missing_key", f"{name} is missing key(s): {sorted(missing)}")
    return result


def _object_with_optional(
    value: object, required: Iterable[str], optional: Iterable[str], name: str
) -> dict[str, object]:
    """Like ``_object`` but ``optional`` keys may be absent entirely.

    Used only for wave-1a-frozen DTOs that gained new optional fields after
    their first release (C0, v0.1.8.1): an old serialized payload that never
    had the key must still parse.  Keys outside ``required | optional`` are
    still rejected, so the object stays closed against typos and drift.
    """

    if type(value) is not dict:
        _fail("wrong_type", f"{name} must be an object")
    result = value
    required_set = frozenset(required)
    optional_set = frozenset(optional)
    expected = required_set | optional_set
    unknown = set(result) - expected
    if unknown:
        _fail("unknown_key", f"{name} contains unknown key(s): {sorted(unknown)}")
    missing = required_set - set(result)
    if missing:
        _fail("missing_key", f"{name} is missing key(s): {sorted(missing)}")
    return result


def _string(value: object, name: str, *, nonempty: bool = True) -> str:
    if type(value) is not str or (nonempty and not value):
        _fail("wrong_type", f"{name} must be a non-empty string")
    return value


def _optional_string(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _string(value, name)


def _bool(value: object, name: str) -> bool:
    if type(value) is not bool:
        _fail("wrong_type", f"{name} must be a boolean")
    return value


def _integer(value: object, name: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if type(value) is not int:
        _fail("wrong_type", f"{name} must be an integer")
    if minimum is not None and value < minimum:
        _fail("invalid_value", f"{name} is below its minimum")
    if maximum is not None and value > maximum:
        _fail("invalid_value", f"{name} is above its maximum")
    return value


def _enum(value: object, enum_type: type[StrEnum], name: str) -> StrEnum:
    if type(value) is not str:
        _fail("wrong_type", f"{name} must be a string enum value")
    try:
        return enum_type(value)
    except ValueError:
        _fail("bad_enum", f"{name} has an unsupported enum value")
        raise AssertionError("unreachable")


def _digest_value(value: object, name: str) -> str:
    value = _string(value, name)
    if not _DIGEST.fullmatch(value):
        _fail("invalid_value", f"{name} must be a sha256 digest")
    return value


def _optional_digest(value: object, name: str) -> str | None:
    if value is None:
        return None
    return _digest_value(value, name)


def _datetime_string(value: object, name: str, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    result = _string(value, name)
    try:
        parsed = datetime.fromisoformat(result.replace("Z", "+00:00"))
    except ValueError as exc:
        _fail("invalid_value", f"{name} must be an ISO-8601 date-time")
        raise AssertionError("unreachable") from exc
    if parsed.tzinfo is None:
        _fail("invalid_value", f"{name} must include a timezone")
    return result


def _strings(value: object, name: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if type(value) is not list:
        _fail("wrong_type", f"{name} must be an array")
    result = tuple(_string(item, f"{name}[{index}]") for index, item in enumerate(value))
    if not allow_empty and not result:
        _fail("invalid_value", f"{name} must not be empty")
    return result


_COUNTRY_CODE = re.compile(r"\A[A-Z]{2}\Z")


def _country_codes(value: object, name: str) -> tuple[str, ...]:
    """ISO-3166-1 alpha-2 codes for ``FindJobsConfig.countries`` (U19)."""

    codes = _strings(value, name, allow_empty=True)
    for index, code in enumerate(codes):
        if not _COUNTRY_CODE.fullmatch(code):
            _fail("invalid_value", f"{name}[{index}] must be an ISO-3166 alpha-2 code")
    return codes


def _enum_list(value: object, enum_type: type[StrEnum], name: str) -> tuple[StrEnum, ...]:
    if type(value) is not list:
        _fail("wrong_type", f"{name} must be an array")
    return tuple(_enum(item, enum_type, f"{name}[{index}]") for index, item in enumerate(value))


def _json_enum(value: object) -> str:
    return value.value if isinstance(value, StrEnum) else _string(value, "enum")


def _json_strings(value: Iterable[str]) -> list[str]:
    return list(value)


def _zero_usage() -> dict[str, object]:
    return {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost": None,
        "currency": None,
        "cost_status": "not_applicable",
    }


@dataclass(frozen=True)
class SourceToggles(_Contract):
    """D9 source switches; HiringCafe is present but off for M1."""

    schema_version: ClassVar[str] = "find-jobs-source-toggles:1"
    exa: bool
    ats: bool
    hiringcafe: bool = False

    def to_json(self) -> dict[str, object]:
        return {"exa": self.exa, "ats": self.ats, "hiringcafe": self.hiringcafe}

    @classmethod
    def from_json(cls, obj: object) -> "SourceToggles":
        value = _object(obj, ("exa", "ats", "hiringcafe"), "source_toggles")
        return cls(_bool(value["exa"], "source_toggles.exa"), _bool(value["ats"], "source_toggles.ats"), _bool(value["hiringcafe"], "source_toggles.hiringcafe"))


# Q1 (v0.1.9): the rolling publication window used when a config sets
# neither `max_age_days` nor a fixed `published_after`. 60 days is the
# SCOPE-ADD-2 decision ("published_after becomes a rolling max_age_days,
# default 60"). `filters.published_cutoff` is the ONE place that turns this
# (or the config's own values) into an actual earliest-date at run time.
DEFAULT_MAX_AGE_DAYS = 60
MAX_AGE_DAYS_MAXIMUM = 365


@dataclass(frozen=True)
class FindJobsConfig(_Contract):
    """Operator-authored ``<target_root>/find-jobs.json`` snapshot.

    Q1 (v0.1.9): ``max_age_days`` is the rolling publication window, an
    additive optional key (omitted from ``to_json`` at its ``None`` default
    so an existing file's digest is unchanged). How it combines with the
    older fixed ``published_after`` -- exactly one rule, implemented once in
    ``filters.published_cutoff``:

    * ``published_after`` set (a fixed ISO date or date-time) -> that date
      is the cutoff, whatever ``max_age_days`` says (a fixed date still wins).
    * else ``max_age_days`` set -> ``now - max_age_days``.
    * else -> ``now - DEFAULT_MAX_AGE_DAYS`` (60).

    The setup wizard writes the rolling form (and clears ``published_after``
    when it does); a hand-edited fixed date keeps working unchanged.
    """

    schema_version: ClassVar[str] = "find-jobs-config:1"
    roles: tuple[str, ...]
    merged_queries: tuple[str, ...]
    location: str | None
    remote: bool
    published_after: str | None
    sources: SourceToggles
    default_assess_cap: int = 10
    default_model_target: ModelTarget = ModelTarget.OLLAMA_LOCAL
    countries: tuple[str, ...] = ()
    visa_sponsorship_required: bool = False
    max_age_days: int | None = None

    @property
    def source_toggles(self) -> SourceToggles:
        """Compatibility name used by packet prose for the ``sources`` field."""

        return self.sources

    @property
    def queries(self) -> tuple[str, ...]:
        """Short alias for callers that call merged queries simply queries."""

        return self.merged_queries

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "roles": _json_strings(self.roles),
            "merged_queries": _json_strings(self.merged_queries),
            "location": self.location,
            "remote": self.remote,
            "published_after": self.published_after,
            "sources": self.sources.to_json(),
            "default_assess_cap": self.default_assess_cap,
            "default_model_target": _json_enum(self.default_model_target),
        }
        # C0 (v0.1.8.1, U12): countries/visa_sponsorship_required are new,
        # optional find-jobs-config:1 keys.  Omitting them at their default
        # keeps to_json() byte-identical to a pre-C0 config, so an old file's
        # digest stays stable when parsed unchanged.  A non-default value
        # (an operator who opted in) does add the key, changing the digest —
        # which is correct, since the logical config actually changed.
        if self.countries:
            value["countries"] = _json_strings(self.countries)
        if self.visa_sponsorship_required:
            value["visa_sponsorship_required"] = self.visa_sponsorship_required
        # Q1 (v0.1.9): same additive rule as C0's keys -- only present when
        # set, so a config that never set it digests exactly as before.
        if self.max_age_days is not None:
            value["max_age_days"] = self.max_age_days
        return value

    @classmethod
    def from_json(cls, obj: object) -> "FindJobsConfig":
        value = _object_with_optional(
            obj,
            ("schema_version", "roles", "merged_queries", "location", "remote", "published_after", "sources", "default_assess_cap", "default_model_target"),
            ("countries", "visa_sponsorship_required", "max_age_days"),
            "find_jobs_config",
        )
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "find_jobs_config.schema_version is unsupported")
        countries = () if "countries" not in value else _country_codes(value["countries"], "countries")
        visa_sponsorship_required = False if "visa_sponsorship_required" not in value else _bool(value["visa_sponsorship_required"], "visa_sponsorship_required")
        max_age_days = (
            None
            if value.get("max_age_days") is None
            else _integer(value["max_age_days"], "max_age_days", minimum=1, maximum=MAX_AGE_DAYS_MAXIMUM)
        )
        return cls(
            _strings(value["roles"], "roles"),
            _strings(value["merged_queries"], "merged_queries"),
            _optional_string(value["location"], "location"),
            _bool(value["remote"], "remote"),
            _optional_string(value["published_after"], "published_after"),
            SourceToggles.from_json(value["sources"]),
            _integer(value["default_assess_cap"], "default_assess_cap", minimum=1, maximum=50),
            _enum(value["default_model_target"], ModelTarget, "default_model_target"),
            countries,
            visa_sponsorship_required,
            max_age_days,
        )


@dataclass(frozen=True)
class PostingRow(_Contract):
    """One normalized public posting, with no private or provider secrets."""

    schema_version: ClassVar[str] = "scout-posting-row:1"
    url: str
    normalized_url: str
    provider: ATSProvider
    board_token: str | None
    company: str
    title: str
    location: str
    published_at: str | None
    content_sha256: str | None
    source_kind: SourceKind
    query_key: str
    text: str | None = None
    sponsorship: SponsorshipStatus | None = None
    # C0 (v0.1.8.1 B1): ISO-3166 alpha-2 codes read from a provider's own
    # structured location field (Lever's `country`, Ashby's
    # `address.postalAddress.addressCountry` + `secondaryLocations`, both
    # normalized to alpha-2), when the provider returns one. `None` means no
    # structured signal was available (Greenhouse/Exa rows, or a Lever/Ashby
    # row whose structured field itself came back null) -- callers fall back
    # to parsing `location`'s free text. An empty tuple is a *trusted* zero
    # result (the field was present but named no recognized country), not
    # "unknown" -- see `filters.country_match`.
    countries: tuple[str, ...] | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "url": self.url,
            "normalized_url": self.normalized_url,
            "provider": _json_enum(self.provider),
            "board_token": self.board_token,
            "company": self.company,
            "title": self.title,
            "location": self.location,
            "published_at": self.published_at,
            "content_sha256": self.content_sha256,
            "source_kind": _json_enum(self.source_kind),
            "query_key": self.query_key,
        }
        # C0 (v0.1.8.1, U25): text/sponsorship are new, optional fields.
        # Omitted at their None default, an old acquire row's to_json() is
        # byte-identical to before, so its digest is unaffected.
        if self.text is not None:
            value["text"] = self.text
        if self.sponsorship is not None:
            value["sponsorship"] = _json_enum(self.sponsorship)
        # C0 (v0.1.8.1 B1): countries is additive/optional the same way;
        # omitted at its None default so a pre-B1 row's digest is unaffected.
        if self.countries is not None:
            value["countries"] = _json_strings(self.countries)
        return value

    @classmethod
    def from_json(cls, obj: object) -> "PostingRow":
        value = _object_with_optional(
            obj,
            ("url", "normalized_url", "provider", "board_token", "company", "title", "location", "published_at", "content_sha256", "source_kind", "query_key"),
            ("text", "sponsorship", "countries"),
            "posting_row",
        )
        sponsorship = None if "sponsorship" not in value else _enum(value["sponsorship"], SponsorshipStatus, "posting_row.sponsorship")
        countries = None if "countries" not in value else _country_codes(value["countries"], "posting_row.countries")
        return cls(
            _string(value["url"], "url"),
            _string(value["normalized_url"], "normalized_url"),
            _enum(value["provider"], ATSProvider, "provider"),
            _optional_string(value["board_token"], "board_token"),
            _string(value["company"], "company"),
            _string(value["title"], "title"),
            _string(value["location"], "location", nonempty=False),
            _optional_string(value["published_at"], "published_at"),
            _optional_digest(value["content_sha256"], "content_sha256"),
            _enum(value["source_kind"], SourceKind, "source_kind"),
            _string(value["query_key"], "query_key"),
            _optional_string(value.get("text"), "posting_row.text") if "text" in value else None,
            sponsorship,
            countries,
        )


NormalizedPostingRow = PostingRow
NormalizedPublicPostingRow = PostingRow


@dataclass(frozen=True)
class PostingRowResult(_Contract):
    """A posting plus the deterministic outcome of this acquisition pass."""

    schema_version: ClassVar[str] = "scout-posting-row-result:1"
    posting: PostingRow
    outcome: RowOutcome

    def to_json(self) -> dict[str, object]:
        return {"posting": self.posting.to_json(), "outcome": _json_enum(self.outcome)}

    @classmethod
    def from_json(cls, obj: object) -> "PostingRowResult":
        value = _object(obj, ("posting", "outcome"), "posting_row_result")
        return cls(PostingRow.from_json(value["posting"]), _enum(value["outcome"], RowOutcome, "outcome"))


@dataclass(frozen=True)
class FailureRow(_Contract):
    """Redacted public-row failure; response bodies and credentials never cross."""

    schema_version: ClassVar[str] = "scout-failure-row:1"
    source_kind: SourceKind
    query_key: str
    url: str | None
    code: str
    message: str

    def to_json(self) -> dict[str, object]:
        return {"source_kind": _json_enum(self.source_kind), "query_key": self.query_key, "url": self.url, "code": self.code, "message": self.message}

    @classmethod
    def from_json(cls, obj: object) -> "FailureRow":
        value = _object(obj, ("source_kind", "query_key", "url", "code", "message"), "failure_row")
        return cls(
            _enum(value["source_kind"], SourceKind, "source_kind"),
            _string(value["query_key"], "query_key"),
            _optional_string(value["url"], "url"),
            _string(value["code"], "code"),
            _string(value["message"], "message"),
        )


@dataclass(frozen=True)
class WatchlistFirstSeen(_Contract):
    source_kind: SourceKind
    source_url: str
    query_key: str
    batch_id: str
    observed_at: str

    def to_json(self) -> dict[str, object]:
        return {"source_kind": _json_enum(self.source_kind), "source_url": self.source_url, "query_key": self.query_key, "batch_id": self.batch_id, "observed_at": self.observed_at}

    @classmethod
    def from_json(cls, obj: object) -> "WatchlistFirstSeen":
        value = _object(obj, ("source_kind", "source_url", "query_key", "batch_id", "observed_at"), "watchlist.first_seen")
        source_kind = _enum(value["source_kind"], SourceKind, "first_seen.source_kind")
        if source_kind is SourceKind.HIRINGCAFE:
            _fail("bad_enum", "first_seen.source_kind must be exa or ats")
        return cls(
            source_kind,
            _string(value["source_url"], "first_seen.source_url"),
            _string(value["query_key"], "first_seen.query_key"),
            _string(value["batch_id"], "first_seen.batch_id"),
            _string(value["observed_at"], "first_seen.observed_at"),
        )


@dataclass(frozen=True)
class WatchlistEntry(_Contract):
    """Exactly ``scout-watchlist:1`` from Amendment 02."""

    schema_version: ClassVar[str] = "scout-watchlist:1"
    watchlist_id: str
    provider: ATSProvider
    board_token: str
    company: str
    state: str
    first_seen: WatchlistFirstSeen

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "watchlist_id": self.watchlist_id,
            "provider": _json_enum(self.provider),
            "board_token": self.board_token,
            "company": self.company,
            "state": self.state,
            "first_seen": self.first_seen.to_json(),
        }

    @classmethod
    def from_json(cls, obj: object) -> "WatchlistEntry":
        value = _object(obj, ("schema_version", "watchlist_id", "provider", "board_token", "company", "state", "first_seen"), "watchlist")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "watchlist.schema_version is unsupported")
        state = _string(value["state"], "state")
        if state != "active":
            _fail("bad_enum", "watchlist.state must be active")
        return cls(
            _string(value["watchlist_id"], "watchlist_id"),
            _enum(value["provider"], ATSProvider, "provider"),
            _string(value["board_token"], "board_token"),
            _string(value["company"], "company"),
            state,
            WatchlistFirstSeen.from_json(value["first_seen"]),
        )


@dataclass(frozen=True)
class NodeContext(_Contract):
    """Serializable, sealed context passed to one graph node."""

    schema_version: ClassVar[str] = "scout-node-context:1"
    run_id: str
    project_id: str
    gig_id: str
    graph_id: str
    graph_version: int
    goal_slug: str
    manifest_digest: str
    operation_key: str
    target_observation_digest: str
    workpad_path: str
    redeemed_consent_ref: str
    model_target: ModelTarget

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "project_id": self.project_id,
            "gig_id": self.gig_id,
            "graph_id": self.graph_id,
            "graph_version": self.graph_version,
            "goal_slug": self.goal_slug,
            "manifest_digest": self.manifest_digest,
            "operation_key": self.operation_key,
            "target_observation_digest": self.target_observation_digest,
            "workpad_path": self.workpad_path,
            "redeemed_consent_ref": self.redeemed_consent_ref,
            "model_target": _json_enum(self.model_target),
        }

    @classmethod
    def from_json(cls, obj: object) -> "NodeContext":
        value = _object(obj, ("schema_version", "run_id", "project_id", "gig_id", "graph_id", "graph_version", "goal_slug", "manifest_digest", "operation_key", "target_observation_digest", "workpad_path", "redeemed_consent_ref", "model_target"), "node_context")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "node_context.schema_version is unsupported")
        for field, prefix, entity_prefix in (("run_id", "run", EntityPrefix.RUN), ("project_id", "project", EntityPrefix.PROJECT), ("gig_id", "gig", EntityPrefix.GIG)):
            identifier = _string(value[field], field)
            try:
                validate_entity_id(identifier, expected_prefix=entity_prefix)
            except ValueError as exc:
                _fail("invalid_value", f"{field} is not a canonical {prefix} ID")
                raise AssertionError("unreachable") from exc
        return cls(
            _string(value["run_id"], "run_id"),
            _string(value["project_id"], "project_id"),
            _string(value["gig_id"], "gig_id"),
            _string(value["graph_id"], "graph_id"),
            _integer(value["graph_version"], "graph_version", minimum=1),
            _string(value["goal_slug"], "goal_slug"),
            _digest_value(value["manifest_digest"], "manifest_digest"),
            _string(value["operation_key"], "operation_key"),
            _digest_value(value["target_observation_digest"], "target_observation_digest"),
            _string(value["workpad_path"], "workpad_path"),
            _string(value["redeemed_consent_ref"], "redeemed_consent_ref"),
            _enum(value["model_target"], ModelTarget, "model_target"),
        )


@dataclass(frozen=True)
class SelectedPosting(_Contract):
    """D6 identity resolved by acquire and pinned for assess."""

    normalized_url: str
    url: str
    content_sha256: str
    role_match: bool

    def to_json(self) -> dict[str, object]:
        return {"normalized_url": self.normalized_url, "url": self.url, "content_sha256": self.content_sha256, "role_match": self.role_match}

    @classmethod
    def from_json(cls, obj: object) -> "SelectedPosting":
        value = _object(obj, ("normalized_url", "url", "content_sha256", "role_match"), "selected_posting")
        return cls(
            _string(value["normalized_url"], "selected_posting.normalized_url"),
            _string(value["url"], "selected_posting.url"),
            _digest_value(value["content_sha256"], "selected_posting.content_sha256"),
            _bool(value["role_match"], "selected_posting.role_match"),
        )


@dataclass(frozen=True)
class SelectionReason(_Contract):
    normalized_url: str
    reason: SelectionReasonCode

    def to_json(self) -> dict[str, object]:
        return {"normalized_url": self.normalized_url, "reason": _json_enum(self.reason)}

    @classmethod
    def from_json(cls, obj: object) -> "SelectionReason":
        value = _object(obj, ("normalized_url", "reason"), "selection_reason")
        reason = _enum(value["reason"], SelectionReasonCode, "selection_reason.reason")
        return cls(_string(value["normalized_url"], "normalized_url"), reason)


@dataclass(frozen=True)
class PinnedResume(_Contract):
    """D7's exact ``record_id``/``revision_id``/content digest triple."""

    record_id: str
    revision_id: str
    content_sha256: str

    def to_json(self) -> dict[str, object]:
        return {"record_id": self.record_id, "revision_id": self.revision_id, "content_sha256": self.content_sha256}

    @classmethod
    def from_json(cls, obj: object) -> "PinnedResume":
        value = _object(obj, ("record_id", "revision_id", "content_sha256"), "pinned_resume")
        return cls(_string(value["record_id"], "record_id"), _string(value["revision_id"], "revision_id"), _digest_value(value["content_sha256"], "content_sha256"))


@dataclass(frozen=True)
class ProfileRef(_Contract):
    """S25 F1-b: which interested profile (and exact content) a run used.

    ``{profile_id, revision, content_digest}`` -- the sealed identity Q3/A2
    of the S25 spike define. ``revision`` is the profile record's own
    monotonic integer; ``content_digest`` is the run's own sealed copy of
    the profile's ``content_digest`` at seal time (independently verifiable
    without trusting the live profile record or journal history -- see
    ``profile_records.retrieve_profile_revision``).
    """

    profile_id: str
    revision: int
    content_digest: str

    def to_json(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "revision": self.revision,
            "content_digest": self.content_digest,
        }

    @classmethod
    def from_json(cls, obj: object) -> "ProfileRef":
        value = _object(obj, ("profile_id", "revision", "content_digest"), "profile_ref")
        return cls(
            _string(value["profile_id"], "profile_ref.profile_id"),
            _integer(value["revision"], "profile_ref.revision", minimum=1),
            _digest_value(value["content_digest"], "profile_ref.content_digest"),
        )


@dataclass(frozen=True)
class FindJobsRunInput(_Contract):
    """Sealed run snapshot written before graph allocation and execution."""

    schema_version: ClassVar[str] = "scout-find-jobs-run-input:1"
    config: FindJobsConfig
    config_digest: str
    selection_cap: int
    selection_rule: SelectionRule
    model_target: ModelTarget
    pinned_resume: PinnedResume
    # S25 F1-b: additive/optional (_object_with_optional), never required --
    # ``None`` only for a run sealed before F1-b shipped (or a caller that
    # never resolves a profile at all, matching PostingRow's own text/
    # sponsorship precedent, contracts.py:516-539). Omitted from to_json() at
    # its None default so an old sealed input's digest stays unaffected.
    profile_ref: ProfileRef | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "config": self.config.to_json(),
            "config_digest": self.config_digest,
            "selection_cap": self.selection_cap,
            "selection_rule": _json_enum(self.selection_rule),
            "model_target": _json_enum(self.model_target),
            "pinned_resume": self.pinned_resume.to_json(),
        }
        if self.profile_ref is not None:
            value["profile_ref"] = self.profile_ref.to_json()
        return value

    @classmethod
    def from_json(cls, obj: object) -> "FindJobsRunInput":
        value = _object_with_optional(
            obj,
            ("schema_version", "config", "config_digest", "selection_cap", "selection_rule", "model_target", "pinned_resume"),
            ("profile_ref",),
            "find_jobs_run_input",
        )
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "find_jobs_run_input.schema_version is unsupported")
        config = FindJobsConfig.from_json(value["config"])
        config_digest = _digest_value(value["config_digest"], "config_digest")
        if config_digest != config.digest():
            _fail("invalid_value", "find_jobs_run_input.config_digest must equal config.digest()")
        profile_ref = None if "profile_ref" not in value else ProfileRef.from_json(value["profile_ref"])
        return cls(
            config,
            config_digest,
            _integer(value["selection_cap"], "selection_cap", minimum=1, maximum=50),
            _enum(value["selection_rule"], SelectionRule, "selection_rule"),
            _enum(value["model_target"], ModelTarget, "model_target"),
            PinnedResume.from_json(value["pinned_resume"]),
            profile_ref,
        )


@dataclass(frozen=True)
class AcquireInput(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-acquire-input:1"
    config: FindJobsConfig
    config_digest: str
    prior_batch_digest: str | None
    rows: tuple[PostingRow, ...]
    selection_cap: int
    selection_rule: SelectionRule

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "config": self.config.to_json(),
            "config_digest": self.config_digest,
            "prior_batch_digest": self.prior_batch_digest,
            "rows": [row.to_json() for row in self.rows],
            "selection_cap": self.selection_cap,
            "selection_rule": _json_enum(self.selection_rule),
        }

    @classmethod
    def from_json(cls, obj: object) -> "AcquireInput":
        value = _object(obj, ("schema_version", "config", "config_digest", "prior_batch_digest", "rows", "selection_cap", "selection_rule"), "acquire_input")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "acquire_input.schema_version is unsupported")
        if type(value["rows"]) is not list:
            _fail("wrong_type", "rows must be an array")
        return cls(
            FindJobsConfig.from_json(value["config"]),
            _digest_value(value["config_digest"], "config_digest"),
            _optional_digest(value["prior_batch_digest"], "prior_batch_digest"),
            tuple(PostingRow.from_json(item) for item in value["rows"]),
            _integer(value["selection_cap"], "selection_cap", minimum=1, maximum=50),
            _enum(value["selection_rule"], SelectionRule, "selection_rule"),
        )


@dataclass(frozen=True)
class URLObservation(_Contract):
    """One normalized URL and the exact fetched content digest observed."""

    url: str
    content_sha256: str | None

    def to_json(self) -> dict[str, object]:
        return {"url": self.url, "content_sha256": self.content_sha256}

    @classmethod
    def from_json(cls, obj: object) -> "URLObservation":
        value = _object(obj, ("url", "content_sha256"), "url_observation")
        return cls(_string(value["url"], "url"), _optional_digest(value["content_sha256"], "content_sha256"))


@dataclass(frozen=True)
class EditedURL(_Contract):
    url: str
    previous_content_sha256: str | None
    current_content_sha256: str | None

    def to_json(self) -> dict[str, object]:
        return {"url": self.url, "previous_content_sha256": self.previous_content_sha256, "current_content_sha256": self.current_content_sha256}

    @classmethod
    def from_json(cls, obj: object) -> "EditedURL":
        value = _object(obj, ("url", "previous_content_sha256", "current_content_sha256"), "edited_url")
        previous = _optional_digest(value["previous_content_sha256"], "previous_content_sha256")
        current = _optional_digest(value["current_content_sha256"], "current_content_sha256")
        if previous == current:
            _fail("invalid_value", "edited_url digests must differ")
        return cls(_string(value["url"], "url"), previous, current)


@dataclass(frozen=True)
class URLSetDiff(_Contract):
    """Mutually exclusive URL-to-content-digest observation categories."""

    added: tuple[URLObservation, ...]
    removed: tuple[URLObservation, ...]
    unchanged: tuple[URLObservation, ...]
    edited: tuple[EditedURL, ...]

    def __post_init__(self) -> None:
        groups = (
            tuple(item.url for item in self.added),
            tuple(item.url for item in self.removed),
            tuple(item.url for item in self.unchanged),
            tuple(item.url for item in self.edited),
        )
        all_urls = [url for group in groups for url in group]
        if len(all_urls) != len(set(all_urls)):
            _fail("invalid_value", "url diff categories must be mutually exclusive")
        for group in groups:
            if len(group) != len(set(group)):
                _fail("invalid_value", "url diff categories must not contain duplicates")

    def to_json(self) -> dict[str, object]:
        return {
            "added": [item.to_json() for item in self.added],
            "removed": [item.to_json() for item in self.removed],
            "unchanged": [item.to_json() for item in self.unchanged],
            "edited": [item.to_json() for item in self.edited],
        }

    @classmethod
    def from_json(cls, obj: object) -> "URLSetDiff":
        value = _object(obj, ("added", "removed", "unchanged", "edited"), "url_set_diff")
        for field in ("added", "removed", "unchanged", "edited"):
            if type(value[field]) is not list:
                _fail("wrong_type", f"url_set_diff.{field} must be an array")
        return cls(
            tuple(URLObservation.from_json(item) for item in value["added"]),
            tuple(URLObservation.from_json(item) for item in value["removed"]),
            tuple(URLObservation.from_json(item) for item in value["unchanged"]),
            tuple(EditedURL.from_json(item) for item in value["edited"]),
        )


@dataclass(frozen=True)
class DropCount(_Contract):
    """How many acquired rows were dropped for one reason (0.1.8.1 B1).

    B1: acquire now drops country/location/remote/role-non-matching rows
    right after fetch instead of leaving them for the UI to filter (raw/
    keeps everything for audit/replay -- see ``_write_raw_payloads``). This
    is the per-reason accounting of that drop, additive on
    :class:`AcquireOutput` so it's visible on the run without re-deriving it
    from ``raw/`` vs ``outputs/acquire.json``.
    """

    reason: NotAssessedReason
    count: int

    def to_json(self) -> dict[str, object]:
        return {"reason": _json_enum(self.reason), "count": self.count}

    @classmethod
    def from_json(cls, obj: object) -> "DropCount":
        value = _object(obj, ("reason", "count"), "drop_count")
        return cls(_enum(value["reason"], NotAssessedReason, "drop_count.reason"), _integer(value["count"], "drop_count.count", minimum=0))


@dataclass(frozen=True)
class CarriedForwardAssessment(_Contract):
    """uat-bug-009: a posting skipped as UNCHANGED, plus its earlier result.

    Produced only when acquire finds a *successful* assessment of the exact
    same content digest, for the current resume revision, from an earlier
    run -- the only case where skipping a posting as "unchanged" is still
    correct (see ``market_acquisition._prior_assessments``). Additive on
    :class:`AcquireOutput`, never on the assessed/not-assessed partition
    :class:`PresentPayload` enforces (a posting cannot be both): the UI
    layer (``present_api.py``, ``ResultsView.jsx``/``boardRows.js``) merges
    this alongside the NotAssessedRow(UNCHANGED) entry so the card shows the
    carried fit/reasons instead of a bare "Not assessed", labelled with the
    run it came from.
    """

    normalized_url: str
    result: "AssessmentResult"
    from_run_date: str | None

    def to_json(self) -> dict[str, object]:
        return {"normalized_url": self.normalized_url, "result": self.result.to_json(), "from_run_date": self.from_run_date}

    @classmethod
    def from_json(cls, obj: object) -> "CarriedForwardAssessment":
        value = _object(obj, ("normalized_url", "result", "from_run_date"), "carried_forward_assessment")
        from_run_date = value["from_run_date"]
        if from_run_date is not None and not isinstance(from_run_date, str):
            _fail("wrong_type", "carried_forward_assessment.from_run_date must be a string or null")
        return cls(_string(value["normalized_url"], "normalized_url"), AssessmentResult.from_json(value["result"]), from_run_date)


@dataclass(frozen=True)
class AcquireOutput(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-acquire-output:1"
    batch_id: str
    batch_ref: str
    progress_ref: str
    progress_status: ProgressStatus
    rows: tuple[PostingRowResult, ...]
    failures: tuple[FailureRow, ...]
    url_set_diff: URLSetDiff
    watchlist_refs: tuple[str, ...]
    selected_postings: tuple[SelectedPosting, ...]
    # C0 (v0.1.8.1 B1): additive/optional -- per-reason counts of rows
    # dropped right after fetch (before ``rows``/``url_set_diff`` are even
    # computed), so old serialized acquire outputs (pre-B1, no drop stage)
    # still parse with this at its `()` default.
    dropped_counts: tuple[DropCount, ...] = ()
    # uat-bug-009: additive/optional -- every UNCHANGED row skipped this run
    # because a successful prior assessment for the current resume revision
    # was found, plus that earlier result. `()` default keeps an old
    # serialized acquire output (pre-uat-bug-009) parsing unchanged.
    carried_forward_assessments: tuple[CarriedForwardAssessment, ...] = ()
    # P6: additive/optional -- one Jev pre-rank score per candidate row, in
    # the order the ranking call scored them; `()` for a run with no Jev key
    # (fail open) or one sealed before P6 shipped. `RankScore` lives in
    # ``jev_contracts.py`` (plan's shared-DTO rule); imported lazily here so
    # this module -- imported by ``jev_contracts.py`` itself -- never forms
    # an import cycle.
    rank_scores: tuple["RankScore", ...] = ()

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "schema_version": self.schema_version,
            "batch_id": self.batch_id,
            "batch_ref": self.batch_ref,
            "progress_ref": self.progress_ref,
            "progress_status": _json_enum(self.progress_status),
            "rows": [row.to_json() for row in self.rows],
            "failures": [failure.to_json() for failure in self.failures],
            "url_set_diff": self.url_set_diff.to_json(),
            "watchlist_refs": _json_strings(self.watchlist_refs),
            "selected_postings": [posting.to_json() for posting in self.selected_postings],
        }
        if self.dropped_counts:
            value["dropped_counts"] = [item.to_json() for item in self.dropped_counts]
        if self.carried_forward_assessments:
            value["carried_forward_assessments"] = [item.to_json() for item in self.carried_forward_assessments]
        if self.rank_scores:
            value["rank_scores"] = [item.to_json() for item in self.rank_scores]
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AcquireOutput":
        value = _object_with_optional(
            obj,
            ("schema_version", "batch_id", "batch_ref", "progress_ref", "progress_status", "rows", "failures", "url_set_diff", "watchlist_refs", "selected_postings"),
            ("dropped_counts", "carried_forward_assessments", "rank_scores"),
            "acquire_output",
        )
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "acquire_output.schema_version is unsupported")
        if type(value["rows"]) is not list or type(value["failures"]) is not list or type(value["selected_postings"]) is not list:
            _fail("wrong_type", "acquire_output arrays are malformed")
        dropped_counts: tuple[DropCount, ...] = ()
        if "dropped_counts" in value:
            if type(value["dropped_counts"]) is not list:
                _fail("wrong_type", "acquire_output.dropped_counts must be an array")
            dropped_counts = tuple(DropCount.from_json(item) for item in value["dropped_counts"])
        carried_forward_assessments: tuple[CarriedForwardAssessment, ...] = ()
        if "carried_forward_assessments" in value:
            if type(value["carried_forward_assessments"]) is not list:
                _fail("wrong_type", "acquire_output.carried_forward_assessments must be an array")
            carried_forward_assessments = tuple(CarriedForwardAssessment.from_json(item) for item in value["carried_forward_assessments"])
        rank_scores: tuple["RankScore", ...] = ()
        if "rank_scores" in value:
            if type(value["rank_scores"]) is not list:
                _fail("wrong_type", "acquire_output.rank_scores must be an array")
            from .jev_contracts import RankScore

            rank_scores = tuple(RankScore.from_json(item) for item in value["rank_scores"])
        return cls(
            _string(value["batch_id"], "batch_id"),
            _string(value["batch_ref"], "batch_ref"),
            _string(value["progress_ref"], "progress_ref"),
            _enum(value["progress_status"], ProgressStatus, "progress_status"),
            tuple(PostingRowResult.from_json(item) for item in value["rows"]),
            tuple(FailureRow.from_json(item) for item in value["failures"]),
            URLSetDiff.from_json(value["url_set_diff"]),
            _strings(value["watchlist_refs"], "watchlist_refs", allow_empty=True),
            tuple(SelectedPosting.from_json(item) for item in value["selected_postings"]),
            dropped_counts,
            carried_forward_assessments,
            rank_scores,
        )


@dataclass(frozen=True)
class RequirementMatrixRow(_Contract):
    requirement: str
    resume_evidence: tuple[str, ...]
    status: MatrixStatus
    # P2 (v0.1.9): additive, JSON key "class" (a reserved word); omitted
    # when None so an old serialized matrix row is byte-identical to before.
    requirement_class: RequirementClass | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"requirement": self.requirement, "resume_evidence": _json_strings(self.resume_evidence), "status": _json_enum(self.status)}
        if self.requirement_class is not None:
            value["class"] = _json_enum(self.requirement_class)
        return value

    @classmethod
    def from_json(cls, obj: object) -> "RequirementMatrixRow":
        value = _object_with_optional(obj, ("requirement", "resume_evidence", "status"), ("class",), "matrix_row")
        requirement_class = None if "class" not in value else _enum(value["class"], RequirementClass, "matrix_row.class")
        return cls(_string(value["requirement"], "requirement"), _strings(value["resume_evidence"], "resume_evidence", allow_empty=True), _enum(value["status"], MatrixStatus, "status"), requirement_class)


@dataclass(frozen=True)
class NotAssessedRow(_Contract):
    """One candidate row intentionally left visible but not sent to assess."""

    posting: PostingRow
    reason: NotAssessedReason

    def to_json(self) -> dict[str, object]:
        return {"posting": self.posting.to_json(), "reason": _json_enum(self.reason)}

    @classmethod
    def from_json(cls, obj: object) -> "NotAssessedRow":
        value = _object(obj, ("posting", "reason"), "not_assessed_row")
        return cls(PostingRow.from_json(value["posting"]), _enum(value["reason"], NotAssessedReason, "reason"))


_QUESTION_ID = re.compile(r"\A[a-z0-9._-]+:[a-z0-9._-]+\Z")


@dataclass(frozen=True)
class AssessmentQuestion(_Contract):
    """P2 (v0.1.9): one structured, askable question from the S29 r1 prompt.

    ``question_id`` fits the same pattern as ``experience_qa``'s
    ``question_id`` (C10) so an answered question can be recorded there
    without translation: ``"<category>:<value>"``, lowercase.
    """

    question_id: str
    question: str
    requirement: str | None

    def to_json(self) -> dict[str, object]:
        return {"question_id": self.question_id, "question": self.question, "requirement": self.requirement}

    @classmethod
    def from_json(cls, obj: object) -> "AssessmentQuestion":
        value = _object(obj, ("question_id", "question", "requirement"), "assessment_question")
        question_id = _string(value["question_id"], "assessment_question.question_id")
        if not _QUESTION_ID.fullmatch(question_id):
            _fail("invalid_value", "assessment_question.question_id must match <category>:<value>")
        return cls(question_id, _string(value["question"], "assessment_question.question"), _optional_string(value["requirement"], "assessment_question.requirement"))


@dataclass(frozen=True)
class AssessmentResult(_Contract):
    posting: SelectedPosting
    matrix: tuple[RequirementMatrixRow, ...]
    suggestions: tuple[str, ...]
    questions: tuple[str, ...]
    proposal_revision_ref: str | None
    sponsorship: SponsorshipStatus | None = None
    # P2 (v0.1.9): additive verdict/structured-question fields (plan section
    # "P2"). All three are omitted at their defaults so an old serialized
    # assessment result's to_json() stays byte-identical to before.
    verdict: Verdict | None = None
    structured_questions: tuple[AssessmentQuestion, ...] = ()
    not_a_match_reason: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"posting": self.posting.to_json(), "matrix": [row.to_json() for row in self.matrix], "suggestions": _json_strings(self.suggestions), "questions": _json_strings(self.questions), "proposal_revision_ref": self.proposal_revision_ref}
        # C0 (v0.1.8.1, U12): sponsorship is a new, optional field (the
        # model's read, for the UI badge).  Omitted at its None default, an
        # old assessment result's to_json() is byte-identical to before.
        if self.sponsorship is not None:
            value["sponsorship"] = _json_enum(self.sponsorship)
        if self.verdict is not None:
            value["verdict"] = _json_enum(self.verdict)
        if self.structured_questions:
            value["structured_questions"] = [item.to_json() for item in self.structured_questions]
        if self.not_a_match_reason is not None:
            value["not_a_match_reason"] = self.not_a_match_reason
        return value

    @classmethod
    def from_json(cls, obj: object) -> "AssessmentResult":
        value = _object_with_optional(
            obj,
            ("posting", "matrix", "suggestions", "questions", "proposal_revision_ref"),
            ("sponsorship", "verdict", "structured_questions", "not_a_match_reason"),
            "assessment_result",
        )
        if type(value["matrix"]) is not list:
            _fail("wrong_type", "assessment_result.matrix must be an array")
        sponsorship = None if "sponsorship" not in value else _enum(value["sponsorship"], SponsorshipStatus, "assessment_result.sponsorship")
        verdict = None if "verdict" not in value else _enum(value["verdict"], Verdict, "assessment_result.verdict")
        structured_questions: tuple[AssessmentQuestion, ...] = ()
        if "structured_questions" in value:
            if type(value["structured_questions"]) is not list:
                _fail("wrong_type", "assessment_result.structured_questions must be an array")
            structured_questions = tuple(AssessmentQuestion.from_json(item) for item in value["structured_questions"])
        not_a_match_reason = None if "not_a_match_reason" not in value else _optional_string(value["not_a_match_reason"], "assessment_result.not_a_match_reason")
        return cls(
            SelectedPosting.from_json(value["posting"]),
            tuple(RequirementMatrixRow.from_json(item) for item in value["matrix"]),
            _strings(value["suggestions"], "suggestions", allow_empty=True),
            _strings(value["questions"], "questions", allow_empty=True),
            _optional_string(value["proposal_revision_ref"], "proposal_revision_ref"),
            sponsorship,
            verdict,
            structured_questions,
            not_a_match_reason,
        )


@dataclass(frozen=True)
class UsageBlock(_Contract):
    measured: bool
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    cost: str | None
    currency: str | None
    cost_status: str

    def to_json(self) -> dict[str, object]:
        return {"measured": self.measured, "input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "total_tokens": self.total_tokens, "cost": self.cost, "currency": self.currency, "cost_status": self.cost_status}

    def to_goal_json(self) -> dict[str, object]:
        """Render the exact common-schema usage object (without local ``measured``)."""

        return {"input_tokens": self.input_tokens, "output_tokens": self.output_tokens, "total_tokens": self.total_tokens, "cost": self.cost, "currency": self.currency, "cost_status": self.cost_status}

    @classmethod
    def from_json(cls, obj: object) -> "UsageBlock":
        value = _object(obj, ("measured", "input_tokens", "output_tokens", "total_tokens", "cost", "currency", "cost_status"), "usage")
        def optional_int(item: object, name: str) -> int | None:
            if item is None:
                return None
            return _integer(item, name, minimum=0)
        cost = _optional_string(value["cost"], "usage.cost")
        if cost is not None and not re.fullmatch(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", cost):
            _fail("invalid_value", "usage.cost must be a decimal string")
        currency = _optional_string(value["currency"], "usage.currency")
        if currency is not None and not re.fullmatch(r"[A-Z]{3}", currency):
            _fail("invalid_value", "usage.currency must be an uppercase ISO code")
        cost_status = _string(value["cost_status"], "usage.cost_status")
        if cost_status not in {"provider_reported", "derived", "unavailable", "not_applicable"}:
            _fail("bad_enum", "usage.cost_status is unsupported")
        return cls(_bool(value["measured"], "usage.measured"), optional_int(value["input_tokens"], "usage.input_tokens"), optional_int(value["output_tokens"], "usage.output_tokens"), optional_int(value["total_tokens"], "usage.total_tokens"), cost, currency, cost_status)


@dataclass(frozen=True)
class Producer(_Contract):
    callable: str
    version: str
    actor: str
    model_target: ModelTarget
    adapter: str

    def to_json(self) -> dict[str, object]:
        return {"callable": self.callable, "version": self.version, "actor": self.actor, "model_target": _json_enum(self.model_target), "adapter": self.adapter}

    @classmethod
    def from_json(cls, obj: object) -> "Producer":
        value = _object(obj, ("callable", "version", "actor", "model_target", "adapter"), "producer")
        return cls(_string(value["callable"], "producer.callable"), _string(value["version"], "producer.version"), _string(value["actor"], "producer.actor"), _enum(value["model_target"], ModelTarget, "producer.model_target"), _string(value["adapter"], "producer.adapter"))


@dataclass(frozen=True)
class GoalError(_Contract):
    code: str
    message: str
    retryable: bool
    invocation_id: str | None

    def to_json(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message, "retryable": self.retryable, "invocation_id": self.invocation_id}

    @classmethod
    def from_json(cls, obj: object) -> "GoalError":
        value = _object(obj, ("code", "message", "retryable", "invocation_id"), "error")
        code = _string(value["code"], "error.code")
        if not re.fullmatch(r"[a-z][a-z0-9_]*", code):
            _fail("invalid_value", "error.code is not a valid code")
        invocation_id = _optional_string(value["invocation_id"], "error.invocation_id")
        if invocation_id is not None and not re.fullmatch(r"inv_[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}", invocation_id):
            _fail("invalid_value", "error.invocation_id is not canonical")
        return cls(code, _string(value["message"], "error.message"), _bool(value["retryable"], "error.retryable"), invocation_id)


@dataclass(frozen=True)
class ArtifactRef(_Contract):
    path: str
    content_sha256: str
    media_type: str
    size_bytes: int
    canonical_sha256: str | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"path": self.path, "content_sha256": self.content_sha256, "media_type": self.media_type, "size_bytes": self.size_bytes}
        if self.canonical_sha256 is not None:
            value["canonical_sha256"] = self.canonical_sha256
        return value

    @classmethod
    def from_json(cls, obj: object) -> "ArtifactRef":
        if type(obj) is not dict:
            _fail("wrong_type", "artifact_ref must be an object")
        unknown = set(obj) - {"path", "content_sha256", "media_type", "size_bytes", "canonical_sha256"}
        if unknown:
            _fail("unknown_key", f"artifact_ref contains unknown key(s): {sorted(unknown)}")
        missing = {"path", "content_sha256", "media_type", "size_bytes"} - set(obj)
        if missing:
            _fail("missing_key", f"artifact_ref is missing key(s): {sorted(missing)}")
        path = _string(obj["path"], "artifact_ref.path")
        if len(path) > 4096 or path.startswith("/") or "\\" in path or ".." in path.split("/"):
            _fail("invalid_value", "artifact_ref.path must be relative")
        canonical = _optional_digest(obj.get("canonical_sha256"), "artifact_ref.canonical_sha256")
        media_type = _string(obj["media_type"], "artifact_ref.media_type")
        if len(media_type) > 255:
            _fail("invalid_value", "artifact_ref.media_type is too long")
        return cls(path, _digest_value(obj["content_sha256"], "artifact_ref.content_sha256"), media_type, _integer(obj["size_bytes"], "artifact_ref.size_bytes", minimum=0), canonical)


@dataclass(frozen=True)
class NodeFailure(_Contract):
    code: str
    message: str

    def to_json(self) -> dict[str, object]:
        return {"code": self.code, "message": self.message}

    @classmethod
    def from_json(cls, obj: object) -> "NodeFailure":
        value = _object(obj, ("code", "message"), "node_failure")
        return cls(_string(value["code"], "failure.code"), _string(value["message"], "failure.message"))


@dataclass(frozen=True)
class NodeReceipt(_Contract):
    schema_version: ClassVar[str] = "scout-node-receipt:1"
    goal_id: str
    goal_version: int
    executor: str
    node_slug: str
    operation_key: str
    status: NodeStatus
    outcome: str | None
    errors: tuple[GoalError, ...]
    evidence: tuple[ArtifactRef, ...]
    producer: Producer
    usage: UsageBlock | None
    started_at: str
    finished_at: str | None
    failure: NodeFailure | None

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "goal_id": self.goal_id,
            "goal_version": self.goal_version,
            "executor": self.executor,
            "node_slug": self.node_slug,
            "operation_key": self.operation_key,
            "status": _json_enum(self.status),
            "outcome": self.outcome,
            "errors": [item.to_json() for item in self.errors],
            "evidence": [item.to_json() for item in self.evidence],
            "producer": self.producer.to_json(),
            "usage": self.usage.to_json() if self.usage is not None else None,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "failure": self.failure.to_json() if self.failure is not None else None,
        }

    @classmethod
    def from_json(cls, obj: object) -> "NodeReceipt":
        value = _object(obj, ("schema_version", "goal_id", "goal_version", "executor", "node_slug", "operation_key", "status", "outcome", "errors", "evidence", "producer", "usage", "started_at", "finished_at", "failure"), "node_receipt")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "node_receipt.schema_version is unsupported")
        if type(value["errors"]) is not list or type(value["evidence"]) is not list:
            _fail("wrong_type", "node_receipt.errors and evidence must be arrays")
        usage = None if value["usage"] is None else UsageBlock.from_json(value["usage"])
        failure = None if value["failure"] is None else NodeFailure.from_json(value["failure"])
        outcome = _optional_string(value["outcome"], "outcome")
        if outcome is not None and not re.fullmatch(r"[A-Z][A-Z0-9_]*", outcome):
            _fail("invalid_value", "node_receipt.outcome must be an uppercase outcome")
        goal_id = _string(value["goal_id"], "goal_id")
        try:
            validate_entity_id(goal_id, expected_prefix=EntityPrefix.GOAL)
        except ValueError as exc:
            _fail("invalid_value", "goal_id is not a canonical goal ID")
            raise AssertionError("unreachable") from exc
        executor = _string(value["executor"], "executor")
        if len(executor) > 255:
            _fail("invalid_value", "executor is too long")
        started_at = _datetime_string(value["started_at"], "started_at")
        finished_at = _datetime_string(value["finished_at"], "finished_at", optional=True)
        return cls(goal_id, _integer(value["goal_version"], "goal_version", minimum=1), executor, _string(value["node_slug"], "node_slug"), _string(value["operation_key"], "operation_key"), _enum(value["status"], NodeStatus, "status"), outcome, tuple(GoalError.from_json(item) for item in value["errors"]), tuple(ArtifactRef.from_json(item) for item in value["evidence"]), Producer.from_json(value["producer"]), usage, started_at or "", finished_at, failure)

    def to_goal_details(self) -> dict[str, object]:
        """Map to schema ``goal_details``; ``interrupted`` is run-level only.

        ``run-details.schema.json`` has no ``interrupted`` goal state.  An
        interrupted NodeReceipt therefore cannot be projected into a goal
        detail and fails closed; the enclosing Run carries that aggregate
        status instead.
        """

        if self.status is NodeStatus.INTERRUPTED:
            _fail("invalid_value", "interrupted is a run-level status, not a goal status")
        status = GoalStatus(self.status.value)
        return {
            "goal_id": self.goal_id,
            "goal_version": self.goal_version,
            "executor": self.executor,
            "status": status.value,
            "outcome": self.outcome,
            "errors": [item.to_json() for item in self.errors],
            "evidence": [item.to_json() for item in self.evidence],
            "usage": self.usage.to_goal_json() if self.usage is not None else _zero_usage(),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }


NodeReceiptStatus = NodeStatus


@dataclass(frozen=True)
class AssessInput(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-assess-input:1"
    acquire_batch_ref: str
    acquire_output_digest: str
    selected_postings: tuple[SelectedPosting, ...]
    selection_cap: int
    selection_reasons: tuple[SelectionReason, ...]
    pinned_resume: PinnedResume
    target: str
    model_target: ModelTarget
    answer_association_version: str

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "acquire_batch_ref": self.acquire_batch_ref,
            "acquire_output_digest": self.acquire_output_digest,
            "selected_postings": [posting.to_json() for posting in self.selected_postings],
            "selection_cap": self.selection_cap,
            "selection_reasons": {reason.normalized_url: _json_enum(reason.reason) for reason in self.selection_reasons},
            "pinned_resume": self.pinned_resume.to_json(),
            "target": self.target,
            "model_target": _json_enum(self.model_target),
            "answer_association_version": self.answer_association_version,
        }

    @classmethod
    def from_json(cls, obj: object) -> "AssessInput":
        value = _object(obj, ("schema_version", "acquire_batch_ref", "acquire_output_digest", "selected_postings", "selection_cap", "selection_reasons", "pinned_resume", "target", "model_target", "answer_association_version"), "assess_input")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "assess_input.schema_version is unsupported")
        if type(value["selected_postings"]) is not list or type(value["selection_reasons"]) is not dict:
            _fail("wrong_type", "assess_input selection arrays are malformed")
        selected = tuple(SelectedPosting.from_json(item) for item in value["selected_postings"])
        reasons = tuple(SelectionReason(_string(key, "selection_reasons.key"), _enum(reason, SelectionReasonCode, f"selection_reasons.{key}")) for key, reason in value["selection_reasons"].items())
        result = cls(_string(value["acquire_batch_ref"], "acquire_batch_ref"), _digest_value(value["acquire_output_digest"], "acquire_output_digest"), selected, _integer(value["selection_cap"], "selection_cap", minimum=1, maximum=50), reasons, PinnedResume.from_json(value["pinned_resume"]), _string(value["target"], "target"), _enum(value["model_target"], ModelTarget, "model_target"), _string(value["answer_association_version"], "answer_association_version"))
        _validate_assess_input(result)
        return result


def _validate_assess_input(value: AssessInput) -> None:
    urls = tuple(item.normalized_url for item in value.selected_postings)
    if len(urls) != len(set(urls)):
        _fail("invalid_value", "assess_input.selected_postings must be unique")
    if len(urls) > value.selection_cap:
        _fail("invalid_value", "assess_input selected postings exceed selection_cap")
    if any(not item.role_match for item in value.selected_postings):
        _fail("invalid_value", "assess_input selected postings must match the role filter")
    reason_urls = tuple(item.normalized_url for item in value.selection_reasons)
    if len(reason_urls) != len(set(reason_urls)):
        _fail("invalid_value", "assess_input selection reasons must be unique")
    if set(reason_urls) != set(urls):
        _fail("invalid_value", "assess_input requires exactly one selection reason per posting")
    if any(item.reason not in {SelectionReasonCode.NEW, SelectionReasonCode.EDITED} for item in value.selection_reasons):
        _fail("bad_enum", "assess_input selection reason is unsupported")


@dataclass(frozen=True)
class AssessOutput(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-assess-output:1"
    selected_postings: tuple[SelectedPosting, ...]
    pinned_resume: PinnedResume
    target: str
    selection_cap: int
    selection_rule: SelectionRule
    candidate_rows: tuple[PostingRowResult, ...]
    assessments: tuple[AssessmentResult, ...]
    not_assessed: tuple[NotAssessedRow, ...]
    proposal_revision_refs: tuple[str, ...]
    model_target: ModelTarget
    producer: Producer
    usage: UsageBlock | None
    failures: tuple[FailureRow, ...]

    def to_json(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "selected_postings": [item.to_json() for item in self.selected_postings],
            "pinned_resume": self.pinned_resume.to_json(),
            "target": self.target,
            "selection_cap": self.selection_cap,
            "selection_rule": _json_enum(self.selection_rule),
            "candidate_rows": [item.to_json() for item in self.candidate_rows],
            "assessments": [item.to_json() for item in self.assessments],
            "not_assessed": [item.to_json() for item in self.not_assessed],
            "proposal_revision_refs": _json_strings(self.proposal_revision_refs),
            "model_target": _json_enum(self.model_target),
            "producer": self.producer.to_json(),
            "usage": self.usage.to_json() if self.usage is not None else None,
            "failures": [item.to_json() for item in self.failures],
        }

    @classmethod
    def from_json(cls, obj: object) -> "AssessOutput":
        value = _object(obj, ("schema_version", "selected_postings", "pinned_resume", "target", "selection_cap", "selection_rule", "candidate_rows", "assessments", "not_assessed", "proposal_revision_refs", "model_target", "producer", "usage", "failures"), "assess_output")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "assess_output.schema_version is unsupported")
        if type(value["selected_postings"]) is not list or type(value["candidate_rows"]) is not list or type(value["assessments"]) is not list or type(value["not_assessed"]) is not list or type(value["failures"]) is not list:
            _fail("wrong_type", "assess_output arrays are malformed")
        usage = None if value["usage"] is None else UsageBlock.from_json(value["usage"])
        result = cls(tuple(SelectedPosting.from_json(item) for item in value["selected_postings"]), PinnedResume.from_json(value["pinned_resume"]), _string(value["target"], "target"), _integer(value["selection_cap"], "selection_cap", minimum=1, maximum=50), _enum(value["selection_rule"], SelectionRule, "selection_rule"), tuple(PostingRowResult.from_json(item) for item in value["candidate_rows"]), tuple(AssessmentResult.from_json(item) for item in value["assessments"]), tuple(NotAssessedRow.from_json(item) for item in value["not_assessed"]), _strings(value["proposal_revision_refs"], "proposal_revision_refs", allow_empty=True), _enum(value["model_target"], ModelTarget, "model_target"), Producer.from_json(value["producer"]), usage, tuple(FailureRow.from_json(item) for item in value["failures"]))
        selected_urls = tuple(item.normalized_url for item in result.selected_postings)
        candidate_urls = {item.posting.normalized_url for item in result.candidate_rows}
        if len(selected_urls) != len(set(selected_urls)):
            _fail("invalid_value", "assess_output selected postings must be unique")
        if len(selected_urls) > result.selection_cap:
            _fail("invalid_value", "assess_output selected postings exceed selection_cap")
        if any(not item.role_match for item in result.selected_postings):
            _fail("invalid_value", "assess_output selected postings must match the role filter")
        if not set(selected_urls) <= candidate_urls:
            _fail("invalid_value", "assess_output selected postings must be candidate rows")
        candidate_by_url = {item.posting.normalized_url: item.posting for item in result.candidate_rows}
        if any(candidate_by_url[item.normalized_url].content_sha256 != item.content_sha256 for item in result.selected_postings):
            _fail("invalid_value", "assess_output selected posting content digest differs from candidate row")
        _validate_assessment_partition(result.candidate_rows, result.assessments, result.not_assessed)
        return result


def _validate_assessment_partition(
    candidates: tuple[PostingRowResult, ...],
    assessments: tuple[AssessmentResult, ...],
    not_assessed: tuple[NotAssessedRow, ...],
) -> None:
    candidate_urls = tuple(item.posting.normalized_url for item in candidates)
    if len(candidate_urls) != len(set(candidate_urls)):
        _fail("invalid_value", "candidate rows must be unique by normalized_url")
    assessed_urls = tuple(item.posting.normalized_url for item in assessments)
    not_urls = tuple(item.posting.normalized_url for item in not_assessed)
    if len(assessed_urls) != len(set(assessed_urls)) or len(not_urls) != len(set(not_urls)):
        _fail("invalid_value", "assessed and not-assessed rows must be unique")
    if set(assessed_urls) & set(not_urls):
        _fail("invalid_value", "a posting cannot be both assessed and not-assessed")
    if set(assessed_urls) | set(not_urls) != set(candidate_urls):
        _fail("invalid_value", "every candidate row must be assessed or not-assessed")
    candidate_by_url = {item.posting.normalized_url: item.posting for item in candidates}
    for assessment in assessments:
        source = candidate_by_url[assessment.posting.normalized_url]
        if source.content_sha256 != assessment.posting.content_sha256:
            _fail("invalid_value", "assessment posting content digest differs from candidate row")


@dataclass(frozen=True)
class PresentInput(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-present-input:1"
    batch_ref: str
    assessment_ref: str | None
    node_receipts: tuple[NodeReceipt, ...]

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "batch_ref": self.batch_ref, "assessment_ref": self.assessment_ref, "node_receipts": [item.to_json() for item in self.node_receipts]}

    @classmethod
    def from_json(cls, obj: object) -> "PresentInput":
        value = _object(obj, ("schema_version", "batch_ref", "assessment_ref", "node_receipts"), "present_input")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "present_input.schema_version is unsupported")
        if type(value["node_receipts"]) is not list:
            _fail("wrong_type", "node_receipts must be an array")
        return cls(_string(value["batch_ref"], "batch_ref"), _optional_string(value["assessment_ref"], "assessment_ref"), tuple(NodeReceipt.from_json(item) for item in value["node_receipts"]))


@dataclass(frozen=True)
class PresentPayload(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-present-payload:1"
    run_id: str
    config: FindJobsConfig
    pinned_resume: PinnedResume | None
    rows: tuple[PostingRowResult, ...]
    failures: tuple[FailureRow, ...]
    assessments: tuple[AssessmentResult, ...]
    not_assessed: tuple[NotAssessedRow, ...]
    node_receipts: tuple[NodeReceipt, ...]
    status: AggregateStatus

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "run_id": self.run_id, "config": self.config.to_json(), "pinned_resume": self.pinned_resume.to_json() if self.pinned_resume is not None else None, "rows": [item.to_json() for item in self.rows], "failures": [item.to_json() for item in self.failures], "assessments": [item.to_json() for item in self.assessments], "not_assessed": [item.to_json() for item in self.not_assessed], "node_receipts": [item.to_json() for item in self.node_receipts], "status": _json_enum(self.status)}

    @classmethod
    def from_json(cls, obj: object) -> "PresentPayload":
        value = _object(obj, ("schema_version", "run_id", "config", "pinned_resume", "rows", "failures", "assessments", "not_assessed", "node_receipts", "status"), "present_payload")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "present_payload.schema_version is unsupported")
        for field in ("rows", "failures", "assessments", "not_assessed", "node_receipts"):
            if type(value[field]) is not list:
                _fail("wrong_type", f"present_payload.{field} must be an array")
        result = cls(_string(value["run_id"], "run_id"), FindJobsConfig.from_json(value["config"]), None if value["pinned_resume"] is None else PinnedResume.from_json(value["pinned_resume"]), tuple(PostingRowResult.from_json(item) for item in value["rows"]), tuple(FailureRow.from_json(item) for item in value["failures"]), tuple(AssessmentResult.from_json(item) for item in value["assessments"]), tuple(NotAssessedRow.from_json(item) for item in value["not_assessed"]), tuple(NodeReceipt.from_json(item) for item in value["node_receipts"]), _enum(value["status"], AggregateStatus, "status"))
        if result.node_receipts and result.status.value != aggregate_status(item.status for item in result.node_receipts):
            _fail("invalid_value", "present_payload status must agree with node receipt statuses")
        _validate_assessment_partition(result.rows, result.assessments, result.not_assessed)
        return result


@dataclass(frozen=True)
class PresentOutput(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-present-output:1"
    payload: PresentPayload
    aggregate_status: AggregateStatus

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "payload": self.payload.to_json(), "aggregate_status": _json_enum(self.aggregate_status)}

    @classmethod
    def from_json(cls, obj: object) -> "PresentOutput":
        value = _object(obj, ("schema_version", "payload", "aggregate_status"), "present_output")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "present_output.schema_version is unsupported")
        payload = PresentPayload.from_json(value["payload"])
        aggregate = _enum(value["aggregate_status"], AggregateStatus, "aggregate_status")
        if payload.status != aggregate:
            _fail("invalid_value", "present_output aggregate_status must agree with payload.status")
        return cls(payload, aggregate)


def aggregate_status(statuses: Iterable[NodeStatus | str]) -> str:
    """Aggregate node statuses using the frozen D4 precedence.

    ``interrupted`` is intentionally accepted only as a run-level aggregate;
    it is not a status that can be projected into ``goal_details``.
    READY is pending work, while waiting-for-gate and verifying are active
    work.  An all-complete graph is the sole path to ``succeeded``.
    """

    values = []
    for index, status in enumerate(statuses):
        if isinstance(status, NodeStatus):
            values.append(status.value)
        elif type(status) is str:
            try:
                values.append(NodeStatus(status).value)
            except ValueError:
                _fail("bad_enum", f"statuses[{index}] is unsupported")
        else:
            _fail("wrong_type", f"statuses[{index}] must be a node status")
    if not values:
        return "pending"
    if all(value == NodeStatus.COMPLETE.value for value in values):
        return AggregateStatus.SUCCEEDED.value
    if NodeStatus.INTERRUPTED.value in values:
        return AggregateStatus.INTERRUPTED.value
    if NodeStatus.FAILED.value in values:
        return AggregateStatus.FAILED.value
    if NodeStatus.BLOCKED.value in values:
        return AggregateStatus.BLOCKED.value
    if NodeStatus.CANCELLED.value in values:
        return AggregateStatus.CANCELLED.value
    if any(value in {NodeStatus.RUNNING.value, NodeStatus.WAITING_FOR_GATE.value, NodeStatus.VERIFYING.value} for value in values):
        return AggregateStatus.RUNNING.value
    return AggregateStatus.PENDING.value


class AcquireNodeCallable(Protocol):
    def __call__(self, context: NodeContext, input: AcquireInput) -> AcquireOutput: ...


class AssessNodeCallable(Protocol):
    def __call__(self, context: NodeContext, input: AssessInput) -> AssessOutput: ...


class PresentNodeCallable(Protocol):
    def __call__(self, context: NodeContext, input: PresentInput) -> PresentOutput: ...


NodeCallable = (
    Callable[[NodeContext, AcquireInput], AcquireOutput]
    | Callable[[NodeContext, AssessInput], AssessOutput]
    | Callable[[NodeContext, PresentInput], PresentOutput]
)

ACQUIRE_CAPABILITY = "scout.find_jobs.acquire"
ASSESS_CAPABILITY = "scout.find_jobs.assess"
PRESENT_CAPABILITY = "scout.find_jobs.present"
ACQUIRE_CAPABILITY_ID = ACQUIRE_CAPABILITY
ASSESS_CAPABILITY_ID = ASSESS_CAPABILITY
PRESENT_CAPABILITY_ID = PRESENT_CAPABILITY

ACQUIRE_EFFECTS = frozenset({"network_read", "credential_use", "write_workpad"})
ASSESS_EFFECTS = frozenset({"network_read", "credential_use", "write_workpad"})
ASSESS_LOCAL_EFFECTS = frozenset({"write_workpad"})
PRESENT_EFFECTS = frozenset({"write_workpad"})
ACQUIRE_DECLARED_EFFECTS = ACQUIRE_EFFECTS
ASSESS_DECLARED_EFFECTS = ASSESS_EFFECTS
PRESENT_DECLARED_EFFECTS = PRESENT_EFFECTS


class ExaSearchClient(Protocol):
    def search(
        self,
        client: "httpx.Client",
        config: FindJobsConfig,
        *,
        home_root: Path | None = None,
    ) -> tuple[PostingRow, ...]: ...


class ATSBoardClient(Protocol):
    def list_board(self, client: "httpx.Client", provider: str, board_token: str, config: FindJobsConfig) -> tuple[PostingRow, ...]: ...


class URLChangeDetectionClient(Protocol):
    def normalize_url(self, url: str) -> str: ...
    def diff_url_sets(self, previous: Mapping[str, str | None], current: Mapping[str, str | None]) -> URLSetDiff: ...
    def content_hash(self, content: bytes) -> str: ...


class WatchlistClient(Protocol):
    def add_to_watchlist(self, entry: WatchlistEntry) -> WatchlistEntry: ...


def parse_board_url(url: str) -> tuple[str, str] | None:
    """Extract an ATS provider/token from one of the four admitted domains."""

    if type(url) is not str:
        return None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or host is None or parsed.username is not None or parsed.password is not None:
        return None
    host = host.lower().rstrip(".")
    provider_by_host = {
        "boards.greenhouse.io": "greenhouse",
        "job-boards.greenhouse.io": "greenhouse",
        "jobs.lever.co": "lever",
        "jobs.ashbyhq.com": "ashby",
    }
    provider = provider_by_host.get(host)
    if provider is None:
        return None
    parts = tuple(part for part in parsed.path.split("/") if part)
    if any(part in {".", ".."} for part in parts):
        return None
    if parts and parts[0].lower() == "embed":
        if parts != ("embed", "job_board"):
            return None
        token = dict(parse_qsl(parsed.query, keep_blank_values=True)).get("for")
        if not token:
            return None
        return provider, token
    if not parts:
        return None
    return provider, parts[0]


def normalize_url(url: str) -> str:
    """Normalize public posting URLs and remove common tracking parameters."""

    if type(url) is not str:
        _fail("wrong_type", "url must be a string")
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
    except ValueError as exc:
        _fail("invalid_value", "url is malformed")
        raise AssertionError("unreachable") from exc
    if parsed.scheme not in {"http", "https"} or host is None:
        _fail("invalid_value", "url must use http or https")
    if parsed.username is not None or parsed.password is not None:
        _fail("invalid_value", "url userinfo is not allowed")
    try:
        port = parsed.port
    except ValueError as exc:
        _fail("invalid_value", "url has an invalid port")
        raise AssertionError("unreachable") from exc
    if port is not None and not (1 <= port <= 65535):
        _fail("invalid_value", "url has an invalid port")
    hostname = host.lower().rstrip(".")
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if port is not None and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        netloc = f"{netloc}:{port}"
    filtered = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in _URL_TRACKING_KEYS]
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), netloc, path, urlencode(sorted(filtered)), ""))


def _url_observations(value: Mapping[str, str | None] | Iterable[URLObservation], name: str) -> dict[str, URLObservation]:
    if isinstance(value, Mapping):
        items: Iterable[tuple[str, str | None]] = value.items()
    else:
        def observations() -> Iterable[tuple[str, str | None]]:
            for index, item in enumerate(value):
                if isinstance(item, URLObservation):
                    yield item.url, item.content_sha256
                elif type(item) is tuple and len(item) == 2:
                    raw_url, digest = item
                    yield _string(raw_url, f"{name}[{index}].url"), digest  # type: ignore[arg-type]
                else:
                    _fail("wrong_type", f"{name}[{index}] must be a URL observation")
        items = observations()
    result: dict[str, URLObservation] = {}
    for raw_url, digest in items:
        normalized = normalize_url(raw_url)
        content_sha256 = _optional_digest(digest, f"{name}[{raw_url}]")
        current = URLObservation(normalized, content_sha256)
        previous = result.get(normalized)
        if previous is not None and previous.content_sha256 != current.content_sha256:
            _fail("invalid_value", f"{name} contains conflicting observations for one URL")
        result[normalized] = current
    return result


def diff_url_sets(previous: Mapping[str, str | None] | Iterable[URLObservation], current: Mapping[str, str | None] | Iterable[URLObservation]) -> URLSetDiff:
    """Diff normalized URL-to-content-digest observations.

    Equal URLs with differing content digests are ``edited`` rather than
    appearing in both ``removed`` and ``added``; all four categories are
    therefore mutually exclusive.
    """

    previous_by_url = _url_observations(previous, "previous")
    current_by_url = _url_observations(current, "current")
    added = tuple(current_by_url[url] for url in sorted(current_by_url.keys() - previous_by_url.keys()))
    removed = tuple(previous_by_url[url] for url in sorted(previous_by_url.keys() - current_by_url.keys()))
    unchanged = tuple(current_by_url[url] for url in sorted(current_by_url.keys() & previous_by_url.keys()) if current_by_url[url].content_sha256 == previous_by_url[url].content_sha256)
    edited = tuple(EditedURL(url, previous_by_url[url].content_sha256, current_by_url[url].content_sha256) for url in sorted(current_by_url.keys() & previous_by_url.keys()) if current_by_url[url].content_sha256 != previous_by_url[url].content_sha256)
    return URLSetDiff(added, removed, unchanged, edited)


def content_hash(content: bytes) -> str:
    if type(content) is not bytes:
        _fail("wrong_type", "content must be exact bytes")
    return digest_imported_bytes(content)


@dataclass(frozen=True)
class ConsentActor(_Contract):
    kind: str
    id: str

    def to_json(self) -> dict[str, object]:
        return {"kind": self.kind, "id": self.id}

    @classmethod
    def from_json(cls, obj: object) -> "ConsentActor":
        value = _object(obj, ("kind", "id"), "actor")
        if value["kind"] != "operator" or value["id"] != "local-user":
            _fail("bad_enum", "actor must be the local operator")
        return cls("operator", "local-user")


@dataclass(frozen=True)
class UIConsentEnvelope(_Contract):
    """D5's closed local-UI consent envelope."""

    schema_version: ClassVar[str] = "1.0"
    kind: ClassVar[str] = "operator_run_consent"
    action: ClassVar[str] = "run"
    actor: ConsentActor
    source: ClassVar[str] = "direct_local_ui_confirm"
    invocation_id: str
    occurrence_id: str

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "kind": self.kind, "action": self.action, "actor": self.actor.to_json(), "source": self.source, "invocation_id": self.invocation_id, "occurrence_id": self.occurrence_id}

    @classmethod
    def from_json(cls, obj: object) -> "UIConsentEnvelope":
        value = _object(obj, ("schema_version", "kind", "action", "actor", "source", "invocation_id", "occurrence_id"), "ui_consent")
        if value["schema_version"] != cls.schema_version or value["kind"] != cls.kind or value["action"] != cls.action or value["source"] != cls.source:
            _fail("bad_enum", "ui_consent contains unsupported fixed values")
        return cls(ConsentActor.from_json(value["actor"]), _string(value["invocation_id"], "invocation_id"), _string(value["occurrence_id"], "occurrence_id"))


@dataclass(frozen=True)
class ConfigRequest(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-config-request:1"

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version}

    @classmethod
    def from_json(cls, obj: object) -> "ConfigRequest":
        value = _object(obj, ("schema_version",), "config_request")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "config_request.schema_version is unsupported")
        return cls()


@dataclass(frozen=True)
class ConfigResponse(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-config-response:1"
    config: FindJobsConfig
    resume_preview: PinnedResume | None
    config_digest: str

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "config": self.config.to_json(), "resume_preview": self.resume_preview.to_json() if self.resume_preview else None, "config_digest": self.config_digest}

    @classmethod
    def from_json(cls, obj: object) -> "ConfigResponse":
        value = _object(obj, ("schema_version", "config", "resume_preview", "config_digest"), "config_response")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "config_response.schema_version is unsupported")
        return cls(FindJobsConfig.from_json(value["config"]), None if value["resume_preview"] is None else PinnedResume.from_json(value["resume_preview"]), _digest_value(value["config_digest"], "config_digest"))


@dataclass(frozen=True)
class RunRequest(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-run-request:1"
    consent: UIConsentEnvelope
    config_digest: str
    selection_cap: int
    selection_rule: SelectionRule
    model_target: ModelTarget

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "consent": self.consent.to_json(), "config_digest": self.config_digest, "selection_cap": self.selection_cap, "selection_rule": _json_enum(self.selection_rule), "model_target": _json_enum(self.model_target)}

    @classmethod
    def from_json(cls, obj: object) -> "RunRequest":
        value = _object(obj, ("schema_version", "consent", "config_digest", "selection_cap", "selection_rule", "model_target"), "run_request")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "run_request.schema_version is unsupported")
        return cls(UIConsentEnvelope.from_json(value["consent"]), _digest_value(value["config_digest"], "config_digest"), _integer(value["selection_cap"], "selection_cap", minimum=1, maximum=50), _enum(value["selection_rule"], SelectionRule, "selection_rule"), _enum(value["model_target"], ModelTarget, "model_target"))


@dataclass(frozen=True)
class RunResponse(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-run-response:1"
    run_id: str
    status: AggregateStatus
    node_receipts: tuple[NodeReceipt, ...]

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "run_id": self.run_id, "status": _json_enum(self.status), "node_receipts": [item.to_json() for item in self.node_receipts]}

    @classmethod
    def from_json(cls, obj: object) -> "RunResponse":
        value = _object(obj, ("schema_version", "run_id", "status", "node_receipts"), "run_response")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "run_response.schema_version is unsupported")
        if type(value["node_receipts"]) is not list:
            _fail("wrong_type", "node_receipts must be an array")
        result = cls(_string(value["run_id"], "run_id"), _enum(value["status"], AggregateStatus, "status"), tuple(NodeReceipt.from_json(item) for item in value["node_receipts"]))
        if result.node_receipts and result.status.value != aggregate_status(item.status for item in result.node_receipts):
            _fail("invalid_value", "run_response status must agree with node receipt statuses")
        return result


@dataclass(frozen=True)
class RunLookupRequest(_Contract):
    run_id: str

    def to_json(self) -> dict[str, object]:
        return {"run_id": self.run_id}

    @classmethod
    def from_json(cls, obj: object) -> "RunLookupRequest":
        value = _object(obj, ("run_id",), "run_lookup_request")
        return cls(_string(value["run_id"], "run_id"))


@dataclass(frozen=True)
class RunStatusResponse(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-run-status-response:1"
    run_id: str
    status: AggregateStatus
    node_receipts: tuple[NodeReceipt, ...]

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "run_id": self.run_id, "status": _json_enum(self.status), "node_receipts": [item.to_json() for item in self.node_receipts]}

    @classmethod
    def from_json(cls, obj: object) -> "RunStatusResponse":
        value = _object(obj, ("schema_version", "run_id", "status", "node_receipts"), "run_status_response")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "run_status_response.schema_version is unsupported")
        if type(value["node_receipts"]) is not list:
            _fail("wrong_type", "node_receipts must be an array")
        result = cls(_string(value["run_id"], "run_id"), _enum(value["status"], AggregateStatus, "status"), tuple(NodeReceipt.from_json(item) for item in value["node_receipts"]))
        if result.node_receipts and result.status.value != aggregate_status(item.status for item in result.node_receipts):
            _fail("invalid_value", "run_status_response status must agree with node receipt statuses")
        return result


@dataclass(frozen=True)
class RunResultsResponse(_Contract):
    schema_version: ClassVar[str] = "scout-find-jobs-run-results-response:1"
    run_id: str
    payload: PresentPayload

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "run_id": self.run_id, "payload": self.payload.to_json()}

    @classmethod
    def from_json(cls, obj: object) -> "RunResultsResponse":
        value = _object(obj, ("schema_version", "run_id", "payload"), "run_results_response")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "run_results_response.schema_version is unsupported")
        run_id = _string(value["run_id"], "run_id")
        payload = PresentPayload.from_json(value["payload"])
        if payload.run_id != run_id:
            _fail("invalid_value", "run_results_response run_id must agree with payload.run_id")
        return cls(run_id, payload)


class RouteSpec(NamedTuple):
    method: str
    path: str
    request_type: type[Any]
    response_type: type[Any]
    status_codes: tuple[int, ...]


API_BIND = ("127.0.0.1", 8765)
ROUTES = (
    RouteSpec("GET", "/api/config", ConfigRequest, ConfigResponse, (200, 404, 422)),
    RouteSpec("POST", "/api/run", RunRequest, RunResponse, (202, 400, 403, 409, 422, 504)),
    RouteSpec("GET", "/api/runs/{run_id}", RunLookupRequest, RunStatusResponse, (200, 404)),
    RouteSpec("GET", "/api/runs/{run_id}/results", RunLookupRequest, RunResultsResponse, (200, 404)),
)


@dataclass(frozen=True)
class WatchlistFixture(_Contract):
    schema_version: ClassVar[str] = "fixture-watchlist-v1"
    entries: tuple[WatchlistEntry, ...]

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "entries": [item.to_json() for item in self.entries]}

    @classmethod
    def from_json(cls, obj: object) -> "WatchlistFixture":
        value = _object(obj, ("schema_version", "entries"), "watchlist_fixture")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "watchlist_fixture.schema_version is unsupported")
        if type(value["entries"]) is not list:
            _fail("wrong_type", "watchlist_fixture.entries must be an array")
        return cls(tuple(WatchlistEntry.from_json(item) for item in value["entries"]))


@dataclass(frozen=True)
class NodeReceiptFixture(_Contract):
    schema_version: ClassVar[str] = "fixture-node-receipts-v1"
    receipts: tuple[NodeReceipt, ...]

    def to_json(self) -> dict[str, object]:
        return {"schema_version": self.schema_version, "receipts": [item.to_json() for item in self.receipts]}

    @classmethod
    def from_json(cls, obj: object) -> "NodeReceiptFixture":
        value = _object(obj, ("schema_version", "receipts"), "node_receipts_fixture")
        if value["schema_version"] != cls.schema_version:
            _fail("bad_enum", "node_receipts_fixture.schema_version is unsupported")
        if type(value["receipts"]) is not list:
            _fail("wrong_type", "node_receipts_fixture.receipts must be an array")
        return cls(tuple(NodeReceipt.from_json(item) for item in value["receipts"]))


__all__ = [
    "ACQUIRE_CAPABILITY", "ACQUIRE_CAPABILITY_ID", "ACQUIRE_DECLARED_EFFECTS", "ACQUIRE_EFFECTS",
    "ASSESS_CAPABILITY", "ASSESS_CAPABILITY_ID", "ASSESS_DECLARED_EFFECTS", "ASSESS_EFFECTS", "ASSESS_LOCAL_EFFECTS",
    "API_BIND", "ATSBoardClient", "ATSProvider", "AcquireInput", "AcquireNodeCallable", "AcquireOutput", "ExaSearchClient",
    "AggregateStatus", "ArtifactRef", "AssessmentQuestion", "AssessmentResult", "AssessInput", "AssessNodeCallable", "AssessOutput", "ConsentActor", "ConfigRequest", "ConfigResponse",
    "DEFAULT_MAX_AGE_DAYS", "DropCount", "MAX_AGE_DAYS_MAXIMUM",
    "EditedURL", "FindJobsConfig", "FindJobsContractError", "FailureRow", "FindJobsRunInput", "FindJobsConfig", "GoalError", "GoalStatus", "MatrixStatus", "ModelTarget", "NodeContext",
    "NodeFailure", "NodeReceipt", "NodeReceiptFixture", "NodeReceiptStatus", "NodeStatus", "NodeCallable", "NormalizedPostingRow", "NormalizedPublicPostingRow", "NotAssessedReason",
    "NotAssessedRow", "PRESENT_CAPABILITY", "PRESENT_CAPABILITY_ID", "PRESENT_DECLARED_EFFECTS", "PRESENT_EFFECTS", "PresentInput",
    "PresentNodeCallable", "PresentOutput", "PresentPayload", "PinnedResume", "PostingRow", "PostingRowResult", "Producer", "ProfileRef", "ROUTES",
    "ProgressStatus", "RequirementClass", "RequirementMatrixRow", "RowOutcome", "RouteSpec", "RunLookupRequest", "RunRequest", "RunResponse", "RunResultsResponse", "RunStatusResponse",
    "SelectedPosting", "SelectionReason", "SelectionReasonCode", "SelectionRule", "SourceKind", "SourceToggles", "SponsorshipStatus", "UIConsentEnvelope", "URLChangeDetectionClient", "URLObservation", "URLSetDiff", "Verdict",
    "UsageBlock", "WatchlistClient", "WatchlistEntry", "WatchlistFixture", "WatchlistFirstSeen", "aggregate_status", "content_hash",
    "diff_url_sets", "normalize_url", "parse_board_url",
]
