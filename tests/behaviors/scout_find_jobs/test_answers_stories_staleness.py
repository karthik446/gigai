"""0.1.10.7 C: a changed answer or story marks stale ONLY the assessments it concerns (the 0110-039 basis, targeted).

Real stored quick assessments on a ``build_gig_with_resume`` gig; the END outcome is what
a served assessment says (``assessment_basis.BasisCheck.served``: ``basis_stale``, its
reason and the entries that made it so), not an intermediate.

Three assessments: one left ``cloud:gcp`` open, one left ``language:rust`` open, one left
nothing open and cites nothing. Covers acceptance (e): a new answer to the GCP question
marks only the GCP one; a new story about Rust marks only the Rust one; an answer or a
story about something nobody asked marks none; a cited answer or story that is edited or
deleted marks the assessment that cited it and no other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout import stories, story_bank
from gigai.scout.assessment_basis import BasisCheck

from tests.support.answers_stories_fixtures import MATCH, RESUME, assess, citing, install_model, paths, pending
from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path, resume_text=RESUME)


def _stale(fx: ProfileFixtureGig, *responses) -> list[dict[str, object]]:
    check = BasisCheck(home_root=fx.home_root, target=fx.target)
    return [check.served(response) for response in responses]


@pytest.fixture
def three(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch):
    """``(asks GCP, asks Rust, asks nothing)``: three stored assessments made with an empty bank."""

    install_model(monkeypatch, [pending("cloud:gcp", "Do you have GCP experience?"), pending("language:rust", "Have you written Rust in production?"), MATCH])
    gcp = assess(fx, None, "Posting one. Requirements: Python. GCP.", title="Platform Engineer")
    rust = assess(fx, None, "Posting two. Requirements: Python. Rust.", title="Systems Engineer")
    done = assess(fx, None, "Posting three. Requirements: Python.", title="Backend Engineer")
    assert _stale(fx, gcp, rust, done) == [{"basis_stale": False}] * 3
    return gcp, rust, done


def test_a_new_answer_marks_only_the_assessment_that_asked_it(fx: ProfileFixtureGig, three) -> None:
    gcp, rust, done = three

    story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer="Two years on GCP.", question="Do you have GCP experience?", actor="agent")

    served = _stale(fx, gcp, rust, done)
    assert served[0] == {
        "basis_stale": True,
        "basis_stale_reason": "story_bank_changed",
        "basis_stale_bank": [{
            "match": "exact", "bank_question_id": "cloud:gcp", "bank_question": "Do you have GCP experience?",
            "question_id": "cloud:gcp", "question": "Do you have GCP experience?",
        }],
    }
    assert served[1:] == [{"basis_stale": False}, {"basis_stale": False}], "the Rust question and the finished one are untouched"


def test_a_reworded_answer_marks_only_the_assessment_it_nearly_matches(fx: ProfileFixtureGig, three) -> None:
    gcp, rust, done = three

    story_bank.save_answer(**paths(fx), question_id="tooling:google_cloud_platform", answer="Two years on GCP.", question="Experience with Google Cloud Platform?")

    served = _stale(fx, gcp, rust, done)
    assert served[0]["basis_stale"] is True and [match["match"] for match in served[0]["basis_stale_bank"]] == ["near"]
    assert served[1:] == [{"basis_stale": False}, {"basis_stale": False}]


def test_a_new_story_marks_only_the_assessment_whose_question_it_is_about(fx: ProfileFixtureGig, three) -> None:
    gcp, rust, done = three

    story = stories.save_story(
        **paths(fx), actor="agent",
        fields={"title": "Rewrote the ingest service in Rust", "raw": "I rewrote our ingest service in Rust and halved its memory use.", "tags": ["rust"]},
    )

    served = _stale(fx, gcp, rust, done)
    assert served[0] == {"basis_stale": False} and served[2] == {"basis_stale": False}
    assert served[1] == {
        "basis_stale": True,
        "basis_stale_reason": "story_bank_changed",
        "basis_stale_bank": [{
            "match": "near", "bank_question_id": story.story_id, "bank_question": "Rewrote the ingest service in Rust",
            "question_id": "language:rust", "question": "Have you written Rust in production?",
        }],
    }


def test_an_answer_or_story_about_something_nobody_asked_marks_none(fx: ProfileFixtureGig, three) -> None:
    story_bank.save_answer(**paths(fx), question_id="cloud:aws", answer="One year on AWS.", question="AWS?")
    stories.save_story(**paths(fx), fields={"title": "Settled a disagreement with a designer", "raw": "We tested both onboarding flows."})

    assert _stale(fx, *three) == [{"basis_stale": False}] * 3


def test_editing_a_cited_answer_or_story_marks_only_the_assessment_that_cited_it(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    answer = story_bank.save_answer(**paths(fx), question_id="cloud:gcp", answer="Two years on GCP.")
    story = stories.save_story(**paths(fx), fields={"title": "Ran Kubernetes for the payments team", "raw": "I ran Kubernetes in production for two years."})
    install_model(monkeypatch, [citing("cloud:gcp"), citing(story.story_id), MATCH])
    cites_answer = assess(fx, None, "Posting one. Requirements: Python. GCP.", title="Platform Engineer")
    cites_story = assess(fx, None, "Posting two. Requirements: Python. Kubernetes.", title="SRE")
    cites_nothing = assess(fx, None, "Posting three. Requirements: Python.", title="Backend Engineer")
    assert _stale(fx, cites_answer, cites_story, cites_nothing) == [{"basis_stale": False}] * 3

    story_bank.edit_answer(**paths(fx), question_id="cloud:gcp", answer="Three years on GCP.", expected_revision=answer.revision)
    served = _stale(fx, cites_answer, cites_story, cites_nothing)
    assert served[0]["basis_stale"] is True and served[0]["basis_stale_bank"] == [{"match": "cited", "bank_question_id": "cloud:gcp", "bank_question": "cloud:gcp"}]
    assert served[1:] == [{"basis_stale": False}, {"basis_stale": False}]

    current = stories.get_story(**paths(fx), story_id=story.story_id)
    stories.delete_story(**paths(fx), story_id=story.story_id, expected_revision=current.revision)
    served = _stale(fx, cites_answer, cites_story, cites_nothing)
    assert served[1]["basis_stale"] is True and served[1]["basis_stale_bank"] == [{"match": "cited", "bank_question_id": story.story_id}]
    assert served[2] == {"basis_stale": False}


def test_noting_which_job_used_a_story_is_not_a_change(fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch) -> None:
    """The reuse note adds a job to the story; it must not make the assessment that cited it stale."""

    story = stories.save_story(**paths(fx), fields={"title": "Ran Kubernetes for the payments team", "raw": "I ran Kubernetes in production."})
    install_model(monkeypatch, [citing(story.story_id)])

    response = assess(fx, None, "Requirements: Python. Kubernetes.", title="SRE")

    assert [job.kind for job in stories.get_story(**paths(fx), story_id=story.story_id).jobs] == ["used"]
    assert _stale(fx, response) == [{"basis_stale": False}]
