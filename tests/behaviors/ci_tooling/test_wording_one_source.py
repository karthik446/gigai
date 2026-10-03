"""0.1.10.7 K: each one-line notice has ONE source, and the UI, the docs, the README and the agent skill quote it.

0.1.10.8 adds the network notice (``wording.NETWORK_NOTICE``): the UI's one-time dialog, the README's install
section, the agent start page, the first-10-minutes page, the quickstart and the public llms.txt.

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


def test_the_network_notice_is_one_constant_quoted_by_the_ui_the_readme_the_docs_and_llms_txt() -> None:
    """0.1.10.8 docs item 6: where to run GigAI, in the operator's exact words, said before the first Update sources."""

    # The approved words. The lead is bold wherever it is shown; NETWORK_NOTICE is the Markdown form of the whole.
    assert wording.NETWORK_NOTICE == (
        "**Run GigAI on your own computer and your own network, not a work laptop or office Wi-Fi.** "
        "Scout checks about 10,000 public job boards (Greenhouse, Lever, Ashby): thousands of requests, "
        "and it keeps checking 8 times a day. An employer can see that traffic."
    )
    assert wording.NETWORK_NOTICE == f"**{wording.NETWORK_NOTICE_LEAD}** {wording.NETWORK_NOTICE_BODY}"

    # One Python file spells it out.
    def joined(path: Path) -> str:
        return re.sub(r'"\s*\n\s*f?"', "", path.read_text(encoding="utf-8"))

    for line in (wording.NETWORK_NOTICE_LEAD, wording.NETWORK_NOTICE_BODY):
        holders = sorted(path.relative_to(SRC).as_posix() for path in SRC.rglob("*.py") if line in joined(path))
        assert holders == [HOME], holders

    # The UI copies equal the constants, live in wording.js only, and the one-time dialog shows the lead in bold.
    ui = (UI_SRC / "wording.js").read_text(encoding="utf-8")
    copies = dict(re.findall(r'^export const ([A-Z_]+) = "(.*)";$', ui, flags=re.M))
    assert copies["NETWORK_NOTICE_LEAD"] == wording.NETWORK_NOTICE_LEAD
    assert copies["NETWORK_NOTICE_BODY"] == wording.NETWORK_NOTICE_BODY
    for line in (wording.NETWORK_NOTICE_LEAD, wording.NETWORK_NOTICE_BODY):
        holders = sorted(p.relative_to(UI_SRC).as_posix() for p in UI_SRC.rglob("*.js*") if line in p.read_text(encoding="utf-8"))
        assert holders == ["wording.js"], holders
    dialog = (UI_SRC / "components" / "NetworkNotice.jsx").read_text(encoding="utf-8")
    assert "<strong>{NETWORK_NOTICE_LEAD}</strong> {NETWORK_NOTICE_BODY}" in dialog and 'data-testid="network-notice"' in dialog

    # The package README (the PyPI page) says it in its install section, before the install command.
    readme = _flat((ROOT / "README.md").read_text(encoding="utf-8"))
    quickstart = readme.split("## Quickstart", 1)[1].split("## Let your agent set it up", 1)[0]
    assert wording.NETWORK_NOTICE in quickstart
    assert quickstart.index(wording.NETWORK_NOTICE) < quickstart.index("uv tool install gigai")

    # The docs: the agent start page (twice: up front, and as the words the agent says before the first
    # Update sources), the first-10-minutes page, the quickstart, and the public llms.txt.
    start = _docs("scout/agents/start.md")
    assert start.count(wording.NETWORK_NOTICE) == 2
    assert start.rindex(wording.NETWORK_NOTICE) < start.index("gigai scout sources update"), "the agent says it BEFORE the first update"
    assert wording.NETWORK_NOTICE in _docs("scout/first-10-minutes.md")
    assert wording.NETWORK_NOTICE in _docs("scout/quickstart.md")
    llms = _flat((DOCS.parents[1] / "llms.template.txt").read_text(encoding="utf-8"))
    assert wording.NETWORK_NOTICE in llms
    assert llms.index(wording.NETWORK_NOTICE) < llms.index("gigai scout sources update")
    # The other bold truths ride along on the start page and in llms.txt, in the constants' words.
    for page in (start, llms):
        assert f"**{wording.PRIVACY_PROMISE}** {wording.PRIVACY_PDF_LINE}" in page
        assert f"**{wording.AGENT_WORDING}**" in page

    # No page rewords it: a sentence that starts like the notice is the notice.
    pages = [ROOT / "README.md", *(p for p in sorted(DOCS.rglob("*.md")) if p.name != "changelog.md"), DOCS.parents[1] / "llms.template.txt"]
    for path in pages:
        text = _flat(path.read_text(encoding="utf-8"))
        assert text.count("Run GigAI on your own computer") == text.count(wording.NETWORK_NOTICE), path.relative_to(ROOT)


def test_the_network_notice_states_what_the_code_does() -> None:
    """The numbers in the sentence are the product's: the bundled catalog's boards, their three providers and the
    default weekday check times. A change to either must change the sentence (or the sentence's owner must agree)."""

    from gigai.scout.find_jobs.company_catalog import load_company_catalog
    from gigai.scout.find_jobs.refresh_plan import DEFAULT_WEEKDAY_TIMES

    summary = load_company_catalog().summary()
    assert 9_500 <= summary["records"] <= 10_999, "about 10,000 public job boards"
    assert sorted(summary["by_provider"]) == ["ashby", "greenhouse", "lever"], "(Greenhouse, Lever, Ashby)"  # type: ignore[call-overload]
    assert len(DEFAULT_WEEKDAY_TIMES) == 8, "it keeps checking 8 times a day (weekdays; weekend days have fewer)"


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
