"""0.1.11.10 Part B, packet G5: the job that generates a course (``gigai.scout.learning_job``).

A FAKE MODEL (a script of answers), a FAKE WEB (a table of pages, through the real outbound policy of
``learning_fetch``), a FAKE PATHS STEP handed to the job, a SYNTHETIC master resume, a temporary home. No network,
no real model. The corpus step (G1) is replaced by a canned count that makes one model call; the course steps
(G2), the renderer (G3) and the store are the real ones.

1. THE STEPS run in order and the course is stored: the record is ``done``, its cost is the job's own counters,
   its progress lists every step, the progress file holds the plain lines.
2. RESUME: a step whose stored result still validates is skipped; a resumed job asks only for what is missing.
3. CANCEL: the cancel file stops the next call; below 60 percent the record is ``failed`` / ``cancelled`` with the
   work kept, at 60 percent or more the course is finished with what was written.
4. A DEAD OWNER or a QUIET progress file reads as ``interrupted``; such a job may be resumed.
5. THE CAPS on model calls and page fetches; THE 60 PERCENT RULE at 57, 60 and 63 percent of 35 lessons.
6. FAILURES: the pipeline's retry (60 s doubling, 4 attempts) then ``model_unavailable``; a login failure at once;
   a gate that fails; the paths step that is not built fails before any call.
7. ONE COURSE AT A TIME: a second start is ``learning_running`` and stores nothing.
8. THE RESUME: the master's lines reach the lesson-writing prompts and nothing else (prompts, files, the paths
   step), and the module hands them to one call only.
9. THE ESTIMATE, the contact check on what reaches the record, and reads that take no lock.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
import dataclasses
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gigai.adapters.port import ModelAuthenticationRequired
from gigai.scout import learning_corpus, learning_course, learning_job, learning_store
from gigai.scout.find_jobs.api.learning import pathway_response, pathways_response
from gigai.scout.learning_corpus import LearningCorpusError
from gigai.scout.learning_fetch import PageFetcher
from gigai.scout.learning_job import LearningJob, LearningJobError

from tests.behaviors.scout_find_jobs.test_m1_end_to_end import _fixture
from tests.behaviors.scout_learning.test_learning_course import (  # noqa: F401 - names is the autouse fixture: no resolver is asked
    CANARY, CONCEPTS, CORPUS, HOST, MASTER, ROLE, ScriptedModel, curriculum_answer, lesson_id, module_answer, names, web_for,
)
from tests.behaviors.scout_learning.test_learning_fetch import Clock, FakeWeb

TITLES_PROMPT = "TITLES"
VOCABULARY_REFUSED = (
    "the model's vocabulary for this role was not usable, twice (the last answer: concepts holds 301 objects: 151 more than the 150 allowed "
    "(the bounds are 25 to 150))"
)
#: ``job(..., paths=REAL_PATHS)``: no stand-in, the job looks G4's own step up by name.
REAL_PATHS = object()
PATH_LESSON = "lesson-1-1"


def a_path(lid: str) -> dict[str, object]:
    """A practice path in the renderer's shape, its links on the fake docs host."""

    def item(n: int, do: str) -> dict[str, object]:
        return {"n": n, "do": do, "why": "It is the smallest thing that works.", "done_when": "It runs.", "source": {"title": f"Guide for {lid}", "url": f"{HOST}/{lid}/guide-1#setup"}}

    return {"tool": "Kubernetes", "setup": [item(1, "Install the tool")], "steps": [item(n, f"Do step {n}") for n in range(1, 7)], "sources": [{"title": "Docs", "url": f"{HOST}/{lid}/guide-1"}]}


