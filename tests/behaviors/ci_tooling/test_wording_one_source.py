"""0.1.10.7 K: each one-line notice has ONE source, and the UI, the docs, the README and the agent skill quote it.

The six lines (``gigai.scout.wording``): the Scout label's, the Scout ATS score's, the verdict's, the tailored
resume's, the privacy promise and the agent truth. A sentence that is reworded in one place and not the others
fails here.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from gigai.agent_skill import source_text
from gigai.scout import resume_pii, wording
from gigai.scout.ats_score import ATS_WORDING
from gigai.scout.pipeline.steps import LABEL_WORDING

ROOT = Path(__file__).resolve().parents[3]
SRC = ROOT / "src" / "gigai"
UI_SRC = SRC / "scout" / "ui" / "src"
DOCS = ROOT / "gigai-docs" / "src" / "content" / "docs"

LINES = {
    "label": LABEL_WORDING,
    "ats": ATS_WORDING,
    "verdict": wording.VERDICT_WORDING,
    "tailored": wording.TAILORED_WORDING,
    "privacy": wording.PRIVACY_PROMISE,
    "agents": wording.AGENT_WORDING,
}
#: The one file that spells the sentences out; no other Python file may.
HOME = "scout/wording.py"


def _flat(text: str) -> str:
    """One line, straight apostrophes, no blockquote marks: how a sentence reads whatever the page's wrapping."""

    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    return " ".join(text.replace("’", "'").split())


def _docs(rel: str) -> str:
    if not DOCS.is_dir():
        pytest.skip("gigai-docs is excluded from the offline container build context")
    return _flat((DOCS / rel).read_text(encoding="utf-8"))


def test_the_six_lines_are_the_approved_words() -> None:
    assert LINES == {
        "label": "Scout's own suggestion from your settings, resume and answers. Not a prediction of what an employer will decide.",
        "ats": "GigAI’s own local check of how well this resume reads and matches the posting. Not any real ATS’s score.",
        "verdict": "Your model's reading of the posting against your resume and answers. Check the posting yourself.",
        "tailored": "Every line comes from your resume, answers or stories. Read it before you send it.",
        "privacy": "GigAI never stores your name, email, phone, address or links.",
        "agents": (
            "Anything GigAI gives your agent is sent to that agent's model provider. "
            "Agents get no contact data from GigAI, but an agent with shell access can read local files."
        ),
    }


