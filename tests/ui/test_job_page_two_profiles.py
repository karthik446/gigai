"""0.1.11.9 PJ3: a job two roles found. ONE row on the Jobs list, ONE assessment on its job page, whichever role is selected.

HISTORY (0.1.11.6 AN1). A job two profiles held had two assessments, one per profile, and this file pinned that every
action of the job page acted on the PAGE'S profile and left "the other profile's" untouched. 0.1.11.9 keys the stores
by job: there is no other assessment to leave untouched. A role is a TAG (which saved searches found the job).

Real Chromium against a REAL server of its own, nothing stubbed but `GET /api/setup` (the fixture home was never
through the setup interview), on the synthetic home of `tests/behaviors/scout_find_jobs/test_pick_header_room.py`
(an invented master, one posting, a scripted model, the pipeline off), with a SECOND role.

Pinned:

- THE JOBS LIST AND THE JOB PAGE (`test_a_job_two_roles_found_is_one_row_...`): one row with two role chips, under
  either role's filter chip and with no "assess as <role>" link; opening it does not switch the selected role; the job
  page shows both role tags; assessed from one role's page (ONE model call), the other role's page opens on the same
  assessment with nothing left to assess, and the list still has one row.
- ANSWER + RE-ASSESS (`test_an_answer_re_assesses_the_jobs_one_assessment_...`): on the page of either role the answer
  replaces the job's one assessment; the page stops asking WITHOUT a reload, and the other role's page shows the same.
- EVERY OTHER ACTION (`test_each_action_of_the_page_acts_on_the_job`): what each action changes is the job's, read the
  same from the other role's page; one model call.
- THE APPLIED JOB WHOSE SENT PDF CANNOT BE TOLD (`test_the_job_page_says_when_the_sent_pdf_cannot_be_told`).

Selectors: `[data-testid="job-row"]`, `[data-testid="profile-chip"]` (a role tag of a row), `[data-role="profile-filter-chip"]`
(a role filter), `[data-action="assess-as"]` (gone), `[data-testid="job-role-tags"] [data-testid="role-chip"]` (the job
page's tags), `[data-action="assess-now"]`, `[data-testid="ambiguous-resume-note"]`. Waits: `ui.wait_for_jobs_list()` /
`ui.wait_for_job_page()` then `ui.settle()` after every hash navigation (settle alone does not wait for one).
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlsplit

import pytest

from gigai.scout.find_jobs.discovery.storage import project_id
from gigai.scout.job_store_layout import job_digest
from gigai.scout.job_store_migration import RECORD_SCHEMA, record_path

from tests.behaviors.scout_find_jobs.test_answer_reassess_profile import MATCHED, QUESTION, TYPED, held_by_two, matched_on_both
from tests.behaviors.scout_find_jobs.test_role_is_a_tag import SECOND_ROLE, two_roles
from tests.behaviors.scout_find_jobs.test_pick_header_room import KUBERNETES_LINE, _JOB, _Server, _answer, _ids, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.behaviors.scout_find_jobs.test_waiting_resume_on_open import master_line, script_reassessment
from tests.ui import support
from tests.ui.conftest import _ui_session

pytestmark = pytest.mark.ui
UI_ORDER = 63  # a server and a home of its own: nothing of the shared home is read or written

PAGE = ".job-page"
ROW = '[data-testid="job-row"]'
ROW_CHIP = f'{ROW} [data-testid="profile-chip"]'
FILTER_CHIP = '[data-role="profile-filter-chip"]'
PAGE_TAGS = f'{PAGE} [data-testid="job-role-tags"] [data-testid="role-chip"]'
ASSESS = f'{PAGE} [data-action="assess-now"]'
AMBIGUOUS = f'{PAGE} [data-testid="ambiguous-resume-note"]'
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
    """The job's stored assessment as the job page of the role `profile_id` reads it (`GET /api/assessments`)."""

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


def selected(api) -> str:
    return api.client.get("/api/profiles").json()["selected_profile_id"]


def open_list(ui) -> None:
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.settle()


