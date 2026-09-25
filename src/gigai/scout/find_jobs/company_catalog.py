"""Q2: the bundled US company + ATS-board catalog (S26), shipped in the wheel.

The seed lives at ``gigai/scout/data/companies.json.gz`` (package data,
``pyproject.toml``'s ``[tool.setuptools.package-data]``) and is read only
through :mod:`importlib.resources`, so an installed wheel and a source
checkout resolve the identical bytes.

Revision + digest (why this module has its OWN revision string)
-----------------------------------------------------------------

``gigai.catalog.CATALOG_REVISION`` is the *gig* catalog's revision: it names
the built-in Gig definitions (Scout's ``gig.py``/goalgraphs/instructions)
and is bumped at each GigAI release (decisions.log 2026-09-24: "set version
0.1.9 + CATALOG_REVISION v0.1.9 before tagging"). The company catalog is a
data set with its own cadence -- it changes whenever the S26 seed job is
re-run (a new crawl, more boards verified), independent of any code
release -- so it carries :data:`COMPANY_CATALOG_REVISION`, named after the
seed run that produced it, plus :data:`COMPANY_CATALOG_SHA256`, the pinned
digest of the shipped resource bytes. Reusing ``CATALOG_REVISION`` would tie
a data refresh to a code release (and vice versa) and make "which company
list did this watchlist come from" unanswerable from a receipt.

Every watchlist entry seeded from the catalog records the revision in its
``first_seen.query_key`` (``catalog:<revision>``) and every seeding receipt
records both the revision and the digest (``watchlist.py``), so a later
refresh is auditable per entry.

Swapping in a new seed file (the S26-full drop)
------------------------------------------------

1. ``python -c "from pathlib import Path; from gigai.scout.find_jobs.company_catalog import build_catalog_resource as b; print(b(Path('<full>/companies.json'), Path('src/gigai/scout/data/companies.json.gz')))"``
   -- writes the compact, reproducibly-gzipped resource and prints its
   digest.
2. Set :data:`COMPANY_CATALOG_REVISION` to the new seed's name and
   :data:`COMPANY_CATALOG_SHA256` to the printed digest.
3. ``tests/behaviors/scout_find_jobs/test_company_catalog.py`` asserts the
   pin, the size budget and that every record parses.

The loader is format-agnostic to size: it accepts a JSON array of records
or an object wrapping one (``{"companies": [...]}`` / ``{"records": [...]}``),
never assumes a record count, and skips (counting) any record it cannot
turn into a board -- an unknown ATS, a missing slug, a malformed
``staffing_suspect``/``staffing_confidence`` pair -- rather than failing
the whole load over one bad row. Optional seed fields are read additively:
the ``h1b`` aggregate (Q4b-data) and the rev3 staffing-suspect pair are
``None``/``False`` when absent.

Shipped revisions
-----------------

* ``s26-sample-2026-09-24`` -- the 285-record sample (Q2, 8,131 B gzip).
* ``s26-full-rev3-2026-09-25`` -- the full seed, 10,418 records (greenhouse
  4,649 / lever 2,405 / ashby 3,364; 48 ``staffing_suspect``; 1,345 with an
  ``h1b`` object), 289,333 B gzip (catalog-repin). Built from
  ``research/S26-us-company-directory/full/companies.json`` (rev3) with
  :func:`build_catalog_resource`; every record parses (``skipped == 0``).
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import gzip
from importlib import resources
import io
import json
from pathlib import Path
import re
from typing import Any

from ...canonical import digest_imported_bytes
from .contracts import ATSProvider, SourceKind, WatchlistEntry, WatchlistFirstSeen

#: Package-relative path of the shipped resource, under ``gigai.scout``.
COMPANY_CATALOG_RESOURCE = "data/companies.json.gz"

#: The seed run this resource came from. NOT ``gigai.catalog.CATALOG_REVISION``
#: (the gig catalog) -- see the module docstring. Current value: the S26
#: FULL seed, rev3 of 2026-09-25 (13842 candidates probed, 10697 boards
#: verified live, 10418 included / 279 excluded; the rev3 offline
#: re-classification keeps a Jev staffing verdict below confidence 70 as
#: ``staffing_suspect`` instead of excluding it --
#: ``research/S26-us-company-directory/full/coverage.md``). Replaced
#: ``s26-sample-2026-09-24`` (the 285-record sample) on 2026-09-25.
COMPANY_CATALOG_REVISION = "s26-full-rev3-2026-09-25"

#: Pinned ``digest_imported_bytes`` of the shipped ``companies.json.gz``. A
#: mismatch means the resource was swapped without updating this module (or
#: was corrupted in packaging); :func:`load_company_catalog` fails closed.
COMPANY_CATALOG_SHA256 = "sha256:9bfdf50e910665e6b46b28094c600b666aa27c0ec77dd9e5167bf681c2304b1c"

#: Scope decision (SCOPE-ADD-2, "Catalog delivery for 0.1.9"): the compressed
#: seed inside the wheel stays at or under 5 MB.
COMPANY_CATALOG_SIZE_BUDGET_BYTES = 5 * 1024 * 1024

_PROVIDER_ALIASES = {
    "greenhouse": ATSProvider.GREENHOUSE,
    "lever": ATSProvider.LEVER,
    "ashby": ATSProvider.ASHBY,
    "ashbyhq": ATSProvider.ASHBY,
}
_SAFE_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
_BOARD_URL_TEMPLATES = {
    ATSProvider.GREENHOUSE: "https://job-boards.greenhouse.io/{token}",
    ATSProvider.LEVER: "https://jobs.lever.co/{token}",
    ATSProvider.ASHBY: "https://jobs.ashbyhq.com/{token}",
}


class CompanyCatalogError(ValueError):
    """The shipped catalog is missing, corrupt, or not the pinned bytes."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class CompanyH1B:
    """The catalog's per-company H-1B aggregate (Q4b-data, v0.1.9).

    Read from a record's ``h1b`` object (S26 ``schema.json``: ``fiscal_years``,
    ``approvals``, ``denials``, ``naics``, ``matched_name``,
    ``match_confidence``). The fields the job card shows are kept -- the
    sum of approvals, the fiscal years they span and (orchestrator-approved
    contract addition, 2026-09-25) the sum of denials when the record has
    it -- and all tolerate a thinner shape (the shipped sample, or a future
    revision that drops a key): a missing ``approvals`` reads as 0, missing
    ``fiscal_years`` as ``()``, missing ``denials`` as ``None`` (then left
    out of the JSON). ``to_json()`` is exactly the ``rows[].h1b`` shape
    ``GET /api/runs/{id}/results`` serves.
    """

    approvals: int
    fiscal_years: tuple[str, ...]
    denials: int | None = None

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {"approvals": self.approvals, "fiscal_years": list(self.fiscal_years)}
        if self.denials is not None:
            value["denials"] = self.denials
        return value


