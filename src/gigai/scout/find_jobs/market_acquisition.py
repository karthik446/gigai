"""Wave-1b acquisition node for the Scout find-jobs graph.

The node is deliberately an orchestration boundary: provider clients are
injected protocols, while persistence is delegated to the existing public
acquisition journal.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import gzip
import json
import os
from pathlib import Path
import re
from typing import Any, cast
from urllib.parse import parse_qsl, urlsplit

import httpx

from ...canonical import canonical_json_bytes, digest_imported_bytes
from ..acquisition_records import import_public_rows
from .contracts import (
    ATSBoardClient,
    ATSProvider,
    AcquireInput,
    AcquireOutput,
    AssessmentResult,
    CarriedForwardAssessment,
    DropCount,
    FailureRow,
    FindJobsConfig,
    FindJobsContractError,
    NodeContext,
    NotAssessedReason,
    PostingRow,
    PostingRowResult,
    ProgressStatus,
    RowOutcome,
    SelectedPosting,
    SelectionRule,
    SourceKind,
    URLSetDiff,
    WatchlistEntry,
    WatchlistFirstSeen,
    ExaSearchClient,
    WatchlistClient,
    diff_url_sets,
    normalize_url,
    parse_board_url,
)
from .filters import exclusion_reason, location_mismatch_detail
from .jev_rank import order_by_rank
from .progress import ProgressWriter
from .selection import select_for_assessment
from ...workpad import ResolvedWorkpad, resolve_workpad

# U26: cap on the total size of raw provider responses stored per run, so a
# very large/unbounded response set can't fill the workpad disk unbounded.
RAW_PAYLOAD_CAP_BYTES = 20 * 1024 * 1024

# Query parameter names ATS boards use to carry a job id when the posting is
# served from a custom career-site domain rather than the board's own
# subdomain (U20 dedupe): e.g. "https://www.pinterestcareers.com/jobs?gh_jid=…"
# is the same Greenhouse job as "https://job-boards.greenhouse.io/pinterest/
# jobs/…" for the numeric id in the path.
_JOB_ID_QUERY_KEYS = ("gh_jid", "lever_id", "job_id")
_NUMERIC_PATH_SEGMENT = re.compile(r"^\d{4,}$")


class AcquireAllSourcesFailedError(FindJobsContractError):
    """Every enabled acquisition source failed; no batch was written."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _safe_batch_id(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9_.:-]+", "-", value).strip("-")
    return (value or "acquire")[:120]


def job_id_from_url(url: str) -> str | None:
    """Best-effort ATS job id extracted from a posting URL (U20 dedupe).

    Checks known job-id query parameters first (``gh_jid`` etc., used by
    custom career-site domains that proxy a Greenhouse/Lever board), then the
    last numeric path segment (the board-subdomain URL shape, e.g.
    ``.../pinterest/jobs/7683977``). Returns ``None`` when neither is found
    (e.g. Ashby's UUID-slug job ids, which are already unique per posting and
    don't need this fallback -- the board_token + full path is enough).
    """

    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    query = dict(parse_qsl(parsed.query))
    for key in _JOB_ID_QUERY_KEYS:
        value = query.get(key)
        if value:
            return value
    parts = [part for part in parsed.path.split("/") if part]
    for part in reversed(parts):
        if _NUMERIC_PATH_SEGMENT.match(part):
            return part
    return None


# P4: ``job_input.resolve_job`` reuses the parser; the old private name stays
# an alias so existing callers/tests keep working.
_job_id_from_url = job_id_from_url


def _dedupe_identity(row: PostingRow) -> str:
    """The identity key used to dedupe an Exa row against its ATS row.

    Same normalized URL is the strongest signal (handled by the caller's
    ``seen`` set before this is even consulted); this is the fallback for
    when the same job is served from two different URLs (a board subdomain
    and a custom career-site domain proxying the same board). Falls back to
    the row's own normalized URL when no job id can be parsed out, which
    makes the row unique to itself (no false-positive dedupe).
    """

    job_id = _job_id_from_url(row.url) or _job_id_from_url(row.normalized_url)
    if row.board_token and job_id:
        return f"{row.provider.value}:{row.board_token}:{job_id}"
    return f"self:{row.normalized_url}"


