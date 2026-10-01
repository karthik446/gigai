"""0110-034: the story bank in the UI (``storyBankModel.js``, ``answersModel.js``), under node.

What is pinned:

* the list: search and tag narrow it; an entry saved before the bank shows its id as the question;
  the meta line says who wrote it (you / an agent) and where a shared one comes from;
* an edit sends only what changed, with the ``updated_at`` the page read and ``actor: operator``;
  nothing changed sends nothing; a 409 ``story_bank_changed`` yields the entry as it is now and a
  notice (an agent's change is never overwritten);
* sharing: the options, and the two summary lines;
* the near match: "We already know: <answer>" is offered only while the box is empty and nothing is
  on record; taking it fills the request with ``from_bank``; every request carries the question's
  words and the profile; without the bank argument the answer rules are exactly what they were;
* the panel and the answer box are wired (static check of the sources).

LOUD skip when ``node`` is missing.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI = Path(static_module.__file__).resolve().parents[2] / "ui"
SRC = UI / "src"
STORY_JS = SRC / "storyBankModel.js"
ANSWERS_JS = SRC / "answersModel.js"

GCP = {
    "question_id": "cloud:gcp", "question": "Have you run workloads on GCP?", "answer": "Yes: two years of batch workloads on GCP.",
    "tag": "technical", "owner_profile_id": "p1", "shared": False, "legacy": False, "edited": False, "confirmed_from": None,
    "first_answered_at": "2026-10-01T15:00:00.000000Z", "updated_at": "2026-10-01T15:00:00.000000Z", "revision": 1, "written_by": "operator",
    "history": [], "record_id": "r1", "revision_id": "v1",
    "postings": [
        {"job_identity": "https://jobs.example.test/a", "title": "Staff Engineer", "company": "Acme", "url": "https://jobs.example.test/a", "kind": "answered", "at": "2026-10-01T15:00:00Z"},
        {"job_identity": "https://jobs.example.test/b", "title": "", "company": "", "url": None, "kind": "reused", "at": "2026-10-02T09:00:00Z"},
    ],
}
OLD = {**GCP, "question_id": "years:python", "question": "years:python", "answer": "Six.", "tag": "experience-level", "legacy": True, "postings": [], "revision": 0}
STORY = {**GCP, "question_id": "story:database_led_migration", "question": "Tell me about a database migration you led", "answer": "Led a Postgres cut-over.", "tag": "leadership", "written_by": "agent", "postings": []}
SHARED = {**GCP, "question_id": "cloud:aws", "question": "AWS?", "answer": "Four years.", "owner_profile_id": "p2", "shared": True, "postings": []}
SHARING = {"share_with": "p2", "read_by": [], "profiles": [{"profile_id": "p2", "label": "Second"}, {"profile_id": "p3", "label": "Third"}]}
SUGGESTION = {
    "question_id": "tooling:cloud_google_platform", "bank_question_id": "cloud:gcp", "bank_question": "Have you run workloads on GCP?",
    "answer": "Yes: two years of batch workloads on GCP.", "score": 1.0, "owner_profile_id": "p1", "shared": False,
}


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the story bank UI model check was not run")
    return node


def _run(script: str, payload: dict) -> dict:
    completed = subprocess.run([_node(), "--input-type=module", "-e", script, "--", json.dumps(payload)], capture_output=True, text=True, timeout=60, check=False, cwd=UI)
    assert completed.returncode == 0, f"node failed:\n{completed.stderr}"
    return json.loads(completed.stdout)


BANK_SCRIPT = f"""
import * as bank from {json.dumps(STORY_JS.resolve().as_uri())};
const input = JSON.parse(process.argv[1]);
const [gcp, old, story, shared] = input.entries;
const labels = {{ p1: "Default", p2: "Second" }};
process.stdout.write(JSON.stringify({{
  all: bank.filterEntries(input.entries, {{}}).map((e) => e.question_id),
  search: bank.filterEntries(input.entries, {{ query: "POSTGRES" }}).map((e) => e.question_id),
  tag: bank.filterEntries(input.entries, {{ tag: "technical", query: "aws" }}).map((e) => e.question_id),
  titles: input.entries.map(bank.entryTitle),
  meta: input.entries.map((e) => bank.entryMeta(e, labels)),
  used: input.entries.map(bank.usedByLabel),
  postings: bank.postingLines(gcp),
  unchanged: bank.editBody(gcp, bank.initialEditForm(gcp), "p1"),
  answerOnly: bank.editBody(gcp, {{ ...bank.initialEditForm(gcp), answer: " Three years. " }}, "p1"),
  everything: bank.editBody(old, {{ answer: "Seven.", question: "How many years of Python?", tag: "Python Years" }}, "p1"),
  editErrors: [bank.editError({{ answer: " ", question: "Q", tag: "" }}), bank.editError({{ answer: "A", question: "", tag: "" }}), bank.editError({{ answer: "A", question: "Q", tag: "" }})],
  add: bank.addBody({{ question: " Tell me about X ", answer: " A story. ", tag: "" }}, "p1"),
  addErrors: [bank.addError({{ question: "", answer: "x" }}), bank.addError({{ question: "Q", answer: "" }}), bank.addError({{ question: "Q", answer: "A" }})],
  conflict: bank.conflictOf({{ code: "story_bank_changed", entry: {{ ...gcp, answer: "By the agent.", written_by: "agent" }} }}),
  noConflict: [bank.conflictOf({{ code: "personal_info_refused" }}), bank.conflictOf(null)],
  errors: [
    bank.saveErrorText({{ code: "personal_info_refused", detail: "this answer looks like it holds personal information (email)" }}),
    bank.saveErrorText({{ code: "story_exists" }}),
    bank.saveErrorText({{ message: "The server rejected this request as invalid." }}),
  ],
  replaced: bank.replaceEntry(input.entries, {{ ...gcp, answer: "New." }}).map((e) => e.answer),
  removed: bank.removeEntry(input.entries, "cloud:gcp").map((e) => e.question_id),
  options: bank.sharingOptions(input.sharing),
  summary: bank.sharingSummary(input.sharing),
  alone: bank.sharingSummary({{ share_with: null, read_by: ["p3"], profiles: input.sharing.profiles }}),
  notice: bank.suggestionNotice(input.suggestion),
  sharedNotice: bank.suggestionNotice({{ ...input.suggestion, shared: true, bank_question: "cloud:gcp" }}),
  noNotice: bank.suggestionNotice(null),
}}));
"""


def test_the_story_bank_list_edit_conflict_and_sharing_rules() -> None:
    out = _run(BANK_SCRIPT, {"entries": [GCP, OLD, STORY, SHARED], "sharing": SHARING, "suggestion": SUGGESTION})

    assert out["all"] == ["cloud:gcp", "years:python", "story:database_led_migration", "cloud:aws"]
    assert out["search"] == ["story:database_led_migration"] and out["tag"] == ["cloud:aws"]
    assert out["titles"] == ["Have you run workloads on GCP?", "years:python", "Tell me about a database migration you led", "AWS?"]
    assert out["meta"] == [
        "Written by you · updated 2026-10-01",
        "Written by you · updated 2026-10-01 · saved before the story bank",
        "Written by an agent · updated 2026-10-01",
        "Written by you · updated 2026-10-01 · shared from Second (edit it there)",
    ]
    assert out["used"] == ["Used by 2 jobs", "No job has used this yet", "No job has used this yet", "No job has used this yet"]
    assert [(line["label"], line["url"], line["at"]) for line in out["postings"]] == [
        ("reused by https://jobs.example.test/b", None, "2026-10-02"),
        ("asked by Staff Engineer at Acme", "https://jobs.example.test/a", "2026-10-01"),
    ]

    assert out["unchanged"] is None, "nothing changed: nothing is sent"
    assert out["answerOnly"] == {"answer": "Three years.", "updated_at": GCP["updated_at"], "profile_id": "p1", "actor": "operator"}
    assert out["everything"] == {
        "answer": "Seven.", "question": "How many years of Python?", "tag": "python years",
        "updated_at": OLD["updated_at"], "profile_id": "p1", "actor": "operator",
    }
    assert out["editErrors"][0].startswith("The answer cannot be empty") and out["editErrors"][1] == "The question cannot be empty." and out["editErrors"][2] is None
    assert out["add"] == {"question": "Tell me about X", "answer": "A story.", "profile_id": "p1", "actor": "operator"}
    assert out["addErrors"][0].startswith("Say what the story answers") and out["addErrors"][1] == "Write the story." and out["addErrors"][2] is None

    assert out["conflict"]["entry"]["answer"] == "By the agent."
    assert out["conflict"]["message"].startswith("An agent changed this entry after you opened it.")
    assert out["noConflict"] == [None, None]
    assert out["errors"] == [
        "this answer looks like it holds personal information (email)",
        "This profile already has an entry with that id. Edit that entry instead.",
        "The server rejected this request as invalid.",
    ]
    assert out["replaced"] == ["New.", "Six.", "Led a Postgres cut-over.", "Four years."]
    assert out["removed"] == ["years:python", "story:database_led_migration", "cloud:aws"]

    assert out["options"] == [
        {"value": "", "label": "Only this profile's own answers"},
        {"value": "p2", "label": "Also use the story bank of Second"},
        {"value": "p3", "label": "Also use the story bank of Third"},
    ]
    assert out["summary"] == ["This profile also reads the story bank of Second.", "No other profile reads its answers."]
    assert out["alone"] == ["This profile reads only its own story bank.", "Its own answers are read by: Third."]

    assert out["notice"] == {
        "text": "We already know: Yes: two years of batch workloads on GCP.",
        "source": 'From your answer to "Have you run workloads on GCP?".',
        "action": "Use it",
    }
    assert out["sharedNotice"]["source"] == 'From your answer to "cloud:gcp" (a shared story bank).' and out["noNotice"] is None


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
  taken: answers.answerRequests(taken, "https://jobs.example.test/b", "p1"),
  takenSuggestion: taken[0].suggestion,
  typed: answers.answerRequests(typed, null, "p1"),
  unsaved: answers.unsavedAnswerRequests(taken, "p1"),
  plainStates: plain,
  plainRequests: answers.answerRequests(plain, "https://jobs.example.test/b"),
  plainUnsaved: answers.unsavedAnswerRequests(plain),
}}));
"""


