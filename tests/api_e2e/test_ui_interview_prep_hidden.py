"""uat-bug-051: 'Prep for interview' is hidden in 0.1.9 (kept in source for 0.1.10)."""

from __future__ import annotations

import re
from pathlib import Path

from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
SRC = UI / "src"


def test_the_prep_panel_is_gated_off_in_one_place_and_kept_in_source() -> None:
    panel = (SRC / "components" / "PrepPanel.jsx").read_text()
    assert "export const INTERVIEW_PREP_VISIBLE = false;" in panel
    assert "if (!INTERVIEW_PREP_VISIBLE) return null;" in panel
    assert "Prep for interview" in panel  # hidden, not deleted


def test_every_mount_point_renders_through_the_gated_component() -> None:
    mounts = [p for p in SRC.rglob("*.jsx") if "<PrepPanel" in p.read_text()]
    assert {p.name for p in mounts} == {"PostingCard.jsx", "JobPage.jsx"}
    for path in mounts:
        assert 'import PrepPanel from "' in path.read_text(), path.name
    # the flag is defined once; no mount point overrides or duplicates it
    assert [p.name for p in SRC.rglob("*.js*") if "INTERVIEW_PREP_VISIBLE = " in p.read_text()] == ["PrepPanel.jsx"]


def test_the_built_bundle_carries_no_prep_panel_text() -> None:
    bundles = list((UI / "dist" / "assets").glob("*.js"))
    assert bundles
    for bundle in bundles:
        text = bundle.read_text()
        assert "Prep for interview" not in text
        assert not re.search(r"gigai scout prep", text)
