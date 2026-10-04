"""Flow 7 (REPORT.md 5.3): the Answers and stories page, and "Written by your agent" with its source.

The page is read-only: the user's agent writes answers and stories through the API and the CLI, and the page
browses them. Real server, nothing stubbed. In the second test the "agent" is the test itself, writing through the
real route (`POST /api/answers`, `actor: agent`, a `source`) while the page is open.

Pinned (first test): both lists are what the server holds, in two requests (one each); every answer says who wrote it
("Written by you" for the one typed on a job page, "Written by your agent" for the agent's); an answer a job used
lists that job; a story shows the user's own words and the questions it answers; a tag chip filters without a
request; the page has no text field and wrote nothing.

Pinned (second test): an answer the agent writes while the page is open is there after Refresh, with "Written by your
agent · source: <the agent's own words> · updated <day>"; Delete asks first, sends the revision the page read, and
the answer is gone, here and on the server (which leaves the home as it was found).

MEASURED (14-core laptop, 2026-10-04, three runs: Python 3.11 twice, 3.13 once): the page 1.1 s wall from a new
browser (it is the first page of the run), 0.48 to 0.52 server CPU seconds; the agent's write 1.9 to 2.0 s wall, 0.7
CPU seconds; Refresh 0.31 to 0.32 s.
"""

from __future__ import annotations

import pytest

from tests.ui.support import FIRST_LOAD_WALL_SECONDS, INTERACTIVE_WALL_SECONDS

pytestmark = pytest.mark.ui

PAGE_WALL_SECONDS = FIRST_LOAD_WALL_SECONDS
PAGE_CPU_SECONDS = 3.0  # 0.48 to 0.52 measured idle, up to 0.69 with every core busy
REFRESH_WALL_SECONDS = INTERACTIVE_WALL_SECONDS
WRITE_WALL_SECONDS = 15.0  # the agent's POST /api/answers, a write to the journal: 1.9 to 2.0 s measured
WRITE_CPU_SECONDS = 6.0  # 0.7 measured
SOURCE = "browser-flow agent, session 7"
QUESTION = "Have you operated Kafka in production?"
ANSWER = "Yes. Three years running a 12-broker cluster behind an ingestion pipeline."


def open_page(ui) -> None:
    ui.goto("/#/answers")
    ui.page.locator('[data-role="answers"]').wait_for()
    ui.page.locator('[data-role="stories"]').wait_for()


def entries(ui, section: str) -> list[dict]:
    return ui.page.locator(f'[data-role="{section}"] li.story-entry').evaluate_all(
        """(items) => items.map((item) => ({
          id: item.dataset.itemId,
          title: item.querySelector('.story-entry-head strong').textContent,
          written: Array.from(item.querySelectorAll('p.muted.small')).map((line) => line.textContent).filter((line) => line.startsWith('Written by'))[0] || '',
          raw: (item.querySelector('[data-role="story-raw"]') || {}).textContent || '',
          answers: Array.from(item.querySelectorAll('[data-role="story-answers-questions"] li')).map((line) => line.textContent),
          usedBy: item.querySelector('[data-action="show-jobs"]').textContent,
        }))"""
    )


def written_line(item: dict) -> str:
    """What the page must say under an answer or a story (answersStoriesModel.writtenLine)."""

    parts = ["Written by your agent" if item["written_by"] == "agent" else "Written by you"]
    if item.get("source"):
        parts.append(f"source: {item['source']}")
    parts.append(f"updated {item['updated_at'][:10]}")
    return " · ".join(parts)