class PathsStep:
    """The fake G4 step: a path for the first lesson, one dropped. Keeps what it was called with."""

    def __init__(self, fail: BaseException | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail = fail

    def __call__(self, work_dir: Path, **kwargs: object) -> dict[str, object]:
        self.calls.append({"work_dir": work_dir, **kwargs})
        if self.fail is not None:
            raise self.fail
        (Path(work_dir) / "paths").mkdir(exist_ok=True)
        (Path(work_dir) / "paths" / f"{PATH_LESSON}.json").write_text(json.dumps(a_path(PATH_LESSON)), encoding="utf-8")
        return {"paths": {PATH_LESSON: a_path(PATH_LESSON)}, "dropped": [{"what": "path:lesson-1-2", "reason": "path_unverified"}]}


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """A bound project, G1's corpus step as a canned count that makes one model call, and the synthetic master."""

    home, target, _workpad = _fixture(tmp_path)
    corpus_step = {"fails": False}

    def run_corpus(
        _home: Path, role_text: str, *, model: object, filters: object = None, now: object = None, progress: Callable[[str], None] | None = None,
        store: object = None,
    ) -> dict[str, object]:
        # As G1 does: a step stored for this role is not asked for again, and a step made is handed to the store.
        stored = store.read("titles")  # type: ignore[attr-defined]
        if not (isinstance(stored, dict) and stored.get("role_text") == role_text):
            model.ask(f"{TITLES_PROMPT} {role_text}")  # type: ignore[attr-defined]
            store.write("titles", {"role_text": role_text, "phrases": ["mlops engineer"], "deny": []})  # type: ignore[attr-defined]
        if corpus_step["fails"]:
            raise LearningCorpusError("vocabulary_invalid", VOCABULARY_REFUSED)
        return {"schema_version": learning_corpus.SCHEMA_VERSION, "status": "done", "role_text": role_text, "corpus": CORPUS, "concepts": CONCEPTS}

    monkeypatch.setattr(learning_corpus, "run_corpus", run_corpus)
    monkeypatch.setattr(learning_corpus, "corpus_filters", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(learning_course, "read_master_lines", lambda _home, _target: MASTER)
    return SimpleNamespace(home=home, target=target, slept=[], tmp=tmp_path, corpus_step=corpus_step)


def script(answer: dict[str, object], *, dead: tuple[str, ...] = (), failing: tuple[int, ...] = (), modules: range | None = None) -> list[object]:
    """The answers of one whole job: the corpus call, the curriculum, the source follow-up when lessons are ``dead``, the modules."""

    answers: list[object] = ["titles", answer]
    if dead:
        answers.append({"lessons": []})
    per_module = len(answer["modules"][0]["lessons"])  # type: ignore[index]
    for number in modules or range(1, len(answer["modules"]) + 1):  # type: ignore[arg-type]
        if all(lesson_id(number, n) in dead for n in range(1, per_module + 1)):
            continue
        answers.extend(["unusable", "unusable"] if number in failing else [module_answer()])
    return answers


def job(env: SimpleNamespace, pathway_id: str, model: object, web: FakeWeb, paths: object = None, **options: object) -> LearningJob:
    clock = Clock()
    return LearningJob(
        env.home, env.target, pathway_id, model=model,
        fetcher_factory=lambda budget, cancel: PageFetcher(web, clock=clock, sleep=clock.sleep, budget=budget, cancel=cancel),
        paths_step=None if paths is REAL_PATHS else paths if paths is not None else PathsStep(), sleep=env.slept.append, **options,  # type: ignore[arg-type]
    )


def request(env: SimpleNamespace, role: str = ROLE) -> str:
    return learning_store.create_request(env.home, env.target, role).id


def lines(env: SimpleNamespace, pathway_id: str) -> list[dict[str, object]]:
    path = learning_job.work_dir(env.home, env.target, pathway_id) / "progress.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def steps(record: learning_store.Pathway) -> dict[str, str]:
    return {item["id"]: item["status"] for item in record.progress["steps"]}  # type: ignore[index]


# ---------------------------------------------------------------------------
# 1. The steps, the stored course, the cost, the progress
# ---------------------------------------------------------------------------


def test_the_steps_run_in_order_and_the_course_is_stored_with_its_cost_and_progress(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    web, model, paths = web_for(answer), ScriptedModel(*script(answer)), PathsStep()
    pathway_id = request(env)
    told: list[str] = []
    done = job(env, pathway_id, model, web, paths, on_progress=told.append).run()

    assert done == learning_store.get_pathway(env.home, env.target, pathway_id)
    assert (done.status, done.source, done.imported_from, done.error, done.error_code) == ("done", "requested", None, None, None)
    assert done.course is not None and done.course.lessons == 28 and done.course.generated_at is not None
    # the cost is the job's own counters: 1 corpus call, the curriculum, 7 modules; every request the fetcher sent
    assert model.calls == 9 and len(web.asked) == 85
    assert done.cost == {
        "cli_model_calls": 9, "worker_minutes": done.cost["worker_minutes"], "web_fetches": 85, "web_searches": 0, "tokens": None, "note": None,
    }
    assert isinstance(done.cost["worker_minutes"], float) and done.cost["worker_minutes"] >= 0

    progress = done.progress
    assert progress is not None and tuple(item["id"] for item in progress["steps"]) == learning_job.STEPS == ("corpus", "course", "paths", "render", "audit", "import")  # type: ignore[index, union-attr]
    assert set(steps(done).values()) == {"done"} and progress["step"] is None
    assert all(item["started_at"] and item["finished_at"] for item in progress["steps"])  # type: ignore[union-attr]
    assert (progress["calls"], progress["fetches"], progress["max_calls"], progress["max_fetches"]) == (9, 85, 120, 1500)
    assert progress["dropped"] == [{"what": "path:lesson-1-2", "reason": "path_unverified"}]

    texts = [line["text"] for line in lines(env, pathway_id)]
    assert texts == told, "what the caller is told is what the progress file holds"
    assert "Reading 44 postings" in texts and "Writing module 3 of 7" in texts and "Checking sources for lesson 14 of 28" in texts
    assert "Checking links for 28 lessons (1 dropped so far)" in texts
    order = [line["step"] for line in lines(env, pathway_id)]
    assert [step for index, step in enumerate(order) if index == 0 or order[index - 1] != step] == ["corpus", "course", "render", "audit"]

    # the paths step is G4's contract: the work folder, and these arguments by name
    (call,) = paths.calls
    assert call["work_dir"] == learning_job.work_dir(env.home, env.target, pathway_id)
    assert set(call) == {"work_dir", "model", "fetcher", "course", "sources", "cancel", "progress"}
    assert call["course"]["kept_lessons"] == 28 and set(call["sources"]["lessons"]) == {lesson_id(m, n) for m in range(1, 8) for n in range(1, 5)}  # type: ignore[index]

    # the stored course is served like an imported one, with the practice page and what is not included
    index = learning_store.resolve_course_file(env.home, env.target, pathway_id, "")
    assert index is not None and learning_store.resolve_course_file(env.home, env.target, pathway_id, f"practice-{PATH_LESSON}.html") is not None
    page = index.read_text(encoding="utf-8")
    assert "Not included" in page and "Practice path for lesson-1-2: too few of its practice steps could be verified" in page
    assert not (learning_store.learning_dir(env.home, env.target) / "live.json").exists(), "the project's job slot is free again"
    assert learning_job.running_id(env.home, env.target) is None and learning_job.live_status(env.home, env.target, done) == "done"


# ---------------------------------------------------------------------------
# 2. Resume: a step with a valid stored result is skipped
# ---------------------------------------------------------------------------


def test_the_corpus_steps_are_kept_in_the_work_folder_so_a_failed_run_can_be_read_and_resumed(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    # the first run: the titles are made, then the vocabulary answer is not usable
    first = ScriptedModel("titles")
    env.corpus_step["fails"] = True
    failed = job(env, pathway_id, first, web_for(answer)).run()
    env.corpus_step["fails"] = False
    # the record says which bound the answer missed, in the corpus step's own words
    assert (failed.status, failed.error_code, failed.error) == ("failed", "vocabulary_invalid", VOCABULARY_REFUSED)
    assert steps(failed)["corpus"] == "failed" and first.calls == 1
    work = learning_job.work_dir(env.home, env.target, pathway_id)
    assert json.loads((work / "titles.json").read_text(encoding="utf-8")) == {"role_text": ROLE, "phrases": ["mlops engineer"], "deny": []}
    assert not (work / "corpus.json").exists(), "the step did not finish: its result is not stored, its parts are"

    # the resume: the titles call is not made again (the script holds no answer for it)
    second = ScriptedModel(*script(answer)[1:])
    done = job(env, pathway_id, second, web_for(answer)).run()
    assert done.status == "done" and steps(done)["corpus"] == "done" and (work / "corpus.json").is_file()
    assert done.cost["cli_model_calls"] == 1 + second.calls  # type: ignore[index]


def test_a_resumed_job_skips_every_step_whose_result_is_stored_and_asks_only_for_what_is_missing(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    # the first run stops at module 5: the model is out of reach four times
    down = LearningCorpusError("model_unavailable", "CLI model process failed with exit code 1: usage limit")

    def unavailable(_prompt: str) -> str:
        raise down

    first = ScriptedModel(*script(answer, modules=range(1, 5)), *[unavailable] * 4)
    failed = job(env, pathway_id, first, web_for(answer)).run()
    assert (failed.status, failed.error_code, failed.error) == ("failed", "model_unavailable", "Your model login refused or timed out; resume later.")
    assert steps(failed) == {"corpus": "done", "course": "failed", "paths": "pending", "render": "pending", "audit": "pending", "import": "pending"}
    assert failed.course is None and learning_store.resolve_course_file(env.home, env.target, pathway_id, "") is None
    work = learning_job.work_dir(env.home, env.target, pathway_id)
    assert sorted(path.name for path in (work / "modules").iterdir()) == ["m1.json", "m2.json", "m3.json", "m4.json"], "what was written is kept"

    # the resume: no corpus call, no curriculum call, no fetch for a source, three module calls
    second, web = ScriptedModel(*[module_answer()] * 3), web_for(answer)
    done = job(env, pathway_id, second, web).run()
    assert done.status == "done" and done.course is not None and done.course.lessons == 28
    assert second.calls == 3 and web.pages_asked() == []
    assert steps(done) == {"corpus": "skipped", "course": "done", "paths": "done", "render": "done", "audit": "done", "import": "done"}
    assert done.cost["cli_model_calls"] == first.calls + 3 and done.progress["calls"] == first.calls + 3  # type: ignore[index]

    with pytest.raises(LearningJobError) as refused:
        learning_job.resume(env.home, env.target, pathway_id)
    assert refused.value.code == "not_resumable"


def test_a_resume_after_a_late_failure_spends_no_call_and_no_fetch(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    failed = job(env, pathway_id, ScriptedModel(*script(answer)), web_for(answer), PathsStep(fail=LearningJobError("paths_broken", "the paths step broke"))).run()
    assert (failed.status, failed.error_code) == ("failed", "paths_broken") and steps(failed)["paths"] == "failed"

    model, web = ScriptedModel(), FakeWeb({})
    done = job(env, pathway_id, model, web).run()
    assert done.status == "done" and model.calls == 0 and web.asked == []
    assert steps(done) == {"corpus": "skipped", "course": "skipped", "paths": "done", "render": "done", "audit": "done", "import": "done"}


# ---------------------------------------------------------------------------
# 3. Cancel
# ---------------------------------------------------------------------------


def cancelling(env: SimpleNamespace, pathway_id: str, after_modules: int, total: int = 7) -> list[object]:
    """Module answers where the stop is asked while module ``after_modules`` is being written."""

    def stop(prompt: str) -> str:
        assert learning_job.cancel(env.home, env.target, pathway_id) is True
        return module_answer()(prompt)

    return [*[module_answer()] * (after_modules - 1), stop, *[module_answer()] * (total - after_modules)]


def test_a_cancel_below_sixty_percent_fails_the_job_and_keeps_the_work(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    model = ScriptedModel("titles", answer, *cancelling(env, pathway_id, 2))
    stopped = job(env, pathway_id, model, web_for(answer)).run()

    assert model.calls == 4, "the call in flight finished; no call started after the stop"
    assert (stopped.status, stopped.error_code) == ("failed", "cancelled") and "Stopped" in str(stopped.error)
    assert steps(stopped)["course"] == "done" and steps(stopped)["paths"] == "pending"
    assert {"what": "module:m3", "reason": "cancelled"} in stopped.progress["dropped"]  # type: ignore[index]
    work = learning_job.work_dir(env.home, env.target, pathway_id)
    assert (work / "modules" / "m2.json").is_file() and (work / "course.json").is_file()
    assert learning_job.cancel(env.home, env.target, pathway_id) is False, "no job runs for it any more"

    # the resume takes the stop back and writes the rest
    done = job(env, pathway_id, ScriptedModel(*[module_answer()] * 5), web_for(answer)).run()
    assert done.status == "done" and done.course is not None and done.course.lessons == 28 and not (work / "cancel").exists()


def test_a_cancel_with_sixty_percent_written_finishes_the_course_with_what_was_written(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    model, paths = ScriptedModel("titles", answer, *cancelling(env, pathway_id, 5)), PathsStep()
    done = job(env, pathway_id, model, web_for(answer), paths).run()

    assert model.calls == 7 and paths.calls == [], "nothing is asked after the stop: not a module, not a practice path"
    assert done.status == "done" and done.course is not None and done.course.lessons == 20  # 20 of 28 is 71 percent
    assert steps(done) == {"corpus": "done", "course": "done", "paths": "skipped", "render": "done", "audit": "done", "import": "done"}
    assert done.progress["dropped"] == [{"what": "module:m6", "reason": "cancelled"}, {"what": "module:m7", "reason": "cancelled"}]  # type: ignore[index]
    page = learning_store.resolve_course_file(env.home, env.target, pathway_id, "").read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert "Module Module 6 (4 lessons): the course was stopped before it was written" in page


def test_the_cancel_file_is_read_before_a_fetch_and_another_process_can_write_it(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    work = learning_job.work_dir(env.home, env.target, pathway_id)

    def curriculum(_prompt: str) -> str:
        (work / "cancel").write_text("now", encoding="utf-8")  # what `gigai scout learning cancel` leaves, from any process
        return json.dumps(answer)

    web = web_for(answer)
    stopped = job(env, pathway_id, ScriptedModel("titles", curriculum), web).run()
    assert (stopped.status, stopped.error_code) == ("failed", "cancelled") and web.asked == []


# ---------------------------------------------------------------------------
# 4. A dead owner, a quiet progress file: interrupted
# ---------------------------------------------------------------------------


def dead_pid() -> int:
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def running_record(env: SimpleNamespace, *, pid: int, age_seconds: float = 0.0) -> learning_store.Pathway:
    """A record a job left ``running``, owned by ``pid``, its progress file ``age_seconds`` old."""

    pathway = learning_store.create_request(env.home, env.target, ROLE)
    work = learning_job.work_dir(env.home, env.target, pathway.id)
    work.mkdir(parents=True)
    claim = {"id": pathway.id, "pid": pid, "token": "another-process", "run": "0" * 32, "claimed_at": learning_store.now_text()}
    for path in (work / "owner.json", learning_store.learning_dir(env.home, env.target) / "live.json"):
        path.write_text(json.dumps(claim), encoding="utf-8")
    (work / "progress.jsonl").write_text("", encoding="utf-8")
    then = os.path.getmtime(work / "progress.jsonl") - age_seconds
    for name in ("progress.jsonl", "owner.json"):
        os.utime(work / name, (then, then))
    with learning_store.write_lock(env.home, env.target):
        return learning_store.write_pathway(env.home, env.target, dataclasses.replace(pathway, status="running", revision=2))


def test_a_running_record_whose_owner_is_gone_reads_as_interrupted_and_can_be_resumed(env: SimpleNamespace) -> None:
    record = running_record(env, pid=dead_pid())
    assert learning_job.live_status(env.home, env.target, record) == "interrupted"
    assert learning_job.running_id(env.home, env.target) is None
    listed = pathways_response(env.home, env.target)["pathways"]
    assert listed[0]["status"] == "interrupted" and pathway_response(record, status="interrupted")["pathway"]["status"] == "interrupted"  # type: ignore[index]
    assert learning_store.get_pathway(env.home, env.target, record.id).status == "running", "a read changes nothing: the record still says running"  # type: ignore[union-attr]
    assert learning_job.cancel(env.home, env.target, record.id) is False

    answer = curriculum_answer()
    done = job(env, record.id, ScriptedModel(*script(answer)), web_for(answer)).run()
    assert done.status == "done" and learning_job.live_status(env.home, env.target, done) == "done"


def test_a_live_owner_reads_as_running_until_its_progress_file_is_quiet_for_fifteen_minutes(env: SimpleNamespace) -> None:
    record = running_record(env, pid=os.getppid(), age_seconds=14 * 60)
    assert learning_job.live_status(env.home, env.target, record) == "running" and learning_job.running_id(env.home, env.target) == record.id
    with pytest.raises(LearningJobError) as refused:
        learning_job.resume(env.home, env.target, record.id)
    assert refused.value.code == "learning_running"

    work = learning_job.work_dir(env.home, env.target, record.id)
    then = os.path.getmtime(work / "progress.jsonl") - 2 * 60
    for name in ("progress.jsonl", "owner.json"):
        os.utime(work / name, (then, then))
    assert learning_job.QUIET_SECONDS == 15 * 60
    assert learning_job.live_status(env.home, env.target, record) == "interrupted" and learning_job.running_id(env.home, env.target) is None


def test_a_request_that_was_never_started_stays_queued(env: SimpleNamespace) -> None:
    pathway = learning_store.create_request(env.home, env.target, ROLE)
    assert learning_job.live_status(env.home, env.target, pathway) == "queued"


# ---------------------------------------------------------------------------
# 5. The caps, the 60 percent rule
# ---------------------------------------------------------------------------


def test_the_call_cap_fails_a_job_that_has_too_little_and_finishes_one_that_has_enough(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    low = request(env)
    model = ScriptedModel(*script(answer))
    capped = job(env, low, model, web_for(answer), max_calls=4).run()
    assert model.calls == 4 and (capped.status, capped.error_code) == ("failed", "cap_reached")
    assert "cap of 120 model calls or 1500 page fetches" in str(capped.error) and capped.progress["max_calls"] == 4  # type: ignore[index]
    assert (learning_job.work_dir(env.home, env.target, low) / "modules" / "m2.json").is_file()

    enough = request(env, "Platform engineer")
    model, paths = ScriptedModel(*script(answer)), PathsStep()
    done = job(env, enough, model, web_for(answer), paths, max_calls=7).run()
    assert model.calls == 7 and paths.calls == []
    assert done.status == "done" and done.course is not None and done.course.lessons == 20
    assert {item["reason"] for item in done.progress["dropped"]} == {"cap_reached"}  # type: ignore[index, union-attr]


def test_the_fetch_cap_ends_the_source_checks_and_too_few_lessons_is_cap_reached(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    web = web_for(answer)
    capped = job(env, pathway_id, ScriptedModel(*script(answer)), web, max_fetches=31).run()
    assert len(web.asked) == 31, "no request after the cap"
    assert (capped.status, capped.error_code) == ("failed", "cap_reached") and capped.progress["fetches"] == 31  # type: ignore[index]


@pytest.mark.parametrize("dead_lessons, kept, status", [(5, 20, "failed"), (4, 21, "done"), (3, 22, "done")])
def test_the_sixty_percent_rule_at_57_60_and_63_percent(env: SimpleNamespace, dead_lessons: int, kept: int, status: str) -> None:
    # 35 lessons planned (7 modules of 5); modules 6 and 7 fail their text (10 lessons) and 3 to 5 lessons have no source.
    answer = curriculum_answer(modules=7, per_module=5)
    dead = tuple(lesson_id(1, n) for n in range(1, dead_lessons + 1))
    pathway_id = request(env)
    ended = job(env, pathway_id, ScriptedModel(*script(answer, dead=dead, failing=(6, 7))), web_for(answer, dead=dead)).run()

    work = learning_job.work_dir(env.home, env.target, pathway_id)
    course = json.loads((work / "course.json").read_text(encoding="utf-8"))
    assert (course["planned_lessons"], course["kept_lessons"]) == (35, kept)
    assert ended.status == status
    reasons = [item["reason"] for item in ended.progress["dropped"]]  # type: ignore[index, union-attr]
    assert reasons.count("no_verified_source") == dead_lessons and reasons.count("module_text_failed") == 2
    if status == "failed":
        assert ended.error_code == "too_little_verified" and ended.error == (
            "Too few lessons passed the checks (20 of 35; a course needs 60 percent). What was written is kept; resume to try the rest again."
        )
        assert ended.course is None and (work / "modules" / "m2.json").is_file(), "the work folder stays for a resume"
    else:
        assert ended.course is not None and ended.course.lessons == kept
        page = learning_store.resolve_course_file(env.home, env.target, pathway_id, "").read_text(encoding="utf-8")  # type: ignore[union-attr]
        assert "Not included" in page and "Lesson Lesson 1.1: no source page for it could be verified" in page
        assert "Module Module 6 (5 lessons): its text did not pass the checks, twice" in page


# ---------------------------------------------------------------------------
# 6. Failures
# ---------------------------------------------------------------------------


def test_a_model_failure_is_retried_after_60_120_and_240_seconds_and_a_fifth_attempt_is_never_made(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    attempts: list[int] = []

    def flaky(_prompt: str) -> str:
        attempts.append(1)
        if len(attempts) < 4:
            raise LearningCorpusError("model_unavailable", "CLI model process failed with exit code 1: overloaded")
        return json.dumps(answer)

    model = ScriptedModel("titles", flaky, flaky, flaky, flaky, *[module_answer()] * 7)
    done = job(env, pathway_id, model, web_for(answer)).run()
    assert done.status == "done" and len(attempts) == 4
    assert learning_job.RETRY_ATTEMPTS == 4 and sum(env.slept) == 60 + 120 + 240 and max(env.slept) <= 1.0
    assert done.cost["cli_model_calls"] == 12, "an attempt that failed is a call that was made"
    assert "The model did not answer (attempt 2 of 4); trying again in 2 min" in [line["text"] for line in lines(env, pathway_id)]


def test_a_call_that_timed_out_is_retried_at_once_with_its_own_progress_line(env: SimpleNamespace) -> None:
    # G7c: a timeout (the adapter's own words: "CLI model invocation timed out") is not a rate limit easing off;
    # it is retried at once, and the progress line says "took too long", not "did not answer".
    answer = curriculum_answer()
    pathway_id = request(env)
    attempts: list[int] = []

    def slow(_prompt: str) -> str:
        attempts.append(1)
        if len(attempts) < 2:
            raise LearningCorpusError("model_unavailable", "CLI model invocation timed out")
        return json.dumps(answer)

    model = ScriptedModel("titles", slow, slow, *[module_answer()] * 7)
    done = job(env, pathway_id, model, web_for(answer)).run()
    assert done.status == "done" and len(attempts) == 2
    assert env.slept == [], "a timeout is retried at once: no rate-limit doubling wait, not even a zero-second one"
    texts = [str(line["text"]) for line in lines(env, pathway_id)]
    assert "The model took too long to answer (attempt 1 of 4); trying again now" in texts
    assert not any("did not answer" in text for text in texts), "a timeout gets its own words, never the rate-limit wording"


def test_a_login_failure_fails_at_once_with_the_adapters_own_words(env: SimpleNamespace) -> None:
    pathway_id = request(env)

    def not_logged_in(_prompt: str) -> str:
        try:
            raise ModelAuthenticationRequired("authentication_required: Not logged in. Please run /login")
        except ModelAuthenticationRequired as exc:
            raise LearningCorpusError("model_unavailable", str(exc)) from exc

    model = ScriptedModel(not_logged_in)
    failed = job(env, pathway_id, model, FakeWeb({})).run()
    assert model.calls == 1 and env.slept == []
    assert (failed.status, failed.error_code) == ("failed", "model_authentication_required")
    assert failed.error == "authentication_required: Not logged in. Please run /login" and steps(failed)["corpus"] == "failed"


def test_a_gate_that_fails_fails_the_course_with_the_first_reason_and_no_address_in_the_record(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    monkeypatch.setattr(learning_job, "verified_urls", lambda _sources, _paths: set())  # as if no link had been verified in this run
    web = web_for(answer)
    failed = job(env, pathway_id, ScriptedModel(*script(answer)), web).run()
    assert (failed.status, failed.error_code) == ("failed", "audit_failed") and steps(failed)["audit"] == "failed" and steps(failed)["import"] == "pending"
    assert failed.error == "The finished course did not pass its checks: a link leads to a page that was not verified in this run."
    assert "http" not in json.dumps(failed.to_json()), "the record holds no address"
    audit = json.loads((learning_job.work_dir(env.home, env.target, pathway_id) / "audit.json").read_text(encoding="utf-8"))
    assert audit["problems"][0]["gate"] == "external_links" and HOST in audit["problems"][0]["detail"]
    assert len(web.asked) == 85, "the audit fetched nothing"


def test_every_gate_names_what_it_found_on_a_finished_folder(env: SimpleNamespace, tmp_path: Path) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    assert job(env, pathway_id, ScriptedModel(*script(answer)), web_for(answer)).run().status == "done"
    work = learning_job.work_dir(env.home, env.target, pathway_id)
    course = json.loads((work / "course.json").read_text(encoding="utf-8"))
    sources = json.loads((work / "sources.json").read_text(encoding="utf-8"))
    paths = {PATH_LESSON: a_path(PATH_LESSON)}
    verified = learning_job.verified_urls(sources, paths)
    site = work / "site"
    assert learning_job.audit_site(site, course=course, paths=paths, verified=verified) == []

    page = site / "concept-lesson-2-1.html"
    original = page.read_text(encoding="utf-8")
    page.write_text(
        original.replace("</body>", '<a href="missing.html">x</a><a href="https://elsewhere.example.com/page" target="_blank">y</a><script>var a = 1;</script>☃</body>'),
        encoding="utf-8",
    )
    found = learning_job.audit_site(site, course={**course, "role": "write to someone@example.com"}, paths=paths, verified=verified)
    assert {item["gate"] for item in found} == {"internal_links", "external_links", "glyphs", "inline_script", "contact_data"}


def test_without_the_paths_step_the_job_fails_before_any_call(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(learning_job, "_PATHS_MODULE", "gigai.scout.no_such_module_of_g4")
    with pytest.raises(LearningJobError) as missing:
        learning_job.paths_step()
    assert missing.value.code == "paths_step_not_built"

    pathway_id = request(env)
    model = ScriptedModel()
    clock = Clock()
    failed = LearningJob(
        env.home, env.target, pathway_id, model=model,
        fetcher_factory=lambda budget, cancel: PageFetcher(FakeWeb({}), clock=clock, sleep=clock.sleep, budget=budget, cancel=cancel),
    ).run()
    assert (failed.status, failed.error_code) == ("failed", "paths_step_not_built") and model.calls == 0


def test_the_paths_step_is_found_by_name_once_it_is_there(monkeypatch: pytest.MonkeyPatch) -> None:
    # A stand-in module under a made-up name: the adapter needs no code change when G4's module lands.
    stand_in = SimpleNamespace(run_paths_step=lambda work_dir, **_kwargs: {"paths": {}, "dropped": []})
    monkeypatch.setitem(sys.modules, "gigai.scout.learning_job_test_paths", stand_in)
    monkeypatch.setattr(learning_job, "_PATHS_MODULE", "gigai.scout.learning_job_test_paths")
    monkeypatch.setattr("importlib.util.find_spec", lambda name, *_args: object() if name.endswith("learning_job_test_paths") else None)
    assert learning_job.paths_step() is stand_in.run_paths_step


def test_g4s_own_paths_step_runs_under_the_jobs_model_fetcher_cap_and_cancel(env: SimpleNamespace) -> None:
    # No stand-in here: learning_paths.run_paths_step itself, found by name. Every path answer of this script is
    # unusable, so no lesson gets a practice page: what is proved is the wiring (the arguments, the counters, the
    # dropped list on the first page) and that a course without practice pages is still a course.
    answer = curriculum_answer()
    pathway_id = request(env)
    model = ScriptedModel(*script(answer), *["not an answer"] * 200)
    web = web_for(answer)
    done = job(env, pathway_id, model, web, REAL_PATHS).run()

    assert done.status == "done" and done.course is not None and done.course.lessons == 28, (done.error, done.error_code)
    assert steps(done)["paths"] == "done" and model.calls > 9 and done.cost["cli_model_calls"] == model.calls
    assert done.cost["web_fetches"] == len(web.asked) > 85, "the crawl and the re-fetches went through the job's fetcher and its budget"
    dropped = done.progress["dropped"]  # type: ignore[index]
    assert len(dropped) == 28 and all(item["what"].startswith("path:lesson-") for item in dropped)  # type: ignore[union-attr]
    assert {item["reason"] for item in dropped} <= {"path_unverified", "path_model_failed"}  # type: ignore[union-attr]
    page = learning_store.resolve_course_file(env.home, env.target, pathway_id, "").read_text(encoding="utf-8")  # type: ignore[union-attr]
    assert "Practice path for Lesson 1.1: " in page and learning_store.resolve_course_file(env.home, env.target, pathway_id, "practice-lesson-1-1.html") is None
    # the resume still reaches the seven lesson-writing prompts only: no practice-path prompt holds a line of it
    assert [index for index, prompt in enumerate(model.prompts) if CANARY in prompt] == list(range(2, 9))


# ---------------------------------------------------------------------------
# 7. One course at a time
# ---------------------------------------------------------------------------


def test_a_second_course_is_refused_while_one_is_generated_and_stores_nothing(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    seen: list[object] = []

    def curriculum(_prompt: str) -> str:
        record = learning_store.get_pathway(env.home, env.target, pathway_id)
        seen.append((record.status, learning_job.live_status(env.home, env.target, record), learning_job.running_id(env.home, env.target)))  # type: ignore[union-attr, arg-type]
        for start in (
            lambda: learning_job.start(env.home, env.target, "Another role"),
            lambda: learning_job.run_foreground(env.home, env.target, role_text="Another role"),
            lambda: learning_job.resume(env.home, env.target, pathway_id),
        ):
            with pytest.raises(LearningJobError) as refused:
                start()
            seen.append(refused.value.code)
        return json.dumps(answer)

    done = job(env, pathway_id, ScriptedModel("titles", curriculum, *[module_answer()] * 7), web_for(answer)).run()
    assert seen == [("running", "running", pathway_id), "learning_running", "learning_running", "learning_running"]
    assert done.status == "done" and [item.id for item in learning_store.list_pathways(env.home, env.target)] == [pathway_id]


def test_start_stores_a_queued_request_and_runs_the_job_on_a_thread(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    made: list[LearningJob] = []

    def factory(home: Path, target: Path, pathway_id: str) -> LearningJob:
        made.append(job(SimpleNamespace(home=home, target=target, slept=env.slept), pathway_id, ScriptedModel(*script(answer)), web_for(answer)))
        return made[-1]

    queued = learning_job.start(env.home, env.target, f"  {ROLE}  ", job_factory=factory)
    assert (queued.status, queued.role_text, queued.source, queued.course) == ("queued", ROLE, "requested", None)
    assert learning_job.wait_for_jobs(timeout=120)
    done = learning_store.get_pathway(env.home, env.target, queued.id)
    assert done is not None and done.status == "done" and done.requested_at == queued.requested_at

    for bad, code in (("", "invalid_value"), ("x" * 201, "invalid_value"), ("reach me at someone@example.com", "personal_info_refused")):
        with pytest.raises(learning_store.LearningError) as refused:
            learning_job.start(env.home, env.target, bad, job_factory=factory)
        assert refused.value.code == code
    assert len(learning_store.list_pathways(env.home, env.target)) == 1 and len(made) == 1 and learning_job.running_id(env.home, env.target) is None


# ---------------------------------------------------------------------------
# 8. The resume is the reference, and goes to the lesson text only
# ---------------------------------------------------------------------------


def test_the_masters_lines_reach_the_lesson_prompts_and_nothing_else(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)
    model, paths = ScriptedModel(*script(answer)), PathsStep()
    done = job(env, pathway_id, model, web_for(answer), paths).run()
    assert done.status == "done"

    with_resume = [index for index, prompt in enumerate(model.prompts) if CANARY in prompt]
    assert with_resume == list(range(2, 9)), "the seven lesson-writing prompts, and neither the corpus call nor the curriculum"
    assert all("LESSON id=" in model.prompts[index] for index in with_resume)
    # nothing the paths step is handed holds a resume line, and it is given no resume argument
    assert CANARY not in json.dumps({key: value for key, value in paths.calls[0].items() if key in ("course", "sources")})
    # no file of the job, the record or the stored course holds it: a course shows a short QUOTE of a line, checked verbatim, never the line
    learning = learning_store.learning_dir(env.home, env.target)
    holding = [str(path.relative_to(learning)) for path in learning.rglob("*") if path.is_file() and CANARY.encode() in path.read_bytes()]
    assert holding == []


def test_the_job_module_hands_the_masters_lines_to_one_call_only() -> None:
    source = Path(learning_job.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    readers = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", "")) == "read_master_lines"]
    assert len(readers) == 1, "the master is read in one place"
    handed = [
        (getattr(call.func, "attr", ""), keyword.arg)
        for call in ast.walk(tree) if isinstance(call, ast.Call)
        for keyword in call.keywords if keyword.value is readers[0]
    ]
    assert handed == [("run_course_steps", "master_lines")], "and handed to G2's steps as master_lines, nowhere else"
    for name in ("load_master", "model_resume", "resume_for_profile", "read_pinned_resume", "master_store"):
        assert name not in source, f"the job reads the resume through read_master_lines only ({name})"


# ---------------------------------------------------------------------------
# 9. The estimate, the contact check, reads without a lock
# ---------------------------------------------------------------------------


def _done_course(env: SimpleNamespace, cost: Mapping[str, object], *, source: str = "requested", at: datetime) -> learning_store.Pathway:
    """A synthetic DONE pathway with ``cost`` as recorded, requested at ``at`` (no job run: a store-level fixture)."""

    queued = learning_store.create_request(env.home, env.target, "Synthetic role " + learning_store.new_id(), now=at)
    course = learning_store.Course(entry=learning_store.COURSE_ENTRY, lessons=28, size_bytes=1024, generated_at=learning_store.now_text(at))
    with learning_store.write_lock(env.home, env.target):
        return learning_store.write_pathway(
            env.home, env.target,
            dataclasses.replace(queued, status="done", source=source, course=course, cost=learning_store.clean_cost(cost)),
        )


def test_the_estimate_is_the_constants_until_a_done_course_has_a_cost_record(env: SimpleNamespace) -> None:
    asked = learning_job.ask_response(env.home, env.target, f" {ROLE} ")
    assert asked == {
        "schema_version": "scout-learning-generate:1", "status": "ask", "role_text": ROLE,
        "estimate": {"calls": 60, "fetches": 1000, "minutes": 45, "tokens": None, "basis": "none"},
        "caps": {"calls": 120, "fetches": 1500}, "warning": "learning_higher_usage",
    }
    assert learning_store.list_pathways(env.home, env.target) == [], "asking stores nothing"
    assert learning_job.estimate_words(asked["estimate"]) == (  # type: ignore[arg-type]
        "about 60 model calls, about 1000 page fetches from public documentation sites, about 45 minutes (no recorded course yet to estimate tokens or time from)"
    )
    with pytest.raises(learning_store.LearningError) as refused:
        learning_job.ask_response(env.home, env.target, "call 415 555 0100 about the role")
    assert refused.value.code == "personal_info_refused"

    # a queued or failed request, and an IMPORTED done course, never size the estimate (section: learning_job.estimate)
    learning_store.create_request(env.home, env.target, "Still queued")
    _done_course(
        env, {"cli_model_calls": 99, "worker_minutes": 999, "web_fetches": 9999, "tokens": 9_999_999},
        source="imported", at=datetime(2026, 10, 1, tzinfo=UTC),
    )
    assert learning_job.estimate(env.home, env.target)["basis"] == "none"

    # one done, requested course with a cost record: the estimate IS that course's own totals, not a scaled average
    _done_course(env, {"cli_model_calls": 54, "worker_minutes": 165.3, "web_fetches": 1445, "tokens": 1_014_000}, at=datetime(2026, 10, 2, tzinfo=UTC))
    found = learning_job.estimate(env.home, env.target)
    assert found == {"calls": 54, "fetches": 1445, "minutes": 166, "tokens": 1_014_000, "basis": "history"}
    assert "about 1014k tokens (sized from your last course(s))" in learning_job.estimate_words(found)

    # a second done course: the average of the two (newest first; list_pathways already sorts that way)
    _done_course(env, {"cli_model_calls": 22, "worker_minutes": 25.0, "web_fetches": 1445, "tokens": 436_000}, at=datetime(2026, 10, 3, tzinfo=UTC))
    found = learning_job.estimate(env.home, env.target)
    assert found == {"calls": 38, "fetches": 1445, "minutes": 96, "tokens": 725_000, "basis": "history"}

    # a third, done but with NO cost record at all (an older build, or one never metered): skipped, not averaged as 0
    _done_course(env, {}, at=datetime(2026, 10, 4, tzinfo=UTC))
    found = learning_job.estimate(env.home, env.target)
    assert found == {"calls": 38, "fetches": 1445, "minutes": 96, "tokens": 725_000, "basis": "history"}, "a cost-less done course does not count"

    # a third WITH calls but no tokens: tokens of the average are skipped for it, calls/fetches/minutes are not
    _done_course(env, {"cli_model_calls": 60, "worker_minutes": 40.0, "web_fetches": 1000}, at=datetime(2026, 10, 5, tzinfo=UTC))
    found = learning_job.estimate(env.home, env.target)
    assert found == {"calls": 45, "fetches": 1297, "minutes": 77, "tokens": 725_000, "basis": "history"}, "tokens averages only over the ones that have it"

    # a fourth done course: only the last 3 (HISTORY_COURSES) size the estimate, the oldest drops out
    _done_course(env, {"cli_model_calls": 10, "worker_minutes": 5.0, "web_fetches": 10, "tokens": 1000}, at=datetime(2026, 10, 6, tzinfo=UTC))
    assert learning_job.HISTORY_COURSES == 3
    found = learning_job.estimate(env.home, env.target)
    assert found["calls"] == round((22 + 60 + 10) / 3)


def test_a_progress_line_that_looks_like_contact_data_never_reaches_the_record(env: SimpleNamespace) -> None:
    answer = curriculum_answer()
    pathway_id = request(env)

    def chatty(work_dir: Path, *, progress: Callable[[str], None], **_kwargs: object) -> dict[str, object]:
        progress("Reading https://private.example.com/u/someone and someone@example.com")
        progress("Checking links for lesson 14 of 30 (2 dropped so far)")
        return {"paths": {}, "dropped": [{"what": "write to someone@example.com", "reason": "path_unverified"}]}

    done = job(env, pathway_id, ScriptedModel(*script(answer)), web_for(answer), chatty).run()
    texts = [line["text"] for line in lines(env, pathway_id)]
    assert "Working" in texts and "Checking links for lesson 14 of 30 (2 dropped so far)" in texts
    stored = json.dumps(done.to_json()) + (learning_job.work_dir(env.home, env.target, pathway_id) / "progress.jsonl").read_text(encoding="utf-8")
    assert "someone@example.com" not in stored and "private.example.com" not in stored
    assert done.progress["dropped"] == [{"what": "an item", "reason": "path_unverified"}]  # type: ignore[index]

    for bad in ({**done.progress, "extra": 1}, {**done.progress, "dropped": [{"what": "mail someone@example.com", "reason": "x"}]}, {**done.progress, "step": "Not A Code"}):  # type: ignore[dict-item]
        with pytest.raises(learning_store.LearningError):
            learning_store.clean_progress(bad)
    with pytest.raises(ValueError):
        learning_store.pathway_from_json({**done.to_json(), "error_code": "Not A Code"})


def test_a_record_written_before_progress_still_reads_and_a_read_takes_no_lock(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    old = learning_store.create_request(env.home, env.target, ROLE)
    path = learning_store.records_dir(env.home, env.target) / f"{old.id}.json"
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert "progress" not in stored and "error_code" not in stored, "a record with no generation is stored as it always was"
    read = learning_store.get_pathway(env.home, env.target, old.id)
    assert read == old and read.progress is None and read.error_code is None
    assert pathway_response(old)["pathway"]["progress"] is None and pathway_response(old)["pathway"]["error_code"] is None, "the API always says both"
    running = running_record(env, pid=dead_pid())

    def no_lock(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a read took the store's writer lock")

    monkeypatch.setattr(learning_store, "write_lock", no_lock)
    before = {item: item.read_bytes() for item in learning_store.learning_dir(env.home, env.target).rglob("*") if item.is_file()}
    rows = {row["id"]: row for row in pathways_response(env.home, env.target)["pathways"]}  # type: ignore[union-attr]
    assert rows[old.id]["status"] == "queued" and rows[running.id]["status"] == "interrupted"
    assert set(rows[old.id]) == {"id", "role_text", "requested_at", "status", "cost", "course", "url", "source", "imported_from", "error", "error_code", "progress"}
    assert {item: item.read_bytes() for item in learning_store.learning_dir(env.home, env.target).rglob("*") if item.is_file()} == before, "a read writes nothing"
