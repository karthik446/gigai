"""0.1.11 UINOTICE: ``ui/src/modelNoticeModel.js`` (the job page's model notice line) is pure JavaScript, run under the system ``node``.

The sentence is the server's (``evaluated_models.ModelNotice.text``); the model only reads the served item's
``model_notice`` and turns ``link: {label, path}`` into a docs URL. What lives in JSX is pinned by reading the source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.evaluated_models import RESULTS_LINK_LABEL, RESULTS_PAGE, model_notice
from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

SCRIPT = """
const m = await import(process.argv[1]);
const served = JSON.parse(process.argv[2]);
const link = served.link;
console.log(JSON.stringify({
  docs: m.DOCS_URL,
  served: m.modelNoticeLine({ model_notice: served }),
  none: [m.modelNoticeLine(null), m.modelNoticeLine({}), m.modelNoticeLine({ model_notice: null }), m.modelNoticeLine({ model_notice: { text: "  " } }), m.modelNoticeLine({ model_notice: { text: 5 } })],
  bareString: m.modelNoticeLine({ model_notice: { text: "t", link: "scout/accuracy-0-1-11" } }),
  badPath: [m.modelNoticeLine({ model_notice: { text: "t", link: { label: "L", path: "https://evil.test/x" } } }), m.modelNoticeLine({ model_notice: { text: "t", link: { label: "L", path: "../x" } } })],
  noLabel: m.modelNoticeLine({ model_notice: { text: "t", link: { label: " ", path: link.path } } }),
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("LOUD: node is not on PATH; the model notice model was NOT run")
    served = model_notice("claude_cli", "claude-haiku-4-5")
    assert served is not None
    done = subprocess.run(
        [node, "--input-type=module", "-e", SCRIPT, (UI_SRC / "modelNoticeModel.js").as_uri(), json.dumps(served.to_json())],
        capture_output=True, text=True, timeout=60, check=False,
    )  # fmt: skip
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_a_served_notice_is_its_text_and_a_link_under_words(out: dict) -> None:
    served = out["served"]
    assert served["text"].startswith("Assessed with claude-haiku-4-5.") and RESULTS_PAGE not in served["text"]
    assert served["label"] == RESULTS_LINK_LABEL == "GigAI's accuracy results"
    assert served["href"] == f"{out['docs']}{RESULTS_PAGE}/" == "https://karthik446.github.io/gigai/latest/scout/accuracy-0-1-11/"


def test_no_notice_shows_nothing_and_a_bad_link_shows_the_text_alone(out: dict) -> None:
    assert out["none"] == [None] * 5
    for line in (out["bareString"], *out["badPath"], out["noLabel"]):
        assert line["text"] == "t" and line["label"] is None and line["href"] is None


def test_the_job_page_shows_it_under_the_verdict_only_for_a_served_item() -> None:
    page = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert 'modelNoticeLine(job.quick)' in page and 'job.assessmentSource === "quick"' in page
    assert page.index('data-role="verdict-wording"') < page.index('data-role="model-notice"')
