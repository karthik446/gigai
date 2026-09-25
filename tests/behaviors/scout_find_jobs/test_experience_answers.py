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

import multiprocessing
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


def _concurrent_answer(opts: dict[str, object], question_id: str, prompt: str, answer: str, queue: object) -> None:
    try:
        result = record_answer(**opts, question_id=question_id, prompt=prompt, answer=answer)  # type: ignore[arg-type]
        queue.put(("ok", result.record_id, result.revision_id))  # type: ignore[attr-defined]
    except PrivateRecordError as exc:
        queue.put((exc.code, "", ""))  # type: ignore[attr-defined]


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


# --- question_id contract validation, BEFORE any write (Terra review P2) --------------


def test_question_id_with_invalid_characters_is_rejected_before_any_write(fx: ProfileFixtureGig) -> None:
    # "/" survives normalization's own tokenizing (it's not one of the
    # `-`/`_`/whitespace separators normalize_question_id splits on) but
    # fails the experience_question contract's character class -- the
    # review's own concrete example.
    with pytest.raises(PrivateRecordError) as excinfo:
        record_answer(**_opts(fx), question_id="cloud:gcp/invalid", prompt="GCP?", answer="Yes.")
    assert excinfo.value.code == "answer_invalid"
    assert list_native_records(**_opts(fx)) == []  # rejected before any write


def test_question_id_over_128_chars_after_normalization_is_rejected_before_any_write(fx: ProfileFixtureGig) -> None:
    with pytest.raises(PrivateRecordError) as excinfo:
        record_answer(**_opts(fx), question_id=f"cloud:{'a' * 200}", prompt="?", answer="Yes.")
    assert excinfo.value.code == "answer_invalid"
    assert list_native_records(**_opts(fx)) == []


def test_question_id_at_exactly_128_chars_after_normalization_is_accepted(fx: ProfileFixtureGig) -> None:
    # 128 total: "cloud:" (6) + 122 'a's.
    question_id = f"cloud:{'a' * 122}"
    result = record_answer(**_opts(fx), question_id=question_id, prompt="?", answer="Yes.")
    assert result.created


# --- retry-safety under the journal writer (Terra review P1) --------------------------


def test_two_concurrent_answers_appending_at_31_both_land_no_duplicate_no_error(fx: ProfileFixtureGig) -> None:
    # Pre-fill one record to 31 answered questions (one below the 32-cap),
    # then race two DIFFERENT new answers for the 32nd slot. Exactly one
    # process's update wins the parent revision at 31; the other hits
    # `stale_parent`, re-reads (record now at 32, full), and rolls over into
    # a fresh record -- both retries must land without error, without a
    # duplicate fact, and without silently dropping either answer.
    for i in range(31):
        record_answer(**_opts(fx), question_id=f"years:skill{i}", prompt=f"skill {i}?", answer=f"answer {i}")
    assert len(read_answers(**_opts(fx))) == 31

    opts = _opts(fx)
    context = multiprocessing.get_context("spawn")
    queue = context.Queue()
    workers = [
        context.Process(target=_concurrent_answer, args=(opts, "cloud:gcp", "GCP?", "Yes, two years.", queue)),
        context.Process(target=_concurrent_answer, args=(opts, "years:python", "Years of Python?", "Six.", queue)),
    ]
    for worker in workers:
        worker.start()
    outcomes = [queue.get(timeout=60) for _ in workers]
    for worker in workers:
        worker.join(timeout=60)
        assert worker.exitcode == 0

    # No error surfaced to either caller: the retry absorbed the race.
    assert [outcome[0] for outcome in outcomes] == ["ok", "ok"]

    answers = read_answers(**opts)
    assert len(answers) == 33  # 31 pre-filled + both new answers, no duplicate
    assert "cloud:gcp" in answers and "years:python" in answers

    # One of the two landed in the original (now-full, 32-question) record;
    # the other rolled over into a second record (operator answer 4).
    experience_records = [row for row in list_native_records(**opts) if row["kind"] == "experience_qa"]
    assert len(experience_records) == 2
    record_ids = {answers["cloud:gcp"].record_id, answers["years:python"].record_id}
    assert len(record_ids) == 2  # the two new answers did NOT land in the same record


def test_identical_retried_answer_after_ambiguous_response_is_not_duplicated(fx: ProfileFixtureGig) -> None:
    # Simulate a client that retried after an ambiguous response (e.g. its
    # first call's HTTP response was lost, but the write actually committed):
    # calling record_answer again with the SAME question_id/answer must not
    # error and must not create a second fact or a spurious extra revision.
    first = record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="Yes, two years.")

    retried = record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="Yes, two years.")

    assert retried.record_id == first.record_id
    assert retried.revision_id == first.revision_id  # identical retry -> same committed revision, not a new one

    answers = read_answers(**_opts(fx))
    assert len(answers) == 1
    assert answers["cloud:gcp"].answer == "Yes, two years."
