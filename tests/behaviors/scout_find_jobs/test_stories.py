"""0.1.10.7 C: ``gigai.scout.stories`` -- the user's stories and what an assessment does with them.

Real gig (``build_gig_with_resume``), synthetic stories only; the model is a scripted
binding that keeps every prompt.

Covers: a story has the contract's shape; a stale revision is refused with the current
story; contact-shaped text in ANY field is refused; acceptance (b): the story that matches
a posting is in that posting's assess prompt and the one that does not match is not; the
search writes no story text to any SQLite file; a cited story records the job that used
it; interview prep is ``answers_questions`` pooled across the stories.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout import stories, story_bank
from gigai.scout.story_bank import StoryBankError

from tests.support.answers_stories_fixtures import MATCH, RESUME, assess, citing, install_model, paths
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_CI = {
    "title": "Cut CI time 60% at Acme",
    "company": "Acme",
    "role": "Staff Engineer",
    "period": "2023",
    "raw": "Our builds took forty minutes so I moved the runners to Kubernetes and cached the layers.",
    "narrative": {
        "situation": "Builds took forty minutes and blocked every merge.",
        "action": "Moved the Jenkins runners to Kubernetes and cached the image layers.",
        "result": "Build time fell 60 percent.",
    },
    "tags": ["ci", "delivery"],
    "answers_questions": ["Tell me about a time you improved a slow process"],
    "sources": [{"question_id": "tooling:kubernetes", "job_identity": "https://jobs.example.invalid/acme/1"}],
}
_DESIGN = {
    "title": "Settled a disagreement with a designer",
    "raw": "We disagreed about the onboarding flow; I ran a small test and we kept her version.",
    "tags": ["conflict"],
    "answers_questions": ["Tell me about a disagreement with a colleague", "Tell me about a time you improved a slow process"],
}
_KUBERNETES_POSTING = "Requirements: 5+ years of Python. Production Kubernetes. Terraform is a plus."


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=RESUME)


def test_a_story_has_the_contract_shape(fx: ProfileFixtureGig) -> None:
    saved = stories.save_story(**paths(fx), fields=_CI, actor="agent")

    body = saved.to_json()
    assert set(body) == {
        "story_id", "title", "company", "role", "period", "raw", "narrative", "tags", "answers_questions", "sources", "jobs",
        "written_by", "created_at", "updated_at", "revision", "history",
    }
    # story:<the title's first words>, as the id normalizer writes them (its words sorted).
    assert body["story_id"] == stories.story_id_for(_CI["title"]) == "story:60_acme_ci_cut_time"
    for key in ("title", "company", "role", "period", "raw", "narrative", "tags", "answers_questions", "sources"):
        assert body[key] == _CI[key], key
    assert (body["written_by"], body["revision"], body["jobs"]) == ("agent", 1, []) and body["created_at"] == body["updated_at"]
    assert [story.to_json() for story in stories.list_stories(**paths(fx))] == [body]
    assert stories.get_story(**paths(fx), story_id="Story:Cut_CI_Time_60_Acme").to_json() == body, "any spelling of the id"
    assert story_bank.stories_path(fx.home_root, fx.target).stat().st_mode & 0o077 == 0, "readable by the user only"


def test_a_title_is_required_and_parts_of_the_narrative_are_optional(fx: ProfileFixtureGig) -> None:
    with pytest.raises(StoryBankError) as untitled:
        stories.save_story(**paths(fx), fields={"raw": "Something happened."})
    assert untitled.value.code == "invalid_value"
    with pytest.raises(StoryBankError) as unknown:
        stories.save_story(**paths(fx), fields={"title": "A story", "summary": "not a field"})
    assert unknown.value.code == "unknown_key"
    with pytest.raises(StoryBankError) as part:
        stories.save_story(**paths(fx), fields={"title": "A story", "narrative": {"moral": "be kind"}})
    assert part.value.code == "unknown_key"

    bare = stories.save_story(**paths(fx), fields={"title": "A story", "narrative": {"result": "It worked."}})
    assert bare.to_json()["narrative"] == {"result": "It worked."} and bare.tags == () and bare.raw == ""

    with pytest.raises(StoryBankError) as twice:
        stories.save_story(**paths(fx), fields={"title": "A story"})
    assert twice.value.code == "story_exists" and twice.value.entry.to_json() == bare.to_json()


def test_a_stale_revision_is_refused_with_the_current_story(fx: ProfileFixtureGig) -> None:
    """Acceptance (c)."""

    read = stories.save_story(**paths(fx), fields=_CI)
    agent = stories.edit_story(**paths(fx), story_id=read.story_id, fields={"period": "2022-2023"}, actor="agent", expected_revision=read.revision)
    assert (agent.revision, agent.written_by, agent.period, agent.title) == (2, "agent", "2022-2023", _CI["title"])

    for write in (
        lambda: stories.edit_story(**paths(fx), story_id=read.story_id, fields={"role": "Engineer"}, expected_revision=read.revision),
        lambda: stories.delete_story(**paths(fx), story_id=read.story_id, expected_revision=read.revision),
    ):
        with pytest.raises(StoryBankError) as refused:
            write()
        assert refused.value.code == "revision_conflict" and refused.value.entry.to_json() == agent.to_json()
    assert stories.get_story(**paths(fx), story_id=read.story_id).role == "Staff Engineer"

    assert stories.delete_story(**paths(fx), story_id=read.story_id, expected_revision=2) == read.story_id
    assert stories.list_stories(**paths(fx)) == ()
    with pytest.raises(StoryBankError) as gone:
        stories.edit_story(**paths(fx), story_id=read.story_id, fields={"role": "Engineer"}, expected_revision=2)
    assert gone.value.code == "not_found"


@pytest.mark.parametrize(
    "field,value",
    [
        ("title", "Cut CI time, ask fixture.person@example.invalid"),
        ("company", "Acme, 415-555-0134"),
        ("raw", "I wrote it up at https://www.linkedin.com/in/fixture-person afterwards."),
        ("narrative", {"action": "Mailed fixture.person@example.invalid the plan."}),
        ("answers_questions", ["Call 415-555-0134 about a slow process?"]),
    ],
)
def test_contact_shaped_text_in_any_field_is_refused(fx: ProfileFixtureGig, field: str, value: object) -> None:
    """Acceptance (c): the contact-data check runs on every text of every write."""

    with pytest.raises(StoryBankError) as refused:
        stories.save_story(**paths(fx), fields={**_CI, field: value})
    assert refused.value.code == "personal_info_refused"
    assert stories.list_stories(**paths(fx)) == ()

    saved = stories.save_story(**paths(fx), fields=_CI)
    with pytest.raises(StoryBankError) as edited:
        stories.edit_story(**paths(fx), story_id=saved.story_id, fields={field: value}, expected_revision=1)
    assert edited.value.code == "personal_info_refused"
    assert stories.get_story(**paths(fx), story_id=saved.story_id).to_json() == saved.to_json()


def test_only_the_story_that_matches_the_job_is_searched_out(fx: ProfileFixtureGig) -> None:
    ci = stories.save_story(**paths(fx), fields=_CI)
    design = stories.save_story(**paths(fx), fields=_DESIGN)
    every = stories.list_stories(**paths(fx))

    assert stories.relevant_stories(every, title="Platform Engineer", text=_KUBERNETES_POSTING) == (ci,)
    assert stories.relevant_stories(every, title="Platform Engineer", text="Requirements: K8s in production.") == (ci,), "an alias of the keyword"
    assert stories.relevant_stories(every, title="Product Designer", text="Requirements: Figma. Rust.") == ()
    assert design not in stories.relevant_stories(every, title="Platform Engineer", text=_KUBERNETES_POSTING)
    assert stories.relevant_stories((), title="Platform Engineer", text=_KUBERNETES_POSTING) == ()


def test_at_most_three_stories_go_to_one_job(fx: ProfileFixtureGig) -> None:
    for number in range(5):
        stories.save_story(**paths(fx), fields={"title": f"Kubernetes migration {number}", "raw": "Ran Kubernetes in production."})

    found = stories.relevant_stories(stories.list_stories(**paths(fx)), title="Platform Engineer", text=_KUBERNETES_POSTING)

    assert len(found) == stories.MAX_PROMPT_STORIES == 3


def test_the_relevant_story_is_in_the_prompt_and_the_irrelevant_one_is_not(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance (b): the END outcome, read from the prompt the model was sent."""

    ci = stories.save_story(**paths(fx), fields=_CI, actor="agent")
    design = stories.save_story(**paths(fx), fields=_DESIGN, actor="agent")
    binding = install_model(monkeypatch, [citing(ci.story_id, "moved the runners to Kubernetes"), MATCH])

    response = assess(fx, None, _KUBERNETES_POSTING, title="Platform Engineer")

    prompt = binding.port.prompts[0]
    assert f"- {ci.story_id} | asked: Tell me about a time you improved a slow process | answer: Cut CI time 60% at Acme" in prompt
    assert "Moved the Jenkins runners to Kubernetes" in prompt, "the story's telling is the evidence"
    assert design.story_id not in prompt and "Settled a disagreement" not in prompt and "kept her version" not in prompt
    # Sealed with the assessment, and the job that cited it is on the story.
    assert response.story_bank is not None and response.story_bank.entries[ci.story_id] == stories.mark(ci)
    used = stories.get_story(**paths(fx), story_id=ci.story_id)
    assert [(job.kind, job.title) for job in used.jobs] == [("used", "Platform Engineer")] and used.revision == 1
    assert stories.get_story(**paths(fx), story_id=design.story_id).jobs == ()

    # A job neither story is about gets neither.
    assess(fx, None, "Requirements: 5+ years of Python. Figma.", title="Design Engineer")
    assert "story:" not in binding.port.prompts[1]


