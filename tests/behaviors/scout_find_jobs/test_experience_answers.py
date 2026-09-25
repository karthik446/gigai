"""P3 (v0.1.9): ``gigai.scout.experience_answers`` -- read/record answered
``experience_qa`` questions for the Q&A loop.

Real gig (``build_gig_with_resume``: journal-authoritative workpad), the
same fixture ``test_quick_assess.py``/``test_present_assess_api.py`` use.
Covers: create-on-first-answer, append, rollover at 32 (operator answer 4),
read-back, an id that drifted across two model calls for the SAME fact still
joining to one answer (the normalizer, P3's own addition), a genuine
re-answer updating in place rather than duplicating, and answer-text bounds
(``answer_cli.py``'s own rule, C10 edges: non-empty, <=16,000 chars).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.native_records import list_native_records, read_native_record
from gigai.private_records import PrivateRecordError
from gigai.scout.experience_answers import read_answers, record_answer

from tests.support.scout_profile_fixtures import ProfileFixtureGig, build_gig_with_resume


@pytest.fixture
def fx(tmp_path: Path) -> ProfileFixtureGig:
    return build_gig_with_resume(tmp_path)


def _opts(fx: ProfileFixtureGig) -> dict[str, object]:
    return {"home_root": fx.home_root, "requested_target": fx.target, "gig_id": fx.resolved.gig_id}


# --- create-on-first-answer / append / read-back -------------------------------------


def test_first_answer_creates_a_record_and_reads_back(fx: ProfileFixtureGig) -> None:
    assert read_answers(**_opts(fx)) == {}

    result = record_answer(**_opts(fx), question_id="cloud:gcp", prompt="Have you used GCP?", answer="Yes, two years.")
    assert result.created

    answers = read_answers(**_opts(fx))
    assert set(answers) == {"cloud:gcp"}
    assert answers["cloud:gcp"].answer == "Yes, two years."
    assert answers["cloud:gcp"].record_id == result.record_id

    rows = list_native_records(**_opts(fx))
    assert [row["kind"] for row in rows if row["kind"] == "experience_qa"] == ["experience_qa"]


def test_second_answer_appends_to_the_same_record(fx: ProfileFixtureGig) -> None:
    first = record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="Yes.")
    second = record_answer(**_opts(fx), question_id="years:python", prompt="Years of Python?", answer="Six.")

    assert second.record_id == first.record_id
    assert second.revision_id != first.revision_id

    answers = read_answers(**_opts(fx))
    assert set(answers) == {"cloud:gcp", "years:python"}
    assert all(item.record_id == first.record_id for item in answers.values())


# --- rollover at 32 (operator answer 4) -----------------------------------------------


def test_rollover_at_32_answers_creates_a_new_record(fx: ProfileFixtureGig) -> None:
    first_record_id = None
    for i in range(32):
        result = record_answer(**_opts(fx), question_id=f"years:skill{i}", prompt=f"skill {i}?", answer=f"answer {i}")
        if first_record_id is None:
            first_record_id = result.record_id
        assert result.record_id == first_record_id  # still filling the same record

    answers = read_answers(**_opts(fx))
    assert len(answers) == 32

    rollover = record_answer(**_opts(fx), question_id="years:skill33", prompt="skill 33?", answer="answer 33")
    assert rollover.record_id != first_record_id
    assert rollover.created

    answers = read_answers(**_opts(fx))
    assert len(answers) == 33
    assert answers["years:skill33"].record_id == rollover.record_id

    experience_records = [row for row in list_native_records(**_opts(fx)) if row["kind"] == "experience_qa"]
    assert len(experience_records) == 2


# --- the normalizer joins a drifted id to one answer ----------------------------------


def test_drifted_question_id_for_the_same_fact_joins_to_one_answer(fx: ProfileFixtureGig) -> None:
    # S29 r1's own rerun drift (question_ids.py's docstring/tests): the SAME
    # underlying fact, phrased with a different raw slug on a later call.
    record_answer(**_opts(fx), question_id="technology:ml_lifecycle_tooling", prompt="ML tooling?", answer="Used MLflow.")
    updated = record_answer(**_opts(fx), question_id="ml_tooling:lifecycle", prompt="ML lifecycle tooling?", answer="Used MLflow and Kubeflow.")

    answers = read_answers(**_opts(fx))
    assert len(answers) == 1
    joined = next(iter(answers.values()))
    assert joined.answer == "Used MLflow and Kubeflow."
    assert joined.record_id == updated.record_id


def test_a_genuine_reanswer_updates_in_place_not_duplicated(fx: ProfileFixtureGig) -> None:
    first = record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="No experience yet.")
    second = record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="Now I have two years.")

    assert second.record_id == first.record_id
    answers = read_answers(**_opts(fx))
    assert len(answers) == 1
    assert answers["cloud:gcp"].answer == "Now I have two years."

    metadata = read_native_record(home_root=fx.home_root, requested_target=fx.target, gig_id=fx.resolved.gig_id, record_id=first.record_id)
    assert metadata["revision_id"] == second.revision_id


# --- an id not seen in any assessment yet is still accepted (plan edge) --------------


def test_preanswering_an_unseen_question_id_is_accepted(fx: ProfileFixtureGig) -> None:
    result = record_answer(**_opts(fx), question_id="clearance:secret", prompt="Do you hold a clearance?", answer="No.")
    assert result.created
    assert "clearance:secret" in read_answers(**_opts(fx))


# --- provenance matches answer_cli.py exactly -----------------------------------------


def test_provenance_matches_answer_cli_exactly(fx: ProfileFixtureGig) -> None:
    result = record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="Yes.")
    full = read_native_record(
        home_root=fx.home_root, requested_target=fx.target, gig_id=fx.resolved.gig_id,
        record_id=result.record_id, revision_id=result.revision_id, content=True,
    )
    from gigai.canonical import parse_json_bytes

    content = parse_json_bytes(full["content"])
    question = content["payload"]["questions"][0]
    assert question["state"] == "answered"
    assert question["provenance"] == {"kind": "user_reported", "source_refs": []}
    assert content["scope"] == {"mode": "saved_default", "task_context_id": None, "base": None}


# --- answer text bounds (answer_cli.py's own rule) ------------------------------------


def test_empty_answer_text_is_rejected(fx: ProfileFixtureGig) -> None:
    with pytest.raises(PrivateRecordError) as excinfo:
        record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="   ")
    assert excinfo.value.code == "answer_invalid"


def test_answer_text_over_16000_chars_is_rejected(fx: ProfileFixtureGig) -> None:
    with pytest.raises(PrivateRecordError) as excinfo:
        record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="x" * 16_001)
    assert excinfo.value.code == "answer_invalid"


def test_answer_text_at_exactly_16000_chars_is_accepted(fx: ProfileFixtureGig) -> None:
    result = record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="x" * 16_000)
    assert result.created
