from __future__ import annotations

import json
from pathlib import Path

import pytest


FIXTURES = Path(__file__).with_name("fixtures")


@pytest.fixture
def fixture_root() -> Path:
    return FIXTURES


def load_fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))