def test_a_near_match_is_offered_then_confirmed_and_the_old_answer_rules_are_unchanged() -> None:
    questions = [
        {"question_id": "tooling:cloud_google_platform", "question": "Do you have hands-on Google Cloud Platform experience?", "requirement": "GCP"},
        {"question_id": "years:python", "question": "How many years of Python?", "requirement": "Python"},
    ]
    out = _run(ANSWERS_SCRIPT, {"questions": questions, "prior": [{"question_id": "years:python", "answer": "Six."}], "suggestions": [SUGGESTION]})

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
            "question": "Do you have hands-on Google Cloud Platform experience?", "from_bank": "cloud:gcp", "profile_id": "p1",
        },
        {
            "question_id": "years:python", "answer": "Six.", "reassess": {"job_identity": "https://jobs.example.test/b"},
            "question": "How many years of Python?", "profile_id": "p1",
        },
    ]
    assert [(item["question_id"], item.get("from_bank")) for item in out["unsaved"]] == [("tooling:cloud_google_platform", "cloud:gcp")]
    assert out["typed"][0] == {
        "question_id": "tooling:cloud_google_platform", "answer": "My own words.", "reassess": None,
        "question": "Do you have hands-on Google Cloud Platform experience?", "profile_id": "p1",
    }

    # Without the bank argument: the states and bodies of before, key for key.
    assert out["plainStates"] == [
        {"question_id": "tooling:cloud_google_platform", "value": "My own words.", "recorded": "", "filled": True, "isNew": True},
        {"question_id": "years:python", "value": "Six.", "recorded": "Six.", "filled": True, "isNew": False},
    ]
    assert out["plainRequests"] == [
        {"question_id": "tooling:cloud_google_platform", "answer": "My own words.", "reassess": None},
        {"question_id": "years:python", "answer": "Six.", "reassess": {"job_identity": "https://jobs.example.test/b"}},
    ]
    assert out["plainUnsaved"] == [{"question_id": "tooling:cloud_google_platform", "answer": "My own words.", "reassess": None}]


