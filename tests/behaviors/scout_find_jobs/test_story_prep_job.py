"""0.1.11.7 T1: ``gigai scout story prep --job-url URL``: one job's rehearsal list from its STORED assessment.

The END outcome, on a SYNTHETIC fixture shaped like the SimSpace example (two open questions nothing answers, one met row
with a master line, one row told by a saved story, one row that rests only on a personal lab line):

- the list is exactly that: both open questions ``uncovered``, a walkthrough with its line id, the story id, the lab mark;
- ZERO model calls (the model runner is patched to raise) and the home is byte-identical before and after;
- a row shows at most 60 characters of the posting, however long the requirement and the question are;
- a job assessed for two profiles with no ``--profile`` is refused with a sentence naming ``--profile``;
- ``--json`` holds exactly the documented keys; a job with no stored assessment is one sentence and a non-zero exit.
"""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gigai.scout import stories, story_prep_job
from gigai.scout.scout_cli import scout_group

from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import (  # noqa: F401 - `fx` is the fixture
    _URL, PYTHON_LINE, _assess, _ids, _master, _patch_v9_template, fx,
)
from tests.support.posting_fixtures import PostingsFixture

LONG_NET = "Hands-on networking fundamentals: diagnose DNS, routing and firewall faults on live Zephyrgate ranges"
LONG_LINUX = "Strong Linux administration, including systemd, kernel parameters and package management at Quokkaflux scale"
NET_Q = "Describe a time you diagnosed a DNS or routing fault on a network you did not build, step by step"
LINUX_Q = "How have you administered Linux hosts: services, kernel settings, packages, and recovering a host that would not boot"
STORY_REQ = "Ship production-grade distributed services"
STORY_TITLE = "Moved the services to Kubernetes"
LAB_REQ = "Familiarity with cyber ranges"
LAB_LINE = "Built a private DNS hierarchy in Docker and traced delegation with dig"
TOP = {"ok", "schema_version", "labels", "_labels", "job_identity", "profile_id", "rows", "summary"}
ROW = {"id", "class", "status", "lab", "posting_words", "questions"}
QUESTION = {"kind", "question_id", "line_id", "line_text", "lab", "story_id", "answered_by", "uncovered"}
ANSWERED_BY = {"stories", "answer_id", "near_answer", "master_lines"}


def _cli(fx: PostingsFixture, *args: str, job: bool = True):
    return CliRunner().invoke(scout_group, ["story", "prep", *(["--job-url", _URL] if job else []), "--home", str(fx.home_root), "--target", str(fx.target), *args])


