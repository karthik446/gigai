"""0.1.10.7-fix2: four UI defects found while looking at every release screenshot, run under node.

Each asserts the END outcome the user reads: the company name on the Generate
PDF page, the source chip on a posting assessed from the Jobs list, and a
posting's line breaks. (The fourth, the "73 -> 91 after tailoring" chip, went
with the tailoring in 0.1.11: the timeline has no such line, pinned below.)
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
const display = await import(process.argv[1] + "/display.js");
const job = await import(process.argv[1] + "/jobModel.js");
const pipe = await import(process.argv[1] + "/pipelineModel.js");
const posting = "About the role.\\n\\nRequirements:\\n- 5+ years of Python\\n- Postgres  and   SQL\\n\\t- Kubernetes\\r\\n\\nNice to have:\\n- Rust";
console.log(JSON.stringify({
  at: [display.atCompany("tallgrass-health"), display.atCompany("Customer.io"), display.atCompany(""), display.atCompany(null)],
  chip: [
    job.showQuickAssessChip({ status: "on_demand", quick: { origin: "job_page" } }),
    job.showQuickAssessChip({ status: "on_demand", quick: { origin: "quick_assess" } }),
    job.showQuickAssessChip({ status: "on_demand", quick: {} }),
    job.showQuickAssessChip({ status: "posting", quick: null }),
  ],
  variantLine: typeof pipe.variantLine,
  excerpt: job.jdExcerpt(posting, { target: 5000, limit: 5000 }),
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the fix2 UI models were NOT checked")
    done = subprocess.run(
        ["node", "--input-type=module", "-e", SCRIPT, str(UI_SRC)],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(done.stdout)


def test_generate_pdf_names_the_company_not_the_board_slug(out: dict) -> None:
    assert out["at"] == [" at Tallgrass Health", " at Customer.io", "", ""]
    view = (UI_SRC / "views" / "PdfView.jsx").read_text(encoding="utf-8")
    assert "atCompany(job && job.company)" in view and "${job.company}" not in view


def test_a_posting_assessed_from_the_jobs_list_is_not_called_a_quick_assess(out: dict) -> None:
    assert out["chip"] == [False, True, True, False]
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert "showQuickAssessChip(job) ? <QuickAssessChip" in page


def test_the_before_and_after_tailoring_line_is_gone(out: dict) -> None:
    # 0.1.11 (SPEC 4.2): one assessment, no tailored variant: the line has nothing to print.
    assert out["variantLine"] == "undefined"
    timeline = (UI_SRC / "components" / "PipelineTimeline.jsx").read_text(encoding="utf-8")
    assert "tailored-variant" not in timeline and "variantLine" not in timeline


def test_the_job_description_keeps_its_line_breaks_as_text(out: dict) -> None:
    assert out["excerpt"]["text"] == (
        "About the role.\n\nRequirements:\n- 5+ years of Python\n- Postgres and SQL\n- Kubernetes\n\nNice to have:\n- Rust"
    )
    for path in UI_SRC.rglob("*.jsx"):
        source = path.read_text(encoding="utf-8")
        assert "dangerouslySetInnerHTML" not in source and ".innerHTML" not in source, path.name
    assert "white-space: pre-wrap" in (UI_SRC / "styles.css").read_text(encoding="utf-8").split(".jd-excerpt", 1)[1].split("}", 1)[0]
