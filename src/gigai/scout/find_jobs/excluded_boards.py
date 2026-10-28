"""The shipped list of boards GigAI does not read (``data/excluded-boards-v1.json``).

A board lands here when its employer's terms forbid automated access (``reason`` ``terms``) or its ``robots.txt``
disallows the public job path (``robots``); the review that decided it is cited on every row (``evidence``, ``since``,
``decided_by``). The list is the one place the product consults: seeding skips these boards
(``watchlist.catalog_records_for_prefs``), ``watchlist add <url>`` refuses them (``excluded_board``), and
``sources status`` names them (``skipped:`` lines), so an excluded employer is visible, never silently missing.

A row's ``token`` is either one board (``relx.wd3.RiskSolutions``) or a whole Workday tenant
(``cvshealth.wd1.*``: every site on that tenant). Matching lower-cases the tenant and pod (the host), as
``providers.py`` does for the URL; the site keeps its case, as the catalog's tokens do.

The file is read once per process (``lru_cache``) and FAILS CLOSED: a missing resource, a malformed row, an unknown
provider or reason raises ``ExcludedBoardsError`` rather than reading a board the review said not to read.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from importlib import resources
import json

from gigai.scout.find_jobs.contracts import ATSProvider

#: Package-relative path of the shipped resource, under ``gigai.scout``.
EXCLUDED_BOARDS_RESOURCE = "data/excluded-boards-v1.json"
EXCLUDED_BOARDS_SCHEMA = "scout-excluded-boards:1"
EXCLUDED_REASONS = frozenset({"terms", "robots"})

#: Longest ``evidence_short`` (one status line per excluded board).
EVIDENCE_SHORT_CHARS = 120


class ExcludedBoardsError(ValueError):
    """The shipped list is missing or malformed (the product fails closed)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExcludedBoard:
    provider: ATSProvider
    token: str
    company: str
    reason: str
    evidence: str
    since: str
    decided_by: str

    @property
    def tenant_wide(self) -> bool:
        return self.token.endswith(".*")

    def matches(self, provider: ATSProvider, token: str) -> bool:
        if provider is not self.provider:
            return False
        if self.tenant_wide:
            prefix = self.token[:-1].lower()  # ``<tenant>.<pod>.``
            return token.lower().startswith(prefix)
        own_host, _, own_site = self.token.rpartition(".")
        host, _, site = token.rpartition(".")
        return own_host.lower() == host.lower() and own_site == site

    @property
    def evidence_short(self) -> str:
        """The evidence's first clause for a one-line status (``skipped: terms - <company> (<evidence short>)``):
        the trailing citation ``(workers/..., date)`` and anything after the first ``;`` dropped, at most
        :data:`EVIDENCE_SHORT_CHARS`."""

        text = self.evidence.strip()
        if text.endswith(")") and " (" in text:
            text = text[: text.rindex(" (")].rstrip()
        text = text.split(";", 1)[0].strip()
        if len(text) > EVIDENCE_SHORT_CHARS:
            text = text[: EVIDENCE_SHORT_CHARS - 3].rstrip() + "..."
        return text

    def to_json(self) -> dict[str, str]:
        return {
            "provider": self.provider.value,
            "token": self.token,
            "company": self.company,
            "reason": self.reason,
            "evidence": self.evidence,
            "evidence_short": self.evidence_short,
            "since": self.since,
            "decided_by": self.decided_by,
        }


def _required_str(raw: dict, key: str, index: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ExcludedBoardsError("malformed_row", f"{EXCLUDED_BOARDS_RESOURCE} row {index}: {key!r} must be a non-empty string")
    return value.strip()


def parse_excluded_boards(payload: object) -> tuple[ExcludedBoard, ...]:
    """The resource's rows as records; any malformed row raises (fail closed)."""

    if not isinstance(payload, dict) or payload.get("schema_version") != EXCLUDED_BOARDS_SCHEMA:
        raise ExcludedBoardsError("bad_schema", f"{EXCLUDED_BOARDS_RESOURCE} is not a {EXCLUDED_BOARDS_SCHEMA} document")
    rows = payload.get("boards")
    if not isinstance(rows, list):
        raise ExcludedBoardsError("bad_schema", f"{EXCLUDED_BOARDS_RESOURCE}: 'boards' must be a list")
    boards: list[ExcludedBoard] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise ExcludedBoardsError("malformed_row", f"{EXCLUDED_BOARDS_RESOURCE} row {index} is not an object")
        provider_name = _required_str(raw, "provider", index)
        try:
            provider = ATSProvider(provider_name)
        except ValueError:
            raise ExcludedBoardsError("unknown_provider", f"{EXCLUDED_BOARDS_RESOURCE} row {index}: unknown provider {provider_name!r}") from None
        reason = _required_str(raw, "reason", index)
        if reason not in EXCLUDED_REASONS:
            raise ExcludedBoardsError("unknown_reason", f"{EXCLUDED_BOARDS_RESOURCE} row {index}: unknown reason {reason!r} (one of {sorted(EXCLUDED_REASONS)})")
        token = _required_str(raw, "token", index)
        if " " in token or token.count(".") < 2:
            raise ExcludedBoardsError("malformed_row", f"{EXCLUDED_BOARDS_RESOURCE} row {index}: token {token!r} is not <tenant>.<pod>.<site> or <tenant>.<pod>.*")
        boards.append(
            ExcludedBoard(
                provider=provider,
                token=token,
                company=_required_str(raw, "company", index),
                reason=reason,
                evidence=_required_str(raw, "evidence", index),
                since=_required_str(raw, "since", index),
                decided_by=_required_str(raw, "decided_by", index),
            )
        )
    return tuple(boards)


def read_excluded_boards_resource_bytes() -> bytes:
    """The shipped resource, exactly as packaged (``importlib.resources``)."""

    resource = resources.files("gigai.scout").joinpath(EXCLUDED_BOARDS_RESOURCE)
    if not resource.is_file():
        raise ExcludedBoardsError(
            "resource_missing",
            f"gigai.scout is missing {EXCLUDED_BOARDS_RESOURCE}: the excluded-boards list was not shipped "
            "(check [tool.setuptools.package-data] 'gigai.scout' in pyproject.toml)",
        )
    return resource.read_bytes()


@lru_cache(maxsize=1)
def load_excluded_boards() -> tuple[ExcludedBoard, ...]:
    """The shipped list, read once per process."""

    try:
        payload = json.loads(read_excluded_boards_resource_bytes().decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ExcludedBoardsError("bad_json", f"{EXCLUDED_BOARDS_RESOURCE} is not valid JSON: {error}") from error
    return parse_excluded_boards(payload)


def find_excluded(provider: ATSProvider, token: str) -> ExcludedBoard | None:
    """The row that covers ``(provider, token)``, or ``None`` when the board may be read."""

    for board in load_excluded_boards():
        if board.matches(provider, token):
            return board
    return None


def excluded_boards_json() -> list[dict[str, str]]:
    """The shipped rows for the status snapshot (``sources_update.read_status``) and the UI."""

    return [board.to_json() for board in load_excluded_boards()]


__all__ = [
    "EXCLUDED_BOARDS_RESOURCE",
    "EXCLUDED_BOARDS_SCHEMA",
    "EXCLUDED_REASONS",
    "ExcludedBoard",
    "ExcludedBoardsError",
    "excluded_boards_json",
    "find_excluded",
    "load_excluded_boards",
    "parse_excluded_boards",
    "read_excluded_boards_resource_bytes",
]
