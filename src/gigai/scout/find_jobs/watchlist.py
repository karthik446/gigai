"""Journal-authoritative Scout ATS watchlist records."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import re
import uuid

from ...canonical import canonical_json_bytes, digest_imported_bytes, parse_json_bytes
from ...journal import JournalArtifact, JournalTransition, run_with_journal_writer
from .contracts import (
    ATSProvider,
    FindJobsContractError,
    SourceKind,
    WatchlistClient,
    WatchlistEntry,
    WatchlistFirstSeen,
    normalize_url,
    parse_board_url,
)
from ...workpad import ResolvedWorkpad, resolve_workpad


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _resolved(home_root: Path, target: Path, gig_id: str | None) -> ResolvedWorkpad:
    if isinstance(target, ResolvedWorkpad):
        return target
    return resolve_workpad(
        home_root=home_root,
        requested_target=target,
        gig_id=gig_id,
        allow_semantic_state=True,
    )


def _receipt_path(operation: str, key: str) -> str:
    digest = digest_imported_bytes(key.encode("utf-8")).removeprefix("sha256:")
    return f"records/operations/{operation}-{digest}.json"


def _ref(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": "application/json",
        "size_bytes": len(data),
    }


def _snapshot_entries(writer: object) -> tuple[WatchlistEntry, ...]:
    snapshot = writer.snapshot(("records/scout-watchlist/",))  # type: ignore[attr-defined]
    entries: list[WatchlistEntry] = []
    for path, data in sorted(snapshot.artifacts.items()):
        if not path.endswith(".json"):
            continue
        try:
            entries.append(WatchlistEntry.from_json(parse_json_bytes(data)))
        except Exception:
            continue
    return tuple(entries)


def list_active(
    home_root: Path,
    target: Path,
    gig_id: str | None = None,
) -> tuple[WatchlistEntry, ...]:
    """Return active entries from the authenticated journal snapshot."""

    resolved = _resolved(home_root, target, gig_id)

    def read(writer: object) -> tuple[WatchlistEntry, ...]:
        return tuple(item for item in _snapshot_entries(writer) if item.state == "active")

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=read,
    )


class JournalWatchlistClient:
    """A ``WatchlistClient`` bound to one authenticated workpad."""

    def __init__(self, home_root: Path, target: Path, gig_id: str | None = None) -> None:
        self._resolved = _resolved(home_root, target, gig_id)

    def add_to_watchlist(self, entry: WatchlistEntry) -> WatchlistEntry:
        key = f"scout_watchlist:{entry.provider}:{entry.board_token}"
        record_path = f"records/scout-watchlist/{entry.watchlist_id}.json"
        record_bytes = canonical_json_bytes(entry.to_json())
        receipt_path = _receipt_path("scout_watchlist_add", key)
        payload = {"provider": entry.provider.value, "board_token": entry.board_token}
        payload_sha = digest_imported_bytes(canonical_json_bytes(payload))

        def publish(writer: object) -> WatchlistEntry:
            snapshot = writer.snapshot(("records/",))  # type: ignore[attr-defined]
            for data in snapshot.artifacts.values():
                try:
                    receipt = parse_json_bytes(data)
                except Exception:
                    continue
                if not isinstance(receipt, dict) or receipt.get("operation_key") != key:
                    continue
                refs = receipt.get("artifact_refs")
                if isinstance(refs, list):
                    paths = [item.get("path") for item in refs if isinstance(item, dict)]
                    if record_path in paths and record_path in snapshot.artifacts:
                        return WatchlistEntry.from_json(parse_json_bytes(snapshot.artifacts[record_path]))

            receipt = {
                "schema_version": "1.0",
                "operation_id": f"operation_{uuid.uuid4()}",
                "project_id": self._resolved.project_id,
                "gig_id": self._resolved.gig_id,
                "operation": "scout_watchlist_add",
                "operation_key": key,
                "payload_sha256": payload_sha,
                "outcome": "committed",
                "artifact_refs": [_ref(record_path, record_bytes)],
                "created_at": _now(),
            }
            receipt_bytes = canonical_json_bytes(receipt)
            artifacts = (
                JournalArtifact(record_path, record_bytes),
                JournalArtifact(receipt_path, receipt_bytes),
            )
            metadata = {
                "project_id": self._resolved.project_id,
                "gig_id": self._resolved.gig_id,
                "operation": "scout_watchlist_add",
                "operation_key": key,
                "artifact_refs": [_ref(path, data) for path, data in ((record_path, record_bytes), (receipt_path, receipt_bytes))],
            }
            writer.record(  # type: ignore[attr-defined]
                JournalTransition(
                    f"handoff_{uuid.uuid4()}",
                    "scout_public_acquisition_progress",
                    "Committed Scout ATS watchlist entry.",
                    artifacts,
                    metadata,
                )
            )
            return entry

        return run_with_journal_writer(
            workpad=self._resolved.path,
            project_id=self._resolved.project_id,
            gig_id=self._resolved.gig_id,
            operation=publish,
        )


def add_to_watchlist(
    entry: WatchlistEntry,
    home_root: Path,
    target: Path,
    gig_id: str | None = None,
) -> WatchlistEntry:
    """Persist one entry, idempotently keyed by provider and board token."""

    return JournalWatchlistClient(home_root, target, gig_id).add_to_watchlist(entry)


# --- Q1 (v0.1.9, SCOPE-ADD-2): "Add company" by board URL -----------------
#
# Region owned by packet Q1. Q2 adds its catalog *seeding* function to this
# same module below this region; the two share `JournalWatchlistClient` and
# nothing else.

# The `query_key`/`batch_id` an operator-added entry's `first_seen` carries.
# `WatchlistFirstSeen` was designed for entries acquire discovers through a
# source (`exa`/`ats`), so an operator's own URL is recorded as an ATS
# sighting of the board itself with these fixed markers -- distinguishable
# from a discovered entry (whose query_key is a real search query and whose
# batch_id is a real acquire batch) without widening the sealed contract.
OPERATOR_ADDED_QUERY_KEY = "operator_add"
OPERATOR_ADDED_BATCH_ID = "manual"

_PROVIDER_BY_NAME = {item.value: item for item in ATSProvider}


class WatchlistUrlError(FindJobsContractError):
    """A URL that cannot become a watchlist entry (typed, never a bare ValueError).

    ``code`` is one of:

    * ``unsupported_board_host`` -- parses as a URL but is not on one of the
      four admitted ATS hosts (``boards.greenhouse.io``,
      ``job-boards.greenhouse.io``, ``jobs.lever.co``, ``jobs.ashbyhq.com``),
      or names no board token on one of them.
    * ``invalid_value`` -- not a usable http(s) URL at all
      (``contracts.normalize_url``'s own refusal, re-raised under this type).
    """


def watchlist_entry_from_url(url: str, *, observed_at: str | None = None) -> WatchlistEntry:
    """Pure: a Greenhouse / Lever / Ashby board URL (or a job URL on those
    hosts) -> the ``WatchlistEntry`` it names. No I/O.

    ``contracts.parse_board_url`` is the ONLY host/token rule (the same one
    acquire's Exa path uses to admit a result), so a job URL like
    ``https://jobs.ashbyhq.com/kong/ea7b507b-...`` maps to the ``kong``
    board exactly as a bare ``https://jobs.ashbyhq.com/kong`` does; the
    entry's ``watchlist_id`` follows acquire's own
    ``scout_watchlist:<provider>:<token>`` convention
    (``market_acquisition._watchlist_add``) so an operator add and a later
    Exa discovery of the same board collapse onto one record. ``company`` is
    the board token, the same "board-token-as-company-name" fallback the
    ATS clients use (``ats_board_clients._company_from_token``).
    """

    if type(url) is not str or not url.strip():
        raise WatchlistUrlError("invalid_value", "url must be a non-empty string")
    candidate = url.strip()
    try:
        normalized = normalize_url(candidate)
    except FindJobsContractError as exc:
        raise WatchlistUrlError("invalid_value", f"url is not a usable http(s) URL: {exc}") from None
    parsed = parse_board_url(normalized)
    if parsed is None:
        raise WatchlistUrlError(
            "unsupported_board_host",
            "url must be a Greenhouse (boards.greenhouse.io / job-boards.greenhouse.io), "
            "Lever (jobs.lever.co) or Ashby (jobs.ashbyhq.com) board or job URL",
        )
    provider_name, board_token = parsed
    provider = _PROVIDER_BY_NAME[provider_name]
    return WatchlistEntry(
        watchlist_id=f"scout_watchlist:{provider.value}:{board_token}",
        provider=provider,
        board_token=board_token,
        company=board_token,
        state="active",
        first_seen=WatchlistFirstSeen(
            SourceKind.ATS,
            normalized,
            OPERATOR_ADDED_QUERY_KEY,
            OPERATOR_ADDED_BATCH_ID,
            observed_at if observed_at is not None else _now(),
        ),
    )


def add_company_from_url(
    url: str,
    home_root: Path,
    target: Path,
    gig_id: str | None = None,
) -> WatchlistEntry:
    """Add the board a Greenhouse / Lever / Ashby URL names to the watchlist.

    The ONE function behind ``gigai scout watchlist add <url>``, ``POST
    /api/watchlist`` and the UI's "Add company" form. Idempotent: the
    journal's ``scout_watchlist:<provider>:<token>`` receipt key
    (``JournalWatchlistClient.add_to_watchlist``) makes a second add of the
    same board return the ORIGINAL committed entry (its own ``first_seen``),
    never a second record. Raises ``WatchlistUrlError`` for any other host
    or an unusable URL, before touching the workpad at all.
    """

    entry = watchlist_entry_from_url(url)
    return JournalWatchlistClient(home_root, target, gig_id).add_to_watchlist(entry)


# --- Q2 (v0.1.9, SCOPE-ADD-2): seeding the watchlist from the catalog ------
#
# Region owned by packet Q2 (Q1's add-by-URL region sits above; the two
# share ``JournalWatchlistClient``/the module helpers and nothing else).
#
# Watchlist = catalog filtered by the user's preferences (countries, excluded
# companies) + whatever the user/discovery added; existing entries are never
# touched (a previously-added board stays even if the prefs would exclude it
# now -- removal is a separate, explicit action, never a side effect of
# seeding). Idempotent: a board already on the watchlist is skipped, and a
# call that adds nothing writes nothing. Journal-committed exactly like
# ``add_to_watchlist``: the same ``records/scout-watchlist/<id>.json`` record
# shape, one ``records/operations/scout_watchlist_seed-<digest>.json``
# receipt, but ONE transition for the whole batch (thousands of per-entry
# commits would make a first run over the full catalog take minutes).
#
# When it runs: at the start of every find-jobs acquire pass that has the ATS
# source on (``market_acquisition._seed_watchlist``), once prefs exist (no
# prefs -> no seeding, recorded as such). It is explicit, not silent: the
# result is written to the run's ``progress/watchlist-seed.json``, printed on
# the run's stderr, and each seeded entry carries ``catalog:<revision>`` in
# ``first_seen.query_key``. ``seed_watchlist_from_catalog`` is public so a CLI
# or API command can call it directly too.


@dataclass(frozen=True)
class WatchlistSeedResult:
    """What one seeding pass did; JSON-safe via :meth:`to_json`."""

    catalog_revision: str
    catalog_digest: str
    catalog_records: int
    eligible: int
    added: int
    already_present: int
    excluded_by_country: int
    excluded_by_company: int
    added_watchlist_ids: tuple[str, ...]
    receipt_path: str | None
    # catalog-repin amendment (orchestrator msg_b3ca2e58203a, 2026-09-25):
    # records the seed flagged ``staffing_suspect`` are kept in the catalog
    # but not seeded by default; counted here so the receipt says so.
    excluded_as_staffing_suspect: int = 0

    def to_json(self) -> dict[str, object]:
        return {
            "catalog_revision": self.catalog_revision,
            "catalog_digest": self.catalog_digest,
            "catalog_records": self.catalog_records,
            "eligible": self.eligible,
            "added": self.added,
            "already_present": self.already_present,
            "excluded_by_country": self.excluded_by_country,
            "excluded_by_company": self.excluded_by_company,
            "excluded_as_staffing_suspect": self.excluded_as_staffing_suspect,
            "receipt_path": self.receipt_path,
        }


def _company_key(value: str | None) -> str:
    """``discovery.merge.normalize_company``'s rule, inlined so this module never imports the discovery package."""

    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def _record_keys(record: object) -> set[str]:
    keys = {_company_key(getattr(record, "name", None)), _company_key(getattr(record, "board_token", None))}
    domain = getattr(record, "domain", None)
    if isinstance(domain, str) and domain:
        keys.add(_company_key(domain))
        keys.add(_company_key(domain.split(".", 1)[0]))
    keys.discard("")
    return keys


def _pref_keys(values: object) -> set[str]:
    return {key for key in (_company_key(str(value)) for value in (values or ())) if key}


def catalog_records_for_prefs(records: tuple[object, ...], prefs: object) -> tuple[list[object], int, int, int]:
    """Filter catalog records by ``prefs`` (``DiscoveryPrefs``-shaped).

    Rules, in order:

    * ``exclude_companies`` (name/slug/domain, normalized like discovery's
      ``normalize_company``) always drops the record.
    * ``watch_companies`` always keeps it (the user asked for it by name), even
      outside the country filter and even when it is a staffing suspect.
    * a record the seed flagged ``staffing_suspect`` (S26 rev3: Jev leaned
      staffing/consulting below the exclusion threshold) is not seeded by
      default -- orchestrator decision 2026-09-25 (msg_b3ca2e58203a): kept in
      the catalog with the flag, skipped here; the operator can still add
      the board by name/URL (``add_company_from_url``, ``watch_companies``).
    * ``countries`` (ISO alpha-2) keeps a record whose ``hq_country`` matches,
      or -- when ``US`` is asked for -- one with US postings on record
      (``us_posting_count > 0``); no ``countries`` pref means no country
      filter at all.

    Returns ``(kept, excluded_by_country, excluded_by_company, excluded_as_staffing_suspect)``.
    """

    excludes = _pref_keys(getattr(prefs, "exclude_companies", ()))
    watches = _pref_keys(getattr(prefs, "watch_companies", ()))
    countries = {str(code).upper() for code in (getattr(prefs, "countries", ()) or ())}
    kept: list[object] = []
    by_country = 0
    by_company = 0
    by_staffing = 0
    for record in records:
        keys = _record_keys(record)
        if keys & excludes:
            by_company += 1
            continue
        if keys & watches:
            kept.append(record)
            continue
        if getattr(record, "staffing_suspect", False) is True:
            by_staffing += 1
            continue
        if countries:
            hq = getattr(record, "hq_country", None)
            us_postings = getattr(record, "us_posting_count", None)
            in_country = isinstance(hq, str) and hq.upper() in countries
            us_ok = "US" in countries and isinstance(us_postings, int) and us_postings > 0
            if not (in_country or us_ok):
                by_country += 1
                continue
        kept.append(record)
    return kept, by_country, by_company, by_staffing


def seed_watchlist_from_catalog(
    home_root: Path,
    target: Path,
    gig_id: str | None = None,
    *,
    prefs: object,
    catalog: object | None = None,
    now: str | None = None,
) -> WatchlistSeedResult:
    """Add every catalog board the prefs admit that isn't on the watchlist yet.

    One journal transition for the whole batch (see the region comment
    above); a no-op batch records nothing. ``catalog`` defaults to the
    shipped :func:`company_catalog.load_company_catalog`; tests pass a small
    decoded one.
    """

    from .company_catalog import load_company_catalog

    active = catalog if catalog is not None else load_company_catalog()
    records = tuple(getattr(active, "records", ()))
    revision = str(getattr(active, "revision", ""))
    digest = str(getattr(active, "digest", ""))
    kept, by_country, by_company, by_staffing = catalog_records_for_prefs(records, prefs)
    observed_at = now or _now()
    resolved = _resolved(home_root, target, gig_id)

    def publish(writer: object) -> WatchlistSeedResult:
        # Case-insensitive on purpose: the record path is a filename, and a
        # case-insensitive filesystem (macOS) would make ``...:Abe.json`` and
        # ``...:abe.json`` the same file -- the journal refuses that as a
        # byte conflict. An existing entry under either spelling counts as
        # present.
        existing = {entry.watchlist_id.lower() for entry in _snapshot_entries(writer)}
        snapshot = writer.snapshot(("records/scout-watchlist/",))  # type: ignore[attr-defined]
        present_paths = {path.lower() for path in snapshot.artifacts}
        artifacts: list[JournalArtifact] = []
        refs: list[dict[str, object]] = []
        added_ids: list[str] = []
        already = 0
        for record in kept:
            entry = record.to_watchlist_entry(revision=revision, observed_at=observed_at)  # type: ignore[attr-defined]
            record_path = f"records/scout-watchlist/{entry.watchlist_id}.json"
            if entry.watchlist_id.lower() in existing or record_path.lower() in present_paths:
                already += 1
                continue
            existing.add(entry.watchlist_id.lower())
            record_bytes = canonical_json_bytes(entry.to_json())
            artifacts.append(JournalArtifact(record_path, record_bytes))
            refs.append(_ref(record_path, record_bytes))
            added_ids.append(entry.watchlist_id)
        if not artifacts:
            return WatchlistSeedResult(
                revision, digest, len(records), len(kept), 0, already, by_country, by_company, (), None, by_staffing,
            )
        payload = {"catalog_revision": revision, "catalog_digest": digest, "watchlist_ids": sorted(added_ids)}
        payload_sha = digest_imported_bytes(canonical_json_bytes(payload))
        key = f"scout_watchlist_seed:{revision}:{payload_sha.removeprefix('sha256:')[:16]}"
        receipt_path = _receipt_path("scout_watchlist_seed", key)
        receipt = {
            "schema_version": "1.0",
            "operation_id": f"operation_{uuid.uuid4()}",
            "project_id": resolved.project_id,
            "gig_id": resolved.gig_id,
            "operation": "scout_watchlist_seed",
            "operation_key": key,
            "payload_sha256": payload_sha,
            "catalog_revision": revision,
            "catalog_digest": digest,
            "outcome": "committed",
            "added": len(added_ids),
            "already_present": already,
            "excluded_by_country": by_country,
            "excluded_by_company": by_company,
            "excluded_as_staffing_suspect": by_staffing,
            "artifact_refs": refs,
            "created_at": observed_at,
        }
        receipt_bytes = canonical_json_bytes(receipt)
        artifacts.append(JournalArtifact(receipt_path, receipt_bytes))
        metadata = {
            "project_id": resolved.project_id,
            "gig_id": resolved.gig_id,
            "operation": "scout_watchlist_seed",
            "operation_key": key,
            "catalog_revision": revision,
            "catalog_digest": digest,
            "artifact_refs": [*refs, _ref(receipt_path, receipt_bytes)],
        }
        writer.record(  # type: ignore[attr-defined]
            JournalTransition(
                f"handoff_{uuid.uuid4()}",
                "scout_public_acquisition_progress",
                f"Seeded {len(added_ids)} Scout ATS watchlist entries from company catalog {revision}.",
                tuple(artifacts),
                metadata,
            )
        )
        return WatchlistSeedResult(
            revision, digest, len(records), len(kept), len(added_ids), already, by_country, by_company,
            tuple(added_ids), receipt_path, by_staffing,
        )

    return run_with_journal_writer(
        workpad=resolved.path,
        project_id=resolved.project_id,
        gig_id=resolved.gig_id,
        operation=publish,
    )


__all__ = [
    "JournalWatchlistClient",
    "OPERATOR_ADDED_BATCH_ID",
    "OPERATOR_ADDED_QUERY_KEY",
    "WatchlistClient",
    "WatchlistSeedResult",
    "WatchlistUrlError",
    "add_company_from_url",
    "add_to_watchlist",
    "catalog_records_for_prefs",
    "list_active",
    "seed_watchlist_from_catalog",
    "watchlist_entry_from_url",
]
