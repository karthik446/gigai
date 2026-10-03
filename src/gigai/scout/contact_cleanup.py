"""0110-046 packet 4: the one-time cleanup of contact data an install stored before 0.1.10.7.

GigAI no longer stores the user's name or contact details. An install from before kept them in two
places, and this removes them once:

1. ``<home>/scout/resume-display.json``: the saved ``name`` and ``contact`` items. The file is
   rewritten with its layout (titles, spacing, auto fit) only.
2. Every stored resume of the workpad (each resume record's current revision, and any revision a
   profile pins): the text goes through ``resume_pii.strip_contact_lines`` (what an import does since
   0.1.10.7) and, when that changes it, the clean text is stored through the normal write path: a new
   reference and a new record revision (a new record for a pinned older revision), and every profile
   that pinned the old revision is re-pointed to the clean one (``profile_records.write_profile``).

The workpad's journal is append-only: the earlier revisions stay in its local history (git), and this
does NOT rewrite it. The report says so. A run's "unchanged posting" skip compares resume revisions; a
cleaned revision is the same resume for that check (``same_resume_revision``: the model never saw the
removed lines), so nothing is re-assessed because of the cleanup, and stored assessments are not marked
stale by it (``assessment_basis`` never compares the resume).

The outcome is kept in ``<home>/scout/contact-cleanup.json`` (0600): per workpad and for the display
file, WHAT KINDS and HOW MANY were removed and where (counts only, never a value), the revision aliases,
and whether the UI has shown the report. Idempotent: a workpad or a display file already cleaned is not
touched again. Safe on a read-only or odd home: any failure is reported (``status: "failed"`` with a
code) and nothing is marked done, so the next start tries again; it never raises.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "scout-contact-cleanup:1"
REPORT_SCHEMA = "scout-contact-cleanup-report:1"
#: The report's line about history (0110-046: say it plainly; a purge is a later packet).
HISTORY_NOTE = (
    "Earlier copies remain in the local history of your Scout workpad (an append-only journal on this "
    "machine); this cleanup does not rewrite that history."
)


def marker_path(home_root: Path) -> Path:
    return home_root / "scout" / "contact-cleanup.json"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read(home_root: Path) -> dict[str, object]:
    path = marker_path(home_root)
    try:
        if path.is_symlink() or not path.is_file():
            return {}
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if type(raw) is dict and raw.get("schema_version") == SCHEMA_VERSION else {}


def _write(home_root: Path, state: dict[str, object]) -> None:
    from gigai.scout.find_jobs.discovery.storage import atomic_write

    path = marker_path(home_root)
    if path.is_symlink():
        raise OSError("contact cleanup state path is a symlink")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    atomic_write(path, (json.dumps({**state, "schema_version": SCHEMA_VERSION}, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    os.chmod(path, 0o600)


def _add(total: dict[str, int], more: dict[str, int]) -> None:
    for kind, count in more.items():
        total[kind] = total.get(kind, 0) + count


# --- the display file ---------------------------------------------------------------------------


def _clean_display(home_root: Path) -> dict[str, int]:
    """Drop a saved name / contact from the display file (layout kept); what was removed, by field."""

    from .resume_display import DisplaySettings, legacy_contact_fields, load_display, save_display

    found = legacy_contact_fields(home_root)
    if found:
        save_display(home_root, load_display(home_root) or DisplaySettings())
    return {key: value for key, value in found.items() if key in ("name", "contact")}


# --- the stored resumes -------------------------------------------------------------------------


@dataclass(frozen=True)
class _Revision:
    record_id: str
    revision_id: str
    latest: bool


def _resume_revisions(resolved, home_root: Path, target: Path | None) -> list[_Revision]:
    """Each resume record's current revision, plus every revision a profile pins."""

    from .. import private_records
    from . import profile_records

    references = {
        item["reference_id"]
        for item in private_records.list_imports(home_root=home_root, requested_target=target, family="reference", gig_id=resolved.gig_id)
        if item.get("kind") == "resume" and isinstance(item.get("reference_id"), str)
    }
    snapshot = private_records._private_snapshot(resolved)
    record_ids = sorted({
        Path(path).parts[1] for path in snapshot.artifacts
        if len(Path(path).parts) == 4 and Path(path).parts[0] == "records" and Path(path).parts[2] == "revisions"
    })
    found: dict[tuple[str, str], _Revision] = {}
    resume_revisions: dict[tuple[str, str], bool] = {}
    for record_id in record_ids:
        revisions = private_records.list_revisions(resolved=resolved, record_id=record_id, snapshot=snapshot)
        for index, revision in enumerate(revisions):
            content = revision.get("content")
            if isinstance(content, dict) and content.get("family") == "g45_reference" and content.get("reference_id") in references:
                resume_revisions[(record_id, str(revision["revision_id"]))] = index == len(revisions) - 1
    for key, latest in resume_revisions.items():
        if latest:
            found[key] = _Revision(key[0], key[1], True)
    for profile in profile_records.list_profiles(resolved):
        key = (profile.resume_ref.record_id, profile.resume_ref.revision_id)
        if key in resume_revisions and key not in found:
            found[key] = _Revision(key[0], key[1], False)
    return sorted(found.values(), key=lambda item: (item.record_id, item.revision_id))