def parse_company_h1b(raw: object) -> CompanyH1B | None:
    """A record's ``h1b`` value -> :class:`CompanyH1B`, or ``None`` when no match was recorded.

    ``None``/``false``/a non-object (the pre-aggregate ``true`` some rows
    may carry) yields ``None``: the aggregate exists only when the seed
    recorded the USCIS join itself.
    """

    if not isinstance(raw, dict):
        return None
    approvals = _optional_int(raw.get("approvals"))
    denials = _optional_int(raw.get("denials"))
    years_raw = raw.get("fiscal_years")
    years = tuple(item.strip() for item in years_raw if isinstance(item, str) and item.strip()) if isinstance(years_raw, list) else ()
    return CompanyH1B(
        approvals=max(approvals, 0) if approvals is not None else 0,
        fiscal_years=years,
        denials=max(denials, 0) if denials is not None else None,
    )


@dataclass(frozen=True)
class CompanyRecord:
    """One catalog company with a public ATS board.

    Only the fields Scout uses are kept; the seed file's other keys
    (``employment_types_seen``, ``fulltime_share``, ...) are ignored so the
    loader never breaks on an additive seed-format change.
    """

    name: str
    provider: ATSProvider
    board_token: str
    board_url: str
    hq_country: str | None = None
    domain: str | None = None
    employer_type: str | None = None
    posting_count: int | None = None
    us_posting_count: int | None = None
    h1b: bool = False
    last_verified: str | None = None
    # Q4b-data: the aggregate behind the ``h1b`` flag, when the record
    # carries the USCIS join object (see :class:`CompanyH1B`); ``None`` for
    # a null/absent/boolean ``h1b``. ``h1b`` (the bool) keeps its meaning.
    h1b_summary: CompanyH1B | None = None
    # catalog-repin (S26 rev3, 2026-09-25): the seed's OPTIONAL
    # ``staffing_suspect`` / ``staffing_confidence`` pair -- Jev called the
    # company staffing/consulting at a confidence BELOW the exclusion
    # threshold (70), so it stayed in the catalog flagged rather than being
    # dropped on a weak verdict. Absent on every other record, read as
    # ``False`` / ``None`` here. Nothing in seeding filters on it today; a
    # stricter list is the orchestrator's call (see ``staffing_suspects``).
    staffing_suspect: bool = False
    staffing_confidence: int | None = None

    @property
    def watchlist_id(self) -> str:
        """Same key shape ``market_acquisition._watchlist_add`` uses for Exa finds."""

        return f"scout_watchlist:{self.provider.value}:{self.board_token}"

    def to_watchlist_entry(self, *, revision: str, observed_at: str) -> WatchlistEntry:
        return WatchlistEntry(
            watchlist_id=self.watchlist_id,
            provider=self.provider,
            board_token=self.board_token,
            company=self.name,
            state="active",
            first_seen=WatchlistFirstSeen(
                SourceKind.ATS,
                self.board_url,
                f"catalog:{revision}",
                f"catalog-seed:{revision}",
                observed_at,
            ),
        )

    def to_json(self) -> dict[str, object]:
        value: dict[str, object] = {
            "name": self.name,
            "ats": self.provider.value,
            "board_slug": self.board_token,
            "board_url": self.board_url,
            "hq_country": self.hq_country,
            "domain": self.domain,
            "employer_type": self.employer_type,
            "posting_count": self.posting_count,
            "us_posting_count": self.us_posting_count,
            "h1b": self.h1b,
            "last_verified": self.last_verified,
        }
        if self.staffing_suspect:
            # Same rule as the seed's own ``to_json``: the pair appears only
            # on a flagged record, so unflagged records serialize unchanged.
            value["staffing_suspect"] = True
            value["staffing_confidence"] = self.staffing_confidence
        return value


