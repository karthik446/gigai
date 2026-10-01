"""Update sources takes the shipped snapshot first, and never fails because of it (0110-026)."""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout.find_jobs import snapshot, sources_update


def test_update_sources_asks_for_the_snapshot_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[Path, Path | None]] = []
    monkeypatch.setattr(snapshot, "maybe_import_snapshot", lambda home, *a, target=None, **k: calls.append((home, target)))

    sources_update._import_shipped_snapshot(tmp_path, tmp_path / "scout")

    assert calls == [(tmp_path, tmp_path / "scout")]


def test_a_snapshot_failure_never_fails_the_update(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("release server on fire")

    monkeypatch.setattr(snapshot, "maybe_import_snapshot", boom)

    sources_update._import_shipped_snapshot(tmp_path, tmp_path / "scout")  # does not raise