def _store_clean(resolved, home_root: Path, target: Path | None, revision: _Revision, text: str, label: str) -> tuple[str, str, str]:
    """``(record_id, revision_id, content_sha256)`` of the clean copy, through the normal write path."""

    from ..canonical import digest_imported_bytes
    from ..private_records import create_record, import_reference

    data = text.encode("utf-8")
    digest = digest_imported_bytes(data)
    directory = Path(tempfile.mkdtemp(prefix="gigai-cleanup-")).resolve()
    try:
        source = directory / label
        source.write_bytes(data)
        imported = import_reference(
            home_root=home_root, requested_target=target, gig_id=resolved.gig_id, kind="resume", source=source,
            operation_key=f"scout-contact-cleanup:{revision.revision_id}",
        )
    finally:
        shutil.rmtree(directory, ignore_errors=True)
    record = create_record(
        home_root=home_root, requested_target=target, gig_id=resolved.gig_id, kind="imported_reference",
        content_family="g45_reference", content_id=imported.item_id, actor={"kind": "gigai", "id": "contact-cleanup"},
        origin="imported", operation_key=f"scout-contact-cleanup-record:{revision.revision_id}",
        record_id=revision.record_id if revision.latest else None, parent_revision=revision.revision_id if revision.latest else None,
    )
    return record.record_id, record.revision_id, digest