def test_a_job_two_roles_found_is_one_row_with_two_role_chips_and_one_assessment_on_its_page(page) -> None:  # noqa: ANN001
    ui, fixture, api = page, page.fx, page.api
    first, second = two_roles(fixture)
    labels = {item["profile_id"]: item["label"] for item in api.client.get("/api/postings").json()["profiles"]}
    assert labels[second] == SECOND_ROLE and set(labels) == {first, second}
    fixture.base.model.assessed = _answer(fixture)
    fixture.base.model.assess_prompts.clear()
    select(api, first)

    # THE JOBS LIST: one row for the job, a chip for each role that found it, and no "assess as <role>".
    open_list(ui)
    assert ui.job_rows() == 1, "the job is listed once"
    assert sorted(ui.page.locator(ROW_CHIP).all_text_contents()) == sorted(labels.values())
    assert ui.page.locator('[data-action="assess-as"]').count() == 0
    assert ui.page.locator(ROW).get_attribute("data-state") == "not_assessed"
    # A role chip is a FILTER on the tags: under either one the same row with both chips.
    for label in labels.values():
        with ui.page.expect_response(lambda response: urlsplit(response.url).path == "/api/postings"):
            ui.page.locator(FILTER_CHIP, has_text=label).click()
        ui.wait_for_jobs_list()
        ui.settle()
        assert ui.job_rows() == 1 and sorted(ui.page.locator(ROW_CHIP).all_text_contents()) == sorted(labels.values()), label
        ui.page.locator(FILTER_CHIP, has_text=label).click()  # off again (the unfiltered list the page kept: maybe no request)
        ui.page.wait_for_function("(label) => Array.from(document.querySelectorAll('[data-role=\"profile-filter-chip\"]')).every((chip) => chip.getAttribute('aria-pressed') !== 'true')", arg=label)
        ui.wait_for_jobs_list()
        ui.settle()

    # THE JOB PAGE, opened from the row: both role tags; the selected role is not switched by opening it.
    ui.page.locator(f"{ROW} [data-action='open-job']").click()
    ui.wait_for_job_page()
    ui.settle()
    assert selected(api) == first
    assert sorted(ui.page.locator(PAGE_TAGS).all_text_contents()) == sorted(labels.values())
    assert ui.page.locator(AMBIGUOUS).count() == 0
    # Assessed from THIS role's page: one model call.
    ui.step("before-assess")
    with ui.page.expect_response(lambda response: response.request.method == "POST" and urlsplit(response.url).path == "/api/assess", timeout=60_000) as made:
        ui.page.locator(ASSESS).first.click()
    assert made.value.status in (200, 201), made.value.text()
    assert made.value.request.post_data_json["resume"] == {"profile_id": first}
    ui.settle()
    assert len(fixture.base.model.assess_prompts) == 1
    assert ui.page.locator(ASSESS).count() == 0 and ui.page.locator(f"{PAGE} [data-role='job-state']").get_attribute("data-state") != "not_assessed"
    mine = stored(api, first)

    # THE OTHER ROLE's page of the same job: the same assessment, nothing left to assess, no model call.
    ui.step("other-role")
    open_job(ui, second)
    assert stored(api, second) == mine, "the other role's page reads another assessment"
    assert ui.page.locator(ASSESS).count() == 0, "the other role's page offers to assess the job again"
    assert ui.page.locator(f"{PAGE} [data-role='job-state']").get_attribute("data-state") != "not_assessed"
    assert sorted(ui.page.locator(PAGE_TAGS).all_text_contents()) == sorted(labels.values())
    assert [write for write in ui.writes_after("other-role") if write not in ("POST /api/profiles/selection", "POST /api/tailored-resumes/preview")] == []
    assert len(fixture.base.model.assess_prompts) == 1, "the job was assessed a second time through its other role"

    # Back on the list: still one row, with the job's one verdict. (The list shows the rows it kept at once and reads
    # them again in place: the wait is on the row's own state, a hash navigation is not something `settle` waits for.)
    ui.step("back-to-list")
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.page.wait_for_function("() => { const row = document.querySelector('[data-testid=\"job-row\"]'); return Boolean(row) && row.dataset.state === 'matched'; }")
    ui.settle()
    assert ui.requests_after("back-to-list", "/api/postings") >= 1, "the kept list was not read again after the job was assessed on its page"
    assert ui.job_rows() == 1 and ui.page.locator(ROW).get_attribute("data-state") == "matched"
    assert sorted(ui.page.locator(ROW_CHIP).all_text_contents()) == sorted(labels.values())
    ui.assert_clean()


@pytest.mark.parametrize("page_profile", ["default", "second"])
def test_an_answer_re_assesses_the_jobs_one_assessment_from_either_roles_page(page, page_profile: str) -> None:  # noqa: ANN001
    ui, fixture, api = page, page.fx, page.api
    mine, other, _ids = held_by_two(fixture, page_profile=page_profile)
    before = stored(api, mine)
    assert stored(api, other) == before, "one job, one assessment: both roles' pages read the same one"
    assert before["resume"]["profile_id"] == other  # the role that asked last is the one recorded on it

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

    # The END outcome, on the server: the job's ONE assessment is the new one, read the same under both roles.
    after = stored(api, mine)
    assert stored(api, other) == after
    assert at(after) != at(before) and after["result"]["verdict"] == MATCHED and not after["result"].get("structured_questions")
    assert answered.request.post_data_json["reassess"] == {"job_identity": _JOB, "profile_id": mine}
    assert answered.json()["reassessed"]["resume"]["profile_id"] == mine  # recorded on it; it selected nothing
    # (The preview is rendered again for the resume the new assessment picked: a render stores nothing.)
    assert [write for write in ui.writes_after("typed") if write != "POST /api/tailored-resumes/preview"] == ["POST /api/answers"], "one write"
    assert len(fixture.base.model.assess_prompts) == 1, "one model call"

    # The page stops asking, without a reload; a fresh load shows the same; so does the OTHER role's page.
    assert ui.page.locator(QUESTIONS).count() == 0, "the page still asks the answered question"
    assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") != "needs_answers"
    ui.reload()
    ui.wait_for_job_page()
    ui.settle()
    assert ui.page.locator(QUESTIONS).count() == 0, "after a reload the page asks the answered question again"
    open_job(ui, other)
    assert ui.page.locator(QUESTIONS).count() == 0, "the other role's page still asks: it shows another assessment"
    assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") != "needs_answers"
    assert len(fixture.base.model.assess_prompts) == 1
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