def _prep(fx: PostingsFixture, *args: str) -> dict:
    result = _cli(fx, "--json", *args)
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _snapshot(fx: PostingsFixture) -> dict[str, str]:
    found: dict[str, str] = {}
    for path in sorted(fx.home_root.rglob("*")):
        if path.is_file() and ".git" not in path.parts and "__pycache__" not in path.parts and "cache" not in path.parts:
            found[str(path.relative_to(fx.home_root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def _lab_line(fx: PostingsFixture) -> str:
    """Add a personal lab line to the master (the T2 command); returns its id."""

    master = _master(fx)
    entry = next(iter(master.master.entries))
    result = CliRunner().invoke(scout_group, [
        "resume", "master", "add", "--entry", entry, "--text", LAB_LINE, "--lab", "--when", "Oct 2026",
        "--revision", str(master.revision.revision), "--home", str(fx.home_root), "--target", str(fx.target), "--json",
    ])
    assert result.exit_code == 0, result.output
    return next(item.id for item in _master(fx).master.items.values() if item.lab)


@pytest.fixture
def simspace(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> PostingsFixture:
    """A job assessed with five rows: two open questions, a met row on a master line, a met row told by a story, a lab-only row."""

    _patch_v9_template(monkeypatch)
    ids = _ids(fx)
    lab_id = _lab_line(fx)
    story = stories.save_story(home_root=fx.home_root, target=fx.target, fields={"title": STORY_TITLE, "raw": "We moved them."}, actor="agent")
    rows = [
        {"requirement": LONG_NET, "class": "askable", "status": "unclear", "resume_evidence": []},
        {"requirement": LONG_LINUX, "class": "askable", "status": "unclear", "resume_evidence": []},
        {"requirement": "Production Python", "class": "hard", "status": "met", "resume_evidence": ["Built Python services"], "sources": [ids[PYTHON_LINE]]},
        {"requirement": STORY_REQ, "class": "hard", "status": "met", "resume_evidence": ["a story"], "sources": [f"A {story.story_id}"]},
        {"requirement": LAB_REQ, "class": "list_item", "status": "met", "resume_evidence": ["a lab"], "sources": [lab_id]},
    ]
    questions = [
        {"question_id": "skill:networking", "question": NET_Q, "requirement": LONG_NET},
        {"question_id": "tooling:linux", "question": LINUX_Q, "requirement": LONG_LINUX},
    ]
    for row in rows:
        row["class_basis"] = f"Requirements: {row['requirement']}"
    answer = json.dumps({"verdict": "pending_user_answers", "matrix": rows, "questions": questions, "suggestions": [], "not_a_match_reason": None})
    _assess(fx, answer)
    return fx


def test_the_list_is_exactly_the_two_uncovered_questions_the_walkthrough_the_story_and_the_lab_row(simspace: PostingsFixture) -> None:
    body = _prep(simspace)
    assert set(body) == TOP and body["schema_version"] == story_prep_job.SCHEMA_VERSION
    by_words = {row["posting_words"]: row for row in body["rows"]}
    assert len(by_words) == 5 and all(set(row) == ROW for row in body["rows"])
    for row in body["rows"]:
        assert all(set(question) == QUESTION and set(question["answered_by"]) == ANSWERED_BY for question in row["questions"])

    for key, question_id in ((NET_Q, "skill:networking"), (LINUX_Q, "tooling:linux")):
        row = by_words[story_prep_job.clip(key)]
        (question,) = row["questions"]
        assert (row["status"], question["kind"], question["question_id"], question["uncovered"]) == ("unclear", "open_question", question_id, True)
        assert question["answered_by"] == {"stories": [], "answer_id": None, "near_answer": None, "master_lines": []}

    ids = _ids(simspace)
    walked = by_words["Production Python"]
    (walk,) = walked["questions"]
    assert (walk["kind"], walk["line_id"], walk["line_text"], walk["uncovered"], walk["lab"]) == ("walkthrough", ids[PYTHON_LINE], PYTHON_LINE, False, False)
    assert walk["answered_by"]["master_lines"] == [ids[PYTHON_LINE]] and walked["lab"] is False

    (told,) = by_words[STORY_REQ]["questions"]
    story = next(item for item in stories.list_stories(home_root=simspace.home_root, target=simspace.target) if item.title == STORY_TITLE)
    assert (told["kind"], told["story_id"], told["uncovered"]) == ("story", story.story_id, False)
    assert told["answered_by"]["stories"] == [story.story_id]

    labbed = by_words[LAB_REQ]
    (lab,) = labbed["questions"]
    assert labbed["lab"] is True and lab["lab"] is True and lab["line_text"] == f"{LAB_LINE} (personal lab, Oct 2026)"
    assert [row["lab"] for row in body["rows"]].count(True) == 1
    assert body["summary"] == {"rows": 5, "questions": 5, "uncovered": 2}


def test_the_text_form_lists_the_same_and_marks_uncovered_and_lab(simspace: PostingsFixture) -> None:
    result = _cli(simspace)
    assert result.exit_code == 0, result.output
    assert result.output.count("uncovered") == 3  # two questions + the summary line
    assert "Open question skill:networking" in result.output and "Tell me about:" in result.output
    assert "[lab: personal lab only, not production]" in result.output and "[lab]" in result.output
    assert "Tell the story story:" in result.output


def test_it_calls_no_model_and_writes_nothing(simspace: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> None:
    calls = len(simspace.base.model.assess_prompts)

    def never(prompt: str):
        raise AssertionError("story prep --job-url called the model")

    monkeypatch.setattr(simspace.base.model, "answer", never)  # the one runner every model call of the fixture goes through
    monkeypatch.setattr("gigai.scout.proposal_execution.resolve_model_adapter", never)
    _prep(simspace)  # one read first: scratch caches settle
    before = _snapshot(simspace)
    first, again, plain = _prep(simspace), _prep(simspace), _cli(simspace)
    assert first == again and plain.exit_code == 0
    assert _snapshot(simspace) == before, "story prep --job-url wrote something"
    assert len(simspace.base.model.assess_prompts) == calls and not simspace.base.model.tailor_prompts


def test_a_row_shows_at_most_sixty_characters_of_the_posting(simspace: PostingsFixture) -> None:
    body = _prep(simspace)
    for row in body["rows"]:
        assert len(row["posting_words"]) <= 60
    assert NET_Q[:59].rstrip() + "…" in {row["posting_words"] for row in body["rows"]}
    text = _cli(simspace).output
    for long in (LONG_NET, LONG_LINUX, NET_Q, LINUX_Q):
        assert long[:61] not in text, "more than 60 characters of the posting were printed"
    assert body["_labels"]["/rows/*/posting_words"] == "public-untrusted"
    assert body["_labels"]["/rows/*/questions/*/line_text"] == "user-private"


def test_clip_is_one_line_of_at_most_sixty_characters() -> None:
    assert story_prep_job.clip("a\n\tb\x00c " * 40) .count("\n") == 0
    assert len(story_prep_job.clip("x" * 500)) == 60 and story_prep_job.clip("short  text") == "short text"


def test_a_job_assessed_for_two_profiles_needs_profile(simspace: PostingsFixture) -> None:
    from tests.behaviors.scout_find_jobs.test_assessment_v9_flow import _assessment_path

    path = _assessment_path(simspace)
    stored = json.loads(path.read_text(encoding="utf-8"))
    other = "profile_00000000-0000-4000-8000-0000000000bb"
    copy = path.parents[1] / other
    copy.mkdir()
    text = path.read_text(encoding="utf-8").replace(simspace.default_profile_id, other)
    assert text != json.dumps(stored)
    (copy / path.name).write_text(text, encoding="utf-8")

    refused = _cli(simspace)
    assert refused.exit_code != 0 and "--profile" in refused.output and "profile_" in refused.output
    refused_json = _cli(simspace, "--json")
    assert refused_json.exit_code != 0 and json.loads(refused_json.stdout)["error"]["code"] == "profile_ambiguous"
    named = _cli(simspace, "--profile", simspace.default_profile_id, "--json")
    assert named.exit_code == 0, named.output
    assert json.loads(named.stdout)["profile_id"] == simspace.default_profile_id


def test_a_job_with_no_stored_assessment_is_one_sentence_and_a_failure(fx: PostingsFixture) -> None:
    result = _cli(fx)
    assert result.exit_code != 0 and "assess it first" in result.output and "gigai scout jobs assess" in result.output
    as_json = _cli(fx, "--json")
    assert as_json.exit_code != 0 and json.loads(as_json.stdout)["error"]["code"] == "assessment_missing"


def test_without_job_url_the_pooled_list_is_unchanged_and_profile_alone_is_refused(fx: PostingsFixture) -> None:
    stories.save_story(home_root=fx.home_root, target=fx.target, fields={"title": "A story", "answers_questions": ["Tell me about a hard bug"]})
    body = json.loads(_cli(fx, "--json", job=False).stdout)
    assert set(body) == {"ok", "schema_version", "questions"} and body["questions"][0]["question"] == "Tell me about a hard bug"
    refused = _cli(fx, "--profile", "x", job=False)
    assert refused.exit_code != 0 and "--job-url" in refused.output


# --- the pure builder ---------------------------------------------------------------------------------------------------


def test_the_builder_covers_a_question_by_a_story_a_saved_answer_or_a_near_match() -> None:
    story = stories.Story(story_id="story:dns_range", title="Unbound dropped my range answers", raw="x", answers_questions=("Tell me about a DNS fault you diagnosed",))
    entry = SimpleNamespace(question_id="skill:linux", question="Linux administration", answer="x")
    banks = story_prep_job.Banks((story,), (entry,), {})
    rows = (
        story_prep_job.PrepRow("req-aaaaaa", "askable", "unclear", "DNS", (), "skill:dns", "Tell me about a DNS fault you diagnosed"),
        story_prep_job.PrepRow("req-bbbbbb", "askable", "unclear", "Linux", (), "skill:linux", "Linux administration"),
        story_prep_job.PrepRow("req-cccccc", "askable", "unclear", "Mainframes", (), "skill:cobol", "Mainframe operations"),
    )
    body = story_prep_job.build("https://x.test/j", "profile_1", rows, banks)
    covered = [row["questions"][0]["answered_by"] for row in body["rows"]]
    assert covered[0]["stories"] == ["story:dns_range"] and covered[1]["answer_id"] == "skill:linux"
    assert [row["questions"][0]["uncovered"] for row in body["rows"]] == [False, False, True]
    assert body["summary"]["uncovered"] == 1
