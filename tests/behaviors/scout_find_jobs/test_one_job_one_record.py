"""0.1.11.9 PJ2: a job has ONE assessment, ONE resume and ONE suggestion record, whichever role acts on it, or none.

Until 0.1.11.9 these were kept once per ROLE (`quick_assess/<role id>/`, `resumes/<role id>/`, `suggestions/<role id>/`).
A job two roles held had two of each, and every action had to say for which role; the job page of one role then
re-assessed, picked or edited the other role's copy, or made a second one. The stores are keyed by JOB now
(`<store>/job/<sha256(job identity)>.json`); a `profile_id` is still taken everywhere and picks nothing.

Pinned, on a synthetic home (the invented master and posting of `test_pick_header_room.py`, a scripted model, two
roles on the same resume, the real server):

(a) THE SYMPTOM. Assess under role A; then answer a question and re-assess, re-assess, pick, edit a point, take a
    point off, set the length and the spacing, add a suggestion, each under role B or under NO role. After every one:
    exactly one assessment, one resume and one suggestion record on disk, in the job's folders, no role's folder at
    all, and the read a page makes (under A, under B, under none) shows the change.
(b) A HOME THAT WAS NOT MIGRATED (the job's files are in role A's folder, as a GigAI before 0.1.11.9 left them) still
    reads, under any role, and reading writes nothing; the first WRITE puts the job in the job's folders (all of it:
    the assessment, the resume with its spacing, the record) and role A's folder is byte for byte what it was.
(c) THE SAME HOME MIGRATED FIRST (`gigai scout migrate-job-stores --apply`) reads the same and ends the same.
(d) A suggested resume that WAITED beside its record (a `proposed` selection) is still taken after the migration: it
    replaces the job's stored resume (the first copy of 0.1.11.9 named the waiting file itself as its target).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gigai.scout import job_store_migration as migration
from gigai.scout import jobs_folder
from gigai.scout.job_store_layout import JOB_STORES, job_digest
from gigai.workpad import committed_read_cache

from tests.behaviors.scout_find_jobs.test_answer_reassess_profile import QUESTION, TYPED, asking, assess, second_profile, settled
from tests.behaviors.scout_find_jobs.test_pick_header_room import _JOB, _URL, _Server, _answer, _pipeline_off, fx, server  # noqa: F401 - the fixtures
from tests.support.job_store_fixtures import project_dir, role_folders, to_role_folder
from tests.support.posting_fixtures import PostingsFixture

EDITED = "Rebuilt the night-shift roster engine so a schedule change reaches every clinic in under a minute."
DIGEST = job_digest(_JOB)


# --- what is on disk, and what a page reads ----------------------------------------------------------------------------


def _on_disk(fixture: PostingsFixture) -> dict[str, dict[str, list[str]]]:
    """store -> folder -> the files in it (a folder with no file in it is not listed: a lock makes one)."""

    root = project_dir(fixture.home_root, fixture.target)
    found: dict[str, dict[str, list[str]]] = {}
    for store in JOB_STORES:
        folders = sorted(folder for folder in (root / store).iterdir() if folder.is_dir()) if (root / store).is_dir() else []
        held = {folder.name: sorted(path.name for path in folder.iterdir()) for folder in folders if any(folder.iterdir())}
        if held:
            found[store] = held
    return found


def _one_of_each(fixture: PostingsFixture, *, layout: bool = False) -> None:
    """THE symptom: one assessment, one resume (its markdown beside it) and one suggestion record, all the job's."""

    resume = [f"{DIGEST}.json", *([f"{DIGEST}.layout"] if layout else []), f"{DIGEST}.md"]
    assert _on_disk(fixture) == {"quick_assess": {"job": [f"{DIGEST}.json"]}, "resumes": {"job": resume}, "suggestions": {"job": [f"{DIGEST}.json"]}}


def _roles(fixture: PostingsFixture, other: str) -> tuple[str | None, ...]:
    """Every way a page names its role: the default one, the other one, none."""

    return (fixture.default_profile_id, other, None)


def _params(role: str | None, **more: str) -> dict[str, str]:
    return {**({"profile_id": role} if role else {}), **more}