#: What each action of the page sends, and whether its request names the page's selected role (`False`: it never names one).
#: A named role is RECORDED on what the action writes; it selects nothing (the job has one assessment and one resume).
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
def test_each_action_of_the_page_acts_on_the_job(page, page_profile: str) -> None:  # noqa: ANN001
    ui, fixture, api = page, page.fx, page.api
    mine, other = matched_on_both(fixture, page_profile=page_profile)
    # A master line both resumes print (a requirement's evidence) is retired: both assessments are old, Re-assess is on.
    master_line(api, _ids(fixture)[KUBERNETES_LINE], "retire")
    script_reassessment(fixture, api)
    before = {"assessment": at(stored(api, mine)), "resume": resume_of(api, mine)["updated_at"]}
    assert (stored(api, other), resume_of(api, other)) == (stored(api, mine), resume_of(api, mine)), "one job: one assessment, one resume"
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

    # Every request that names a role names the page's selected one, and each action was sent as the table says.
    naming = [(method, path, profiles) for method, path, profiles in sent if [item for item in profiles if item != "ephemeral"]]
    assert [item for item in naming if item[2] != [mine]] == [], "a request of the page names another role"
    for name, (method, path, with_profile) in ACTIONS.items():
        seen = [profiles for sent_method, sent_path, profiles in sent if (sent_method, sent_path) == (method, path)]
        assert seen, f"{name}: {method} {path} was never sent"
        assert all(profiles == ([mine] if with_profile else []) for profiles in seen), f"{name}: {seen}"
    for path in READS:
        assert [mine] in [profiles for method, sent_path, profiles in sent if (method, sent_path) == ("GET", path)], f"GET {path} does not name the page's selected role"

    # What changed is the JOB's: the assessment and the resume are new, and the other role's page reads the same ones.
    assert at(stored(api, mine)) > before["assessment"] and resume_of(api, mine)["updated_at"] > before["resume"]
    assert (stored(api, other), resume_of(api, other)) == (stored(api, mine), resume_of(api, mine))
    assert len(fixture.base.model.assess_prompts) == 1, "one model call: the re-assessment"

    # The application is the JOB's too: the other role's page of this job shows it.
    assert ui.page.locator(f'{PAGE} [data-role="application-badge"]').get_attribute("data-status") == "applied"
    open_job(ui, other)
    assert ui.page.locator(f'{PAGE} [data-role="application-badge"]').get_attribute("data-status") == "applied"
    assert ui.page.locator(f'{PAGE} [data-role="job-state"]').get_attribute("data-state") == "applied"
    ui.assert_clean()


def test_the_job_page_says_when_the_sent_pdf_cannot_be_told(page) -> None:  # noqa: ANN001
    """One of the operator's 8 applied jobs with a stored resume under two roles (synthetic: the migration's own record)."""

    ui, fixture, api = page, page.fx, page.api
    mine, _other = matched_on_both(fixture, page_profile="default")
    entry = {
        "kept_profile_id": mine, "rule": "application", "profiles": [mine, _other],
        "ambiguous_applied_resume": {"resumes": ["scout/x/resumes/role_a/1.json", "scout/x/resumes/role_b/1.json"], "note": "kept"},
    }
    project = project_id(fixture.home_root, fixture.target)
    path = record_path(fixture.home_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": RECORD_SCHEMA, "rule_order": [], "projects": {project: {"jobs": {job_digest(_JOB): entry}}}}), encoding="utf-8")

    open_job(ui, mine)
    note = ui.page.locator(AMBIGUOUS)
    note.wait_for()
    text = note.text_content() or ""
    assert "cannot tell which PDF you sent" in text and "all of them are kept" in text
    assert "profile" not in text.lower() and "scout/x" not in text  # "role" is the word; never a file path
    assert ui.page.locator(AMBIGUOUS).count() == 1
    ui.assert_clean()
