"""0.1.11.10 shipped lessons, slices 4b and 5b: building the user's own pathway page of a shipped course.

``learning_pathway_build`` (the estimate, the build, its state), ``find_jobs/api/learning.py`` (``local_page``: the
row) and the command ``gigai scout learning pathway ROLE_KEY``. The page itself is ``learning_pathway_local``'s
and the comparison ``learning_markers``' (their own files).

THE RESUME IS ALWAYS THE REFERENCE: there is no build without the comparison. What is asked first is the consent
to a model call.

1. THE ESTIMATE says what the comparison takes and sends, calls no model and writes nothing; with no resume line
   it is zero and says that nothing is sent.
2. THE BUILD compares (one scripted call), writes the page with the checked markers and a record with the master
   revision and what it took, and releases the project's job slot. With no master it makes no call and the page
   marks nothing; with a master of no line every subtopic is new, without a call.
3. A BUILD THAT DOES NOT FINISH (an unusable answer twice, a model failure, a stop, no search index, the slot
   taken) writes nothing of a page, leaves the page that was there byte for byte, says a plain fixed line with a
   code, and never passes on a model's or an adapter's own words. No index is found out before anything is sent.
4. THE STATE: ``running`` while a live build runs (started on a thread), nothing after it finished, ``failed``
   after one that did not, ``interrupted`` for one whose process is gone.
5. THE ROW: ``stale`` with the reason for another GigAI version, another master revision, and a page built with no
   resume when there is one now. A list read reuses the master's revision for a short while.
6. THE COMMAND prints the estimate and the consent line and stops with 'Nothing was sent. Approve with --yes.';
   ``--yes`` builds and says what was sent; with no resume it builds without asking and says nothing was sent.

Everything is SYNTHETIC: the widget course, the board 'widgetco' and the home of ``test_learning_pathway_local.py``
(``local_home``: the user's own model target raises, every socket is refused), made-up resume lines and ids. The
model is a scripted port.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import (
    learning_course, learning_job, learning_pathway_build as build, learning_pathway_local as local, learning_shipped_store as store,
    learning_store, master_store, run_supervisor,
)
from gigai.scout.find_jobs.api import learning as learning_api
from gigai.scout.find_jobs.api.learning import pathways_response, shipped_response, shipped_summary
from gigai.scout.learning_corpus import LearningCorpusError
from gigai.scout.master_store import MasterRevision

from tests.behaviors.scout_learning.test_learning_pathway_local import KEY, ROLE, local_home  # noqa: F401 - local_home is the fixture
from tests.behaviors.scout_learning.test_learning_store import home_target  # noqa: F401 - the fixture local_home is built on

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

PAGE_URL = f"/learning/shipped/{KEY}/pathway.html"
NOT_BUILT = {"state": "not_built", "built_at": None, "reasons": [], "build": None, "url": PAGE_URL}
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ")
#: A made-up master: one experience line, a skills list.
MASTER = (("e-0a1b2c", "Ran the Widgetflow line for three widget plants"), ("s-112233", "Gearbox, Widgetflow, Sprocket"))
FIRST = MasterRevision(1, "rev-0a1b2c3d", None, "operator", "2026-10-01T00:00:00Z", "sha256:" + "ab" * 32)
SECOND = MasterRevision(2, "rev-4e5f6a7b", "rev-0a1b2c3d", "operator", "2026-10-02T00:00:00Z", "sha256:" + "cd" * 32)
FIRST_REF = {"revision_id": "rev-0a1b2c3d", "content_sha256": "sha256:" + "ab" * 32}
#: One KNOWN, one SOME from the skills list, and one KNOWN whose quote is not in the line (code makes it NEW).
ANSWER = {
    "markers": {
        "lesson-alpha/lesson-alpha-first": {"marker": "KNOWN", "evidence": {"id": "e-0a1b2c", "line": "Ran the Widgetflow line"}, "missing": None},
        "lesson-beta/lesson-beta-first": {
            "marker": "SOME", "evidence": {"id": "s-112233", "line": "Gearbox"}, "missing": "The line names Gearbox, not the first part of Beta.",
        },
        "lesson-gamma/lesson-gamma-first": {"marker": "KNOWN", "evidence": {"id": "e-0a1b2c", "line": "Ran gamma widgets at scale"}, "missing": None},
    }
}
ADAPTER_WORDS = "stderr of the adapter: token sk-not-a-real-one"


class Scripted:
    """A scripted model port: each ``ask`` takes the next answer (a dict is returned as JSON, an exception raised)."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str:  # noqa: ARG002
        self.prompts.append(prompt)
        if not self.answers:
            raise AssertionError("the script has no answer left: one call too many")
        found = self.answers.pop(0)
        if isinstance(found, BaseException):
            raise found
        return found if isinstance(found, str) else json.dumps(found)

    def invalid_output(self) -> None:
        return None