def _merge_exa_and_ats_rows(rows: Sequence[PostingRow]) -> list[PostingRow]:
    """Dedupe Exa vs ATS rows for the same job; the ATS row wins (U20).

    Two dedupe passes:

    1. Exact normalized-URL collision (unchanged from before this packet):
       whichever row is seen first for that URL wins, but an ATS row is now
       sorted first so ties resolve to it deterministically.
    2. Identity collision (:func:`_dedupe_identity`: same board_token + job
       id parsed from the URL, reached via two different hostnames -- e.g. a
       Greenhouse-hosted board URL and the employer's custom career-site
       domain proxying the same posting). When both an Exa and an ATS row
       share an identity, the ATS row's fuller title/location/text/hash wins
       and the Exa row is dropped entirely (not kept as a second entry).
    """

    # Sort ATS-sourced rows first so both passes prefer them on a tie.
    ordered = sorted(rows, key=lambda row: 0 if row.source_kind is SourceKind.ATS else 1)

    by_url: dict[str, PostingRow] = {}
    order: list[str] = []
    for row in ordered:
        normalized = normalize_url(row.normalized_url or row.url)
        if normalized != row.normalized_url:
            # dataclasses.replace (not a positional PostingRow(...) rebuild)
            # so a future additive field (like B1's `countries`) carries
            # through automatically instead of silently reverting to its
            # default on every URL-normalization pass.
            row = replace(row, normalized_url=normalized)
        if normalized in by_url:
            continue  # ATS-first ordering already means the first seen wins.
        by_url[normalized] = row
        order.append(normalized)

    url_deduped = [by_url[key] for key in order]

    by_identity: dict[str, PostingRow] = {}
    identity_order: list[str] = []
    for row in url_deduped:
        identity = _dedupe_identity(row)
        existing = by_identity.get(identity)
        if existing is None:
            by_identity[identity] = row
            identity_order.append(identity)
            continue
        if existing.source_kind is not SourceKind.ATS and row.source_kind is SourceKind.ATS:
            by_identity[identity] = row
        # else: keep whichever ATS/first row is already stored; the Exa
        # duplicate is dropped.

    return [by_identity[key] for key in identity_order]


def _role_match(row: PostingRow, roles: Sequence[str]) -> bool:
    haystack = f"{row.title} {row.company} {row.location}".casefold()
    return any(str(role).casefold().strip() in haystack for role in roles)


def _digest(row: PostingRow) -> str:
    return row.content_sha256 or digest_imported_bytes(canonical_json_bytes(row.to_json()))


def _progress_writer(
    context: NodeContext, home_root: Path | None, target: Path | None
) -> ProgressWriter | None:
    """Best-effort ``ProgressWriter`` for this run, or ``None`` if unresolvable.

    Progress is purely additive UX; a caller that can't resolve a workpad
    (most direct-call unit tests construct ``NodeContext`` without a real
    on-disk run) must still get the exact same sealed ``AcquireOutput`` as
    before this packet, so every failure mode here degrades to ``None``
    rather than raising.
    """

    try:
        resolved = _resolved(context, home_root, target)
        run_id = context.run_id
        if not run_id:
            return None
        return ProgressWriter(resolved.path / "runs" / run_id)
    except Exception:  # noqa: BLE001 - progress must never break the sealed run
        return None


def _resolved(context: NodeContext, home_root: Path | None, target: Path | None) -> ResolvedWorkpad:
    if home_root is not None or target is not None:
        return resolve_workpad(
            home_root=home_root or Path.home() / ".gigai",
            requested_target=target,
            gig_id=context.gig_id,
            allow_semantic_state=True,
        )
    workpad = Path(context.workpad_path)
    return ResolvedWorkpad(
        project_id=context.project_id,
        gig_id=context.gig_id,
        path=workpad,
        target_root=workpad,
        target_kind="directory",
    )


def _watchlist_entries(watchlist: WatchlistClient) -> tuple[WatchlistEntry, ...]:
    for name in ("active_entries", "list_active", "entries", "list"):
        method = getattr(watchlist, name, None)
        if callable(method):
            try:
                values = method()
            except TypeError:
                continue
            return tuple(v if isinstance(v, WatchlistEntry) else WatchlistEntry.from_json(v) for v in values)
    values = getattr(watchlist, "active", ())
    return tuple(v if isinstance(v, WatchlistEntry) else WatchlistEntry.from_json(v) for v in values)


def _watchlist_add(watchlist: WatchlistClient, row: PostingRow, *, query_key: str, batch_id: str) -> str | None:
    if row.board_token is None:
        return None
    entry = WatchlistEntry(
        watchlist_id=f"scout_watchlist:{row.provider.value}:{row.board_token}",
        provider=row.provider,
        board_token=row.board_token,
        company=row.company,
        state="active",
        first_seen=WatchlistFirstSeen(SourceKind.EXA, row.url, query_key, batch_id, _now()),
    )
    result = watchlist.add_to_watchlist(entry)
    return getattr(result, "watchlist_id", entry.watchlist_id)


def _prior_observations(root: Path, current_batch: str) -> dict[str, str | None]:
    base = root / "records" / "scout-acquisition"
    result: dict[str, str | None] = {}
    if not base.is_dir():
        return result
    for input_file in sorted(base.glob("*/input.json")):
        if input_file.parent.name == current_batch:
            continue
        try:
            payload = json.loads(input_file.read_text())
            for row in payload.get("rows", []):
                url = row.get("url") or row.get("normalized_url")
                if url:
                    result[normalize_url(str(url))] = row.get("source_snapshot", {}).get("content_sha256") or row.get("content_sha256")
        except (OSError, ValueError, TypeError):
            continue
    return result