def _clean_resumes(resolved, home_root: Path, target: Path | None) -> tuple[dict[str, object], dict[str, str]]:
    """``(report, {new revision: old revision})`` for one workpad."""

    from .. import private_records
    from . import profile_records
    from .find_jobs.contracts import PinnedResume
    from .resume_import import _stored_name
    from .resume_pii import strip_contact_lines

    revisions = _resume_revisions(resolved, home_root, target)
    references = private_records.list_imports(home_root=home_root, requested_target=target, family="reference", gig_id=resolved.gig_id)
    removed: dict[str, int] = {}
    replaced: dict[tuple[str, str], tuple[str, str, str]] = {}
    for revision in revisions:
        stored = private_records.read_record(
            home_root=home_root, requested_target=target, record_id=revision.record_id, revision_id=revision.revision_id,
            content=True, gig_id=resolved.gig_id,
        )
        content = stored.get("content")
        if not isinstance(content, bytes):
            continue
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            continue
        stripped = strip_contact_lines(text)
        # The stored label is the imported file's name: one holding the removed name is not kept either.
        reference_id = _reference_of(resolved, revision)
        old_label = next((item.get("label") for item in references if item.get("reference_id") == reference_id), None)
        label = _stored_name(old_label, stripped.name_words) if isinstance(old_label, str) and old_label else "resume.md"
        if not stripped.changed and label == old_label:
            continue
        clean = stripped.text if stripped.text.endswith("\n") or not stripped.text else stripped.text + "\n"
        replaced[(revision.record_id, revision.revision_id)] = _store_clean(resolved, home_root, target, revision, clean, label)
        _add(removed, stripped.removed if stripped.changed else {"name": 1})
    repointed = 0
    for profile in profile_records.list_profiles(resolved):
        new = replaced.get((profile.resume_ref.record_id, profile.resume_ref.revision_id))
        if new is None:
            continue
        profile_records.write_profile(resolved, profile_id=profile.profile_id, resume_ref=PinnedResume(record_id=new[0], revision_id=new[1], content_sha256=new[2]))
        repointed += 1
    report = {"resumes_checked": len(revisions), "resumes_cleaned": len(replaced), "removed": removed, "profiles_repointed": repointed}
    return report, {new[1]: old[1] for old, new in replaced.items()}


def _reference_of(resolved, revision: _Revision) -> str | None:
    from .. import private_records

    for item in private_records.list_revisions(resolved=resolved, record_id=revision.record_id):
        if item.get("revision_id") == revision.revision_id and isinstance(item.get("content"), dict):
            value = item["content"].get("reference_id")
            return value if isinstance(value, str) else None
    return None


# --- the one entry point ------------------------------------------------------------------------


def run_cleanup(*, home_root: Path, target: Path | None) -> dict[str, object]:
    """Clean what is not cleaned yet (the display file once per home, the target's workpad once) and return the report.

    Never raises. ``status`` is ``"done"`` when this call cleaned something new, ``"already_done"`` when nothing was
    left to do, ``"failed"`` (with ``code``) when a part could not run: that part is not marked done, so the next
    call tries it again. A target with no Scout workpad yet has nothing to clean (and is not marked either)."""

    from ..workpad import resolve_workpad

    state = _read(home_root)
    workpads = dict(state.get("workpads") or {}) if isinstance(state.get("workpads"), dict) else {}
    aliases = dict(state.get("aliases") or {}) if isinstance(state.get("aliases"), dict) else {}
    changed, failure = False, None
    if not isinstance(state.get("display"), dict):
        try:
            state["display"] = {"done_at": _now(), "removed": _clean_display(home_root)}
            changed = True
        except Exception as exc:  # noqa: BLE001 - reported, never raised (a read-only or odd home)
            failure = getattr(exc, "code", type(exc).__name__)
    resolved = None
    if target is not None and failure is None:
        try:
            resolved = resolve_workpad(home_root=home_root, requested_target=target, gig_id=None, allow_semantic_state=True)
        except Exception:  # noqa: BLE001 - no Scout workpad for this folder yet: nothing stored to clean
            resolved = None
    if resolved is not None and f"{resolved.project_id}:{resolved.gig_id}" not in workpads:
        try:
            report, new_aliases = _clean_resumes(resolved, home_root, target)
            workpads[f"{resolved.project_id}:{resolved.gig_id}"] = {"done_at": _now(), **report}
            aliases.update(new_aliases)
            changed = True
        except Exception as exc:  # noqa: BLE001 - reported, never raised; the next start tries again
            failure = getattr(exc, "code", type(exc).__name__)
    if changed:
        state.update({"workpads": workpads, "aliases": aliases})
        removed_any = bool(_display_removed(state)) or any(item.get("resumes_cleaned") for item in workpads.values() if isinstance(item, dict))
        if removed_any and not state.get("shown_at"):
            state["shown"] = False
        elif "shown" not in state:
            state["shown"] = True  # nothing to tell
        try:
            _write(home_root, state)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            failure = failure or getattr(exc, "code", type(exc).__name__)
    if failure is not None:
        return {**report_json(state, status="failed"), "code": failure}
    return report_json(state, status="done" if changed else "already_done")