def _assessment(api: _Server, role: str | None) -> dict:
    """The job's assessment as the job page of ``role`` reads it (GET /api/assessments, the page's own list)."""

    listed = api.client.get("/api/assessments", params=_params(role))
    assert listed.status_code == 200, listed.text
    (item,) = [item for item in listed.json()["items"] if item["job"]["job_identity"] == _JOB]
    return item


def _resume(api: _Server, role: str | None) -> dict:
    listed = api.client.get("/api/tailored-resumes", params=_params(role, job_identity=_JOB))
    assert listed.status_code == 200, listed.text
    (item,) = listed.json()["items"]
    return item


def _suggestions(api: _Server, role: str | None) -> dict:
    answer = api.client.get("/api/jobs/suggestions", params=_params(role, url=_URL))
    assert answer.status_code == 200, answer.text
    return answer.json()


def _bullets(held: dict) -> list[dict]:
    return [line for section in held["result"]["sections"] for entry in section.get("entries", ()) for line in entry["bullets"]]


def _item(line: dict) -> str:
    base = line.get("edited_from") or line
    return next(ref["item_id"] for ref in base["refs"] if ref.get("item_id"))


def _every_page_reads(api: _Server, fixture: PostingsFixture, other: str) -> tuple[dict, dict, dict]:
    """The assessment, the resume and the suggestions are the SAME under every role and under none; returns them."""

    seen = [(_assessment(api, role), _resume(api, role), _suggestions(api, role)) for role in _roles(fixture, other)]
    first = seen[0]
    for later in seen[1:]:
        assert (later[0]["stored_path"], later[0].get("updated_at"), later[0]["result"]) == (first[0]["stored_path"], first[0].get("updated_at"), first[0]["result"])
        assert (later[1]["stored_path"], later[1]["updated_at"], later[1]["markdown"]) == (first[1]["stored_path"], first[1]["updated_at"], first[1]["markdown"])
        assert later[2] == first[2]
    return first


# --- (a) -------------------------------------------------------------------------------------------------------------