def test_the_panel_and_the_answer_box_are_wired() -> None:
    panel = (SRC / "components" / "StoryBankPanel.jsx").read_text(encoding="utf-8")
    for needle in ("getStoryBank(profileId)", "putStory(entry.question_id, body)", "deleteStory(entry.question_id, { profileId, updatedAt: entry.updated_at })",
                   "putStoryBankSharing(profileId, value || null)", "conflictOf(err)", 'window.addEventListener("focus", load)', "!entry.shared &&"):
        assert needle in panel, needle
    assert "<StoryBankPanel key={`story-bank-${selected.profile_id}`} profile={selected} />" in (SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")

    body = (SRC / "components" / "AssessmentBody.jsx").read_text(encoding="utf-8")
    assert "suggestionNotice(state && state.suggestion)" in body and 'data-action="use-suggestion"' in body
    assert body.count("onUseSuggestion={answers.applySuggestion}") == 2, "both layouts (in the table, and questions first)"
    drafts = (SRC / "answerDrafts.js").read_text(encoding="utf-8")
    assert "getStoryBankMatch({ profileId, questionId: question.question_id, question: question.question })" in drafts
    assert "answerRequests(states, jobIdentity, profileId)" in drafts and "unsavedAnswerRequests(states, profileId)" in drafts

    api = (SRC / "api.js").read_text(encoding="utf-8")
    assert 'request("DELETE", `/api/story-bank/${encodeURIComponent(questionId)}' in api
    assert 'body || method === "DELETE" ? { "Content-Type": "application/json" }' in api, "a DELETE is a write: the CSRF check wants the JSON content type"
    assert "getAnswers(profileId)" in (SRC / "views" / "JobPage.jsx").read_text(encoding="utf-8")