def test_a_story_line_redacts_and_stays_short(fx: ProfileFixtureGig) -> None:
    long = stories.save_story(**paths(fx), fields={"title": "Long one", "raw": "Kubernetes " * 400})

    line = stories.prompt_line(long)

    assert line is not None and line.question_id == long.story_id and len(line.summary) <= stories.MAX_STORY_LINE_CHARS
    assert line.question == "Long one", "no interview question: the title stands in"


def test_the_search_writes_no_story_text_to_any_sqlite_file(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Coordinator note 7: the per-job search is in memory; no SQLite file ever holds story text."""

    import sqlite3

    opened: list[str] = []
    real_connect = sqlite3.connect

    def connect(database, *args, **kwargs):
        opened.append(str(database))
        return real_connect(database, *args, **kwargs)

    stories.save_story(**paths(fx), fields=_CI)
    stories.save_story(**paths(fx), fields=_DESIGN)
    binding = install_model(monkeypatch, [MATCH])
    monkeypatch.setattr(sqlite3, "connect", connect)

    assess(fx, None, _KUBERNETES_POSTING, title="Platform Engineer")
    stories.relevant_stories(stories.list_stories(**paths(fx)), title="Platform Engineer", text=_KUBERNETES_POSTING)

    assert "Cut CI time 60% at Acme" in binding.port.prompts[0], "the search ran"
    assert ":memory:" in opened, "an in-memory table"
    needles = [b"Cut CI time", b"forty minutes", b"Jenkins runners", b"onboarding flow"]
    on_disk = [path for path in tmp_path.rglob("*") if path.is_file() and path.read_bytes()[:16] == b"SQLite format 3\x00"]
    on_disk += [path for path in tmp_path.rglob("*") if path.is_file() and path.suffix in {".sqlite", ".sqlite3", ".db", ".db-wal", ".sqlite-wal"} and path not in on_disk]
    for path in on_disk:
        data = path.read_bytes()
        assert not [needle for needle in needles if needle in data], f"story text in {path}"
    # The one file that holds the stories is the plain JSON store.
    holding = sorted(path.name for path in tmp_path.rglob("*") if path.is_file() and b"forty minutes" in path.read_bytes())
    assert holding == ["stories.json"]


def test_interview_prep_is_the_questions_pooled_across_the_stories(fx: ProfileFixtureGig) -> None:
    ci = stories.save_story(**paths(fx), fields=_CI)
    design = stories.save_story(**paths(fx), fields=_DESIGN)

    pooled = stories.prep_questions(stories.list_stories(**paths(fx)))

    assert pooled == [
        {
            "question": "Tell me about a time you improved a slow process",
            "stories": [{"story_id": ci.story_id, "title": ci.title}, {"story_id": design.story_id, "title": design.title}],
        },
        {"question": "Tell me about a disagreement with a colleague", "stories": [{"story_id": design.story_id, "title": design.title}]},
    ]
    assert stories.prep_questions(()) == []


def test_a_story_id_never_takes_an_answers_id(fx: ProfileFixtureGig) -> None:
    story_bank.save_answer(**paths(fx), question_id="story:led_migration", answer="I led the database migration.")

    with pytest.raises(StoryBankError) as taken:
        stories.save_story(**paths(fx), fields={"title": "Led migration"})

    assert taken.value.code == "story_exists"
    assert stories.save_story(**paths(fx), fields={"title": "Led migration"}, story_id="story:database_migration").story_id == "story:database_migration"
