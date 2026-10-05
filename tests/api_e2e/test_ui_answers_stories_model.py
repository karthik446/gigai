"""0.1.10.7 C: the Answers and stories page in the UI (``answersStoriesModel.js``, ``answersModel.js``), under node.

What is pinned:

* the page is READ-ONLY: it lists answers and stories, shows which jobs used each, and deletes;
  its source has no text-entry control (no ``<input>``, ``<textarea>``, ``<select>``, no
  ``contentEditable``) and calls no write route but the two deletes; the 0.1.10.5 story-bank
  panel, its model and its add / edit / sharing calls are gone;
* the list: tag chips narrow answers and stories; an answer with only an id shows the id; the line
  under each says who wrote it (you / your agent); a story shows where and when and the narrative
  parts that are there, in STAR order; an earlier answer kept by the migration is shown;
* which jobs used it: one line per job, newest first, by kind (asked by / reused by / used by);
* delete: sends the ``revision`` the page read; a 409 ``revision_conflict`` yields the answer or
  story as it is now and a notice, and nothing is deleted;
* the near match on the job page: "We already know: <answer>" is offered only while the box is
  empty and nothing is on record; taking it fills the request with ``from_bank``; a request carries
  the question's words and NO profile (an answer is the user's);
* the page has a route and Settings links to it.

LOUD skip when ``node`` is missing.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
SRC = UI / "src"
MODEL_JS = SRC / "answersStoriesModel.js"
ANSWERS_JS = SRC / "answersModel.js"
VIEW_JSX = SRC / "views" / "AnswersStoriesView.jsx"

GCP = {
    "question_id": "cloud:gcp", "question": "Do you have GCP experience?", "answer": "Yes, 4 years, GKE + BigQuery", "tag": "technical",
    "written_by": "agent", "created_at": "2026-10-01T15:00:00.000000Z", "updated_at": "2026-10-02T15:00:00.000000Z", "revision": 2,
    "history": [
        {"at": "2026-09-20T10:00:00Z", "by": "operator", "action": "earlier answer from profile second, replaced when answers became shared", "answer": "Two years on GCP."},
        {"at": "2026-10-02T15:00:00Z", "by": "agent", "action": "answer changed"},
    ],
    "jobs": [
        {"job_identity": "https://jobs.example.test/a", "title": "Staff Engineer", "company": "Acme", "url": "https://jobs.example.test/a", "kind": "answered", "at": "2026-10-01T15:00:00Z"},
        {"job_identity": "https://jobs.example.test/b", "title": "", "company": "", "url": None, "kind": "reused", "at": "2026-10-02T09:00:00Z"},
    ],
}
GCP["source"] = "from the user's repo infra-charts, at the user's request"
OLD = {**GCP, "source": None, "question_id": "years:python", "question": "years:python", "answer": "Six.", "tag": "experience-level", "written_by": "operator", "jobs": [], "history": [], "revision": 0}
STORY = {
    "story_id": "story:60_acme_ci_cut_time", "title": "Cut CI time 60% at Acme", "company": "Acme", "role": "Staff Engineer", "period": "2023",
    "raw": "Our builds took forty minutes.", "narrative": {"result": "Build time fell 60 percent.", "situation": "Builds took forty minutes."},
    "tags": ["ci", "technical"], "answers_questions": ["Tell me about a time you improved a slow process"], "sources": [],
    "jobs": [{"job_identity": "https://jobs.example.test/c", "title": "Platform Engineer", "company": "Globex", "url": "https://jobs.example.test/c", "kind": "used", "at": "2026-10-02T10:00:00Z"}],
    "written_by": "agent", "created_at": "2026-10-02T09:00:00.000000Z", "updated_at": "2026-10-02T09:00:00.000000Z", "revision": 1, "history": [],
}
BARE_STORY = {**STORY, "story_id": "story:bare", "title": "A bare story", "company": "", "role": "", "period": "", "raw": "", "narrative": {}, "tags": [], "answers_questions": [], "jobs": []}
SUGGESTION = {
    "question_id": "tooling:cloud_google_platform", "bank_question_id": "cloud:gcp", "bank_question": "Do you have GCP experience?",
    "answer": "Yes, 4 years, GKE + BigQuery", "score": 1.0,
}


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the answers and stories UI model check was not run")
    return node


def _run(script: str, payload: dict) -> dict:
    completed = subprocess.run([_node(), "--input-type=module", "-e", script, "--", json.dumps(payload)], capture_output=True, text=True, timeout=60, check=False, cwd=UI)
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


PAGE_SCRIPT = f"""
import * as page from {json.dumps(MODEL_JS.resolve().as_uri())};
const input = JSON.parse(process.argv[1]);
const [gcp, old] = input.answers;
const [story, bare] = input.stories;
process.stdout.write(JSON.stringify({{
  exports: Object.keys(page).sort(),
  tags: page.allTags(input.answers, input.stories),
  allAnswers: page.filterByTag(input.answers, "").map(page.itemId),
  technicalAnswers: page.filterByTag(input.answers, "technical").map(page.itemId),
  technicalStories: page.filterByTag(input.stories, "technical").map(page.itemId),
  ciStories: page.filterByTag(input.stories, "ci").map(page.itemId),
  titles: input.answers.map(page.answerTitle),
  written: [gcp, old, story].map(page.writtenLine),
  earlier: page.earlierAnswers(gcp),
  noEarlier: page.earlierAnswers(old),
  where: [page.storyWhere(story), page.storyWhere(bare)],
  parts: [page.narrativeParts(story), page.narrativeParts(bare)],
  used: [gcp, old, story].map(page.usedByLabel),
  answerJobs: page.jobLines(gcp),
  storyJobs: page.jobLines(story),
  counts: [page.countLine(1, 2, "answer", "answers"), page.countLine(1, 1, "story", "stories"), page.countLine(0, 0, "story", "stories")],
  questions: [page.deleteQuestion(gcp), page.deleteQuestion(story)],
  answerConflict: page.conflictOf({{ code: "revision_conflict", answer: {{ ...gcp, answer: "By the agent.", revision: 3 }} }}),
  storyConflict: page.conflictOf({{ code: "revision_conflict", story: {{ ...story, written_by: "operator", revision: 2 }} }}),
  noConflict: [page.conflictOf({{ code: "not_found" }}), page.conflictOf({{ code: "revision_conflict" }}), page.conflictOf(null)],
  errors: [page.deleteErrorText({{ detail: "there is no answer for 'cloud:gcp'" }}), page.deleteErrorText(null)],
  replaced: page.replaceItem(input.answers, {{ ...gcp, answer: "New." }}).map((item) => item.answer),
  replacedStory: page.replaceItem(input.stories, {{ ...story, title: "New title" }}).map((item) => item.title),
  removed: page.removeItem(input.answers, "cloud:gcp").map(page.itemId),
  removedStory: page.removeItem(input.stories, "story:bare").map(page.itemId),
  notice: page.suggestionNotice(input.suggestion),
  idNotice: page.suggestionNotice({{ ...input.suggestion, bank_question: "cloud:gcp" }}),
  noNotice: page.suggestionNotice(null),
}}));
"""


def test_the_page_lists_narrows_shows_jobs_and_deletes_and_nothing_else() -> None:
    out = _run(PAGE_SCRIPT, {"answers": [GCP, OLD], "stories": [STORY, BARE_STORY], "suggestion": SUGGESTION})

    # Read-only: the model has no function that builds an add or an edit.
    assert not [name for name in out["exports"] if re.search(r"add|edit|save|form|body|sharing", name, re.IGNORECASE)], out["exports"]

    assert out["tags"] == ["ci", "experience-level", "technical"]
    assert out["allAnswers"] == ["cloud:gcp", "years:python"] and out["technicalAnswers"] == ["cloud:gcp"]
    assert out["technicalStories"] == ["story:60_acme_ci_cut_time"] and out["ciStories"] == ["story:60_acme_ci_cut_time"]
    assert out["titles"] == ["Do you have GCP experience?", "years:python"], "only an id known: the id is shown"
    assert out["written"] == [
        "Written by your agent · source: from the user's repo infra-charts, at the user's request · updated 2026-10-02",
        "Written by you · updated 2026-10-02", "Written by your agent · updated 2026-10-02",
    ]
    assert out["earlier"] == [{
        "key": "2026-09-20T10:00:00Z:Two years on GCP.", "at": "2026-09-20",
        "note": "earlier answer from profile second, replaced when answers became shared", "answer": "Two years on GCP.",
    }]
    assert out["noEarlier"] == []

    assert out["where"] == ["Staff Engineer, Acme, 2023", ""]
    assert out["parts"] == [
        [{"key": "situation", "label": "Situation", "text": "Builds took forty minutes."}, {"key": "result", "label": "Result", "text": "Build time fell 60 percent."}],
        [],
    ], "the parts that are there, in STAR order"

    # Which jobs used them.
    assert out["used"] == ["Used by 2 jobs", "No job has used this yet", "Used by 1 job"]
    assert out["answerJobs"] == [
        {"key": "reused:https://jobs.example.test/b", "label": "reused by https://jobs.example.test/b", "url": None, "at": "2026-10-02"},
        {"key": "answered:https://jobs.example.test/a", "label": "asked by Staff Engineer at Acme", "url": "https://jobs.example.test/a", "at": "2026-10-01"},
    ]
    assert out["storyJobs"] == [{"key": "used:https://jobs.example.test/c", "label": "used by Platform Engineer at Globex", "url": "https://jobs.example.test/c", "at": "2026-10-02"}]
    assert out["counts"] == ["1 of 2 answers", "1 of 1 story", "0 of 0 stories"]

    # Delete: asked first; a stale revision shows what the agent wrote and deletes nothing.
    assert "Delete this answer?" in out["questions"][0] and "Delete this story?" in out["questions"][1]
    assert out["answerConflict"]["item"]["answer"] == "By the agent." and out["answerConflict"]["item"]["revision"] == 3
    assert out["answerConflict"]["message"].startswith("Your agent changed this after you opened the page.") and "was not deleted" in out["answerConflict"]["message"]
    assert out["storyConflict"]["item"]["revision"] == 2 and out["storyConflict"]["message"].startswith("Another window changed this")
    assert out["noConflict"] == [None, None, None]
    assert out["errors"] == ["there is no answer for 'cloud:gcp'", "It could not be deleted."]
    assert out["replaced"] == ["New.", "Six."] and out["replacedStory"] == ["New title", "A bare story"]
    assert out["removed"] == ["years:python"] and out["removedStory"] == ["story:60_acme_ci_cut_time"]

    assert out["notice"] == {"text": "We already know: Yes, 4 years, GKE + BigQuery", "source": 'From your answer to "Do you have GCP experience?".', "action": "Use it"}
    assert out["idNotice"]["source"] == 'From your answer to "cloud:gcp".' and out["noNotice"] is None


ANSWERS_SCRIPT = f"""
import * as answers from {json.dumps(ANSWERS_JS.resolve().as_uri())};
const input = JSON.parse(process.argv[1]);
const prior = new Map(input.prior.map((item) => [item.question_id, item]));
const bank = (used) => ({{ suggestions: new Map(input.suggestions.map((item) => [item.question_id, item])), used }});
const offered = answers.answerStates(input.questions, {{}}, prior, bank({{}}));
const taken = answers.answerStates(input.questions, {{ "tooling:cloud_google_platform": input.suggestions[0].answer }}, prior, bank({{ "tooling:cloud_google_platform": "cloud:gcp" }}));
const typed = answers.answerStates(input.questions, {{ "tooling:cloud_google_platform": "My own words." }}, prior, bank({{}}));
const plain = answers.answerStates(input.questions, {{ "tooling:cloud_google_platform": "My own words." }}, prior);
process.stdout.write(JSON.stringify({{
  offered: offered.map((s) => ({{ id: s.question_id, filled: s.filled, suggestion: s.suggestion && s.suggestion.bank_question_id, fromBank: s.fromBank }})),
  offeredGate: answers.reassessGate({{ assessed: true, states: offered.filter((s) => !s.recorded) }}).enabled,
  taken: answers.answerRequests(taken, "https://jobs.example.test/b"),
  takenSuggestion: taken[0].suggestion,
  typed: answers.answerRequests(typed, null),
  plainStates: plain,
  plainRequests: answers.answerRequests(plain, "https://jobs.example.test/b"),
}}));
"""


def test_a_near_match_is_offered_then_confirmed_and_no_request_names_a_profile() -> None:
    questions = [
        {"question_id": "tooling:cloud_google_platform", "question": "Do you have hands-on Google Cloud Platform experience?", "requirement": "GCP"},
        {"question_id": "years:python", "question": "How many years of Python?", "requirement": "Python"},
    ]
    # A GET /api/answers row, in the one Answer shape.
    out = _run(ANSWERS_SCRIPT, {"questions": questions, "prior": [{**OLD, "answer": "Six."}], "suggestions": [SUGGESTION]})

    # Offered, never filled in for the user: the box stays empty until "Use it".
    assert out["offered"] == [
        {"id": "tooling:cloud_google_platform", "filled": False, "suggestion": "cloud:gcp", "fromBank": None},
        {"id": "years:python", "filled": True, "suggestion": None, "fromBank": None},
    ]
    assert out["offeredGate"] is False, "an offered suggestion alone does not arm Re-assess"

    # Taken: the request stores it as the answer to THIS question, naming where it came from.
    assert out["takenSuggestion"] is None, "once the box is filled the offer is gone"
    assert out["taken"] == [
        {
            "question_id": "tooling:cloud_google_platform", "answer": SUGGESTION["answer"], "reassess": None,
            "question": "Do you have hands-on Google Cloud Platform experience?", "from_bank": "cloud:gcp",
        },
        {
            "question_id": "years:python", "answer": "Six.", "reassess": {"job_identity": "https://jobs.example.test/b"},
            "question": "How many years of Python?",
        },
    ]
    assert out["typed"][0] == {
        "question_id": "tooling:cloud_google_platform", "answer": "My own words.", "reassess": None,
        "question": "Do you have hands-on Google Cloud Platform experience?",
    }
    # POST /api/answers refuses a profile_id (an answer is the user's): no request carries one.
    assert not [body for key in ("taken", "typed", "plainRequests") for body in out[key] if "profile_id" in body]

    # Without the bank argument: the states and bodies of before, key for key.
    assert out["plainStates"] == [
        {"question_id": "tooling:cloud_google_platform", "value": "My own words.", "recorded": "", "filled": True, "isNew": True},
        {"question_id": "years:python", "value": "Six.", "recorded": "Six.", "filled": True, "isNew": False},
    ]
    assert out["plainRequests"] == [
        {"question_id": "tooling:cloud_google_platform", "answer": "My own words.", "reassess": None},
        {"question_id": "years:python", "answer": "Six.", "reassess": {"job_identity": "https://jobs.example.test/b"}},
    ]


def _code(path: Path) -> str:
    """A source file without its comments (the comments describe what is NOT there)."""

    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\{/\*.*?\*/\}", "", text, flags=re.DOTALL)
    return "\n".join(line for line in text.splitlines() if not line.strip().startswith("//"))


def test_the_page_is_read_only_and_the_0_1_10_5_forms_are_gone() -> None:
    view = _code(VIEW_JSX)

    # No text-entry control anywhere on the page.
    for control in ("<input", "<textarea", "<select", "contentEditable", "onChange=", 'type="text"', 'type="search"'):
        assert control not in view, f"the Answers and stories page must have no text entry: {control}"
    # It reads two lists and deletes; it calls no other write.
    imported = re.search(r'import \{([^}]*)\} from "\.\./api\.js";', view)
    assert imported is not None and sorted(name.strip() for name in imported.group(1).split(",")) == ["deleteAnswer", "deleteStory", "getAnswers", "getStories"]
    for needle in ("await remove(itemId(item), item.revision)", "conflictOf(err)", "window.confirm(deleteQuestion(item))", 'window.addEventListener("focus", load)', 'data-action="delete-item"', "jobLines(item)"):
        assert needle in view, needle

    api = _code(SRC / "api.js")
    assert 'request("GET", "/api/answers")' in api and 'request("GET", "/api/stories")' in api
    assert 'request("DELETE", `/api/answers/${encodeURIComponent(questionId)}${bankQuery({ revision })}`)' in api
    assert 'request("DELETE", `/api/stories/${encodeURIComponent(storyId)}${bankQuery({ revision })}`)' in api
    assert 'body || method === "DELETE" ? { "Content-Type": "application/json" }' in api, "a DELETE is a write: the CSRF check wants the JSON content type"
    # The only write of an answer the UI keeps is the job page's question box; it never writes a story.
    assert api.count('"/api/answers"') == 2 and 'request("POST", "/api/answers", fields)' in api
    assert not re.search(r'request\("(POST|PUT)", [`"]/api/stories', api)
    assert not re.search(r'request\("PUT", [`"]/api/answers', api)

    # The 0.1.10.5 story bank panel, its model and its routes are gone from the sources.
    assert not (SRC / "components" / "StoryBankPanel.jsx").exists() and not (SRC / "storyBankModel.js").exists()
    for path in sorted(SRC.rglob("*.js")) + sorted(SRC.rglob("*.jsx")):
        code = _code(path)
        for gone in ("/api/story-bank", "StoryBankPanel", "storyBankModel", "putStoryBankSharing", "addStory(", "putStory("):
            assert gone not in code, f"{gone} in {path.name}"

    # One page, with a route, reached from Settings; the job page reads the user's answers.
    assert '{ view: "answers", path: "#/answers", label: "Answers and stories"' in (SRC / "routing.js").read_text(encoding="utf-8")
    assert '{route.view === "answers" && <AnswersStoriesView />}' in (SRC / "App.jsx").read_text(encoding="utf-8")
    settings = (SRC / "views" / "SettingsView.jsx").read_text(encoding="utf-8")
    assert 'data-action="open-answers-stories" href={ANSWERS_HASH}' in settings
    assert "StoryBank" not in (SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    assert "getAnswers()" in _code(SRC / "views" / "JobPage.jsx")

    body = (SRC / "components" / "AssessmentBody.jsx").read_text(encoding="utf-8")
    assert "suggestionNotice(state && state.suggestion)" in body and 'data-action="use-suggestion"' in body
    assert body.count("onUseSuggestion={answers.applySuggestion}") == 2, "both layouts (in the table, and questions first)"
    drafts = (SRC / "answerDrafts.js").read_text(encoding="utf-8")
    assert "getAnswerMatch({ questionId: question.question_id, question: question.question })" in drafts
    assert "answerRequests(states, jobIdentity)" in drafts and "unsavedAnswerRequests" not in drafts  # 0.1.11: no "save before tailoring" step