def _default_profile_id(resolved: ResolvedWorkpad | None) -> str | None:
    """The gig's migrated-default profile id, or ``None`` if unresolvable.

    S25 F1-b / Legacy-run policy: a run sealed before F1-b (no
    ``profile_ref``) is attributed to the gig's ``origin ==
    "migrated_default"`` profile for cache-key purposes -- never "visible to
    all profiles," never "excluded" (see the S25 spike's Legacy-run policy
    section). ``resolved`` is ``None`` for the many direct-call unit tests
    that construct a fake/non-journaled workpad (``_resolved`` in this same
    module returns a plain ``ResolvedWorkpad`` for those, but
    ``profile_records.list_profiles`` requires a REAL journaled workpad) --
    degrades to ``None`` rather than raise, exactly like
    ``_run_profile_identity``'s own "no sealed input -> None" precedent:
    callers treat ``None`` as "cannot attribute," never a match.
    """

    if resolved is None:
        return None
    try:
        from .. import profile_records
    except ImportError:
        return None
    try:
        profiles = profile_records.list_profiles(resolved)
    except Exception:
        return None
    for profile in profiles:
        if profile.origin == "migrated_default":
            return profile.profile_id
    return None


@dataclass(frozen=True)
class _RunProfileIdentity:
    """A run's sealed ``(resume_revision_id, profile_id)`` pair for the A2 cache key."""

    resume_revision_id: str
    profile_id: str | None


def _run_profile_identity(root: Path, run_id: str, *, default_profile_id: str | None) -> _RunProfileIdentity | None:
    """The pinned resume revision + profile identity sealed for ``run_id``.

    uat-bug-009: reads ``runs/<run_id>/sealed/find-jobs-run-input.json`` --
    the same sealed file ``proposal_execution.py``'s own ``_read_sealed_config``
    reads for its config -- rather than growing ``AcquireInput``'s sealed
    contract with a resume field (a schema change). Written by
    ``run.launch_find_jobs_run`` before the graph is even scheduled, so it is
    on disk before this node's body runs on a real run; degrades to ``None``
    for the many direct-call unit tests that construct ``AcquireInput``
    without a real on-disk run (older run dirs too) -- callers treat ``None``
    the same as "no config": never a match, so an unchanged row's prior
    assessment is never trusted without a known, current resume revision to
    compare it against.

    S25 A2/F1-b: the corrected cache key adds ``profile_id`` ALONGSIDE
    ``resume_revision_id`` (never replacing it -- r1's design defect the
    coordinator's r2 review fixed, see the S25 spike's A2 section). A run
    with a sealed ``profile_ref`` uses its own ``profile_id`` directly; a
    legacy run (no ``profile_ref``) is attributed to ``default_profile_id``
    (the Legacy-run policy) but KEEPS its own sealed
    ``pinned_resume.revision_id`` -- never coerced to any fixed stand-in.
    """

    path = root / "runs" / run_id / "sealed" / "find-jobs-run-input.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        from .contracts import FindJobsRunInput

        payload = json.loads(path.read_text(encoding="utf-8"))
        sealed = FindJobsRunInput.from_json(payload)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    profile_id = sealed.profile_ref.profile_id if sealed.profile_ref is not None else default_profile_id
    return _RunProfileIdentity(sealed.pinned_resume.revision_id, profile_id)


def _read_sealed_run_input_for_rank(root: Path, run_id: str) -> object | None:
    """Same sealed-file read as ``_run_profile_identity``, returning the whole DTO."""

    path = root / "runs" / run_id / "sealed" / "find-jobs-run-input.json"
    if path.is_symlink() or not path.is_file():
        return None
    try:
        from .contracts import FindJobsRunInput

        return FindJobsRunInput.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, KeyError):
        return None


def _read_resume_text_for_rank(
    resolved: ResolvedWorkpad, sealed: object, *, home_root: Path
) -> str | None:
    """The sealed run's pinned resume text, or ``None`` if it can't be read.

    P6: ranking is display-ordering only, never a sealed authority the way
    ``proposal_execution._read_pinned_resume`` is for assess -- so this
    degrades to ``None`` (no rank calls made) on any failure (resume record
    missing, digest mismatch, I/O error) rather than raising and failing the
    whole acquire step. Never verifies the digest match `_read_pinned_resume`
    enforces; a stale/rotated resume at rank time just means a slightly-stale
    score, corrected the moment the real assess call re-derives the matrix.
    """

    try:
        from ..private_records import read_record

        value = read_record(
            home_root=home_root,
            requested_target=resolved.path,
            record_id=sealed.pinned_resume.record_id,  # type: ignore[attr-defined]
            revision_id=sealed.pinned_resume.revision_id,  # type: ignore[attr-defined]
            content=True,
            gig_id=resolved.gig_id,
        )
    except Exception:
        return None
    content = value.get("content")
    if not isinstance(content, bytes):
        return None
    return content.decode("utf-8", errors="replace")


def _jev_http_client() -> httpx.Client:
    """The transport ``_rank_candidates`` uses for its Jev calls.

    A module-level function (not inlined) so ``bindings.py`` can monkeypatch
    it under ``GIGAI_SCOUT_FIND_JOBS_TEST_JEV=1`` -- the same seam shape as
    ``proposal_execution.resolve_model_adapter`` (C1): the production test
    harness patches exactly this name, never a value imported from it.
    """

    return httpx.Client(timeout=30.0)


