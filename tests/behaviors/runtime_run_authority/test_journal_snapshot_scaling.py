"""journal-snapshot-perf: a committed snapshot scales ~linearly with the

number of artifacts one commit published, and its fail-closed checks are
unchanged on that shared-index path.

Before this fix ``_capture_committed_snapshot`` re-parsed the publishing
commit's handoff (whose ``artifact_refs`` lists every sibling artifact)
and re-scanned that commit's file list once PER artifact, and asked
``git cat-file --batch`` for ``<head>:<path>`` (git re-scans the containing
tree per lookup). A Scout watchlist seed publishes ~10k records in one
commit, so ``list_active`` was O(n^2): 4.3 s at 1,000 records, 15.7 s at
2,000, 380 s at 10,370 (measured on the reference host, 3.13). Now the
commit's file list (``_CommitFiles``) and the handoff (``_HandoffIndex``)
are indexed once per snapshot and blobs are read by oid: 0.2 s / 0.4 s /
~1.5 s.

Lane: these tests write real journal commits (git subprocesses,
``tmp_path``), so ``tests/conftest.py`` classifies them ``integration``,
i.e. the behavior/source lanes, not ``make unit-tests``.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

import gigai.journal as journal
from gigai.canonical import digest_imported_bytes
from gigai.journal import (
    JournalArtifact,
    JournalConflictError,
    read_committed_artifact,
    run_with_journal_writer,
)
from tests.behaviors.runtime_run_authority.test_journal_snapshot_equivalence import (
    GIG_ID,
    PROJECT_ID,
    _workpad,
    _write,
)

_PREFIX = "records/scout-watchlist/"


def _record_path(index: int) -> str:
    return f"{_PREFIX}scout_watchlist:greenhouse:board{index:06d}.json"


def _record_bytes(index: int) -> bytes:
    return json.dumps(
        {
            "watchlist_id": f"scout_watchlist:greenhouse:board{index:06d}",
            "provider": "greenhouse",
            "board_token": f"board{index:06d}",
            "state": "active",
        },
        sort_keys=True,
    ).encode()


def _ref(path: str, data: bytes) -> dict[str, object]:
    return {
        "path": path,
        "content_sha256": digest_imported_bytes(data),
        "media_type": "application/json",
        "size_bytes": len(data),
    }


def _seed(tmp_path: Path, count: int, *, artifact_refs: list[dict[str, object]] | None = None) -> Path:
    """One transition publishing ``count`` watchlist records + a receipt --

    the exact shape ``watchlist.seed_watchlist_from_catalog`` commits.
    """

    tmp_path.mkdir(exist_ok=True)  # _workpad expects an existing parent
    workpad = _workpad(tmp_path)
    artifacts = tuple(JournalArtifact(_record_path(i), _record_bytes(i)) for i in range(count)) + (
        JournalArtifact("records/operations/scout_watchlist_seed-test.json", b'{"ok":true}'),
    )
    kwargs: dict[str, object] = {"artifacts": artifacts}
    if artifact_refs is not None:
        kwargs["front_matter"] = {"artifact_refs": artifact_refs}
    _write(workpad, f"seed-{count}", transition="scout_public_acquisition_progress", **kwargs)
    return workpad


def _snapshot(workpad: Path) -> journal.JournalSnapshot:
    return run_with_journal_writer(
        workpad=workpad,
        project_id=PROJECT_ID,
        gig_id=GIG_ID,
        operation=lambda writer: writer.snapshot((_PREFIX,)),
    )


def _timed_snapshot(workpad: Path, expected: int) -> float:
    best = float("inf")
    for _ in range(2):  # best of two: the first read warms the page cache
        started = time.perf_counter()
        snapshot = _snapshot(workpad)
        best = min(best, time.perf_counter() - started)
        assert len(snapshot.artifacts) == expected
    return best


# --------------------------------------------------------------------------
# Scaling
# --------------------------------------------------------------------------


def test_snapshot_time_grows_linearly_with_artifacts_of_one_commit(tmp_path: Path) -> None:
    small, large = 1000, 4000
    t_small = _timed_snapshot(_seed(tmp_path / "small", small), small)
    t_large = _timed_snapshot(_seed(tmp_path / "large", large), large)
    # Quadratic (pre-fix) was ~14x here (4.3 s -> ~60 s); linear-ish is ~3x.
    # 6x leaves room for noise on a loaded CI host without admitting O(n^2).
    assert t_large < 6 * t_small, f"snapshot scaled {t_large / t_small:.1f}x for 4x the artifacts"
    # Generous absolute budget (measured ~0.6 s on the reference host): a
    # regression to the quadratic shape blows through it by two orders.
    assert t_large < 20.0, f"snapshot of {large} artifacts took {t_large:.1f}s"


def test_snapshot_parses_a_shared_handoff_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    count = 50
    workpad = _seed(tmp_path, count)
    calls: list[int] = []
    real = journal.parse_json_front_matter

    def counting(document: bytes):
        calls.append(len(document))
        return real(document)

    monkeypatch.setattr(journal, "parse_json_front_matter", counting)
    snapshot = _snapshot(workpad)
    assert len(snapshot.artifacts) == count
    # One publishing commit -> one handoff -> parsed exactly once for all
    # `count` artifacts (it was `count` times before).
    assert len(calls) == 1


# --------------------------------------------------------------------------
# Fail-closed on the shared-index path
# --------------------------------------------------------------------------


def test_shared_index_still_rejects_tampered_missing_and_extra_artifacts(tmp_path: Path) -> None:
    count = 30
    workpad = _seed(tmp_path, count)
    assert len(_snapshot(workpad).artifacts) == count

    tampered = workpad / _record_path(7)
    original = tampered.read_bytes()
    tampered.write_bytes(original + b" ")
    with pytest.raises(JournalConflictError, match="working evidence differs"):
        _snapshot(workpad)
    tampered.write_bytes(original)
    assert len(_snapshot(workpad).artifacts) == count

    tampered.unlink()
    with pytest.raises(JournalConflictError, match="working evidence differs"):
        _snapshot(workpad)
    tampered.write_bytes(original)

    extra = workpad / _PREFIX / "scout_watchlist:greenhouse:uncommitted.json"
    extra.write_bytes(b"{}")
    with pytest.raises(JournalConflictError, match="extra or redirected"):
        _snapshot(workpad)
    extra.unlink()
    assert len(_snapshot(workpad).artifacts) == count


def test_shared_index_keeps_per_artifact_reference_checks(tmp_path: Path) -> None:
    """The handoff is parsed once, but each artifact is still matched against

    ITS OWN reference: a duplicated ref for one path is "ambiguous" and a
    ref whose digest is wrong is "differs from publication", for that path
    only -- the single-artifact reader agrees on both.
    """

    count = 20
    receipt = ("records/operations/scout_watchlist_seed-test.json", b'{"ok":true}')

    def refs(*, duplicate: int | None = None, wrong_digest: int | None = None) -> list[dict[str, object]]:
        listed = []
        for i in range(count):
            ref = _ref(_record_path(i), _record_bytes(i))
            if i == wrong_digest:
                ref["content_sha256"] = digest_imported_bytes(b"not these bytes")
            listed.append(ref)
            if i == duplicate:
                listed.append(dict(ref))
        listed.append(_ref(*receipt))
        return listed

    ambiguous = _seed(tmp_path / "ambiguous", count, artifact_refs=refs(duplicate=3))
    with pytest.raises(JournalConflictError, match="reference is ambiguous"):
        _snapshot(ambiguous)
    with pytest.raises(JournalConflictError, match="reference is ambiguous"):
        read_committed_artifact(workpad=ambiguous, project_id=PROJECT_ID, gig_id=GIG_ID, path=_record_path(3))
    # A sibling published by the same commit/handoff is still readable.
    data, _commit = read_committed_artifact(workpad=ambiguous, project_id=PROJECT_ID, gig_id=GIG_ID, path=_record_path(4))
    assert data == _record_bytes(4)

    forged = _seed(tmp_path / "forged", count, artifact_refs=refs(wrong_digest=11))
    with pytest.raises(JournalConflictError, match="digest differs from publication"):
        _snapshot(forged)
    with pytest.raises(JournalConflictError, match="digest differs from publication"):
        read_committed_artifact(workpad=forged, project_id=PROJECT_ID, gig_id=GIG_ID, path=_record_path(11))
    data, _commit = read_committed_artifact(workpad=forged, project_id=PROJECT_ID, gig_id=GIG_ID, path=_record_path(12))
    assert data == _record_bytes(12)


def test_shared_index_rejects_a_missing_reference(tmp_path: Path) -> None:
    count = 10
    refs = [_ref(_record_path(i), _record_bytes(i)) for i in range(count) if i != 5]
    refs.append(_ref("records/operations/scout_watchlist_seed-test.json", b'{"ok":true}'))
    workpad = _seed(tmp_path, count, artifact_refs=refs)
    with pytest.raises(JournalConflictError, match="reference is ambiguous"):
        _snapshot(workpad)
    with pytest.raises(JournalConflictError, match="reference is ambiguous"):
        read_committed_artifact(workpad=workpad, project_id=PROJECT_ID, gig_id=GIG_ID, path=_record_path(5))