@dataclass(frozen=True)
class CompanyCatalog:
    revision: str
    digest: str
    size_bytes: int
    records: tuple[CompanyRecord, ...]
    skipped: int

    def __len__(self) -> int:
        return len(self.records)

    def by_board(self) -> dict[tuple[ATSProvider, str], CompanyRecord]:
        return {(record.provider, record.board_token): record for record in self.records}

    def h1b_by_board(self) -> dict[tuple[ATSProvider, str], CompanyH1B]:
        """``(provider, board token lower-cased)`` -> aggregate, for records whose ``h1b`` is an object.

        The token is lower-cased because a posting's ``board_token`` is
        whatever the watchlist/board URL carried, and the catalog itself
        dedupes case-insensitively (``parse_catalog_payload``).
        """

        return {
            (record.provider, record.board_token.lower()): record.h1b_summary
            for record in self.records
            if record.h1b_summary is not None
        }

    def staffing_suspects(self) -> tuple[CompanyRecord, ...]:
        """The records the seed flagged ``staffing_suspect`` (S26 rev3: 48 of 10418)."""

        return tuple(record for record in self.records if record.staffing_suspect)

    def summary(self) -> dict[str, object]:
        by_provider: dict[str, int] = {}
        for record in self.records:
            by_provider[record.provider.value] = by_provider.get(record.provider.value, 0) + 1
        return {
            "revision": self.revision,
            "digest": self.digest,
            "size_bytes": self.size_bytes,
            "records": len(self.records),
            "skipped": self.skipped,
            "by_provider": dict(sorted(by_provider.items())),
            "staffing_suspect": len(self.staffing_suspects()),
        }


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_company_record(raw: object) -> CompanyRecord | None:
    """One seed row -> :class:`CompanyRecord`, or ``None`` when it isn't a board."""

    if not isinstance(raw, dict):
        return None
    provider_raw = raw.get("ats") if "ats" in raw else raw.get("provider")
    provider = _PROVIDER_ALIASES.get(provider_raw.strip().lower()) if isinstance(provider_raw, str) else None
    if provider is None:
        return None
    token = _optional_str(raw.get("board_slug") if "board_slug" in raw else raw.get("board_token"))
    if token is None:
        return None
    token = token.strip()
    if not _SAFE_TOKEN.fullmatch(token):
        # A board token becomes a path segment in the provider API URL and a
        # journal record filename (``scout_watchlist:<provider>:<token>.json``):
        # anything outside [A-Za-z0-9._-] (the S26 sample has "harrison&star"
        # and "1st Formations") is not a usable board and is skipped, counted.
        return None
    name = _optional_str(raw.get("name")) or token
    board_url = _optional_str(raw.get("board_url")) or _BOARD_URL_TEMPLATES[provider].format(token=token)
    h1b_raw = raw.get("h1b")
    h1b = bool(h1b_raw) if not isinstance(h1b_raw, str) else h1b_raw.strip().lower() in {"1", "true", "yes"}
    h1b_summary = parse_company_h1b(h1b_raw)
    staffing = _parse_staffing_flag(raw)
    if staffing is None:
        # Fail closed, like a bad token: a record whose staffing pair is
        # malformed (a non-boolean flag, a confidence that is not an integer
        # in 0..100, or a confidence without the flag -- the seed schema's
        # ``dependencies``) is not a record this loader can vouch for, so it
        # is skipped and counted rather than shipped unflagged.
        return None
    staffing_suspect, staffing_confidence = staffing
    return CompanyRecord(
        name=name.strip(),
        provider=provider,
        board_token=token,
        board_url=board_url,
        hq_country=_optional_str(raw.get("hq_country")),
        domain=_optional_str(raw.get("domain")),
        employer_type=_optional_str(raw.get("employer_type")),
        posting_count=_optional_int(raw.get("posting_count")),
        us_posting_count=_optional_int(raw.get("us_posting_count")),
        h1b=h1b,
        last_verified=_optional_str(raw.get("last_verified")),
        h1b_summary=h1b_summary,
        staffing_suspect=staffing_suspect,
        staffing_confidence=staffing_confidence,
    )