def _resume_is(monkeypatch: pytest.MonkeyPatch, lines: tuple[tuple[str, str], ...], *chain: MasterRevision) -> None:
    """The master resume is ``lines`` at the revision chain ``chain`` (none: there is no master)."""

    monkeypatch.setattr(learning_course, "read_master_lines", lambda _home, _target: lines)
    monkeypatch.setattr(master_store, "master_revisions", lambda **_kwargs: list(chain))
    build._MASTER_REFS.clear()  # a list read may reuse the revision this process read up to 30 s ago


def _course() -> store.ShippedCourse:
    course = store.get_course(KEY)
    assert course is not None
    return course


def _row(home: Path, target: Path) -> dict:
    return shipped_summary(home, target, _course())["local_page"]  # type: ignore[return-value]


def _learning(home: Path, target: Path) -> Path:
    return learning_store.learning_dir(home, target)


# --- 1. the estimate -------------------------------------------------------------------------------------------------


def test_the_estimate_says_what_the_comparison_takes_and_sends_and_calls_nothing(local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = local_home  # the user's own model target raises here: asking makes no call
    _resume_is(monkeypatch, MASTER, FIRST)

    asked = build.ask_response(home, target, _course())

    assert asked == {
        "schema_version": "scout-learning-pathway-build:1", "status": "ask", "role_key": KEY, "role_text": ROLE, "resume": {"lines": 2},
        "estimate": asked["estimate"], "sends": build.CONSENT,
    }
    estimate = asked["estimate"]
    assert set(estimate) == {"calls", "tokens_in", "tokens_out", "seconds"} and estimate["calls"] == 1  # type: ignore[arg-type,index]
    assert estimate["tokens_in"] > 10_000 and estimate["tokens_out"] == 8 * 70 and estimate["seconds"] > 0  # type: ignore[index,operator]
    assert "reads your resume" in build.CONSENT and "contact lines removed" in build.CONSENT and "to your model" in build.CONSENT
    assert build.estimate_words(estimate) == f"about 1 model call, about {(estimate['tokens_in'] + estimate['tokens_out']) / 1000:.0f}k tokens, about {estimate['seconds']} seconds"  # type: ignore[arg-type,index,operator]
    raw = json.dumps(asked)
    for word in ("Widgetflow line", "e-0a1b2c", "Gearbox"):
        assert word not in raw, f"{word}: the estimate holds numbers and fixed sentences, nothing of the resume"
    assert _row(home, target) == NOT_BUILT and not (_learning(home, target) / "local").exists(), "asking writes nothing"

    # No master resume, and a master with no line: nothing to send.
    for chain in ((), (FIRST,)):
        _resume_is(monkeypatch, (), *chain)
        nothing = build.ask_response(home, target, _course())
        assert nothing["resume"] == {"lines": 0} and nothing["sends"] == build.NO_RESUME
        assert nothing["estimate"] == {"calls": 0, "tokens_in": 0, "tokens_out": 0, "seconds": 0}


# --- 2. the build ----------------------------------------------------------------------------------------------------


def test_the_build_compares_the_resume_writes_the_page_and_records_what_it_took(local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = local_home
    _resume_is(monkeypatch, MASTER, FIRST)
    port = Scripted(ANSWER)
    said: list[str] = []

    built = build.run(home, target, _course(), model_port=port, on_progress=said.append)

    assert len(port.prompts) == 1, "one model call"
    prompt = port.prompts[0]
    assert "lesson-alpha/lesson-alpha-first" in prompt and "e-0a1b2c | Ran the Widgetflow line for three widget plants" in prompt
    assert "ship widgets" not in prompt.lower() and "widgetco" not in prompt.lower(), "no posting text and no vocabulary phrase is sent"
    assert said == ["Starting", build.COUNTING, build.COMPARING, build.WRITING]
    assert built.page.compared and built.page.tally == {"NEW": 6, "SOME": 1, "KNOWN": 1}, "the quote that is not in the line is NEW"
    assert built.usage == {"calls": 1, "tokens_in": None, "tokens_out": None} and built.resume_lines == 2 and built.master_found
    text = built.page.path.read_text(encoding="utf-8")
    assert built.page.path == store.pathway_page_path(home, target, KEY)
    assert "Production evidence" in text and "Ran the Widgetflow line" in text and "Ran gamma widgets at scale" not in text
    assert local.NOT_COMPARED not in text and f"Your pathway: {ROLE}" in text

    learning = _learning(home, target)
    record = local.read_local_record(learning, KEY)
    assert record is not None and record["master"] == FIRST_REF and record["resume_compared"] is True
    assert record["cost"] == {"calls": 1, "tokens_in": None, "tokens_out": None}
    raw = local.local_record_path(learning, KEY).read_text(encoding="utf-8")
    for word in ("Widgetflow line", "e-0a1b2c", "s-112233", "names Gearbox", "KNOWN"):
        assert word not in raw, f"{word}: the record holds nothing of a resume"
    assert not build.state_path(learning, KEY).exists(), "a build that finished leaves no state"
    assert learning_job.running_id(home, target) is None, "the project's job slot is free again"
    row = _row(home, target)
    assert row["state"] == "built" and STAMP.fullmatch(str(row["built_at"])) and row["reasons"] == [] and row["build"] is None
    assert pathways_response(home, target)["pathways"][0]["local_page"] == row  # type: ignore[index]


def test_with_no_master_no_call_is_made_and_with_a_master_of_no_line_every_subtopic_is_new(
    local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    home, target = local_home  # no port is given below: the user's own target would raise
    assert build.current_master_ref(home, target) is None and learning_course.read_master_lines(home, target) == (), "this home has no master"

    built = build.run(home, target, _course())

    assert built.page.compared is False and built.usage["calls"] == 0 and (built.resume_lines, built.master_found) == (0, False)
    assert local.NOT_COMPARED in built.page.path.read_text(encoding="utf-8")
    record = local.read_local_record(_learning(home, target), KEY)
    assert record is not None and record["master"] is None and record["cost"] == {"calls": 0, "tokens_in": None, "tokens_out": None}
    assert (_row(home, target)["state"], _row(home, target)["reasons"]) == ("built", [])

    # A resume arrives: the page that was built without one is out of date.
    _resume_is(monkeypatch, MASTER, FIRST)
    assert (_row(home, target)["state"], _row(home, target)["reasons"]) == ("stale", ["master"])

    # A master with a revision and no line: compared, everything new, still no call.
    _resume_is(monkeypatch, (), FIRST)
    empty = build.run(home, target, _course())
    assert empty.page.compared and empty.page.tally == {"NEW": 8, "SOME": 0, "KNOWN": 0} and empty.usage["calls"] == 0 and empty.master_found
    record = local.read_local_record(_learning(home, target), KEY)
    assert record is not None and record["master"] == FIRST_REF
    assert (_row(home, target)["state"], _row(home, target)["reasons"]) == ("built", [])


# --- 3. a build that does not finish ---------------------------------------------------------------------------------


def test_a_build_that_does_not_finish_writes_nothing_keeps_the_old_page_and_says_a_plain_line(
    local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    home, target = local_home
    _resume_is(monkeypatch, MASTER, FIRST)
    learning = _learning(home, target)
    course = _course()

    # With no page yet: nothing is written by a failed build.
    with pytest.raises(build.PathwayBuildError) as refused:
        build.run(home, target, course, model_port=Scripted("no json here", {"markers": ["KNOWN"]}))
    assert refused.value.code == "markers_invalid" and "Nothing was written" in str(refused.value)
    assert store.pathway_page(home, target, KEY) is None and local.read_local_record(learning, KEY) is None
    failed = _row(home, target)
    assert (failed["state"], failed["build"]["status"], failed["build"]["error_code"]) == ("not_built", "failed", "markers_invalid")
    assert failed["build"]["error"] == str(refused.value) and STAMP.fullmatch(failed["build"]["finished_at"])

    # A good build, then every way a rebuild can stop: the page and the record stay byte for byte.
    build.run(home, target, course, model_port=Scripted(ANSWER))
    assert _row(home, target)["build"] is None, "a finished build clears the failure"
    page = store.pathway_page_path(home, target, KEY)
    before = (page.read_bytes(), local.local_record_path(learning, KEY).read_bytes())

    def unavailable(text: str = ADAPTER_WORDS, cause: BaseException | None = None) -> LearningCorpusError:
        error = LearningCorpusError("model_unavailable", text)
        error.__cause__ = cause
        return error

    from gigai.adapters.port import ModelAuthenticationRequired

    cases = [
        (Scripted("still not json", "nor this"), "markers_invalid", 2),
        (Scripted(unavailable()), "model_unavailable", 1),
        (Scripted(unavailable(cause=ModelAuthenticationRequired("sign in"))), "model_authentication_required", 1),
    ]
    for port, code, calls in cases:
        with pytest.raises(build.PathwayBuildError) as refused:
            build.run(home, target, course, model_port=port)
        assert refused.value.code == code and len(port.prompts) == calls, code
        assert ADAPTER_WORDS not in str(refused.value) and "sign in" != str(refused.value), "a fixed line, never the adapter's words"
        assert (page.read_bytes(), local.local_record_path(learning, KEY).read_bytes()) == before, f"{code}: the page that was there stays"
        row = _row(home, target)
        assert (row["state"], row["build"]["status"], row["build"]["error_code"]) == ("built", "failed", code)
        assert ADAPTER_WORDS not in json.dumps(row) and learning_job.running_id(home, target) is None

    # Stopped before the call: no prompt is sent.
    real_claim = learning_job._claim

    def claimed_and_stopped(*args: object):
        current = real_claim(*args)
        current.cancel.set()
        return current

    stopped = Scripted(ANSWER)
    with monkeypatch.context() as patched:
        patched.setattr(learning_job, "_claim", claimed_and_stopped)
        with pytest.raises(build.PathwayBuildError) as refused:
            build.run(home, target, course, model_port=stopped)
    assert refused.value.code == "cancelled" and stopped.prompts == []

    # No search index: found out BEFORE anything is sent.
    def no_index(*_args: object, **_kwargs: object) -> None:
        raise LearningCorpusError("search_index_unavailable", "the search index did not answer (synthetic)")

    unsent = Scripted(ANSWER)
    with monkeypatch.context() as patched:
        patched.setattr(local, "count_postings", no_index)
        with pytest.raises(build.PathwayBuildError) as refused:
            build.run(home, target, course, model_port=unsent)
    assert refused.value.code == "search_index_unavailable" and str(refused.value) == "the search index did not answer (synthetic)"
    assert unsent.prompts == [], "the resume is not sent for a page that cannot be built"

    # The project's one job slot is taken (a course is being generated): refused, and that job's state is not touched.
    state_before = build.state_path(learning, KEY).read_bytes()
    holder = real_claim(home, target, None)
    try:
        waiting = Scripted(ANSWER)
        with pytest.raises(build.PathwayBuildError) as refused:
            build.run(home, target, course, model_port=waiting)
        assert refused.value.code == "learning_running" and waiting.prompts == []
        assert build.state_path(learning, KEY).read_bytes() == state_before
    finally:
        learning_job._release(home, target, holder)
    assert (page.read_bytes(), local.local_record_path(learning, KEY).read_bytes()) == before


# --- 4. the state ----------------------------------------------------------------------------------------------------


def test_a_started_build_says_running_until_it_ends_and_a_dead_one_says_interrupted(local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = local_home
    _resume_is(monkeypatch, MASTER, FIRST)
    learning = _learning(home, target)
    gate, asked = threading.Event(), threading.Event()

    class Held(Scripted):
        def ask(self, prompt: str, *, items: int = 1, timeout_seconds: float | None = None) -> str:
            asked.set()
            assert gate.wait(60), "the test never let the answer go"
            return super().ask(prompt, items=items, timeout_seconds=timeout_seconds)

    build.start(home, target, _course(), model_port=Held(ANSWER))
    try:
        first = _row(home, target)["build"]
        assert first["status"] == "running" and STAMP.fullmatch(first["started_at"]), "running from the moment start returns"
        assert asked.wait(60)
        running = _row(home, target)
        assert running["state"] == "not_built" and running["build"]["status"] == "running" and running["build"]["detail"] == build.COMPARING
        assert learning_job.running_id(home, target) == "", "the project's job slot is held"
        with pytest.raises(build.PathwayBuildError) as refused:
            build.start(home, target, _course(), model_port=Scripted(ANSWER))
        assert refused.value.code == "learning_running"
    finally:
        gate.set()
    assert learning_job.wait_for_jobs(timeout=60)
    done = _row(home, target)
    assert done["state"] == "built" and done["build"] is None and not build.state_path(learning, KEY).exists()

    # A state file that says running for a process that is gone: the build was interrupted.
    dead = {"schema": build.STATE_SCHEMA, "role_key": KEY, "status": "running", "pid": os.getpid(), "token": "another-process", "run": "gone", "started_at": "2026-10-09T12:00:00Z", "detail": "Starting"}
    build.state_path(learning, KEY).write_text(json.dumps(dead), encoding="utf-8")
    assert build.build_state(learning, KEY) == {"status": "failed", "error_code": "interrupted", "error": build._MESSAGES["interrupted"], "finished_at": None}
    for broken in ("{not json", json.dumps({**dead, "role_key": "another-course"}), json.dumps({**dead, "schema": "something-else:1"}), json.dumps({**dead, "status": "odd"})):
        build.state_path(learning, KEY).write_text(broken, encoding="utf-8")
        assert build.build_state(learning, KEY) is None
    assert build.build_state(learning, "../escape") is None


# --- 5. the row ------------------------------------------------------------------------------------------------------


def test_the_row_says_stale_for_another_version_and_another_master_revision_and_a_rebuild_makes_it_current(
    local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    home, target = local_home
    _resume_is(monkeypatch, MASTER, FIRST)
    build.run(home, target, _course(), model_port=Scripted(ANSWER))
    built_at = _row(home, target)["built_at"]

    with monkeypatch.context() as patched:
        patched.setattr(store, "gigai_version", lambda: "99.0.0")
        stale = _row(home, target)
        assert (stale["state"], stale["reasons"], stale["built_at"]) == ("stale", ["gigai_version"], built_at)

    _resume_is(monkeypatch, MASTER, FIRST, SECOND)
    assert (_row(home, target)["state"], _row(home, target)["reasons"]) == ("stale", ["master"])
    rebuilt = build.run(home, target, _course(), model_port=Scripted(ANSWER))
    assert rebuilt.page.compared and (_row(home, target)["state"], _row(home, target)["reasons"]) == ("built", [])
    record = local.read_local_record(_learning(home, target), KEY)
    assert record is not None and record["master"]["revision_id"] == "rev-4e5f6a7b"

    # The master is gone: out of date. A chain that cannot be read: nothing is said about it.
    _resume_is(monkeypatch, ())
    assert (_row(home, target)["state"], _row(home, target)["reasons"]) == ("stale", ["master"])

    def unreadable(**_kwargs: object) -> None:
        raise master_store.MasterStoreError("master_unreadable", "synthetic")

    monkeypatch.setattr(master_store, "master_revisions", unreadable)
    build._MASTER_REFS.clear()
    assert build.current_master_ref(home, target) is build.NOT_GIVEN and _row(home, target)["state"] == "built"


def test_a_list_read_reuses_the_masters_revision_for_a_short_while(local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    import subprocess

    home, target = local_home
    asked: list[int] = []
    real_chain = master_store.master_revisions

    # The real chain of this home (no master), with the processes it starts counted: what one such read costs.
    spawned: list[object] = []
    real_popen = subprocess.Popen.__init__

    def spy(self, *args, **kwargs):
        spawned.append(args[0] if args else kwargs.get("args"))
        return real_popen(self, *args, **kwargs)

    with monkeypatch.context() as counting:
        counting.setattr(subprocess.Popen, "__init__", spy)
        assert real_chain(home_root=home, target=target) == []
    assert spawned, "reading the chain is not free (about 17 git processes): that is why a list read reuses the answer"

    _resume_is(monkeypatch, MASTER, FIRST)
    monkeypatch.setattr(master_store, "master_revisions", lambda **_kwargs: asked.append(1) or [FIRST])
    assert pathways_response(home, target)["total"] == 1 and asked == [], "not built: the master is not looked at"
    build.run(home, target, _course(), model_port=Scripted(ANSWER))
    assert asked == [1], "the build reads the chain now"
    for _read in range(3):
        assert pathways_response(home, target)["pathways"][0]["local_page"]["state"] == "built"  # type: ignore[index]
    assert asked == [1], "three list reads right after it reuse that answer: no process is started for them"
    monkeypatch.setattr(build, "MASTER_REF_SECONDS", 0.0)
    pathways_response(home, target)
    assert asked == [1, 1], "an answer older than the limit is read again"


def test_the_routes_module_reads_no_resume_and_the_build_reads_it_in_one_place() -> None:
    routes = Path(learning_api.__file__).read_text(encoding="utf-8")
    for name in ("load_master(", "read_master_lines", "read_record(", "resolve_model_adapter", "MeteredRoleModel", "MeteredCourseModel"):
        assert name not in routes, f"{name}: the routes read no resume text and reach no model"
    source = Path(build.__file__).read_text(encoding="utf-8")
    assert source.count("learning_course.read_master_lines(") == 1, "the build reads the master's lines in one place, for the comparison"
    for name in ("load_master(", "read_record(", "resolve_model_adapter"):
        assert name not in source, name


# --- 6. the command --------------------------------------------------------------------------------------------------


def _cli(home: Path, target: Path, *args: str):
    return CliRunner().invoke(cli, ["scout", "learning", *args, "--home", str(home), "--target", str(target)], catch_exceptions=False)


def _json(result) -> dict:
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, result.output
    return json.loads(lines[0])


def test_the_command_asks_first_and_builds_only_with_yes(local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = local_home
    _resume_is(monkeypatch, MASTER, FIRST)
    stopped = run_supervisor.ScoutStatus(state="stopped", project_id="project_x", url=None, pid=None)
    monkeypatch.setattr(run_supervisor, "status", lambda **_kwargs: stopped)
    port = Scripted(ANSWER, ANSWER, ANSWER)
    monkeypatch.setattr(build, "_model_port", lambda: port)

    # Without --yes (no terminal): the estimate and the consent line, and nothing is sent or written.
    asked = _cli(home, target, "pathway", KEY)
    assert asked.exit_code == 0, asked.output
    lines = asked.output.splitlines()
    assert lines[0] == f"Build your pathway page of '{ROLE}'?"
    assert re.fullmatch(r"Estimate: about 1 model call, about \d+k tokens, about \d+ seconds\.", lines[1]), lines[1]
    assert lines[2] == build.CONSENT and lines[3] == "Nothing was sent. Approve with --yes." and len(lines) == 4
    assert _json(_cli(home, target, "pathway", KEY, "--json")) == build.ask_response(home, target, _course())
    assert port.prompts == [] and _row(home, target) == NOT_BUILT and not (_learning(home, target) / "local").exists()

    # With --yes: the build, in the foreground.
    built = _cli(home, target, "pathway", KEY, "--yes")
    assert built.exit_code == 0, built.output
    lines = built.output.splitlines()
    assert lines[:4] == ["Starting", build.COUNTING, build.COMPARING, build.WRITING]
    assert lines[4] == f"Built your pathway page of {ROLE} ({KEY})."
    assert re.fullmatch(
        r"Counted: \d+ of your stored postings of the last 90 days \(3 of any date\); a posting names 7 of the 10 things the course lists and 4 of its 4 lessons\.",
        lines[5],
    ), lines[5]
    assert lines[6].startswith("Fewer than 25 postings of the last 90 days have stored text"), "the corpus's own plain note"
    assert lines[7] == "Your resume: 1 known, 1 in part, 6 new (subtopics of the course)."
    assert lines[8] == "Sent to your model: 1 call."
    assert lines[9] == f"Open it: the Scout server serves it at {PAGE_URL} (start the server with `gigai scout run`)." and len(lines) == 10
    for word in ("widgetco", "You will", "Widgetflow line", "e-0a1b2c"):
        assert word.lower() not in built.output.lower(), f"{word}: the command prints numbers, no posting text and no resume text"
    assert len(port.prompts) == 1 and store.pathway_page(home, target, KEY) is not None
    day = str(_row(home, target)["built_at"])[:10]
    assert f"your pathway page was built on {day}" in _cli(home, target, "list").output.splitlines()[0]
    assert _cli(home, target, "list").output.splitlines()[1] == "More pathways on scout.edgelab.one: https://scout.edgelab.one/learn/"
    shown = _cli(home, target, "show", KEY).output
    assert f"Your pathway page: your pathway page was built on {day}" in shown and f"gigai scout learning pathway {KEY}" in shown

    # With the server running the address is whole; --json --yes is the course's row.
    running = run_supervisor.ScoutStatus(state=run_supervisor.STATE_RUNNING, project_id="project_x", url="http://127.0.0.1:8765/", pid=4242)
    monkeypatch.setattr(run_supervisor, "status", lambda **_kwargs: running)
    assert f"Open it: http://127.0.0.1:8765{PAGE_URL}" in _cli(home, target, "pathway", KEY, "--yes").output.splitlines()
    answered = _json(_cli(home, target, "pathway", KEY, "--yes", "--json"))
    assert answered == shipped_response(home, target, _course())
    assert answered["schema_version"] == "scout-learning-pathway-response:1" and answered["pathway"]["local_page"]["state"] == "built"
    assert len(port.prompts) == 3, "every build compares the resume again"

    # A page that is out of date says why, in the list and in show.
    monkeypatch.setattr(store, "gigai_version", lambda: "99.0.0")
    assert f"your pathway page was built on {day} and is out of date: GigAI was updated since" in _cli(home, target, "list").output.splitlines()[0]
    assert "is out of date: GigAI was updated since" in _cli(home, target, "show", KEY).output


def test_with_no_resume_the_command_builds_without_asking_and_says_nothing_was_sent(local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = local_home  # no master in this home; the user's own model target raises
    stopped = run_supervisor.ScoutStatus(state="stopped", project_id="project_x", url=None, pid=None)
    monkeypatch.setattr(run_supervisor, "status", lambda **_kwargs: stopped)

    built = _cli(home, target, "pathway", KEY)

    assert built.exit_code == 0, built.output
    lines = built.output.splitlines()
    assert f"Built your pathway page of {ROLE} ({KEY})." in lines and build.NO_RESUME in lines and "Nothing was sent to a model." in lines
    assert "Approve with --yes" not in built.output
    page = store.pathway_page(home, target, KEY)
    assert page is not None and local.NOT_COMPARED in page.read_text(encoding="utf-8")


def test_the_command_refuses_an_unknown_role_key_and_says_a_failed_build_plainly(local_home: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    home, target = local_home
    _resume_is(monkeypatch, MASTER, FIRST)
    for name in ("no-such-role", "lp-0a1b2c3d", "../escape"):
        refused = _cli(home, target, "pathway", name, "--yes", "--json")
        assert refused.exit_code == 1 and _json(refused)["error"]["code"] == "pathway_not_found", refused.output
    plain = _cli(home, target, "pathway", "no-such-role")
    assert plain.exit_code == 1 and "gigai scout learning list" in plain.output

    monkeypatch.setattr(build, "_model_port", lambda: Scripted("not json", "nor this"))
    failed = _cli(home, target, "pathway", KEY, "--yes", "--json")
    assert failed.exit_code == 1 and _json(failed)["error"] == {"code": "markers_invalid", "message": build._MESSAGES["markers_invalid"]}
    assert store.pathway_page(home, target, KEY) is None
    listed = _cli(home, target, "list").output.splitlines()[0]
    assert "your pathway page is not built yet; the last build did not finish (markers_invalid)" in listed
