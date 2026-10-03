"""0.1.10.7 C: ``gigai.scout.story_bank`` -- the user's answers, one per question for every profile.

Real gig (``build_gig_with_resume``), synthetic answers only; the model is a scripted
binding that keeps every prompt.

Covers: an answer carries the contract's fields (question, tag, jobs, writer, dates,
revision); an answer written by an AGENT for one job is reused by the assessment of a
DIFFERENT job, for another profile, that asks the same question (it is in the prompt and
in the sealed basis); a write that names a stale revision is refused with the current
answer; contact-shaped text is refused on every write path; delete; the near match.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.private_records import PrivateRecordError
from gigai.scout import story_bank
from gigai.scout.story_bank import StoryBankError

from tests.support.answers_stories_fixtures import MATCH, RESUME, assess, install_model, paths, pending, two_profiles
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume

_GCP = "Yes: two years running batch workloads on GCP."
_JOB_A = {"job_identity": "https://jobs.example.invalid/acme/1", "title": "Platform Engineer", "company": "Acme", "url": "https://jobs.example.invalid/acme/1"}


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=RESUME)


def test_an_answer_has_the_contract_shape(fx: ProfileFixtureGig) -> None:
    saved = story_bank.save_answer(
        **paths(fx), question_id="Cloud:GCP", answer=_GCP, question="Do you have GCP experience?", job=_JOB_A, actor="agent", tag="cloud platforms",
    )

    body = saved.to_json()
    assert set(body) == {"question_id", "question", "answer", "tag", "jobs", "written_by", "created_at", "updated_at", "revision", "history"}
    assert body["question_id"] == "cloud:gcp" and body["question"] == "Do you have GCP experience?" and body["answer"] == _GCP
    assert body["tag"] == "cloud platforms" and body["written_by"] == "agent" and body["revision"] == 1
    assert body["created_at"] and body["created_at"] == body["updated_at"]
    assert body["jobs"] == [{**_JOB_A, "kind": "answered", "at": body["updated_at"]}]
    assert body["history"] == [{"at": body["updated_at"], "by": "agent", "action": "answered"}]
    assert [entry.to_json() for entry in story_bank.read_bank(**paths(fx))] == [body]
    assert "profile" not in " ".join(body), "an answer names no profile: it is the user's"


def test_an_agent_written_answer_is_reused_by_another_jobs_assessment(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """Acceptance (a): the END outcome. Job A asks, an agent answers, job B (another profile) asks the same: no question."""

    default, other = two_profiles(fx)
    binding = install_model(monkeypatch, [pending("cloud:gcp", "Do you have GCP experience?"), MATCH])
    first = assess(fx, default, "Posting A. Requirements: Python. GCP.", title="Platform Engineer A")
    assert [question.question_id for question in first.result.structured_questions] == ["cloud:gcp"]
    assert _GCP not in binding.port.prompts[0]

    story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP, question="Do you have GCP experience?", actor="agent")
    second = assess(fx, other, "Posting B, another company. Requirements: Python. Google Cloud Platform.", title="Data Engineer B")

    prompt = binding.port.prompts[1]
    assert f"- cloud:gcp: {_GCP}" in prompt, "PRIOR ANSWERS: the exact question id is answered, never asked again"
    assert f"- cloud:gcp | asked: Do you have GCP experience? | answer: {_GCP}" in prompt, "STORY BANK: a reworded requirement reuses it"
    # The assessment basis records the answer it was made with.
    answer = story_bank.get_answer(**paths(fx), question_id="cloud:gcp")
    assert answer is not None and answer.written_by == "agent"
    bank = story_bank.assess_bank(**paths(fx))
    assert second.story_bank is not None and second.story_bank.entries == {"cloud:gcp": bank.marks["cloud:gcp"]}
    assert second.resume.profile_id == other and second.result.structured_questions == ()


def test_every_profile_and_a_pasted_resume_read_the_same_answers(fx: ProfileFixtureGig) -> None:
    default, other = two_profiles(fx)
    story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP)

    for profile_id in (default, other, None):
        bank = story_bank.assess_bank(**paths(fx), profile_id=profile_id)
        assert [item.question_id for item in bank.prior_answers] == ["cloud:gcp"], profile_id
    assert set(story_bank.answers_for_reuse(**paths(fx))) == {"cloud:gcp"}


def test_a_stale_revision_is_refused_with_the_current_answer(fx: ProfileFixtureGig) -> None:
    """Acceptance (c): two writers, neither overwrites the other silently."""

    read = story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP)
    agent = story_bank.edit_answer(**paths(fx), question_id="cloud:gcp", answer="Three years on GCP.", actor="agent", expected_revision=read.revision)
    assert agent.revision == 2 and agent.written_by == "agent"

    for write in (
        lambda: story_bank.edit_answer(**paths(fx), question_id="cloud:gcp", answer="One year.", expected_revision=read.revision),
        lambda: story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer="One year.", expected_revision=read.revision),
        lambda: story_bank.delete_answer(**paths(fx), question_id="cloud:gcp", expected_revision=read.revision),
    ):
        with pytest.raises(StoryBankError) as refused:
            write()
        assert refused.value.code == "revision_conflict"
        assert refused.value.entry.to_json() == agent.to_json(), "the caller gets the answer as it is now"
    assert story_bank.get_answer(**paths(fx), question_id="cloud:gcp").answer == "Three years on GCP."

    # The same write with the revision it now reads goes through.
    assert story_bank.edit_answer(**paths(fx), question_id="cloud:gcp", tag="cloud", expected_revision=agent.revision).revision == 3
    assert story_bank.delete_answer(**paths(fx), question_id="cloud:gcp", expected_revision=3) == "cloud:gcp"
    assert story_bank.read_bank(**paths(fx)) == ()
    with pytest.raises(StoryBankError) as gone:
        story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP, expected_revision=3)
    assert gone.value.code == "not_found"


@pytest.mark.parametrize(
    "text",
    [
        "Reach me at fixture.person@example.invalid about GCP.",
        "Call 415-555-0134 to discuss GCP.",
        "See https://www.linkedin.com/in/fixture-person for my GCP work.",
        "I worked at 221 Fixture Street, Springfield on GCP.",
    ],
)
def test_contact_shaped_text_is_refused_on_every_write(fx: ProfileFixtureGig, text: str) -> None:
    """Acceptance (c): shape-only (email, phone, link, street address); nothing is stored."""

    with pytest.raises(StoryBankError) as refused:
        story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=text)
    assert refused.value.code == "personal_info_refused" and "@" not in str(refused.value) and "555" not in str(refused.value)
    with pytest.raises(StoryBankError):
        story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP, question=text)
    assert story_bank.read_bank(**paths(fx)) == ()

    story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP)
    with pytest.raises(StoryBankError) as edited:
        story_bank.edit_answer(**paths(fx), question_id="cloud:gcp", answer=text)
    assert edited.value.code == "personal_info_refused"
    assert story_bank.get_answer(**paths(fx), question_id="cloud:gcp").answer == _GCP


def test_invalid_writes_are_typed(fx: ProfileFixtureGig) -> None:
    with pytest.raises(PrivateRecordError) as empty:
        story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer="   ")
    assert empty.value.code == "answer_invalid"
    with pytest.raises(StoryBankError) as actor:
        story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP, actor="robot")
    assert actor.value.code == "invalid_value"
    with pytest.raises(StoryBankError) as missing:
        story_bank.edit_answer(**paths(fx), question_id="cloud:aws", answer="Four years.")
    assert missing.value.code == "not_found"
    assert story_bank.revision_value("4") == 4 and story_bank.revision_value(None) is None
    with pytest.raises(StoryBankError):
        story_bank.revision_value("four")
    with pytest.raises(StoryBankError):
        story_bank.revision_value(None, required=True)


def test_a_reworded_question_gets_the_near_match(fx: ProfileFixtureGig) -> None:
    story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP, question="Do you have GCP experience?")
    entries = story_bank.read_bank(**paths(fx), with_jobs=False)

    match = story_bank.near_match(entries, question_id="tooling:google_cloud_platform", question="Experience with Google Cloud Platform?")

    assert match is not None and match.bank_question_id == "cloud:gcp" and match.answer == _GCP
    assert set(match.to_json()) == {"question_id", "bank_question_id", "bank_question", "answer", "score"}
    assert story_bank.near_match(entries, question_id="cloud:gcp") is None, "the same id is plain reuse, not a suggestion"
    assert story_bank.near_match(entries, question_id="language:rust", question="Rust?") is None


def test_the_answers_file_is_the_only_new_file_and_holds_no_answer_text(fx: ProfileFixtureGig) -> None:
    story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP)

    folder = story_bank.bank_path(fx.home_root, fx.target).parent
    assert sorted(path.name for path in folder.iterdir()) == ["answers.json"]
    assert _GCP not in story_bank.bank_path(fx.home_root, fx.target).read_text(encoding="utf-8"), "the text lives in the journal record"


# --- carried over from the 0.1.10.5 per-profile bank's tests: what does not depend on a profile --------


def _entry(question_id: str, question: str = "", answer: str = "ok", tag: str = "skill") -> story_bank.BankEntry:
    return story_bank.BankEntry(
        question_id=question_id, question=question, answer=answer, tag=tag, created_at=None, updated_at=None, jobs=(), record_id="r", revision_id="v",
    )


def test_an_ordinary_answer_is_not_mistaken_for_a_name(fx: ProfileFixtureGig) -> None:
    for index, answer in enumerate(("Apache Kafka", "Google Cloud", "Yes", "Six years, mostly at Acme in Denver, CO.")):
        story_bank.save_answer(**paths(fx), question_id=f"skill:s{index}", answer=answer)
    assert len(story_bank.read_bank(**paths(fx))) == 4


def test_what_reaches_the_prompt_is_capped_and_redacted(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.experience_answers import record_answer

    # Saved by a command older than the contact-data check: an old answer with contact details in it.
    record_answer(
        home_root=fx.home_root, requested_target=fx.target, question_id="cloud:azure", prompt="cloud:azure",
        answer="Three years on Azure; ask zq7731@example.test or 555-013-7731, see https://zq7731.example/azure.",
    )
    story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP + " " + "More detail. " * 40, question="Have you run workloads on GCP?")
    binding = install_model(monkeypatch, [MATCH])

    assess(fx, None, "Requirements: Python.")

    prompt = binding.port.prompts[0]
    bank_lines = [line for line in prompt.splitlines() if line.startswith("- ") and " | " in line and " answer: " in line]
    assert [line.split(" | ")[0] for line in bank_lines] == ["- cloud:gcp", "- cloud:azure"], "newest first"
    gcp_line = bank_lines[0]
    assert " | asked: Have you run workloads on GCP? | answer: Yes: two years" in gcp_line
    assert len(gcp_line.split(" answer: ", 1)[1]) <= story_bank.MAX_SUMMARY_CHARS and gcp_line.endswith("...")
    assert bank_lines[1] == "- cloud:azure | answer: Three years on Azure; ask or , see"
    for value in ("zq7731", "555-013", "example.test"):
        assert value not in prompt, value
    assert "STORY BANK (answers" in prompt and 'put "Story bank <question_id>: <the answer>"' in prompt
    assert "- cloud:azure: Three years on Azure; ask or , see" in prompt, "the PRIOR ANSWERS line is redacted too"

    many = tuple(_entry(f"skill:s{index:03d}") for index in range(story_bank.MAX_PROMPT_SUMMARIES + 10))
    assert len(story_bank.prompt_summaries(many)) == story_bank.MAX_PROMPT_SUMMARIES
    named = {item.question_id: item.summary for item in story_bank.prompt_summaries(story_bank.read_bank(**paths(fx)), names=("Zephyrine Azure",))}
    assert named["cloud:azure"] == "Three years on ; ask or , see", "the words of a known name are removed from the line"


def test_with_no_answers_and_no_stories_the_prompt_is_as_it_was_before_the_bank(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    binding = install_model(monkeypatch, [MATCH])

    assess(fx, None, "Requirements: Python.")

    assert "STORY BANK (answers" not in binding.port.prompts[0] and "PRIOR ANSWERS (from earlier" not in binding.port.prompts[0]


@pytest.mark.parametrize(
    "question_id, question, expected",
    [
        ("tooling:google_cloud_platform", "Do you have hands-on Google Cloud Platform experience?", True),
        ("cloud:gcp_bigquery", "Have you used BigQuery on GCP?", True),
        ("cloud:aws", "Have you used AWS?", False),
        ("years:python", "How many years of Python?", False),
        ("cloud:gcp", "Have you used GCP?", False),  # the same id: plain reuse, never a suggestion
    ],
)
def test_the_near_match_is_token_overlap_and_never_the_exact_id(question_id: str, question: str, expected: bool) -> None:
    entry = _entry("cloud:gcp", "Have you run workloads on GCP?", _GCP, "technical")
    assert (story_bank.near_match((entry,), question_id=question_id, question=question) is not None) is expected


def test_an_assessment_that_cites_an_answer_is_noted_on_that_answer(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    from tests.support.answers_stories_fixtures import citing

    story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer=_GCP)
    install_model(monkeypatch, [citing("cloud:gcp", _GCP)])

    response = assess(fx, None, "Requirements: Python. Google Cloud Platform.")

    (entry,) = story_bank.read_bank(**paths(fx))
    assert [(job.job_identity, job.kind) for job in entry.jobs] == [(response.job.job_identity, "reused")]
    assert entry.revision == 1, "a reuse is noted, it is not a write of the answer"


@pytest.mark.parametrize(
    "question_id, question, tag",
    [
        ("cloud:gcp", "", "technical"),
        ("language:kotlin_or_python", "", "technical"),
        ("years:python", "How many years leading Python teams?", "experience-level"),
        ("location:us_region", "", "eligibility"),
        ("education:enrolled", "", "education"),
        ("domain:fintech", "", "domain"),
        ("skill:team", "Have you led a team of engineers?", "leadership"),
        ("story:x", "Tell me about a time you disagreed with a stakeholder", "conflict"),
        ("story:y", "Describe an outage you caused", "failure"),
        ("skill:distributed_systems", "", "system-design"),
        ("unstructured", "", "other"),
    ],
)
def test_the_tag_is_model_free_and_stable(question_id: str, question: str, tag: str) -> None:
    assert story_bank.tag_for(question_id, question) == tag
    assert story_bank.tag_for(question_id, question) == tag
