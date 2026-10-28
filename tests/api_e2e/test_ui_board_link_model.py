"""0.1.11.12 (Workday P6): the job page's "Open on the job board" link knows a Workday tenant's host, the model run under node.

``ui/src/postingsModel.js`` is plain JavaScript: this runs it under the system ``node``. LOUD skip without ``node``.
The server's ``board_url`` for a Workday posting is the stored public address (``<tenant>.wd<N>.myworkdayjobs.com/<site>/job/...``,
never rebuilt from the requisition id: tests/behaviors/scout_pipeline/test_posting_liveness.py).

Pinned: ``boardLink`` answers ``{url, label, down, note}`` for a Workday host (any pod number, a locale segment
included), the company-page-down note as for every board; nothing for a company's own careers host, a Workday host on
plain http, or a host that only ends in the Workday suffix. The add-company form and the run text name Workday.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
WORKDAY = "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/Santa-Clara/Staff-Engineer_JR1990000"
LOCALE = "https://zoom.wd1.myworkdayjobs.com/en-US/Zoom/job/Remote/Engineer_R12"

SCRIPT = """
import * as m from MODEL_URL;

const out = {};
out.workday = m.boardLink({ board_url: WORKDAY, state: "open", company_page: "ok" });
out.locale = m.boardLink({ board_url: LOCALE, state: "closed" });
out.down = m.boardLink({ board_url: WORKDAY, state: "open", company_page: "down", company_page_note: " The company page is down. " });
out.lever = m.boardLink({ board_url: "https://jobs.lever.co/acme/acme-00001", state: "open" });
out.none = [
  m.boardLink({ board_url: "https://careers.nvidia.com/jobs/JR1990000", state: "open" }),
  m.boardLink({ board_url: "http://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/x/y", state: "open" }),
  m.boardLink({ board_url: "https://evil.example/nvidia.wd5.myworkdayjobs.com/x", state: "open" }),
  m.boardLink({ board_url: "https://nvidia.myworkdayjobs.com/x/job/y", state: "open" }),
  m.boardLink({ state: "open" }),
  m.boardLink(null),
];
out.label = m.BOARD_LINK_LABEL;
console.log(JSON.stringify(out));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the board-link model was NOT run")
    script = (
        SCRIPT.replace("MODEL_URL", json.dumps((UI_SRC / "postingsModel.js").resolve().as_uri()))
        .replace("WORKDAY", json.dumps(WORKDAY))
        .replace("LOCALE", json.dumps(LOCALE))
    )
    completed = subprocess.run([node, "--input-type=module", "-e", script], capture_output=True, text=True, timeout=60, check=False)
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_a_workday_posting_has_its_board_link(out: dict) -> None:
    assert out["workday"] == {"url": WORKDAY, "label": "Open on the job board", "down": False, "note": None}
    assert out["locale"] == {"url": LOCALE, "label": "Open on the job board", "down": False, "note": None}, "a locale segment, another pod"
    assert out["down"] == {"url": WORKDAY, "label": "Open on the job board", "down": True, "note": "The company page is down."}
    assert out["lever"]["url"] == "https://jobs.lever.co/acme/acme-00001" and out["label"] == "Open on the job board"


def test_what_is_no_board_address_has_no_link(out: dict) -> None:
    assert out["none"] == [None] * 6, "a company host, plain http, a look-alike path, a host without the pod, no address"


def test_the_add_company_form_and_the_run_text_name_workday() -> None:
    form = (UI_SRC / "components" / "AddCompanyForm.jsx").read_text(encoding="utf-8")
    assert form.count("Pinpoint, Breezy or Workday") == 2, "the hint and the refusal sentence both name Workday"
    assert "excluded_board: null," in form, "a 422 `excluded_board` shows the server's message (company, reason, evidence): `FRIENDLY[code] || detail` falls through to `detail`"
    run_text = (UI_SRC / "runText.js").read_text(encoding="utf-8")
    assert "Recruitee, Pinpoint, Breezy, Workday)" in run_text
