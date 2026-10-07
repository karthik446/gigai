"""0.1.11.6 AN1: a job held by TWO profiles. Every action of its job page acts on the PAGE'S profile.

THE BUG (the operator's home, released 0.1.11.5): he answered a job's open question on the page of one profile.
`POST /api/answers` carried `reassess: {job_identity}` and no profile, and the server re-assessed the job for the
profile whose stored assessment it found first by that identity: the one assessed LAST, which was his other profile.
One model call was spent on the wrong profile, and the page's own assessment stayed as it was, still asking.

Real Chromium against a REAL server of its own, nothing stubbed but `GET /api/setup` (the fixture home was never
through the setup interview), on the synthetic home of `tests/behaviors/scout_find_jobs/test_pick_header_room.py`
(an invented master, one posting, a scripted model, the pipeline off), with a SECOND profile that holds the same job.

Pinned, each on the page of one profile while the other profile's assessment of the job is the newer one:

- ANSWER + RE-ASSESS (`test_an_answer_re_assesses_...`): the request names the page's profile; the assessment that
  changes is the page's profile's, the other profile's is untouched; the page stops asking WITHOUT a reload, and a
  fresh load shows the same.
- EVERY OTHER ACTION (`test_each_action_of_the_page_...`): the request each action of the page builds names the
  page's profile and this job, and what it changes is stored under that profile only.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from tests.behaviors.scout_find_jobs.test_answer_reassess_profile import MATCHED, QUESTION, TYPED, held_by_two, matched_on_both
from tests.behaviors.scout_find_jobs.test_pick_header_room import KUBERNETES_LINE, _JOB, _Server, _ids, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.behaviors.scout_find_jobs.test_waiting_resume_on_open import master_line, script_reassessment
from tests.ui import support
from tests.ui.conftest import _ui_session

pytestmark = pytest.mark.ui
UI_ORDER = 63  # a server and a home of its own: nothing of the shared home is read or written

PAGE = ".job-page"
PANEL = "#job-resume"
PREVIEW = f'{PANEL} [data-testid="resume-preview"]'
POINTS = f'{PANEL} [data-testid="resume-points"]'
QUESTIONS = f'{PAGE} [data-role="questions-section"]'


@pytest.fixture(autouse=True)
def _scratch_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """HOME is a folder of this test: nothing of the person's is read or written."""

    home = support.refuse_real_home(tmp_path / "person")
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def page(request: pytest.FixtureRequest, ui_browser, ui_artifacts: Path, fx, server: _Server, tmp_path: Path):  # noqa: ANN001, F811
    """`ui` on this test's own server; the test makes the job's state before it opens the page."""

    log = tmp_path / "server.log"
    log.write_text("", encoding="utf-8")
    own = SimpleNamespace(url=f"http://127.0.0.1:{server.server.server_address[1]}", pid=os.getpid(), log_path=str(log))
    prefill = server.client.get("/api/setup").json()["error"].get("prefill") or {}
    with _ui_session(request, ui_browser, own, ui_artifacts) as session:
        session.fx, session.api = fx, server
        session.page.route("**/api/setup", lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps({"prefs": prefill})))
        yield session


def select(api, profile_id: str) -> None:
    switched = api.client.post("/api/profiles/selection", json={"profile_id": profile_id}, headers={"Origin": str(api.client.base_url).rstrip("/")})
    assert switched.status_code == 200 and switched.json()["selected_profile_id"] == profile_id, switched.text


def stored(api, profile_id: str) -> dict:
    """The profile's stored assessment of the job, as `GET /api/assessments` serves it."""

    items = api.client.get("/api/assessments", params={"profile_id": profile_id}).json()["items"]
    return next(item for item in items if item["job"]["job_identity"] == _JOB)


def at(item: dict) -> str:
    """When the assessment was made (a first assessment carries only `created_at`)."""

    return item.get("updated_at") or item["created_at"]


def open_job(ui, profile_id: str) -> None:
    select(ui.api, profile_id)
    ui.goto("/#/jobs/" + quote(_JOB, safe=""))
    ui.wait_for_job_page()
    ui.settle()


