from __future__ import annotations

import sys

import pytest

VENDOR_PREFIX = "gigai.evals._vendor"


@pytest.fixture(autouse=True)
def _block_numpy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every eval test runs with numpy unimportable and the vendored stats re-imported under that block.

    `sys.modules["numpy"] = None` makes any `import numpy` raise ImportError whether or
    not numpy is installed; monkeypatch restores the previous entries after the test.
    """

    monkeypatch.setitem(sys.modules, "numpy", None)
    for name in [name for name in sys.modules if name == VENDOR_PREFIX or name.startswith(VENDOR_PREFIX + ".")]:
        monkeypatch.delitem(sys.modules, name)