def _rank_candidates(
    candidates: list[PostingRow],
    *,
    resolved: ResolvedWorkpad,
    run_id: str,
    config: FindJobsConfig,
    profile_id: str | None,
    resume_revision_id: str | None,
    home_root: Path | None,
) -> tuple:
    """P6: score ``candidates`` with Jev, or return ``()`` (fail open).

    Never raises -- every precondition (no key, no sealed run input, no
    readable resume, a Jev transport failure) degrades to ``()``, which
    leaves ``candidates``' selection order exactly as it was before P6
    (``order_by_rank`` is a no-op on an empty ``scores`` tuple). Cost cap:
    ``GIGAI_JEV_COST_CAP_USD`` env var when set (plan section 8, answer 7),
    else ``jev_rank.DEFAULT_COST_CAP_USD``.
    """

    if not candidates or home_root is None:
        return ()
    from .jev_client import JevClient, JevClientError, has_api_key, require_api_key

    if not has_api_key(home_root=home_root):
        return ()
    sealed = _read_sealed_run_input_for_rank(resolved.path, run_id)
    if sealed is None:
        return ()
    resume_text = _read_resume_text_for_rank(resolved, sealed, home_root=home_root)
    if not resume_text:
        return ()
    from .jev_rank import DEFAULT_COST_CAP_USD, RankPreferences, rank_postings

    cost_cap_raw = os.environ.get("GIGAI_JEV_COST_CAP_USD")
    try:
        cost_cap = float(cost_cap_raw) if cost_cap_raw else DEFAULT_COST_CAP_USD
    except ValueError:
        cost_cap = DEFAULT_COST_CAP_USD
    try:
        api_key = require_api_key(home_root=home_root)
        client = JevClient(api_key, _jev_http_client())
        prefs = RankPreferences(
            target_titles=tuple(config.roles),
            countries=tuple(config.countries),
            visa_sponsorship_required=bool(config.visa_sponsorship_required),
        )
        scores, _total_cost, _capped = rank_postings(
            tuple(candidates),
            client=client,
            resume_text=resume_text,
            prefs=prefs,
            profile_id=profile_id,
            resume_revision_id=resume_revision_id,
            home_root=home_root,
            target=resolved.path,
            cost_cap_usd=cost_cap,
        )
        return scores
    except JevClientError:
        return ()
    except Exception:
        return ()


@dataclass(frozen=True)
class _PriorAssessment:
    """One posting's most recent *successful* assessment from an earlier run."""

    result: "AssessmentResult"
    resume_revision_id: str
    profile_id: str | None
    run_date: str | None


def _prior_assessments(
    root: Path, current_run_id: str, *, default_profile_id: str | None = None
) -> dict[str, _PriorAssessment]:
    """Every URL's latest successful assessment from an earlier run's sealed output.

    uat-bug-009 root cause: acquire's candidate loop excluded every
    ``UNCHANGED`` row outright (this function is what lets it stop doing
    that for a row that was never actually, successfully assessed --
    ``NEW_never_assessed``/never-run-2-2-2 the operator's evidence run.md:
    a failed run leaves postings acquired but with no ``outputs/assess.json``
    at all, so those postings are already excluded here -- see the
    ``AssessOutput.assessments`` scan below only ever iterating *sealed*
    outputs that exist).
    ``runs/*/outputs/assess.json`` (sealed per run, never cleaned up) is the
    only durable, resume-revision-tagged record of "which postings were
    actually, successfully assessed" -- ``records/scout-proposals/...`` (the
    R1 immutable revision layer) is keyed by opaque record/revision uuids
    with no content-digest index, so it cannot answer "was this digest ever
    assessed" without an unbounded scan of every proposal record ever
    written; ``AssessOutput`` is already the exact bounded, per-run answer.
    Later runs win ties (sorted by directory name, which is the run's UUID --
    not a true time order, but the exact "from run <date>" wall-clock label
    is resolved separately, from ``outputs/assess.json``'s own mtime, by the
    caller that needs to display it -- ordering here only decides which
    *result* to prefer when a posting was assessed successfully more than
    once, and the newest successful one is always the more useful carry-
    forward, so last-write-wins over ``sorted()`` order is adequate).

    S25 A2/F1-b: each entry also carries the PRODUCING run's profile
    identity (``_run_profile_identity``, legacy runs attributed to
    ``default_profile_id``) -- the caller compares it against the CURRENT
    run's own profile identity before trusting a carry-forward, so a
    profile-B run never carries forward profile-A's assessment of the same
    URL even if their resume revisions happened to coincide.
    """

    from .contracts import AssessOutput

    result: dict[str, _PriorAssessment] = {}
    runs_dir = root / "runs"
    if not runs_dir.is_dir():
        return result
    for output_file in sorted(runs_dir.glob("*/outputs/assess.json")):
        run_id = output_file.parent.parent.name
        if run_id == current_run_id:
            continue
        try:
            payload = json.loads(output_file.read_text(encoding="utf-8"))
            output = AssessOutput.from_json(payload)
        except (OSError, ValueError, TypeError):
            continue
        resume_revision_id = output.pinned_resume.revision_id
        identity = _run_profile_identity(root, run_id, default_profile_id=default_profile_id)
        profile_id = identity.profile_id if identity is not None else default_profile_id
        run_date = None
        try:
            run_date = datetime.fromtimestamp(output_file.stat().st_mtime, tz=timezone.utc).isoformat().replace("+00:00", "Z")
        except OSError:
            pass
        for assessment in output.assessments:
            result[assessment.posting.normalized_url] = _PriorAssessment(assessment, resume_revision_id, profile_id, run_date)
    return result


