"""0.1.11.10 Part B, packet G5: ``gigai scout learning generate | cancel | resume`` against a temporary home.

The whole job runs in the command's own process: G1's corpus step on synthetic boards in the board cache, G2's
course steps, a fake paths step, G3's renderer, the store. The model is a scripted fake on the one adapter seam
(so every call is METERED as it is for a user); the web is a table of pages behind the real outbound policy.
No network, no real model.

1. ESTIMATE FIRST: without ``--yes`` the command says what a course takes and what it sends, and generates
   nothing; a terminal is asked (default no) and a "no" stores nothing.
2. ``--yes`` runs the job in the foreground with plain progress lines, stores the course and says how to open it;
   the cost holds the calls, the fetches and the tokens the meter recorded; the next estimate is from history.
3. A job that fails exits 1, keeps what was written and names ``resume``; ``resume ID`` goes on with it.
4. ``cancel ID`` stops a running job from another command; with no job it says so; one course at a time.
5. ``list`` and ``show`` say how far a generation is.
"""

from __future__ import annotations

from collections.abc import Callable
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import click
from click.testing import CliRunner
import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.cli import cli
from gigai.scout import call_metrics, learning_fetch, learning_job, learning_store
from gigai.scout.find_jobs import search_index

from tests.behaviors.scout_learning.test_learning_corpus import ACME, BOLT, TITLES_OK, concept, lever_job, seed_board, vocabulary
from tests.behaviors.scout_learning.test_learning_course import (  # noqa: F401 - names is the autouse fixture: no resolver is asked
    curriculum_answer, module_answer, names, web_for,
)
from tests.behaviors.scout_learning.test_learning_fetch import Clock, FakeWeb
from tests.support.answers_stories_fixtures import config
from tests.support.scout_profile_fixtures import build_gig_with_resume

pytestmark = pytest.mark.skipif(sqlite3.sqlite_version_info < (3, 9, 0), reason="needs FTS5")

ROLE = "MLOps engineer"
RESUME_MARKER = "Quillfeather inference lead"
COVE = [
    lever_job("cove", n, "ML Platform Engineer", 10 + n, f"Experience with Terraform is required. Posting {n}." if n <= 5 else f"You will own service {n}.")
    for n in range(1, 13)
]


def corpus_answers() -> list[object]:
    """What G1 asks of the model for the three synthetic boards: the titles, the vocabulary, the follow-up."""

    return [TITLES_OK, vocabulary(concept("terraform"), concept("kubernetes", "kubernetes", "k8s"), concept("mlflow"), pad_to=60), '{"concepts": []}']


def course_answer() -> dict[str, object]:
    """A valid curriculum for the two concepts these boards keep (terraform, kubernetes): 7 modules of 4 lessons."""

    answer = curriculum_answer(
        expectations={"responsibilities": [], "scope": []}, technologies={"Infrastructure": ["terraform", "kubernetes"]},
    )
    for module in answer["modules"]:  # type: ignore[union-attr]
        for number, lesson in enumerate(module["lessons"]):
            tool = "terraform" if number % 2 else "kubernetes"
            lesson["concepts"] = [tool]
            for source in lesson["sources"]:  # G7e: a lesson's main tool needs two verified pages of its own, or step 7 asks the model again
                source["title"] = f"{tool.title()} {source['title'].lower()}"
    return answer


def lessons_answer() -> Callable[[str], str]:
    """A module answer as G2's tests script it. Its lessons name Kubernetes (on this course's first page) and Airflow (not on it)."""

    return module_answer()


def seed_boards(home: Path) -> None:
    seed_board(home, "acme", ACME)
    seed_board(home, "bolt", BOLT)
    seed_board(home, "cove", COVE)
    assert search_index.rebuild_from_index(home).available


