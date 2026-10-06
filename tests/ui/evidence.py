"""Screenshots of a changed screen, kept when GIGAI_UI_EVIDENCE_DIR names a folder (synthetic fixtures only; off otherwise)."""

from __future__ import annotations

import os
from pathlib import Path


def shot(ui, name: str) -> None:
    folder = os.environ.get("GIGAI_UI_EVIDENCE_DIR")
    if not folder:
        return
    Path(folder).mkdir(parents=True, exist_ok=True)
    ui.page.screenshot(path=str(Path(folder) / f"{name}.png"))