def test_every_action_under_another_role_or_none_acts_on_the_jobs_one_record(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    role_a, role_b = fx.default_profile_id, second_profile(fx)

    # Assess under role A: it asks one question, so no resume is suggested yet.
    made = assess(fx, role_a, asking(fx))
    ids = {row.requirement: str(row.id) for row in made.result.matrix}
    assert _on_disk(fx) == {"quick_assess": {"job": [f"{DIGEST}.json"]}, "suggestions": {"job": [f"{DIGEST}.json"]}}
    for role in _roles(fx, role_b):
        assert _assessment(server, role)["result"]["verdict"] == "pending_user_answers"

    # 1. Answer the question on role B's page (answer + re-assess): the job's ONE assessment is replaced, its resume picked.
    fx.base.model.assessed = settled(fx, ids)
    answered = server.client.post("/api/answers", json={
        "question_id": QUESTION["question_id"], "question": QUESTION["question"], "answer": TYPED, "reassess": {"job_identity": _JOB, "profile_id": role_b},
    })
    assert answered.status_code == 201, answered.text
    _one_of_each(fx)
    assessment, resume, _record = _every_page_reads(server, fx, role_b)
    assert (assessment["result"]["verdict"], assessment["resume"]["profile_id"]) == ("matched_above_threshold", role_b)
    assert assessment["created_at"] == made.created_at and len(assessment["history"]) == 2, "it is the same assessment, assessed again"

    # 2. Re-assess with NO role (POST /api/assess names the job only).
    again = server.client.post("/api/assess", json={"job": {"job_url": _URL}})
    assert again.status_code == 200, again.text
    _one_of_each(fx)
    assessment, resume, _record = _every_page_reads(server, fx, role_b)
    assert assessment["updated_at"] == again.json()["updated_at"] and len(assessment["history"]) == 3

    # 3. Pick again under role A, on a job role B assessed last.
    picked = server.client.post("/api/job-resumes/pick", json={"job_url": _URL, "profile_id": role_a, "action": "refresh"})
    assert picked.status_code == 200, picked.text
    _one_of_each(fx)
    _assessment_now, resume, _record = _every_page_reads(server, fx, role_b)

    # 4. Edit a point under role B.
    line = next(item for item in _bullets(resume) if item["kind"] == "copy")
    edited = server.client.put("/api/tailored-resumes/lines", json={
        "profile_id": role_b, "job_identity": _JOB, "updated_at": resume["updated_at"], "line_id": line["id"], "use": "custom", "text": EDITED,
    })
    assert edited.status_code == 200, edited.text
    _one_of_each(fx)
    _assessment_now, resume, _record = _every_page_reads(server, fx, role_b)
    assert EDITED in resume["markdown"], "the page does not show the edit"

    # 5. Take a point off with NO role.
    gone = next(item for item in _bullets(resume) if item["kind"] == "copy")
    removed = server.client.put("/api/tailored-resumes/selection", json={"job_identity": _JOB, "updated_at": resume["updated_at"], "use": "remove", "item_id": _item(gone)})
    assert removed.status_code == 200, removed.text
    _one_of_each(fx)
    _assessment_now, resume, _record = _every_page_reads(server, fx, role_b)
    assert gone["text"] not in resume["markdown"] and EDITED in resume["markdown"]

    # 6. The length under role A, and with no role.
    for role in (role_a, None):
        length = server.client.put("/api/tailored-resumes/length", json=_params(role, job_identity=_JOB, updated_at=resume["updated_at"], use="restore"))
        assert length.status_code == 200, length.text
        _one_of_each(fx)
        _assessment_now, resume, _record = _every_page_reads(server, fx, role_b)

    # 7. The spacing with NO role (the preview's slider saves it), read back under each.
    saved = server.client.post("/api/tailored-resumes/preview", json={"job_identity": _JOB, "spacing_scale": 0.85})
    assert saved.status_code == 200 and saved.json()["saved"] is True, saved.text
    _one_of_each(fx, layout=True)
    for role in _roles(fx, role_b):
        shown = server.client.post("/api/tailored-resumes/preview", json=_params(role, job_identity=_JOB))
        assert shown.status_code == 200 and (shown.json()["saved"], shown.json()["spacing_scale"]) == (True, 0.85), shown.text

    # 8. A suggestion added under role B is the job's.
    added = server.client.post("/api/jobs/suggestions", json={
        "job_url": _URL, "profile_id": role_b, "action": "add", "kind": "gap", "why": "Say which storefront it was.", "requirement": next(iter(ids.values())),
    })
    assert added.status_code == 200, added.text
    _one_of_each(fx, layout=True)
    _assessment_now, _resume_now, record = _every_page_reads(server, fx, role_b)
    assert added.json()["added"] in [item["id"] for item in record["suggestions"]]

    # 9. The PDF and the job's folder, under each role: one resume, one folder.
    folders = set()
    for role in _roles(fx, role_b):
        pdf = server.client.post("/api/tailored-resumes/pdf", json=_params(role, job_identity=_JOB))
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf", pdf.text[:200]
        folder = server.client.get("/api/jobs-folder", params=_params(role, job_identity=_JOB))
        assert folder.status_code == 200 and folder.json()["job"] is not None, folder.text
        folders.add(folder.json()["job"]["path"])
    assert len(folders) == 1
    index = jobs_folder._load_index(fx.home_root, jobs_folder.jobs_folder(fx.home_root).path)
    assert list(index) == [f"{project_dir(fx.home_root, fx.target).name}/resumes/job/{DIGEST}"]


# --- (b), (c) ---------------------------------------------------------------------------------------------------------


def _matched_again(fixture: PostingsFixture, stored: dict) -> str:
    """The scripted Matched answer for a job that is assessed already: its rows carry the ids the job's rows have."""

    ids = {row["requirement"]: row["id"] for row in stored["result"]["matrix"]}
    body = json.loads(_answer(fixture))
    for row in body["matrix"]:
        row["id"] = ids[row["requirement"]]
    return json.dumps(body)


def _left_by_an_older_gigai(fixture: PostingsFixture, api: _Server) -> tuple[str, str, dict[str, bytes]]:
    """Role A assessed the job (Matched, its resume picked, a spacing saved); then everything is put where a GigAI
    before 0.1.11.9 kept it: role A's folders.  Returns (role A, role B, those folders' files)."""

    role_a, role_b = fixture.default_profile_id, second_profile(fixture)
    assess(fixture, role_a, _answer(fixture))
    # (The set-up names role A everywhere, as a GigAI before 0.1.11.9 had to: what is under test comes after it.)
    assert api.client.post("/api/tailored-resumes/preview", json={"profile_id": role_a, "job_identity": _JOB, "spacing_scale": 0.85}).json()["saved"] is True
    to_role_folder(fixture.home_root, fixture.target, role_a, _JOB)
    held = role_folders(fixture.home_root, fixture.target)
    names = [f"{DIGEST}.json", f"{DIGEST}.layout", f"{DIGEST}.md"]
    assert _on_disk(fixture) == {"quick_assess": {role_a: names[:1]}, "resumes": {role_a: names}, "suggestions": {role_a: names[:1]}}
    return role_a, role_b, held


def _home_files(fixture: PostingsFixture) -> dict[str, bytes]:
    root = Path(fixture.home_root) / "scout"
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file() and path.suffix in (".json", ".md", ".layout")}