@pytest.mark.parametrize("page_profile", ["default", "second"])
def test_an_answer_re_assesses_the_job_for_the_profile_of_its_page(page, page_profile: str) -> None:  # noqa: ANN001
    ui, fixture, api = page, page.fx, page.api
    mine, other, _ids = held_by_two(fixture, page_profile=page_profile)
    before = {mine: stored(api, mine), other: stored(api, other)}
    assert at(before[other]) > at(before[mine]), "the other profile's assessment is the newer one"

    open_job(ui, mine)
    section = ui.page.locator(QUESTIONS)
    section.wait_for()
    assert section.locator("h3").text_content() == "Questions for you (1)"
    section.locator(f'.row-question[data-question-id="{QUESTION["question_id"]}"] input').fill(TYPED)
    ui.step("typed")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/answers", timeout=60_000) as saved:
        section.locator('[data-action="reassess"]').click()
    answered = saved.value
    assert answered.status == 201, answered.text()
    ui.settle()

    # The END outcome, on the server: the page's profile has the new assessment, the other profile's is untouched.
    after = {mine: stored(api, mine), other: stored(api, other)}
    changed = [name for name, profile in (("the page's profile", mine), ("the OTHER profile", other)) if at(after[profile]) != at(before[profile])]
    assert changed == ["the page's profile"], f"the answer re-assessed {changed or 'nothing'}"
    assert after[mine]["result"]["verdict"] == MATCHED and not after[mine]["result"].get("structured_questions")
    # The other profile's assessment is as it was made; it now reads as old (an answer changed), which is true.
    assert (after[other]["result"], after[other]["created_at"]) == (before[other]["result"], before[other]["created_at"])
    assert answered.request.post_data_json["reassess"] == {"job_identity": _JOB, "profile_id": mine}
    assert answered.json()["reassessed"]["resume"]["profile_id"] == mine
    # (The preview is rendered again for the resume the new assessment picked: a render stores nothing.)
    assert [write for write in ui.writes_after("typed") if write != "POST /api/tailored-resumes/preview"] == ["POST /api/answers"], "one write"
    assert len(fixture.base.model.assess_prompts) == 1, "one model call"

    # The page stops asking, without a reload; a fresh load shows the same.
    assert ui.page.locator(QUESTIONS).count() == 0, "the page still asks the answered question"
    assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") != "needs_answers"
    ui.reload()
    ui.wait_for_job_page()
    ui.settle()
    assert ui.page.locator(QUESTIONS).count() == 0, "after a reload the page asks the answered question again"
    assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") != "needs_answers"
    ui.assert_clean()


def named_profiles(request) -> list[str]:  # noqa: ANN001
    """Every profile id a request of the page names: in its address and anywhere in its JSON body."""

    found = list(parse_qs(urlsplit(request.url).query).get("profile_id", []))

    def walk(value: object) -> None:
        if isinstance(value, dict):
            found.extend(item for key, item in value.items() if key == "profile_id" and isinstance(item, str))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    try:
        walk(request.post_data_json if request.post_data else None)
    except ValueError:
        pass
    return found


def resume_of(api, profile_id: str) -> dict:
    items = api.client.get("/api/tailored-resumes", params={"profile_id": profile_id, "job_identity": _JOB}).json()["items"]
    assert len(items) == 1, items
    return items[0]


#: What each action of the page sends, and whether its request names a profile (`False`: profile-free by design).
ACTIONS = {
    "what one assessment sends": ("POST", "/api/postings/assess", True),
    "the preview": ("POST", "/api/tailored-resumes/preview", True),
    "remove a point": ("PUT", "/api/tailored-resumes/selection", True),
    "edit a point": ("PUT", "/api/tailored-resumes/lines", True),
    "save this wording to my master": ("PUT", "/api/master/lines", False),  # the master is the user's, not a profile's
    "re-assess": ("POST", "/api/assess", True),
    "use the resume that waits": ("POST", "/api/job-resumes/pick", True),
    "mark applied": ("POST", "/api/applications", False),  # an application is the JOB's, whichever profile's page records it
}
#: The reads of the page that take a profile.
READS = ("/api/assessments", "/api/tailored-resumes", "/api/jobs/suggestions", "/api/pipeline/job", "/api/jobs-folder")


