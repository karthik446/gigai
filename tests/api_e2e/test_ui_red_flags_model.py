"""0.1.11.12 dossier-C (gigai-6po): the red flags beside the fit number, run under node.

``ui/src/redFlagsModel.js`` is plain JavaScript; the records here are synthetic and shaped as the server serves them
(``red_flags``: a list of ``{kind, text}`` on the Jobs row, the quick item and ``POST /api/assess``'s response).
The job page and the Jobs row render the model's list as text. LOUD skip without ``node``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

LOCATION = {"kind": "location", "text": "San Francisco, CA - Hybrid - US; New York, NY - Hybrid"}
LANGUAGE = {"kind": "language", "text": "Asks for Java, Kotlin"}
LEVEL = {"kind": "level", "text": "Asks for 3+ years of customer-facing experience in AI/ML"}

SCRIPT = """
import * as m from MODEL_URL;

const LOCATION = LOCATION_JSON;
const LANGUAGE = LANGUAGE_JSON;
const LEVEL = LEVEL_JSON;
const out = {};
// the job page: the served answer first, then the Jobs row it was opened from, then the quick item
out.served = m.redFlagsOf({ served: { red_flags: [LOCATION, LANGUAGE] }, listedRow: { red_flags: [LEVEL] }, quick: { red_flags: [] } });
out.listedRow = m.redFlagsOf({ served: { posting: {} }, listedRow: { red_flags: [LANGUAGE] }, quick: { red_flags: [LEVEL] } });
out.quick = m.redFlagsOf({ served: null, listedRow: null, quick: { red_flags: [LEVEL] } });
out.three = m.redFlagsOf({ listedRow: { red_flags: [LOCATION, LANGUAGE, LEVEL] } });
// a clean record: [] served; no key; null; a value that is not a list; nothing at all
out.clean = m.redFlagsOf({ served: { red_flags: [] }, listedRow: { red_flags: [LEVEL] } });
out.missingKey = m.redFlagsOf({ served: { posting: {} }, listedRow: { fit: 92 }, quick: { result: {} } });
out.nullValue = m.redFlagsOf({ served: { red_flags: null }, listedRow: { red_flags: null }, quick: { red_flags: null } });
out.notAList = m.redFlagsOf({ listedRow: { red_flags: "Asks for Java" } });
out.nothing = m.redFlagsOf({});
out.noArgs = m.redFlagsOf();
// a flag the server sent without a text, or with one that is not a string, is dropped
out.dropped = m.redFlagsOf({ listedRow: { red_flags: [LOCATION, { kind: "level" }, { kind: "language", text: 7 }, null] } });
// the Jobs row's compact marker
out.marker = {
  none: m.redFlagsMarker([]),
  one: m.redFlagsMarker([LANGUAGE]),
  two: m.redFlagsMarker([LOCATION, LANGUAGE]),
  three: m.redFlagsMarker([LOCATION, LANGUAGE, LEVEL]),
};
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the red flags model was NOT run")
    assert node is not None
    script = (
        SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "redFlagsModel.js").resolve().as_uri()))
        .replace("LOCATION_JSON", json.dumps(LOCATION))
        .replace("LANGUAGE_JSON", json.dumps(LANGUAGE))
        .replace("LEVEL_JSON", json.dumps(LEVEL))
    )
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_flags_are_the_served_ones_then_the_rows_then_the_quick_items(out: dict) -> None:
    assert out["served"] == [LOCATION, LANGUAGE]
    assert out["listedRow"] == [LANGUAGE]
    assert out["quick"] == [LEVEL]
    assert out["three"] == [LOCATION, LANGUAGE, LEVEL]


def test_a_clean_record_a_missing_key_and_null_give_no_flag(out: dict) -> None:
    assert out["clean"] == []  # the served answer says [] and wins: the stale row's flag is not shown
    assert out["missingKey"] == []
    assert out["nullValue"] == []
    assert out["notAList"] == []
    assert out["nothing"] == []
    assert out["noArgs"] == []


def test_a_flag_without_a_text_is_dropped(out: dict) -> None:
    assert out["dropped"] == [LOCATION]


def test_the_jobs_row_marker_counts_the_flags_and_names_them_in_its_title(out: dict) -> None:
    assert out["marker"]["none"] is None
    assert out["marker"]["one"] == {"label": "1 red flag", "title": "Asks for Java, Kotlin", "count": 1}
    assert out["marker"]["two"]["label"] == "2 red flags"
    assert out["marker"]["two"]["title"] == f"{LOCATION['text']}\n{LANGUAGE['text']}"
    assert out["marker"]["three"]["label"] == "3 red flags"


def test_the_views_render_the_flags_as_text_beside_the_fit_number() -> None:
    """The flag text is the posting's own words: rendered as text, never as HTML."""
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert 'from "../redFlagsModel.js"' in page
    assert 'data-role="red-flags"' in page, "the job page's score box lists the flags"
    assert "{flag.text}" in page
    rows = (UI_SRC / "views" / "JobsView.jsx").read_text(encoding="utf-8")
    assert 'from "../redFlagsModel.js"' in rows
    assert 'data-testid="red-flags-chip"' in rows, "the Jobs row carries one compact marker beside the score chips"
    for source in (page, rows):
        assert "dangerouslySetInnerHTML" not in source
    styles = (UI_SRC / "styles.css").read_text(encoding="utf-8")
    assert ".score-box-flags" in styles
    assert ".score-chip.red-flags" in styles