@pytest.mark.parametrize("migrated", [False, True], ids=["not_migrated", "migrated"])
def test_a_home_of_before_reads_under_any_role_and_its_first_write_lands_in_the_jobs_folders(fx: PostingsFixture, server: _Server, migrated: bool) -> None:  # noqa: F811
    role_a, role_b, held = _left_by_an_older_gigai(fx, server)
    if migrated:
        with committed_read_cache():
            report = migration.migrate(fx.home_root, fx.target, apply=True)
        assert report.counts["files"] == {"to_copy": 5, "copied": 5, "already": 0}  # the assessment, the record, the resume with its .md and .layout
    before = _home_files(fx)

    # READS, under each role and under none: the job's records, wherever they are. Nothing is written by reading.
    assessment, resume, record = _every_page_reads(server, fx, role_b)
    where = "job" if migrated else role_a
    assert Path(assessment["stored_path"]).parent.name == Path(resume["stored_path"]).parent.name == where
    assert (assessment["result"]["verdict"], assessment["resume"]["profile_id"]) == ("matched_above_threshold", role_a)
    assert record["gate"]["decision"] == "suggest" and record["picked"] is not None
    for role in _roles(fx, role_b):
        shown = server.client.post("/api/tailored-resumes/preview", json=_params(role, job_identity=_JOB))
        assert shown.status_code == 200 and (shown.json()["saved"], shown.json()["spacing_scale"]) == (True, 0.85), "the job's saved spacing was not read"
        folder = server.client.get("/api/jobs-folder", params=_params(role, job_identity=_JOB))
        assert folder.status_code == 200 and folder.json()["job"] is not None, folder.text
    job_folder = server.client.get("/api/jobs-folder", params={"job_identity": _JOB}).json()["job"]["path"]
    assert _home_files(fx) == before, "a read wrote something"

    # THE FIRST WRITE, under role B: an edit of one point.
    line = next(item for item in _bullets(resume) if item["kind"] == "copy")
    edited = server.client.put("/api/tailored-resumes/lines", json={
        "profile_id": role_b, "job_identity": _JOB, "updated_at": resume["updated_at"], "line_id": line["id"], "use": "custom", "text": EDITED,
    })
    assert edited.status_code == 200, edited.text

    names = [f"{DIGEST}.json", f"{DIGEST}.layout", f"{DIGEST}.md"]
    disk = _on_disk(fx)
    assert {store: found["job"] for store, found in disk.items()} == {"quick_assess": names[:1], "resumes": names, "suggestions": names[:1]}
    assert role_folders(fx.home_root, fx.target) == held, "a role's folder was written"
    assessment_after, resume_after, _record = _every_page_reads(server, fx, role_b)
    assert EDITED in resume_after["markdown"] and line["text"] not in resume_after["markdown"]
    assert Path(resume_after["stored_path"]).parent.name == Path(assessment_after["stored_path"]).parent.name == "job"
    # The assessment is the same one (copied, not made again), and the spacing came along.
    assert (assessment_after.get("updated_at"), assessment_after["result"]) == (assessment.get("updated_at"), assessment["result"])
    shown = server.client.post("/api/tailored-resumes/preview", json={"job_identity": _JOB})
    assert (shown.json()["saved"], shown.json()["spacing_scale"]) == (True, 0.85)
    # The job keeps its ONE folder in the jobs folder (made under role A's key), and the edit is in it.
    folder = server.client.get("/api/jobs-folder", params={"job_identity": _JOB}).json()["job"]
    assert folder["path"] == job_folder
    assert EDITED in (Path(job_folder) / "resume.md").read_text(encoding="utf-8")
    assert [path.name for path in Path(job_folder).parent.iterdir()] == [Path(job_folder).name], "a second folder was made for the job"

    # A later migration finds the job's files there and copies nothing over them.
    with committed_read_cache():
        later = migration.migrate(fx.home_root, fx.target, apply=True)
    assert later.counts["files"] == {"to_copy": 0, "copied": 0, "already": 5}
    assert EDITED in _resume(server, None)["markdown"] and role_folders(fx.home_root, fx.target) == held