def _parse_staffing_flag(raw: dict[str, Any]) -> tuple[bool, int | None] | None:
    """``(staffing_suspect, staffing_confidence)`` from a seed row, or ``None`` when malformed.

    Both keys are optional and additive (S26 ``schema.json``, rev3): the flag
    must be a boolean when present, the confidence an integer in 0..100 that
    never appears without the flag key. A well-formed row without the pair
    reads as ``(False, None)``.
    """

    has_flag = "staffing_suspect" in raw
    has_confidence = "staffing_confidence" in raw
    flag = raw.get("staffing_suspect", False)
    if not isinstance(flag, bool):
        return None
    if has_confidence and not has_flag:
        return None
    confidence = raw.get("staffing_confidence")
    if confidence is not None:
        if isinstance(confidence, bool) or not isinstance(confidence, int) or not 0 <= confidence <= 100:
            return None
    return flag, confidence


def parse_catalog_payload(payload: object) -> tuple[tuple[CompanyRecord, ...], int]:
    """Decode either seed shape; duplicate boards keep the first occurrence.

    Duplicates are folded case-insensitively on ``(provider, token)``: the
    S26 sample carries pairs like ``OpenAI``/``openai`` (the same live
    board, verified twice under two spellings), and the journal record path
    is a filename that would collide on a case-insensitive filesystem
    (macOS) anyway. The first spelling wins.
    """

    rows: object
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = next((payload[key] for key in ("companies", "records", "boards") if isinstance(payload.get(key), list)), None)
        if rows is None:
            raise CompanyCatalogError("bad_shape", "company catalog object has no companies/records array")
    else:
        raise CompanyCatalogError("bad_shape", "company catalog must be a JSON array or an object wrapping one")
    seen: set[tuple[ATSProvider, str]] = set()
    records: list[CompanyRecord] = []
    skipped = 0
    for raw in rows:  # type: ignore[union-attr]
        record = parse_company_record(raw)
        if record is None:
            skipped += 1
            continue
        key = (record.provider, record.board_token.lower())
        if key in seen:
            skipped += 1
            continue
        seen.add(key)
        records.append(record)
    return tuple(records), skipped