@pytest.mark.parametrize("page_profile", ["default", "second"])
def test_each_action_of_the_page_acts_on_the_profile_of_the_page(page, page_profile: str) -> None:  # noqa: ANN001
    ui, fixture, api = page, page.fx, page.api
    mine, other = matched_on_both(fixture, page_profile=page_profile)
    # A master line both resumes print (a requirement's evidence) is retired: both assessments are old, Re-assess is on.
    master_line(api, _ids(fixture)[KUBERNETES_LINE], "retire")
    script_reassessment(fixture, api)
    before = {"assessment": stored(api, other), "resume": resume_of(api, other)}
    mine_before = {"assessment": at(stored(api, mine)), "resume": resume_of(api, mine)["updated_at"]}
    sent: list[tuple[str, str, list[str]]] = []
    ui.page.on("request", lambda request: urlsplit(request.url).path.startswith("/api/") and sent.append((request.method, urlsplit(request.url).path, named_profiles(request))))

    open_job(ui, mine)
    ui.page.locator(f'{PANEL}[data-state="stored"]').wait_for()
    ui.page.locator(f'{PREVIEW}[data-state="ready"]').wait_for()

    def answered(method: str, path: str):
        return ui.page.expect_response(lambda response: response.request.method == method and urlsplit(response.url).path == path, timeout=60_000)

    def click(name: str, selector: str) -> None:
        method, path, _profile = ACTIONS[name]
        with answered(method, path) as reply:
            ui.page.locator(selector).first.click()
        assert reply.value.status in (200, 201), f"{name}: {reply.value.status} {reply.value.text()}"
        ui.settle()

    click("what one assessment sends", f'{PAGE} [data-role="assess-sends"] summary')
    click("remove a point", f'{POINTS} [data-action="remove-point"]')
    box = ui.page.locator(f'{POINTS} [data-role="point"] textarea').first
    with answered("PUT", "/api/tailored-resumes/lines") as edited:
        box.fill("Ran the billing pipeline of four regions with no data loss.")
        box.blur()
    assert edited.value.status == 200, edited.value.text()
    ui.settle()
    ui.page.locator(f'{POINTS} [data-action="save-to-master"]').first.click()
    click("save this wording to my master", f'{POINTS} [data-action="confirm-master"]')
    click("re-assess", f'{PAGE} [data-action="reassess"]')
    click("use the resume that waits", f'{PANEL} [data-action="use-proposed"]')
    click("mark applied", f'{PAGE} [data-role="job-state"] [data-event="applied"]')
    ui.page.locator(f'{PREVIEW}[data-state="ready"]').wait_for()
    ui.settle()

    # Every request that names a profile names the page's, and each action was sent as the table says.
    naming = [(method, path, profiles) for method, path, profiles in sent if [item for item in profiles if item != "ephemeral"]]
    assert [item for item in naming if item[2] != [mine]] == [], "a request of the page names another profile"
    for name, (method, path, with_profile) in ACTIONS.items():
        seen = [profiles for sent_method, sent_path, profiles in sent if (sent_method, sent_path) == (method, path)]
        assert seen, f"{name}: {method} {path} was never sent"
        assert all(profiles == ([mine] if with_profile else []) for profiles in seen), f"{name}: {seen}"
    for path in READS:
        assert [mine] in [profiles for method, sent_path, profiles in sent if (method, sent_path) == ("GET", path)], f"GET {path} does not name the page's profile"

    # What changed is stored under the page's profile; the other profile's assessment and resume are as they were.
    after = {"assessment": stored(api, other), "resume": resume_of(api, other)}
    assert (after["assessment"]["result"], at(after["assessment"])) == (before["assessment"]["result"], at(before["assessment"]))
    assert (after["resume"]["updated_at"], after["resume"]["result"]) == (before["resume"]["updated_at"], before["resume"]["result"])
    assert at(stored(api, mine)) > mine_before["assessment"] and resume_of(api, mine)["updated_at"] > mine_before["resume"]
    assert len(fixture.base.model.assess_prompts) == 1, "one model call: the re-assessment"

    # The application is the JOB's: the other profile's page of this job shows it too.
    assert ui.page.locator(f'{PAGE} [data-role="application-badge"]').get_attribute("data-status") == "applied"
    open_job(ui, other)
    assert ui.page.locator(f'{PAGE} [data-role="application-badge"]').get_attribute("data-status") == "applied"
    assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") == "applied"
    ui.assert_clean()