def _display_removed(state: dict[str, object]) -> dict[str, int]:
    display = state.get("display")
    removed = display.get("removed") if isinstance(display, dict) else None
    return dict(removed) if isinstance(removed, dict) else {}


def report_json(state: dict[str, object], *, status: str) -> dict[str, object]:
    """The report the UI shows once and ``gigai scout privacy`` prints: counts only, never a value."""

    display = _display_removed(state)
    resumes: dict[str, int] = {}
    cleaned = checked = repointed = 0
    for item in (state.get("workpads") or {}).values():  # type: ignore[union-attr]
        if isinstance(item, dict):
            _add(resumes, dict(item.get("removed") or {}))
            cleaned += int(item.get("resumes_cleaned") or 0)
            checked += int(item.get("resumes_checked") or 0)
            repointed += int(item.get("profiles_repointed") or 0)
    removed_any = bool(display) or cleaned > 0
    return {
        "schema_version": REPORT_SCHEMA,
        "status": status,
        "removed_any": removed_any,
        "display_settings": display,
        "resumes": {"checked": checked, "cleaned": cleaned, "removed": resumes, "profiles_repointed": repointed},
        "history_note": HISTORY_NOTE if cleaned else None,
        "shown": bool(state.get("shown", True)),
        "text": report_text(display, resumes, cleaned, repointed),
    }


def report_text(display: dict[str, int], resumes: dict[str, int], cleaned: int, repointed: int) -> str:
    """One paragraph, counts only."""

    from .resume_pii import removed_summary

    parts: list[str] = []
    if cleaned:
        noun = "resume" if cleaned == 1 else "resumes"
        parts.append(
            f"Removed contact details from {cleaned} stored {noun} ({removed_summary(resumes)})"
            + (f"; {repointed} profile{'' if repointed == 1 else 's'} now use{'s' if repointed == 1 else ''} the cleaned copy" if repointed else "")
            + "."
        )
    if display:
        what = ", ".join(f"{field} ({display[field]})" if field == "contact" else field for field in ("name", "contact") if field in display)
        parts.append(f"Removed the saved {what} from the PDF settings (title, spacing and auto fit kept).")
    if not parts:
        return "No stored contact details were found."
    if cleaned:
        parts.append(HISTORY_NOTE)
    return " ".join(parts)


def mark_shown(home_root: Path) -> bool:
    """The UI showed the report; ``False`` when it cannot be recorded (it may show again)."""

    state = _read(home_root)
    if not state:
        return False
    try:
        _write(home_root, {**state, "shown": True, "shown_at": _now()})
    except OSError:
        return False
    return True


def current_report(home_root: Path) -> dict[str, object] | None:
    """The stored report without running anything; ``None`` before the first cleanup."""

    state = _read(home_root)
    return report_json(state, status="already_done") if state else None


def same_resume_revision(home_root: Path | None, prior: str | None, current: str | None) -> bool:
    """True when ``prior`` and ``current`` are one resume for a run's unchanged skip: equal, or ``current`` is the
    contact cleanup's clean copy of ``prior`` (followed through repeated cleanups). Reads one small file."""

    if prior is None or current is None:
        return False
    if prior == current:
        return True
    if home_root is None:
        return False
    aliases = _read(home_root).get("aliases")
    if not isinstance(aliases, dict):
        return False
    seen: set[str] = set()
    while current in aliases and current not in seen:
        seen.add(current)
        current = aliases[current]
        if current == prior:
            return True
    return False


__all__ = [
    "HISTORY_NOTE",
    "REPORT_SCHEMA",
    "current_report",
    "mark_shown",
    "marker_path",
    "report_json",
    "report_text",
    "run_cleanup",
    "same_resume_revision",
]