class _CapturedResponse:
    """Redacted record of one HTTP exchange, kept for U26 raw storage.

    Only the response body, status, and the request URL with its query keys
    stripped are kept -- never request headers (where API keys live) and
    never response headers (which can carry rate-limit/auth echo values).
    Request headers are never even read by :class:`_RecordingHTTPClient`.
    """

    __slots__ = ("source", "url", "status_code", "body")

    def __init__(self, source: str, url: str, status_code: int, body: bytes) -> None:
        self.source = source
        self.url = url
        self.status_code = status_code
        self.body = body


class _RecordingHTTPClient:
    """Wraps an httpx-like client to capture every response body (U26).

    Transparent pass-through: `exa.search()`/`ats.list_board()` call `.get`/
    `.post` exactly as before and get the real response back unchanged; this
    just also appends a redacted :class:`_CapturedResponse` to `captured` as
    a side effect, with `source` derived from the request URL's hostname via
    `source_for_url`, since the underlying protocols don't pass a source
    name through explicitly.
    """

    def __init__(self, client: Any, *, source_for_url: "Callable[[str], str]") -> None:
        self._client = client
        self._source_for_url = source_for_url
        self.captured: list[_CapturedResponse] = []

    def _record(self, url: str, response: Any) -> Any:
        try:
            body = response.content
        except Exception:  # noqa: BLE001 - never let capture break the real call
            body = b""
        source = self._source_for_url(url)
        self.captured.append(_CapturedResponse(source, url, getattr(response, "status_code", 0), body))
        return response

    def get(self, url: str, *args: Any, **kwargs: Any) -> Any:
        response = self._client.get(url, *args, **kwargs)
        return self._record(url, response)

    def post(self, url: str, *args: Any, **kwargs: Any) -> Any:
        response = self._client.post(url, *args, **kwargs)
        return self._record(url, response)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def _strip_query(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _source_from_url(url: str) -> str:
    host = (urlsplit(url).hostname or "").lower()
    if "exa.ai" in host:
        return "exa"
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    if "ashbyhq.com" in host:
        return "ashby"
    return "other"


def _write_raw_payloads(resolved: ResolvedWorkpad, run_id: str, captured: Sequence[_CapturedResponse]) -> None:
    """Persist redacted raw provider responses under runs/<run_id>/raw/ (U26).

    Gzip-compressed, one file per response, plus an index.json listing
    source/url/status/bytes/sha256 for every stored (and every skipped, once
    the cap is hit) response. Never writes request headers, response
    headers, or API keys -- only ``_CapturedResponse.body`` (the JSON
    response bytes) and the query-stripped URL.
    """

    if not captured:
        return
    raw_root = resolved.path / "runs" / run_id / "raw"
    per_source_counts: dict[str, int] = {}
    index_entries: list[dict[str, object]] = []
    total_bytes = 0
    for item in captured:
        gzipped = gzip.compress(item.body, compresslevel=6)
        entry: dict[str, object] = {
            "source": item.source,
            "url": _strip_query(item.url),
            "status": item.status_code,
            "bytes": len(item.body),
            "sha256": digest_imported_bytes(item.body),
        }
        if total_bytes + len(gzipped) > RAW_PAYLOAD_CAP_BYTES:
            entry["stored"] = False
            entry["skipped_reason"] = "raw_payload_cap_reached"
            index_entries.append(entry)
            continue
        n = per_source_counts.get(item.source, 0)
        per_source_counts[item.source] = n + 1
        source_dir = raw_root / item.source
        source_dir.mkdir(parents=True, exist_ok=True)
        path = source_dir / f"{n}.json.gz"
        path.write_bytes(gzipped)
        entry["stored"] = True
        entry["path"] = path.relative_to(resolved.path).as_posix()
        total_bytes += len(gzipped)
        index_entries.append(entry)
    raw_root.mkdir(parents=True, exist_ok=True)
    index = {
        "schema_version": "scout-acquire-raw-index:1",
        "cap_bytes": RAW_PAYLOAD_CAP_BYTES,
        "cap_note": "gzip-compressed bytes counted against the cap; entries after the cap is reached are listed but not stored.",
        "entries": index_entries,
    }
    (raw_root / "index.json").write_text(canonical_json_bytes(index).decode("utf-8"))


def _public_row(row: PostingRow) -> dict[str, object]:
    digest = _digest(row)
    opportunity_digest = digest_imported_bytes(row.normalized_url.encode("utf-8"))
    return {
        "opportunity_id": opportunity_digest.split(":", 1)[-1][:32],
        "snapshot_id": digest.split(":", 1)[-1][:32],
        "source_kind": "agent_discovered",
        "title": row.title,
        "employer": row.company,
        "url": row.url,
        "acquisition_state": "considered",
        "source_snapshot": {
            "source_kind": row.source_kind.value,
            "locator": row.normalized_url,
            "url": row.url,
            "status": "observed",
            "captured_at": _now(),
            "content_sha256": digest,
            "media_type": "application/json",
        },
    }


def acquire_node(
    context: NodeContext,
    input: AcquireInput,
    *,
    http_client: Any,
    exa: ExaSearchClient,
    ats: ATSBoardClient,
    watchlist: WatchlistClient,
    home_root: Path | None = None,
    target: Path | None = None,
) -> AcquireOutput:
    # B4: `progress.finish_step("acquire", ...)` must run on every exit path
    # (success or a raised AcquireAllSourcesFailedError/other exception), so
    # the real body is a nested function and this outer frame is the single
    # try/finally around it -- the sealed control flow inside is untouched.
    progress = _progress_writer(context, home_root, target)
    if progress is not None:
        progress.start_step("acquire")
    try:
        output = _acquire_node_body(
            context,
            input,
            http_client=http_client,
            exa=exa,
            ats=ats,
            watchlist=watchlist,
            home_root=home_root,
            target=target,
            progress=progress,
        )
    except BaseException:
        if progress is not None:
            progress.finish_step("acquire", ok=False)
        raise
    if progress is not None:
        progress.finish_step("acquire", ok=True)
    return output


def _acquire_node_body(
    context: NodeContext,
    input: AcquireInput,
    *,
    http_client: Any,
    exa: ExaSearchClient,
    ats: ATSBoardClient,
    watchlist: WatchlistClient,
    home_root: Path | None,
    target: Path | None,
    progress: ProgressWriter | None,
) -> AcquireOutput:
    batch_id = _safe_batch_id(context.operation_key)
    failures: list[FailureRow] = []
    rows: list[PostingRow] = list(input.rows)
    watchlist_refs: list[str] = []
    source_outcomes: dict[str, bool] = {}
    recording_client = _RecordingHTTPClient(http_client, source_for_url=_source_from_url) if http_client is not None else None
    active_client = recording_client if recording_client is not None else http_client

    if not rows:
        if input.config.sources.exa:
            exa_ok = False
            try:
                discovered = tuple(exa.search(active_client, input.config, home_root=home_root))
                rows.extend(discovered)
                for row in discovered:
                    ref = _watchlist_add(watchlist, row, query_key=row.query_key, batch_id=batch_id)
                    if ref:
                        watchlist_refs.append(ref)
                exa_ok = True
            except Exception as exc:
                failures.append(FailureRow(SourceKind.EXA, "exa", None, type(exc).__name__.lower(), "Exa search failed"))
            source_outcomes["exa"] = exa_ok
        if input.config.sources.ats:
            ats_ok = False
            try:
                boards = _watchlist_entries(watchlist)
                if not boards:
                    ats_ok = True
                for board in boards:
                    try:
                        rows.extend(ats.list_board(active_client, board.provider.value, board.board_token, input.config))
                        ats_ok = True
                    except Exception as exc:
                        failures.append(FailureRow(SourceKind.ATS, board.board_token, None, type(exc).__name__.lower(), "ATS board fetch failed"))
            except Exception as exc:
                failures.append(FailureRow(SourceKind.ATS, "ats", None, type(exc).__name__.lower(), "ATS watchlist fetch failed"))
            source_outcomes["ats"] = ats_ok

        # B5 (0.1.8.1, live UAT: a run where every source failed still
        # reported "succeeded" with 0 postings). The original check here
        # was `source_outcomes and not any(source_outcomes.values())`, which
        # has a vacuous-success hole: ATS sets `ats_ok = True` whenever its
        # watchlist has *no boards to fetch* (`boards` empty, :431) -- a
        # legitimate "nothing to do" case on its own, but on a *first* run
        # (or any run where Exa is the only source that would have populated
        # the watchlist and Exa itself failed), that same "no boards yet"
        # state is indistinguishable from "ATS succeeded". `exa: False,
        # ats: True (vacuous)` then made `any(source_outcomes.values())`
        # true, and the node returned COMPLETE with an empty batch instead
        # of raising. The correct invariant (per the B5 ticket: "0 postings
        # and >=1 source failure") doesn't depend on ATS's vacuous-ok
        # bookkeeping at all -- it only needs to know whether *any* row was
        # actually produced and whether *any* source recorded a real
        # failure. `source_outcomes` is kept only for the error message's
        # per-source failure codes below, not as the raise condition itself.
        if not rows and failures:
            codes = ", ".join(f"{failure.source_kind.value}:{failure.code}" for failure in failures)
            detail = f" ({codes})" if codes else ""
            raise AcquireAllSourcesFailedError(
                "acquire_all_sources_failed",
                f"every enabled acquisition source failed{detail}",
            )

    rows = _merge_exa_and_ats_rows(rows)

    # B1 (0.1.8.1, operator: "why are we doing filtering on fucking ui?"):
    # apply the country/location (`exclusion_reason`) and role filters right
    # after fetch, on the full merged set -- not left for UI chips to apply
    # against every row the ATS APIs returned worldwide. A non-matching row
    # is dropped from `results`/`outputs/acquire.json` entirely (never
    # reaches the NEW/EDITED/selection accounting below); `raw/` (written
    # separately, `_write_raw_payloads`) still keeps every response
    # unfiltered for audit/replay. Per-reason counts are recorded additively
    # on the output (`dropped_counts`) so the drop is visible on the run
    # without re-deriving it from raw/ vs acquire.json.
    #
    # 0.1.8.1 r1 (coordinator review, B1's region-token rule): the
    # drop-count key uses `location_mismatch_detail` rather than
    # `exclusion_reason`'s own (coarse, stable) return value, so a
    # region-only location ("AMER", "EMEA", ...) is auditable as its own
    # REGION_ONLY bucket instead of being folded into LOCATION_MISMATCH --
    # `exclusion_reason` itself is still what actually decides whether the
    # row is dropped at all (identical result either way; this only changes
    # which key the drop gets counted under).
    kept_rows: list[PostingRow] = []
    drop_counts: dict[NotAssessedReason, int] = {}
    for row in rows:
        reason = exclusion_reason(row, input.config)
        if reason is NotAssessedReason.LOCATION_MISMATCH:
            reason = location_mismatch_detail(row, input.config) or reason
        if reason is None and not _role_match(row, input.config.roles):
            reason = NotAssessedReason.ROLE_MISMATCH
        if reason is not None:
            drop_counts[reason] = drop_counts.get(reason, 0) + 1
            continue
        kept_rows.append(row)
    rows = kept_rows

    current = {row.normalized_url: _digest(row) for row in rows}
    resolved = _resolved(context, home_root, target)
    previous = _prior_observations(resolved.path, batch_id)
    url_diff = diff_url_sets(previous, current)
    added = {item.url for item in url_diff.added}
    edited = {item.url for item in url_diff.edited}
    # uat-bug-009 root cause: an UNCHANGED row (same content digest as an
    # earlier acquire) used to be excluded from `candidates` outright --
    # even when that earlier run never actually produced a successful
    # assessment for it (a failed run, an over-cap drop, a since-changed
    # resume revision). A run 1 that fails at assess then made every posting
    # in run 2 "unchanged" and permanently unassessable. Fixed: an UNCHANGED
    # row only stays excluded when a *successful* assessment of the exact
    # same content digest exists for the *current* resume revision -- config
    # inputs that affect assess (resume content, via its revision id; the
    # digest already captures the posting content itself) -- otherwise it's
    # still eligible for selection under the cap, same as NEW/EDITED. A
    # changed/unresolvable resume revision makes an earlier assessment not
    # count (never a match), so it never wrongly skips a posting the
    # operator's new resume hasn't actually been assessed against.
    #
    # S25 A2/F1-b: the corrected cache key adds `profile_id` ALONGSIDE the
    # resume-revision dimension (never replacing it -- see
    # `_run_profile_identity`'s own docstring for the r1 design defect this
    # fixes). A legacy run (no sealed `profile_ref`) is attributed to the
    # gig's migrated-default profile for this comparison, resolved once per
    # acquire call.
    default_profile_id = _default_profile_id(resolved)
    current_identity = _run_profile_identity(resolved.path, context.run_id, default_profile_id=default_profile_id)
    current_resume_revision_id = None if current_identity is None else current_identity.resume_revision_id
    current_profile_id = None if current_identity is None else current_identity.profile_id
    prior_assessments = _prior_assessments(resolved.path, context.run_id, default_profile_id=default_profile_id)
    results: list[PostingRowResult] = []
    candidates: list[PostingRow] = []
    carried_forward: dict[str, _PriorAssessment] = {}
    for row in rows:
        if row.normalized_url in added:
            outcome = RowOutcome.NEW
        elif row.normalized_url in edited:
            outcome = RowOutcome.EDITED
        else:
            outcome = RowOutcome.UNCHANGED
        results.append(PostingRowResult(row, outcome))
        is_candidate = outcome in {RowOutcome.NEW, RowOutcome.EDITED}
        if outcome is RowOutcome.UNCHANGED:
            prior = prior_assessments.get(row.normalized_url)
            if (
                prior is not None
                and prior.result.posting.content_sha256 == row.content_sha256
                and current_resume_revision_id is not None
                and prior.resume_revision_id == current_resume_revision_id
                # S25 A2: profile_id must ALSO match -- a profile-B run never
                # carries forward profile-A's assessment of the same URL,
                # even when their resume revisions happen to coincide.
                # `None == None` is a legitimate match: no profile has ever
                # been migrated for this workpad at all (no profile system
                # engaged), which is exactly today's pre-F1-b behaviour and
                # must keep working unchanged, not be newly blocked by an
                # unattributable-profile false negative.
                and prior.profile_id == current_profile_id
            ):
                carried_forward[row.normalized_url] = prior
            else:
                is_candidate = True
        if progress is not None:
            # B4: one line per posting kept after the B1 filter, appended as
            # acquire produces it -- this is what lets a card render before
            # the whole run (or even the whole acquire step) finishes.
            progress.posting_acquired(row.to_json(), outcome=outcome.value)
        if input.selection_rule is SelectionRule.NEW_OR_EDITED_ROLE_MATCH and is_candidate:
            candidates.append(row)

    # P6: Jev pre-rank orders `candidates` before B2's diversity selection
    # picks from them, so a strong-fit posting wins a company-cap tie over a
    # weak one. Fails open by design (no key, no resume, any Jev error) --
    # `_rank_candidates` never raises; an empty `rank_scores` leaves
    # `candidates` in its original (pre-P6) order, so a run with no Jev key
    # behaves exactly as it did before this packet. `rank_scores` is sealed
    # onto `AcquireOutput` below (additive) so assess's own twin recompute
    # (`proposal_execution.py`'s eligible_postings ordering) can reuse the
    # SAME scores rather than re-calling Jev -- the two orderings can never
    # disagree, and a re-assess never re-spends.
    rank_scores = _rank_candidates(
        candidates,
        resolved=resolved,
        run_id=context.run_id,
        config=input.config,
        profile_id=current_profile_id,
        resume_revision_id=current_resume_revision_id,
        home_root=home_root,
    )
    candidates = list(order_by_rank(tuple(candidates), rank_scores))

    # B2 (0.1.8.1 live UAT): the naive first-N-in-batch-order walk let one
    # board's postings fill the entire selection (a run saw 5/5 picks from
    # ClickHouse alone, 3 sharing a title). `select_for_assessment` (a pure,
    # independently-tested module) replaces that walk: dedupe near-identical
    # postings, cap how many one company can contribute, then round-robin
    # fill the remaining cap across companies -- deterministic regardless of
    # `candidates`' order, so re-running on the same batch is a no-op. P6's
    # rank ordering above only breaks *ties* within what this still selects
    # -- the dedupe/company-cap/diversity rules themselves are unchanged.
    selection = select_for_assessment(candidates, cap=input.selection_cap)
    # `select_for_assessment` returns the same `PostingRow` objects it was
    # given (see selection.py's `ordered_selected`); the narrower `Candidate`
    # protocol is only its own input/output typing, so cast back for the
    # richer fields (`.url`, `_digest`) this module needs.
    selected_rows = cast("tuple[PostingRow, ...]", selection.selected)
    selected = [SelectedPosting(row.normalized_url, row.url, _digest(row), True) for row in selected_rows]
    # `selection.dropped`'s "duplicate"/"company_cap"/"over_cap" reasons feed
    # the same additive per-reason accounting as the exclusion-based drops
    # above (coordinator decision): "duplicate" maps onto the existing
    # NotAssessedReason.DUPLICATE; "company_cap" and "over_cap" both mean "an
    # otherwise-eligible row didn't fit under the cap" and fold onto the
    # existing NotAssessedReason.OVER_CAP -- no new enum value. Assess
    # re-derives the same per-row reason from the same pure helper for its
    # own not-assessed labeling (see proposal_execution.py), so the two can
    # never disagree.
    for selection_reason in selection.dropped.values():
        reason = NotAssessedReason.DUPLICATE if selection_reason == "duplicate" else NotAssessedReason.OVER_CAP
        drop_counts[reason] = drop_counts.get(reason, 0) + 1

    if progress is not None:
        # B4: the run's assess cap plus how many candidates it applies to
        # (operator: show "assessing 5 of 42 matches, cap 5"), known as soon
        # as acquire finishes selecting -- well before assess starts.
        progress.cap_known(cap=input.selection_cap, candidate_count=len(candidates))

    status = import_public_rows(resolved=resolved, batch_id=batch_id, rows=[_public_row(row) for row in rows] or [{
        "opportunity_id": "empty", "snapshot_id": digest_imported_bytes(b"empty")[:32], "source_kind": "agent_discovered", "title": "empty", "employer": "empty", "url": "https://example.invalid/empty", "acquisition_state": "excluded", "excluded_reason": "no_rows",
    }])
    if recording_client is not None:
        _write_raw_payloads(resolved, context.run_id, recording_client.captured)
    progress_files = sorted((resolved.path / "records" / "scout-acquisition" / batch_id / "progress").glob("*.json"))
    progress_ref = progress_files[-1].relative_to(resolved.path).as_posix() if progress_files else ""
    return AcquireOutput(
        batch_id=batch_id,
        batch_ref=status.input_ref["path"],
        progress_ref=progress_ref,
        progress_status=ProgressStatus.COMPLETE if status.complete else ProgressStatus.FAILED,
        rows=tuple(results),
        failures=tuple(failures),
        url_set_diff=url_diff,
        watchlist_refs=tuple(dict.fromkeys(watchlist_refs)),
        selected_postings=tuple(selected),
        dropped_counts=tuple(DropCount(reason, count) for reason, count in sorted(drop_counts.items(), key=lambda item: item[0].value)),
        carried_forward_assessments=tuple(
            CarriedForwardAssessment(url, prior.result, prior.run_date)
            for url, prior in sorted(carried_forward.items())
        ),
        rank_scores=rank_scores,
    )


__all__ = ["acquire_node"]
