"""0110-046 packet 4: the one-time cleanup of contact data stored before 0.1.10.7.

A synthetic "pre-upgrade" install: a resume imported RAW (name, email, phone, address, links: the path
an import took before 0.1.10.7) pinned by the default profile, and a ``resume-display.json`` holding a
saved name and contact items.  ``contact_cleanup.run_cleanup`` then:

* rewrites the display file with its layout only, and stores a clean resume revision through the normal
  write path, re-pointing the profile to it;
* reports what kinds and how many were removed, where (counts only), says plainly that earlier copies
  stay in the local history, and is idempotent (a second run changes nothing, not even the journal);
* adds no marker value to any file or to any journal blob written by the cleanup; the only files under
  the workpad that still hold one are the ones committed BEFORE the cleanup (the history, kept honestly);
* keeps a run's unchanged skip (``same_resume_revision``) and never marks an assessment stale for it;
* on a read-only home it reports ``failed`` and raises nothing.
Synthetic markers only.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

from gigai.private_records import read_record
from gigai.scout import contact_cleanup
from gigai.scout.profile_records import ensure_default_profile, list_profiles
from gigai.scout.resume_display import SCHEMA_VERSION, display_path, load_display
from gigai.workpad import resolve_workpad
from tests.support.scout_profile_fixtures import build_gig_with_resume

RAW = (
    "Zora Quillfeather\n"
    "Staff Platform Engineer\n"
    "zora.q@example.invalid | (555) 014-2999 | 12 Quill Street, Nowhere, ZZ 00000\n"
    "linkedin.com/in/zq-invalid\n"
    "\n"
    "## Experience\n"
    "- Quillfeather led the scheduling rebuild on Python and Postgres.\n"
).encode("utf-8")
MARKERS = ("Zora", "Quillfeather", "zora.q@example.invalid", "014-2999", "Quill Street", "zq-invalid")


def _legacy_install(tmp_path: Path):
    fx = build_gig_with_resume(tmp_path, resume_text=RAW)
    resolved = resolve_workpad(home_root=fx.home_root, requested_target=fx.target, gig_id=fx.created.gig_id, allow_semantic_state=True)
    ensure_default_profile(resolved, home_root=fx.home_root, target=fx.target)
    path = display_path(fx.home_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": SCHEMA_VERSION, "name": "Zora Quillfeather",
        "contact": [{"kind": "email", "value": "zora.q@example.invalid"}, {"kind": "phone", "value": "(555) 014-2999"}, {"kind": "linkedin", "value": "linkedin.com/in/zq-invalid"}],
        "titles": {"p": "Staff Platform Engineer"}, "spacing_scale": 1.1, "auto_fit": False, "updated_at": "2026-09-30T00:00:00Z",
    }))
    return fx, resolved


def _git(workpad: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(workpad), *args], capture_output=True, text=True, check=True).stdout


def _hits(path: Path) -> list[str]:
    data = path.read_bytes()
    return [marker for marker in MARKERS if marker.encode() in data]


def test_the_cleanup_removes_reports_and_is_idempotent(tmp_path: Path) -> None:
    fx, resolved = _legacy_install(tmp_path)
    workpad = resolved.path
    before_head = _git(workpad, "rev-parse", "HEAD").strip()
    before_files = set(_git(workpad, "ls-files").splitlines())
    old_ref = list_profiles(resolved)[0].resume_ref

    report = contact_cleanup.run_cleanup(home_root=fx.home_root, target=fx.target)

    assert report["status"] == "done" and report["removed_any"] is True and report["shown"] is False
    assert report["display_settings"] == {"name": 1, "contact": 3}
    assert report["resumes"] == {"checked": 1, "cleaned": 1, "removed": {"name": 2, "email": 1, "phone": 1, "links": 1, "address": 1}, "profiles_repointed": 1}
    assert report["history_note"] == contact_cleanup.HISTORY_NOTE and "local history" in report["text"]
    assert report["text"].startswith("Removed contact details from 1 stored resume (name 2, email 1, phone 1, links 1, address 1); 1 profile now uses the cleaned copy.")
    assert "Removed the saved name, contact (3) from the PDF settings" in report["text"]
    for marker in MARKERS:
        assert marker not in json.dumps(report), marker

    # The display file keeps its layout and titles, nothing else.
    display = display_path(fx.home_root).read_text()
    assert _hits(display_path(fx.home_root)) == [] and load_display(fx.home_root).spacing_scale == 1.1
    assert stat.S_IMODE(contact_cleanup.marker_path(fx.home_root).stat().st_mode) == 0o600 and "Staff Platform Engineer" in display

    # The profile pins the clean revision of the same record; its text has no marker.
    resolved = resolve_workpad(home_root=fx.home_root, requested_target=fx.target, gig_id=fx.created.gig_id, allow_semantic_state=True)
    new_ref = list_profiles(resolved)[0].resume_ref
    assert new_ref.record_id == old_ref.record_id and new_ref.revision_id != old_ref.revision_id
    clean = read_record(home_root=fx.home_root, requested_target=fx.target, record_id=new_ref.record_id, revision_id=new_ref.revision_id, content=True, gig_id=fx.created.gig_id)["content"]
    assert clean.startswith(b"Staff Platform Engineer\n") and not [m for m in MARKERS if m.encode() in clean]

    # Every journal blob the cleanup ADDED is free of markers (git history before it is not rewritten).
    added = [line.split("\t")[-1] for line in _git(workpad, "diff", "--name-status", before_head, "HEAD").splitlines() if line.startswith(("A", "M"))]
    assert added, "the cleanup wrote through the journal"
    for name in added:
        assert [m for m in MARKERS if m.encode() in (workpad / name).read_bytes()] == [], name
    assert _git(workpad, "rev-list", "--count", f"{before_head}..HEAD").strip() != "0"
    assert _git(workpad, "merge-base", "--is-ancestor", before_head, "HEAD") == "", "history is kept, not rewritten"

    # Files that still hold a marker are only the ones committed before the cleanup (the honest history).
    holders = {str(path.relative_to(workpad)) for path in workpad.rglob("*") if path.is_file() and ".git" not in path.parts and _hits(path)}
    assert holders and holders <= before_files, holders - before_files
    home_holders = [path for path in fx.home_root.rglob("*") if path.is_file() and not path.is_symlink() and _hits(path)]
    assert home_holders == [], home_holders

    # A run's unchanged skip treats the clean copy as the same resume; anything else is not.
    assert contact_cleanup.same_resume_revision(fx.home_root, old_ref.revision_id, new_ref.revision_id)
    assert not contact_cleanup.same_resume_revision(fx.home_root, new_ref.revision_id, old_ref.revision_id)
    assert not contact_cleanup.same_resume_revision(fx.home_root, "rev_other", new_ref.revision_id)
    assert not contact_cleanup.same_resume_revision(None, old_ref.revision_id, new_ref.revision_id)

    # Idempotent: a second run touches nothing, not even the journal, and reports the same counts.
    head = _git(workpad, "rev-parse", "HEAD").strip()
    again = contact_cleanup.run_cleanup(home_root=fx.home_root, target=fx.target)
    assert again["status"] == "already_done" and again["resumes"] == report["resumes"] and again["display_settings"] == report["display_settings"]
    assert _git(workpad, "rev-parse", "HEAD").strip() == head

    # Shown once.
    assert contact_cleanup.mark_shown(fx.home_root) is True
    assert contact_cleanup.current_report(fx.home_root)["shown"] is True


def test_an_install_with_nothing_stored_reports_nothing(tmp_path: Path) -> None:
    fx = build_gig_with_resume(tmp_path, resume_text=b"## Summary\nPlatform engineer.\n")
    report = contact_cleanup.run_cleanup(home_root=fx.home_root, target=fx.target)
    assert report["status"] == "done" and report["removed_any"] is False and report["shown"] is True
    assert report["text"] == "No stored contact details were found." and report["history_note"] is None
    assert contact_cleanup.run_cleanup(home_root=fx.home_root, target=fx.target)["status"] == "already_done"


def test_a_read_only_home_is_reported_never_raised(tmp_path: Path) -> None:
    fx, _resolved = _legacy_install(tmp_path)
    scout = fx.home_root / "scout"
    mode = scout.stat().st_mode
    os.chmod(scout, 0o500)
    try:
        report = contact_cleanup.run_cleanup(home_root=fx.home_root, target=fx.target)
    finally:
        os.chmod(scout, stat.S_IMODE(mode))
    assert report["status"] == "failed" and isinstance(report["code"], str)
    assert not contact_cleanup.marker_path(fx.home_root).exists(), "nothing is marked done: the next start tries again"


def test_a_missing_target_cleans_the_display_file_only(tmp_path: Path) -> None:
    home = tmp_path / "home"
    path = display_path(home)
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "name": "Zora Quillfeather", "contact": [], "titles": {}}))
    report = contact_cleanup.run_cleanup(home_root=home, target=None)
    assert report["status"] == "done" and report["display_settings"] == {"name": 1} and report["resumes"]["checked"] == 0
    assert _hits(path) == []


@pytest.mark.parametrize("module", ["proposal_execution.py", "find_jobs/market_acquisition.py"])
def test_both_unchanged_skips_use_the_cleanup_alias_and_staleness_never_reads_the_resume(module: str) -> None:
    src = Path(__file__).resolve().parents[3] / "src" / "gigai" / "scout"
    text = (src / module).read_text(encoding="utf-8")
    assert "same_resume_revision(home_root, prior.resume_revision_id," in text
    assert "prior.resume_revision_id == " not in text
    basis = (src / "assessment_basis.py").read_text(encoding="utf-8")
    assert "resume_revision" not in basis and "content_sha256" not in basis, "a cleaned resume never makes an assessment stale"