def test_a_re_assessment_on_a_home_of_before_replaces_the_copy_and_never_the_roles_file(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    role_a, role_b, held = _left_by_an_older_gigai(fx, server)
    was = _assessment(server, role_b)
    fx.base.model.assessed = _matched_again(fx, was)

    again = server.client.post("/api/assess", json={"job": {"job_url": _URL}, "resume": {"profile_id": role_b}})

    assert again.status_code == 200, again.text
    now = _assessment(server, role_a)
    assert Path(now["stored_path"]).parent.name == "job" and now["resume"]["profile_id"] == role_b
    assert (now["created_at"], len(now["history"])) == (was["created_at"], len(was["history"]) + 1), "the job's history did not carry over"
    assert sorted(_on_disk(fx)["quick_assess"]) == ["job", role_a] and role_folders(fx.home_root, fx.target) == held


# --- (d) -------------------------------------------------------------------------------------------------------------


def test_a_suggested_resume_that_waited_is_still_taken_after_the_migration(fx: PostingsFixture, server: _Server) -> None:  # noqa: F811
    role_a, role_b = fx.default_profile_id, second_profile(fx)
    assess(fx, role_a, _answer(fx))
    resume = _resume(server, role_a)
    line = next(item for item in _bullets(resume) if item["kind"] == "copy")
    edited = server.client.put("/api/tailored-resumes/lines", json={
        "profile_id": role_a, "job_identity": _JOB, "updated_at": resume["updated_at"], "line_id": line["id"], "use": "custom", "text": EDITED,
    })
    assert edited.status_code == 200, edited.text
    # The resume is the user's now: a new assessment's selection WAITS beside the record.
    assess(fx, role_a, _matched_again(fx, _assessment(server, role_a)))
    waiting = f"{DIGEST}.proposed-resume.json"
    assert EDITED in _resume(server, role_a)["markdown"]
    to_role_folder(fx.home_root, fx.target, role_a, _JOB)
    assert _on_disk(fx)["suggestions"] == {role_a: [f"{DIGEST}.json", waiting]}
    with committed_read_cache():
        migration.migrate(fx.home_root, fx.target, apply=True)
    assert _on_disk(fx)["suggestions"]["job"] == [f"{DIGEST}.json", waiting]

    taken = server.client.post("/api/job-resumes/pick", json={"job_url": _URL, "profile_id": role_b, "action": "use_proposed"})

    assert taken.status_code == 200, taken.text
    held = _resume(server, role_b)
    assert EDITED not in held["markdown"] and line["text"] in held["markdown"], "the suggested resume did not replace the stored one"
    disk = _on_disk(fx)
    assert disk["suggestions"]["job"] == [f"{DIGEST}.json"] and disk["resumes"]["job"] == [f"{DIGEST}.json", f"{DIGEST}.md"]
    assert json.loads((project_dir(fx.home_root, fx.target) / "resumes" / "job" / f"{DIGEST}.json").read_bytes())["stored_path"] == held["stored_path"]