def decode_catalog_bytes(compressed: bytes, *, revision: str) -> CompanyCatalog:
    """Gzipped JSON bytes -> :class:`CompanyCatalog` (no digest pin check)."""

    try:
        raw = gzip.decompress(compressed)
    except (OSError, EOFError, ValueError) as exc:
        raise CompanyCatalogError("bad_gzip", "company catalog resource is not valid gzip") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise CompanyCatalogError("bad_json", "company catalog resource is not valid JSON") from exc
    records, skipped = parse_catalog_payload(payload)
    return CompanyCatalog(
        revision=revision,
        digest=digest_imported_bytes(compressed),
        size_bytes=len(compressed),
        records=records,
        skipped=skipped,
    )


def read_catalog_resource_bytes() -> bytes:
    """The shipped resource, exactly as packaged (``importlib.resources``)."""

    resource = resources.files("gigai.scout").joinpath(COMPANY_CATALOG_RESOURCE)
    if not resource.is_file():
        raise CompanyCatalogError(
            "resource_missing",
            f"gigai.scout is missing {COMPANY_CATALOG_RESOURCE}: the company catalog was not shipped "
            "(check [tool.setuptools.package-data] 'gigai.scout' in pyproject.toml)",
        )
    return resource.read_bytes()


@lru_cache(maxsize=1)
def load_company_catalog() -> CompanyCatalog:
    """Load and verify the shipped catalog once per process.

    Fails closed on a digest mismatch (``catalog_digest_mismatch``): the
    revision string and the pinned digest must be updated together with the
    resource, so a watchlist can never be seeded from bytes nobody recorded.
    """

    compressed = read_catalog_resource_bytes()
    digest = digest_imported_bytes(compressed)
    if digest != COMPANY_CATALOG_SHA256:
        raise CompanyCatalogError(
            "catalog_digest_mismatch",
            f"shipped company catalog digest {digest} does not match the pinned "
            f"{COMPANY_CATALOG_SHA256} for revision {COMPANY_CATALOG_REVISION}",
        )
    if len(compressed) > COMPANY_CATALOG_SIZE_BUDGET_BYTES:
        raise CompanyCatalogError(
            "catalog_too_large",
            f"shipped company catalog is {len(compressed)} bytes, over the "
            f"{COMPANY_CATALOG_SIZE_BUDGET_BYTES} byte budget",
        )
    return decode_catalog_bytes(compressed, revision=COMPANY_CATALOG_REVISION)


def build_catalog_resource(source: Path, destination: Path) -> str:
    """Compact + reproducibly gzip a seed ``companies.json`` into the resource.

    Records are re-serialized compactly with sorted keys (the seed's
    indentation is display-only), and gzip's header mtime is pinned to 0 so
    the same input always yields the same bytes -- and therefore the same
    pinned digest. Returns the digest to paste into
    :data:`COMPANY_CATALOG_SHA256`.
    """

    payload = json.loads(source.read_text(encoding="utf-8"))
    records, skipped = parse_catalog_payload(payload)
    if not records:
        raise CompanyCatalogError("empty", f"{source} holds no usable board records ({skipped} skipped)")
    rows: object = payload if isinstance(payload, list) else next(
        payload[key] for key in ("companies", "records", "boards") if isinstance(payload.get(key), list)
    )
    compact = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9, mtime=0) as stream:
        stream.write(compact)
    compressed = buffer.getvalue()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(compressed)
    return digest_imported_bytes(compressed)


def catalog_info(catalog: CompanyCatalog | None = None) -> dict[str, Any]:
    """Small, JSON-safe description for receipts/progress (never the records)."""

    active = catalog if catalog is not None else load_company_catalog()
    return active.summary()


__all__ = [
    "COMPANY_CATALOG_RESOURCE",
    "COMPANY_CATALOG_REVISION",
    "COMPANY_CATALOG_SHA256",
    "COMPANY_CATALOG_SIZE_BUDGET_BYTES",
    "CompanyCatalog",
    "CompanyCatalogError",
    "CompanyH1B",
    "CompanyRecord",
    "build_catalog_resource",
    "catalog_info",
    "decode_catalog_bytes",
    "load_company_catalog",
    "parse_catalog_payload",
    "parse_company_h1b",
    "parse_company_record",
    "read_catalog_resource_bytes",
]