def test_answers_and_stories_are_listed_read_only(ui) -> None:
    answers = ui.server_json("/api/answers")["answers"]
    stories = ui.server_json("/api/stories")["stories"]
    assert answers and stories, "the small home has answers and stories"

    open_page(ui)
    ui.step("shown")
    ui.settle()

    shown_answers, shown_stories = entries(ui, "answers"), entries(ui, "stories")
    assert [item["id"] for item in shown_answers] == [item["question_id"] for item in answers]
    assert [item["id"] for item in shown_stories] == [item["story_id"] for item in stories]
    assert [item["written"] for item in shown_answers] == [written_line(item) for item in answers]
    assert [item["written"] for item in shown_stories] == [written_line(item) for item in stories]
    writers = {item["written_by"] for item in answers}
    assert writers == {"operator", "agent"}, "the small home has an answer of each writer"
    # A story is shown in the user's own words, with the questions it answers.
    for shown, story in zip(shown_stories, stories):
        assert story["raw"] in shown["raw"]
        assert shown["answers"] == [f"Answers: {question}" for question in story["answers_questions"]]

    # The answer a job used lists that job.
    used = next(item for item in answers if item["jobs"])
    row = ui.page.locator(f'[data-role="answers"] li[data-item-id="{used["question_id"]}"]')
    row.locator('[data-action="show-jobs"]').click()
    listed = row.locator("ul.story-postings li a").all_text_contents()
    assert len(listed) == len(used["jobs"]) and all(job["title"] in " ".join(listed) for job in used["jobs"])

    # A tag chip filters what is shown; it asks the server nothing.
    tag = answers[0]["tag"]
    ui.page.click(f'.story-bank-tools [data-tag="{tag}"]')
    assert [item["id"] for item in entries(ui, "answers")] == [item["question_id"] for item in answers if item["tag"] == tag]
    ui.page.click('.story-bank-tools [data-tag=""]')
    assert len(entries(ui, "answers")) == len(answers)

    # Read-only: no text field on the page, one read of each list, nothing written.
    assert ui.page.locator('[data-role="answers-stories"]').locator("input, textarea, select").count() == 0
    assert ui.requests_after("start", "/api/answers") == 1 and ui.requests_after("start", "/api/stories") == 1
    assert ui.writes_after("start") == []
    ui.cpu_budget("Answers and stories page (small home)", PAGE_CPU_SECONDS, "start", "shown")
    ui.wall_budget("Answers and stories page (small home)", PAGE_WALL_SECONDS, "start", "shown")
    ui.assert_clean()  # zero console errors, page errors, HTTP >= 400, failed requests


def test_an_answer_the_agent_writes_shows_written_by_agent_with_its_source(ui) -> None:
    open_page(ui)
    ui.settle()
    before = [item["id"] for item in entries(ui, "answers")]
    ui.step("open")

    # The agent writes while the page is open (the real route, as `gigai scout answers` and an agent use it).
    written = ui.server_json("/api/answers", {"question_id": "kafka:operations", "question": QUESTION, "answer": ANSWER, "actor": "agent", "source": SOURCE})["answer"]
    ui.step("written")
    question_id = written["question_id"]
    try:
        assert written["written_by"] == "agent" and written["source"] == SOURCE
        assert question_id not in before
        ui.cpu_budget("the agent writes an answer (small home)", WRITE_CPU_SECONDS, "open", "written")
        ui.wall_budget("the agent writes an answer (small home)", WRITE_WALL_SECONDS, "open", "written")

        ui.page.click('[data-action="refresh"]')
        row = ui.page.locator(f'[data-role="answers"] li[data-item-id="{question_id}"]')
        row.wait_for()
        ui.step("refreshed")
        shown = next(item for item in entries(ui, "answers") if item["id"] == question_id)
        assert shown["title"] == QUESTION
        assert shown["written"] == f"Written by your agent · source: {SOURCE} · updated {written['updated_at'][:10]}"
        assert row.locator("p.story-answer").text_content() == ANSWER
        ui.settle()
        assert ui.requests_after("written", "/api/answers") == 1 and ui.requests_after("written", "/api/stories") == 1
        ui.wall_budget("Refresh answers and stories (small home)", REFRESH_WALL_SECONDS, "written", "refreshed")

        # Delete asks first, then sends the revision the page read.
        asked: list[str] = []

        def accept(dialog) -> None:
            asked.append(dialog.message)
            dialog.accept()

        ui.page.once("dialog", accept)
        with ui.page.expect_request(lambda request: request.method == "DELETE") as sent:
            row.locator('[data-action="delete-item"]').click()
        row.wait_for(state="detached")
        assert len(asked) == 1 and asked[0], "Delete did not ask first"
        assert sent.value.url.endswith(f"/api/answers/{question_id.replace(':', '%3A')}?revision={written['revision']}"), sent.value.url
        assert [item["id"] for item in entries(ui, "answers")] == before
    finally:
        # Whatever happened above, the shared home ends without this answer.
        left = [item for item in ui.server_json("/api/answers")["answers"] if item["question_id"] == question_id]
        if left:
            ui.server_json(f"/api/answers/{question_id.replace(':', '%3A')}?revision={left[0]['revision']}", method="DELETE")
    assert question_id not in [item["question_id"] for item in ui.server_json("/api/answers")["answers"]]
    ui.assert_clean()
