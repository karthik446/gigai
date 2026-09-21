"""Regression tests for conservative fast-unit lane classification."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import classify_source


def test_pure_function_is_fast_unit(tmp_path: Path) -> None:
    source = tmp_path / "pure_test.py"
    source.write_text(
        "def test_pure():\n"
        "    assert 1 + 1 == 2\n"
        "    assert 'tmp_path' == 'tmp_path'\n"
    )
    assert classify_source(str(source), "test_pure") == "fast_unit"


def test_fixture_process_and_persistence_usage_is_not_fast_unit(tmp_path: Path) -> None:
    source = tmp_path / "integration_test.py"
    source.write_text(
        "import sqlite3\n"
        "def _fixture(tmp_path):\n    return sqlite3.connect(tmp_path / 'state.sqlite')\n"
        "def test_integration():\n    _fixture(None)\n"
    )
    assert classify_source(str(source), "test_integration") == "integration"


def test_release_resource_usage_has_separate_lane(tmp_path: Path) -> None:
    source = tmp_path / "release_test.py"
    source.write_text(
        "import importlib.resources\n"
        "def test_release():\n    return importlib.resources.files('pkg')\n"
    )
    assert classify_source(str(source), "test_release") == "release"


def test_explicit_live_marker_is_not_fast_unit(tmp_path: Path) -> None:
    source = tmp_path / "live_test.py"
    source.write_text(
        "import pytest\n"
        "pytestmark = pytest.mark.g30_live\n"
        "def test_live():\n    assert True\n"
    )
    assert classify_source(str(source), "test_live") == "integration"