def test_each_line_is_spelled_out_in_one_python_file_only() -> None:
    # Python splits a long literal over lines, so compare the source with its string joins and line breaks removed.
    def joined(path: Path) -> str:
        return re.sub(r'"\s*\n\s*f?"', "", path.read_text(encoding="utf-8"))

    for key, line in LINES.items():
        holders = sorted(path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py") if line in joined(path))
        assert holders == [HOME], f"{key}: {holders}"
    # The two older names are the same objects, not copies.
    assert LABEL_WORDING is wording.LABEL_WORDING and ATS_WORDING is wording.ATS_WORDING


def test_the_ui_copies_equal_the_constants_and_live_in_one_file() -> None:
    ui = (UI_SRC / "wording.js").read_text(encoding="utf-8")
    copies = dict(re.findall(r'^export const ([A-Z_]+) = "(.*)";$', ui, flags=re.M))
    assert copies["VERDICT_WORDING"] == wording.VERDICT_WORDING
    assert copies["TAILORED_WORDING"] == wording.TAILORED_WORDING
    assert copies["PRIVACY_PROMISE"] == wording.PRIVACY_PROMISE
    assert copies["PRIVACY_PDF_LINE"] == wording.PRIVACY_PDF_LINE
    for key in ("verdict", "tailored", "privacy"):
        holders = sorted(p.relative_to(UI_SRC).as_posix() for p in UI_SRC.rglob("*.js*") if LINES[key] in p.read_text(encoding="utf-8"))
        assert holders == ["wording.js"], f"{key}: {holders}"
    # The Scout label's and the Scout ATS score's sentences arrive in the server's reply; no UI file spells them.
    for key in ("label", "ats"):
        for path in UI_SRC.rglob("*.js*"):
            text = path.read_text(encoding="utf-8")
            assert LINES[key] not in text and _flat(LINES[key]) not in text, f"{key} is spelled out in {path.name}"
    # Each copy is shown beside its thing.
    assert "VERDICT_WORDING" in (UI_SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
    assert "TAILORED_WORDING" in (UI_SRC / "components" / "TailoredResumePanel.jsx").read_text(encoding="utf-8")
    assert "TAILORED_WORDING" in (UI_SRC / "views" / "PdfView.jsx").read_text(encoding="utf-8")
    form = (UI_SRC / "components" / "GeneratePdfForm.jsx").read_text(encoding="utf-8")
    assert "<strong>{PRIVACY_PROMISE}</strong> {PRIVACY_PDF_LINE}" in form
    assert "<strong>{PRIVACY_PROMISE}</strong>" in (UI_SRC / "components" / "ResumeWarning.jsx").read_text(encoding="utf-8")


def test_the_agent_skill_quotes_the_label_and_ats_lines_as_they_are() -> None:
    quoted = dict(re.findall(r"^- \*\*(Scout label|Scout ATS score)\*\*: (.+)$", source_text(), flags=re.M))
    assert quoted == {"Scout label": LABEL_WORDING, "Scout ATS score": _flat(ATS_WORDING)}


def test_the_numbers_page_quotes_the_four_disclaimers_and_the_tailored_line() -> None:
    page = _docs("scout/numbers.md")
    for key in ("label", "ats", "verdict", "tailored"):
        assert page.count(_flat(LINES[key])) == 1, key
    # The rank line stays as it is: the UI's own honest note.
    rank = re.search(r'RANK_HONEST_NOTE =\s*"(.*)";', (UI_SRC / "rankModel.js").read_text(encoding="utf-8"))
    assert rank and rank.group(1) in page


def test_the_privacy_promise_and_the_agent_truth_are_bold_where_they_are_shown() -> None:
    promise, agents = f"**{wording.PRIVACY_PROMISE}**", f"**{wording.AGENT_WORDING}**"
    readme = _flat((ROOT / "README.md").read_text(encoding="utf-8"))
    assert promise in readme and agents in readme
    assert promise in _docs("scout/privacy.md") and agents in _docs("scout/privacy.md")
    assert promise in _docs("scout/resume.md")
    assert agents in _docs("scout/agents.md")
    assert _flat(wording.TAILORED_WORDING) in _docs("scout/resume.md") and _flat(wording.TAILORED_WORDING) in _docs("scout/agents.md")
    # The limits follow the promise on the privacy page, the first of them in the constant's words.
    assert f"**{wording.PRIVACY_PDF_LINE}**" in _docs("scout/privacy.md")
    for limit in (
        "Older copies can remain in GigAI's local history on your computer.",
        "stays on your computer unless you or your agent send it somewhere",
        "GigAI's own model calls send your resume (without the contact lines) and your answers to the model you picked",
    ):
        assert limit in _docs("scout/privacy.md"), limit


def test_the_stale_notices_are_gone_everywhere() -> None:
    """0110-046 left three statements that stopped being true; none may come back."""

    stale = (
        "Your contact line lives in Settings > Resume display",
        "Remove them before continuing?",
        "adds them back only in your PDF",
        "contact and Resume display fields never leave your machine",
        "The Resume display fields (name, title, contact line)",
        "the name saved in Resume display",
    )
    pages = [ROOT / "README.md", *sorted(UI_SRC.rglob("*.js*")), SRC / "scout" / "resume_pii.py"]
    if DOCS.is_dir():
        pages += [p for p in sorted(DOCS.rglob("*.md")) if p.name != "changelog.md"]
    for path in pages:
        text = _flat(path.read_text(encoding="utf-8"))
        for phrase in stale:
            assert phrase not in text, f"{path.relative_to(ROOT)}: {phrase}"
    assert resume_pii.RESUME_WARNING.endswith("You type your name and contact details only when you make a PDF.")
    assert resume_pii.heads_up(["email"]) == f"This resume seems to contain: email. {resume_pii.HEADS_UP_REMOVED}"
