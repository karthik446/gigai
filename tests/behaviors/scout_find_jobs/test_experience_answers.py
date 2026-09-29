"""P3 (v0.1.9): ``gigai.scout.experience_answers`` -- read/record answered
``experience_qa`` questions for the Q&A loop.

Real gig (``build_gig_with_resume``: journal-authoritative workpad), the
same fixture ``test_quick_assess.py``/``test_present_assess_api.py`` use.
Covers: create-on-first-answer, append, rollover at 32 (operator answer 4),
read-back, an id that drifted across two model calls for the SAME fact still
joining to one answer (the normalizer, P3's own addition), a genuine
re-answer updating in place rather than duplicating, answer-text bounds
(``answer_cli.py``'s own rule, C10 edges: non-empty, <=16,000 chars), and two
concurrent answers at the 31->32 rollover with each interleaving forced by
explicit Barrier/Event hooks (uat-bug-034: no sleeps, no retry-until-pass).
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from gigai import workpad
from gigai.native_records import list_native_records, read_native_record
from gigai.private_records import PrivateRecordError
from gigai.scout import experience_answers
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


def _prefill_to_31(fx: ProfileFixtureGig) -> None:
    for i in range(31):
        record_answer(**_opts(fx), question_id=f"years:skill{i}", prompt=f"skill {i}?", answer=f"answer {i}")
    assert len(read_answers(**_opts(fx))) == 31


def _answer_in_thread(opts: dict[str, object], question_id: str, prompt: str, answer: str, outcomes: dict[str, object]) -> threading.Thread:
    """Run one ``record_answer`` on its own named thread; ANY exception is
    captured as the outcome (never swallowed into a timeout), so a failure
    names the real error rather than an empty queue."""

    def run() -> None:
        try:
            outcomes[question_id] = record_answer(**opts, question_id=question_id, prompt=prompt, answer=answer)  # type: ignore[arg-type]
        except BaseException as exc:  # noqa: BLE001 - the assertion below names it
            outcomes[question_id] = exc

    return threading.Thread(target=run, name=question_id)


def _assert_both_landed_in_two_records(opts: dict[str, object], outcomes: dict[str, object]) -> None:
    # No error surfaced to either caller.
    assert {key: type(value).__name__ for key, value in outcomes.items()} == {
        "cloud:gcp": "NativeRecordResult", "years:python": "NativeRecordResult",
    }, outcomes

    answers = read_answers(**opts)  # type: ignore[arg-type]
    assert len(answers) == 33  # 31 pre-filled + both new answers, no duplicate
    assert answers["cloud:gcp"].answer == "Yes, two years."
    assert answers["years:python"].answer == "Six."

    # One of the two landed in the original (now-full, 32-question) record;
    # the other rolled over into a second record (operator answer 4).
    experience_records = [row for row in list_native_records(**opts) if row["kind"] == "experience_qa"]  # type: ignore[arg-type]
    assert len(experience_records) == 2
    record_ids = {answers["cloud:gcp"].record_id, answers["years:python"].record_id}
    assert len(record_ids) == 2  # the two new answers did NOT land in the same record


def test_two_concurrent_answers_appending_at_31_both_land_no_duplicate_no_error(
    fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Pre-fill one record to 31 answered questions (one below the 32-cap),
    # then race two DIFFERENT new answers for the 32nd slot. The interleaving
    # is forced, not hoped for: both answers select the SAME parent revision
    # at 31 (a Barrier at the update_native_record call) before either
    # publishes; cloud:gcp publishes first, then years:python publishes
    # against the now-stale parent, hits `stale_parent`, re-reads (record now
    # at 32, full), and rolls over into a fresh record -- both must land
    # without error, without a duplicate fact, and without silently dropping
    # either answer.
    _prefill_to_31(fx)
    opts = _opts(fx)

    real_update = experience_answers.update_native_record
    both_selected = threading.Barrier(2, timeout=60)
    winner_published = threading.Event()
    first_calls: dict[str, dict[str, object]] = {}

    def racing_update(**kwargs: object) -> object:
        name = threading.current_thread().name
        if name in first_calls:
            return real_update(**kwargs)  # the loser's retry, unhooked
        call: dict[str, object] = {"parent": kwargs["parent_revision"]}
        first_calls[name] = call
        both_selected.wait()
        if name != "cloud:gcp":
            assert winner_published.wait(60)
        try:
            return real_update(**kwargs)
        except PrivateRecordError as exc:
            call["error"] = exc.code
            raise
        finally:
            winner_published.set()

    monkeypatch.setattr(experience_answers, "update_native_record", racing_update)
    outcomes: dict[str, object] = {}
    threads = [
        _answer_in_thread(opts, "cloud:gcp", "GCP?", "Yes, two years.", outcomes),
        _answer_in_thread(opts, "years:python", "Years of Python?", "Six.", outcomes),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(120)
        assert not thread.is_alive()

    # The race really happened: same parent, and the loser really was stale.
    assert first_calls["cloud:gcp"]["parent"] == first_calls["years:python"]["parent"]
    assert "error" not in first_calls["cloud:gcp"]
    assert first_calls["years:python"]["error"] == "stale_parent"
    _assert_both_landed_in_two_records(opts, outcomes)


def test_an_answer_read_during_another_answers_state_database_commit_does_not_conflict(
    fx: ProfileFixtureGig, monkeypatch: pytest.MonkeyPatch
) -> None:
    # uat-bug-034 (CI: WorkpadConflictError "unexpected top-level state:
    # state.sqlite-journal"). After every publish, native_records rebuilds the
    # Scout projection cache in state.sqlite under the database lock only;
    # SQLite's rollback journal exists until that COMMIT. A second answer
    # resolving the workpad at that moment must not refuse it. Forced: the
    # cloud:gcp answer pauses on its state.sqlite COMMIT with the journal on
    # disk; years:python resolves the workpad (the journal is still there)
    # and only then is cloud:gcp released.
    _prefill_to_31(fx)
    opts = _opts(fx)
    journal = fx.resolved.path / "state.sqlite-journal"

    journal_open = threading.Event()
    reader_validated = threading.Event()
    journal_seen_by_reader: list[bool] = []
    real_connect = sqlite3.connect
    real_validate = workpad._validate_workpad_repository

    def paused_connect(*args: object, **kwargs: object) -> sqlite3.Connection:
        connection = real_connect(*args, **kwargs)  # type: ignore[arg-type]
        if threading.current_thread().name == "cloud:gcp" and str(args[0]) == str(fx.resolved.path / "state.sqlite"):

            def trace(statement: str) -> None:
                if statement.strip().upper() == "COMMIT" and journal.exists() and not journal_open.is_set():
                    journal_open.set()
                    assert reader_validated.wait(60)

            connection.set_trace_callback(trace)
        return connection

    def observed_validate(*args: object, **kwargs: object) -> None:
        if threading.current_thread().name != "years:python" or reader_validated.is_set():
            return real_validate(*args, **kwargs)  # type: ignore[arg-type]
        journal_seen_by_reader.append(journal.exists())
        try:
            return real_validate(*args, **kwargs)  # type: ignore[arg-type]
        finally:
            reader_validated.set()

    monkeypatch.setattr(sqlite3, "connect", paused_connect)
    monkeypatch.setattr(workpad, "_validate_workpad_repository", observed_validate)
    outcomes: dict[str, object] = {}
    writer = _answer_in_thread(opts, "cloud:gcp", "GCP?", "Yes, two years.", outcomes)
    reader = _answer_in_thread(opts, "years:python", "Years of Python?", "Six.", outcomes)
    writer.start()
    assert journal_open.wait(60), "cloud:gcp never reached its state.sqlite COMMIT"
    reader.start()
    for thread in (reader, writer):
        thread.join(120)
        assert not thread.is_alive()

    assert journal_seen_by_reader == [True]  # the reader really resolved mid-commit
    _assert_both_landed_in_two_records(opts, outcomes)


@pytest.mark.parametrize("sidecar", ["state.sqlite-journal", "state.sqlite-wal", "state.sqlite-shm"])
def test_a_sqlite_sidecar_the_layout_declares_does_not_refuse_the_workpad(fx: ProfileFixtureGig, sidecar: str) -> None:
    # The v2 layout's own .gitignore declares these SQLite sidecars; one left
    # on disk (a writer mid-transaction, or a crashed one's hot journal) must
    # not make every read refuse the workpad.
    record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="Yes.")
    (fx.resolved.path / sidecar).write_bytes(b"")
    assert set(read_answers(**_opts(fx))) == {"cloud:gcp"}


def test_an_undeclared_top_level_entry_still_refuses_the_workpad(fx: ProfileFixtureGig) -> None:
    record_answer(**_opts(fx), question_id="cloud:gcp", prompt="GCP?", answer="Yes.")
    (fx.resolved.path / "state.sqlite-backup").write_bytes(b"")
    with pytest.raises(workpad.WorkpadConflictError, match="state.sqlite-backup"):
        read_answers(**_opts(fx))


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