class SeamModel:
    """A scripted binding on ``proposal_execution.resolve_model_adapter``: the answers in order (a callable gets the prompt)."""

    def __init__(self) -> None:
        self.port = self
        self.answers: list[object] = []
        self.prompts: list[str] = []
        self.timeouts: list[float | None] = []

    def invoke(self, request: object) -> InvocationResult:
        prompt = request.prompt  # type: ignore[attr-defined]
        self.prompts.append(prompt)
        self.timeouts.append(getattr(request, "timeout_seconds", None))
        assert self.answers, "the model was called more often than the script allows"
        answer = self.answers.pop(0)
        text = answer(prompt) if callable(answer) else answer if isinstance(answer, str) else json.dumps(answer)
        return InvocationResult(
            status="success", output_text=text, resolved_model="fixture-model", raw_usage={},
            normalized_usage=NormalizedUsage(1000, 200, 1200), cost_status="unavailable",
        )

    def request(
        self, *, role: str, prompt: str, required_capabilities: object = frozenset({"text"}), timeout_seconds: float | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(prompt=prompt, role=role, timeout_seconds=timeout_seconds)

    def close(self) -> None:
        return None


@pytest.fixture
def fx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """A gig with a resume, three synthetic boards indexed, the fake model on the seam, the fake web, a fake paths step."""

    gig = build_gig_with_resume(tmp_path, resume_text=f"# Fixture Resume\n\n{RESUME_MARKER}. (fixture only.)\n".encode())
    home, target = gig.home_root, gig.target
    seed_boards(home)
    model = SeamModel()

    def resolve(_config: object, _adapter_target: str, **_kwargs: object) -> SeamModel:
        return model

    setattr(resolve, "_scout_test_transport", True)
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", resolve)
    monkeypatch.setattr("gigai.config.load_config", config)

    web = web_for(course_answer())
    web.close = lambda: None  # type: ignore[attr-defined]
    clock = Clock()
    real = learning_fetch.PageFetcher
    monkeypatch.setattr(learning_fetch, "HttpxFetcher", lambda: web)
    monkeypatch.setattr(learning_fetch, "PageFetcher", lambda network, **options: real(network, clock=clock, sleep=clock.sleep, **options))
    monkeypatch.setattr(learning_job, "paths_step", lambda: lambda _work, **_kwargs: {"paths": {}, "dropped": []})
    yield SimpleNamespace(home=home, target=target, model=model, web=web)
    search_index.close(home)


def _run(fx: SimpleNamespace, *args: str):
    return CliRunner().invoke(cli, ["scout", "learning", *args, "--home", str(fx.home), "--target", str(fx.target)], catch_exceptions=False)


def _json(result) -> dict[str, object]:
    lines = [line for line in result.output.splitlines() if line.strip()]
    assert len(lines) == 1, result.output
    return json.loads(lines[0])


def _whole_script() -> list[object]:
    return [*corpus_answers(), course_answer(), *[lessons_answer()] * 7]


def test_the_estimate_comes_first_and_nothing_is_generated_without_approval(fx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _run(fx, "generate", ROLE, "--json")
    assert asked.exit_code == 0, asked.output
    assert _json(asked) == {
        "schema_version": "scout-learning-generate:1", "status": "ask", "role_text": ROLE,
        "estimate": {"calls": 60, "fetches": 1000, "minutes": 45, "tokens": None, "basis": "none"},
        "caps": {"calls": 120, "fetches": 1500}, "warning": "learning_higher_usage",
    }

    plain = _run(fx, "generate", ROLE)  # no terminal: the facts are printed, nothing is asked, nothing is generated
    assert plain.exit_code == 0, plain.output
    for part in (
        "Generate a course for 'MLOps engineer'? This is a higher-usage action than an assessment.",
        "Estimate: about 60 model calls, about 1000 page fetches from public documentation sites, about 45 minutes (no recorded course yet to estimate tokens or time from).",
        "The course uses your resume to mark what you already know (contact lines removed; sent to the lesson-writing calls only).",
        "Stops by itself after 120 model calls or 1500 page fetches.",
    ):
        assert part in plain.output, (part, plain.output)
    assert plain.output.rstrip().endswith("Nothing was generated. Approve with --yes.")

    # a terminal is asked, default no
    questions: list[tuple[str, bool]] = []

    def confirm(text: str, default: bool = False, **_kwargs: object) -> bool:
        questions.append((text, default))
        return False

    monkeypatch.setattr(click, "confirm", confirm)
    monkeypatch.setattr("click.testing._NamedTextIOWrapper.isatty", lambda _self: True, raising=False)
    refused = _run(fx, "generate", ROLE)
    assert refused.exit_code == 0 and questions == [("Generate a course for 'MLOps engineer'? This is a higher-usage action than an assessment", False)]
    assert "Estimate: about 60 model calls" in refused.output and refused.output.rstrip().endswith("Nothing was generated. Approve with --yes.")

    assert fx.model.prompts == [] and fx.web.asked == [] and learning_store.list_pathways(fx.home, fx.target) == []
    assert not (learning_store.learning_dir(fx.home, fx.target) / "work").exists()

    bad = _run(fx, "generate", "write to someone@example.com", "--yes", "--json")
    assert bad.exit_code == 1 and _json(bad)["error"]["code"] == "personal_info_refused"  # type: ignore[index]
    assert learning_store.list_pathways(fx.home, fx.target) == []


def test_yes_generates_in_the_foreground_stores_the_course_and_records_what_it_took(fx: SimpleNamespace) -> None:
    fx.model.answers = _whole_script()
    done = _run(fx, "generate", ROLE, "--yes")
    assert done.exit_code == 0, done.output
    (record,) = learning_store.list_pathways(fx.home, fx.target)
    for part in (
        "Asking for the title phrases of this role...", "Reading 17 postings", "Planning the modules and lessons...", "Checking sources for lesson 28 of 28",
        "Writing module 7 of 7", "Drew 29 pages", "Checking links for 28 lessons (0 dropped so far)",
        f"Done: {record.id}  MLOps engineer (28 lessons", f"Open it: gigai scout learning open {record.id}",
    ):
        assert part in done.output, (part, done.output)
    assert "Experience with Terraform is required" not in done.output and RESUME_MARKER not in done.output

    assert (record.status, record.source, record.imported_from) == ("done", "requested", None)
    # 3 corpus calls, the curriculum, 7 modules: every one metered (1200 tokens each), every request counted
    assert record.cost == {
        "cli_model_calls": 11, "worker_minutes": record.cost["worker_minutes"], "web_fetches": len(fx.web.asked), "web_searches": 0,
        "tokens": 13200, "note": "about 13k tokens",
    }
    (row,) = call_metrics.metrics_report(fx.home, fx.target, kind="learning")["comparison"]  # type: ignore[misc]
    assert (row["calls"], row["errors"], row["items"]) == (11, 0, 4 + 28), "a lesson-writing call records its lessons as items"
    # G7c end to end: titles keeps the adapter's own default, vocabulary (plus its follow-up) asks for 300 s,
    # and the curriculum plus every module's lesson text ask for 600 s -- through the real binding, not a fake one.
    assert fx.model.timeouts == [None, 300.0, 300.0, 600.0, *([600.0] * 7)]
    lesson = learning_store.resolve_course_file(fx.home, fx.target, record.id, "concept-lesson-1-1.html")
    assert lesson is not None
    # a technology the first page does not list is plain text: it no longer fails the course at the link audit
    page = lesson.read_text(encoding="utf-8")
    assert '<a href="index.html#tech-kubernetes">Kubernetes</a>' in page and "<span>Airflow</span>" in page

    # the estimate is now this one done course's own recorded totals (one course: the "average" is itself)
    again = _json(_run(fx, "generate", ROLE, "--json"))
    assert again["estimate"] == {"calls": 11, "fetches": len(fx.web.asked), "minutes": again["estimate"]["minutes"], "tokens": 13200, "basis": "history"}  # type: ignore[index]
    assert "sized from your last course(s)" in learning_job.estimate_words(again["estimate"])  # type: ignore[arg-type]

    listed = _run(fx, "list")
    assert f"{record.id}  done" in listed.output and "[requested]" in listed.output
    shown = _run(fx, "show", record.id)
    assert "Status: done" in shown.output and "Generation: 6 of 6 steps, 11 model calls" in shown.output and "about 13k tokens" in shown.output
    assert _json(_run(fx, "show", record.id, "--json"))["pathway"]["progress"]["calls"] == 11  # type: ignore[index]


def test_a_job_that_fails_exits_1_keeps_the_work_and_resume_goes_on_with_it(fx: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.adapters.port import ModelInvocationError

    def down(_prompt: str) -> str:
        raise ModelInvocationError("CLI model process failed with exit code 1: usage limit reached")

    fx.model.answers = [*corpus_answers(), course_answer(), *[lessons_answer()] * 3, *[down] * 4]
    sleeps: list[float] = []
    real = learning_job.LearningJob

    def no_wait(*args: object, **options: object) -> learning_job.LearningJob:
        return real(*args, sleep=sleeps.append, **options)  # type: ignore[arg-type]

    monkeypatch.setattr(learning_job, "LearningJob", no_wait)  # the retry waits are not slept through here
    failed = _run(fx, "generate", ROLE, "--yes")
    (record,) = learning_store.list_pathways(fx.home, fx.target)
    assert failed.exit_code == 1, failed.output
    assert "Not finished: Your model login refused or timed out; resume later. (model_unavailable)" in failed.output
    assert f"What was written is kept. Go on with it: gigai scout learning resume {record.id}" in failed.output
    assert sum(sleeps) == 420 and (record.status, record.error_code) == ("failed", "model_unavailable")
    shown = _run(fx, "show", record.id)
    assert "Status: failed" in shown.output and "(model_unavailable)" in shown.output and f"gigai scout learning resume {record.id}" in shown.output

    fx.model.answers = [lessons_answer()] * 4
    fetched = len(fx.web.asked)
    resumed = _run(fx, "resume", record.id, "--json")
    assert resumed.exit_code == 0, resumed.output
    body = _json(resumed)["pathway"]
    assert body["status"] == "done" and body["course"]["lessons"] == 28 and body["url"] == f"/learning/{record.id}/index.html"  # type: ignore[index]
    assert {step["id"]: step["status"] for step in body["progress"]["steps"]}["corpus"] == "skipped"  # type: ignore[index]
    assert len(fx.web.asked) == fetched and body["cost"]["cli_model_calls"] == 11 + 4  # type: ignore[index]  # 7 that answered and 4 attempts that failed, then 4

    again = _run(fx, "resume", record.id, "--json")
    assert again.exit_code == 1 and _json(again)["error"]["code"] == "not_resumable"  # type: ignore[index]
    unknown = _run(fx, "resume", "lp-00000000")
    assert unknown.exit_code == 1 and "there is no learning pathway" in unknown.output


def test_cancel_stops_a_running_job_from_another_command_and_one_course_runs_at_a_time(fx: SimpleNamespace) -> None:
    seen: dict[str, object] = {}

    def while_running(then: Callable[[str], str]) -> Callable[[str], str]:
        def answer(prompt: str) -> str:
            (record,) = learning_store.list_pathways(fx.home, fx.target)
            seen["list"] = _run(fx, "list").output
            seen["second"] = _json(_run(fx, "generate", "Another role", "--yes", "--json"))
            seen["cancel"] = _run(fx, "cancel", record.id).output
            seen["cancel_json"] = _json(_run(fx, "cancel", record.id, "--json"))
            return then(prompt)

        return answer

    fx.model.answers = [*corpus_answers(), course_answer(), lessons_answer(), while_running(lessons_answer()), *[lessons_answer()] * 5]
    stopped = _run(fx, "generate", ROLE, "--yes")
    (record,) = learning_store.list_pathways(fx.home, fx.target)
    assert stopped.exit_code == 1, stopped.output
    assert "(cancelled)" in stopped.output and f"gigai scout learning resume {record.id}" in stopped.output
    assert len(fx.model.answers) == 5, "no call after the stop"
    assert f"{record.id}  running" in str(seen["list"])
    assert seen["second"] == {"status": "error", "error": {"code": "learning_running", "message": seen["second"]["error"]["message"]}}  # type: ignore[index]
    assert f"Stopping the generation of {record.id}: no further model call or page fetch starts" in str(seen["cancel"])
    assert seen["cancel_json"]["cancel_requested"] is True and seen["cancel_json"]["pathway"]["status"] == "running"  # type: ignore[index]

    idle = _run(fx, "cancel", record.id)
    assert idle.exit_code == 0 and f"No job is generating {record.id} now: nothing to stop." in idle.output
    assert _json(_run(fx, "cancel", record.id, "--json"))["cancel_requested"] is False
    unknown = _run(fx, "cancel", "lp-00000000", "--json")
    assert unknown.exit_code == 1 and _json(unknown)["error"]["code"] == "pathway_not_found"  # type: ignore[index]
