"""0.1.10.7 I3: the "?" beside rank, verdict, Scout label and Scout ATS score opens the docs page that explains it.

``ui/src/wording.js`` is pure JavaScript, run under the system ``node`` (LOUD skip when it is not on PATH). Each
link's anchor is checked against the headings of the docs page itself, so a renamed heading fails here. What lives
in JSX is checked statically, by reading the source.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
ROOT = Path(__file__).resolve().parents[2]
NUMBERS_PAGE = ROOT / "gigai-docs" / "src" / "content" / "docs" / "scout" / "numbers.md"
PAGE_URL = "https://karthik446.github.io/gigai/latest/scout/numbers/"

NODE_SCRIPT = """
const m = await import(process.argv[1]);
console.log(JSON.stringify({
  page: m.NUMBERS_DOCS_URL,
  links: m.HELP_LINKS,
  rank: m.helpLink("rank"),
  verdict: m.helpLink("verdict"),
  label: m.helpLink("scout-label"),
  ats: m.helpLink("ats"),
  unknown: m.helpLink("salary"),
  inherited: m.helpLink("constructor"),
}));
"""


def _run() -> dict[str, object]:
    if shutil.which("node") is None:
        pytest.skip("LOUD: node is not on PATH; the help link model was NOT checked")
    done = subprocess.run(
        ["node", "--input-type=module", "-e", NODE_SCRIPT, (UI_SRC / "wording.js").as_uri()],
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(done.stdout)


def _slug(heading: str) -> str:
    """The anchor the docs site gives a heading (lower case, punctuation dropped, spaces to hyphens)."""

    return re.sub(r"\s+", "-", re.sub(r"[^a-z0-9\s-]", "", heading.lower()).strip())


def test_each_of_the_four_things_has_a_stable_test_id_and_a_link_into_the_numbers_page() -> None:
    out = _run()
    assert out["page"] == PAGE_URL
    assert {topic: link["testId"] for topic, link in out["links"].items()} == {  # type: ignore[union-attr]
        "rank": "help-rank", "verdict": "help-verdict", "scout-label": "help-scout-label", "ats": "help-ats",
    }
    assert out["rank"] == {"testId": "help-rank", "href": f"{PAGE_URL}#rank", "label": "What the rank means"}
    assert out["verdict"]["href"] == f"{PAGE_URL}#verdict"  # type: ignore[index]
    assert out["label"]["href"] == f"{PAGE_URL}#scout-label"  # type: ignore[index]
    assert out["ats"]["href"] == f"{PAGE_URL}#scout-ats-score"  # type: ignore[index]
    # A thing the page does not explain has no link (and an inherited property name is not a topic).
    assert out["unknown"] is None and out["inherited"] is None


def test_every_link_target_is_a_heading_of_the_docs_page() -> None:
    if not NUMBERS_PAGE.is_file():
        pytest.skip("gigai-docs is excluded from the offline container build context")
    out = _run()
    page = NUMBERS_PAGE.read_text(encoding="utf-8")
    assert "title: What Scout's numbers and labels mean" in page
    anchors = {_slug(heading) for heading in re.findall(r"^## (.+)$", page, flags=re.M)}
    assert {"rank", "verdict", "scout-label", "scout-ats-score"} <= anchors
    for topic, link in out["links"].items():  # type: ignore[union-attr]
        base, _, anchor = link["href"].partition("#")
        assert base == PAGE_URL and anchor in anchors, f"{topic}: {link['href']} has no heading on the page"
    # The page is in the Scout sidebar, so the address the UI opens exists on the built site.
    assert "'scout/numbers'" in (ROOT / "gigai-docs" / "astro.config.mjs").read_text(encoding="utf-8")


def test_the_question_mark_sits_beside_each_thing() -> None:
    component = (UI_SRC / "components" / "HelpLink.jsx").read_text(encoding="utf-8")
    assert "data-testid={link.testId}" in component and 'target="_blank"' in component and 'rel="noreferrer"' in component
    assert "aria-label={link.label}" in component  # the "?" alone says nothing to a screen reader
    rank = (UI_SRC / "components" / "RankBadge.jsx").read_text(encoding="utf-8")
    assert '{detail && <HelpLink topic="rank" />}' in rank  # the job page's tile, not every row of the list
    job = (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert job.index("<VerdictChip") < job.index('<HelpLink topic="verdict" />') < job.index('data-role="verdict-wording"')
    timeline = (UI_SRC / "components" / "PipelineTimeline.jsx").read_text(encoding="utf-8")
    assert timeline.index('testId="ats-chip"') < timeline.index('<HelpLink topic="ats" />')
    assert timeline.index('testId="scout-label-chip"') < timeline.index('<HelpLink topic="scout-label" />')
    # No test id is built from a value.
    assert sorted(set(re.findall(r'testId: "(help-[a-z-]+)"', (UI_SRC / "wording.js").read_text(encoding="utf-8")))) == [
        "help-ats", "help-rank", "help-scout-label", "help-verdict",
    ]
