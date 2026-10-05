"""0.1.11 N3 (SPEC 1.4): one requirement list per posting text, with stable, content-derived ids.

No model and no GigAI home: the store is a folder under ``tmp_path`` (the project id is the only thing a home gives
it).  What is pinned: the ids (stable, folded, unique inside a list, lengthened on a collision), the list as a first
assessment's own matrix with the ``elig-`` rows left out, frozen once stored, the first writer winning a race, an
older rules version moved aside and never deleted, and what a later assessment must return.
"""

from __future__ import annotations

import json
from pathlib import Path
import threading

import pytest

from gigai.canonical import parse_json_bytes
from gigai.scout import requirements_list as rl
from gigai.scout.find_jobs.contracts import FindJobsContractError, MatrixStatus, RequirementClass, RequirementMatrixRow

POSTING = "sha256:" + "1" * 64
OTHER_POSTING = "sha256:" + "2" * 64
AT = "2026-10-05T10:00:00Z"


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(rl, "project_id", lambda _home, _target: "project_test")
    return tmp_path


def _row(requirement: str, klass: str = "askable", **more: object) -> dict[str, object]:
    return {"requirement": requirement, "class": klass, "status": "met", "resume_evidence": [], "class_basis": f"Requirements: {requirement}", **more}


MATRIX = [
    _row("8+ years of backend engineering", "hard"),
    _row("Cassandra or MongoDB", alternatives=["Cassandra", "MongoDB"]),
    _row("Authorized to work in the US", "hard", id="elig-sponsorship"),
    _row("Helm", "list_item"),
]


def _extract(matrix: list[dict[str, object]] = MATRIX, posting: str = POSTING, **who: object):
    return rl.extracted(posting, matrix, extracted_at=AT, prompt_version="assess-prompt-v9", model="fixture-model", profile_id="profile_1", **who)


# --- the ids ---------------------------------------------------------------------------------------------------


def test_an_id_is_derived_from_the_posting_and_the_folded_requirement_text() -> None:
    first = rl.assign_ids(POSTING, ["Kubernetes in production", "Go"])
    assert all(row_id.startswith("req-") and len(row_id) == 10 and int(row_id[4:], 16) >= 0 for row_id in first)
    # Stable: the same words give the same id, whatever stands beside them, their case or their spacing.
    assert rl.assign_ids(POSTING, ["Go", "  kubernetes   IN production "]) == (first[1], first[0])
    assert rl.assign_ids(POSTING, ["Kubernetes in production"]) == first[:1]
    # Another posting text is another list: the same words have another id there.
    assert rl.assign_ids(OTHER_POSTING, ["Kubernetes in production"]) != first[:1]
    assert rl.assign_ids(POSTING, ["Kubernetes in prod"]) != first[:1]


def test_the_same_words_twice_in_one_matrix_get_two_ids() -> None:
    ids = rl.assign_ids(POSTING, ["Go", "Go", "go"])
    assert len(set(ids)) == 3 and ids[0] == rl.assign_ids(POSTING, ["Go"])[0]


def test_an_id_is_lengthened_on_a_collision_inside_the_list() -> None:
    # 16**6 six-character prefixes: among a few thousand texts two share one (found, not assumed).
    seen: dict[str, str] = {}
    pair: tuple[str, str] | None = None
    for number in range(200_000):
        text = f"requirement number {number}"
        prefix = rl.assign_ids(POSTING, [text])[0]
        if prefix in seen:
            pair = (seen[prefix], text)
            break
        seen[prefix] = text
    assert pair is not None, "no two of 200,000 texts share a six-character id"
    alone = rl.assign_ids(POSTING, [pair[0]])[0]
    together = rl.assign_ids(POSTING, [*pair, "Go"])
    assert len(set(together)) == 3
    assert together[0].startswith(alone) and len(together[0]) > len(alone) and len(together[1]) > len(alone)
    assert len(together[2]) == len(alone)  # a row that collides with nothing keeps its six characters


# --- extraction ------------------------------------------------------------------------------------------------


def test_a_first_assessments_matrix_becomes_the_list_and_the_rows_about_the_candidate_stay_out() -> None:
    found, row_ids = _extract()
    assert [row.requirement for row in found.rows] == ["8+ years of backend engineering", "Cassandra or MongoDB", "Helm"]
    assert row_ids[2] == "elig-sponsorship" and [row_ids[0], row_ids[1], row_ids[3]] == list(found.ids())
    assert found.rows[1].alternatives == ("Cassandra", "MongoDB") and found.rows[1].class_basis == "Requirements: Cassandra or MongoDB"
    assert [row.requirement_class for row in found.rows] == ["hard", "askable", "list_item"]
    stored = found.to_json()
    assert stored["schema_version"] == "scout-posting-requirements:1" and stored["rules_version"] == rl.RULES_VERSION == "req-rules:1"
    assert stored["extracted_by"] == {"prompt_version": "assess-prompt-v9", "model": "fixture-model", "profile_id": "profile_1"}
    assert stored["digest"] == found.digest == rl.rows_digest(found.rows) and stored["posting_sha256"] == POSTING
    assert rl.PostingRequirements.from_json(parse_json_bytes(json.dumps(stored).encode())) == found
    # The stored contract's rows give the same list (a stored assessment read back).
    typed = [
        RequirementMatrixRow(row["requirement"], (), MatrixStatus.MET, RequirementClass(row["class"]), id=row.get("id"), class_basis=row["class_basis"],
                             alternatives=tuple(row.get("alternatives", ())))
        for row in MATRIX
    ]
    assert rl.extracted(POSTING, typed, extracted_at=AT, prompt_version="assess-prompt-v9", model="fixture-model", profile_id="profile_1") == (found, row_ids)
    # Extracted again from the same words in another order, under another profile: the same ids (another digest: the order is the list's).
    again, _ids = rl.extracted(POSTING, list(reversed(MATRIX)), extracted_at="2027-01-01T00:00:00Z", profile_id="profile_2")
    assert set(again.ids()) == set(found.ids())


def test_a_list_that_was_changed_by_hand_is_not_read() -> None:
    stored = _extract()[0].to_json()
    stored["rows"][0]["requirement"] = "10+ years"
    with pytest.raises(rl.RequirementsListError):
        rl.PostingRequirements.from_json(stored)


# --- the store: frozen, first writer wins ------------------------------------------------------------------------


def test_the_list_is_stored_once_and_never_changes(home: Path) -> None:
    mine, _ids = _extract()
    assert rl.read_list(home, home, POSTING) is None
    ref = rl.settle_first(home, home, mine)
    path = rl.requirements_path(home, home, POSTING)
    assert path.parent == home / "scout" / "project_test" / "requirements" and path.is_file()
    assert ref.to_json() == {"posting_sha256": POSTING, "rules_version": "req-rules:1", "digest": mine.digest, "rows": 3, "list": "stored"}
    written = path.read_bytes()
    assert rl.read_list(home, home, POSTING) == mine and rl.read_list(home, home, OTHER_POSTING) is None
    # A second first assessment that extracted OTHER rows keeps its own; the file is byte for byte what it was.
    other, _ids = _extract([*MATRIX, _row("Terraform")])
    late = rl.settle_first(home, home, other)
    assert late.kind == "own" and late.rows_digest == other.digest != mine.digest and path.read_bytes() == written
    assert not rl.comparable(ref, late) and rl.comparable(ref, mine.ref()) and not rl.comparable(ref, None)
    # One that extracted the very same rows IS the stored list (the ids are content-derived).
    same, _ids = rl.extracted(POSTING, MATRIX, extracted_at="2027-01-01T00:00:00Z", profile_id="profile_2")
    assert rl.settle_first(home, home, same).kind == "stored" and path.read_bytes() == written
    # Another posting text is another file.
    elsewhere, _ids = _extract(posting=OTHER_POSTING)
    assert rl.settle_first(home, home, elsewhere).kind == "stored"
    assert len(list(path.parent.glob("*.json"))) == 2


def test_two_first_assessments_at_once_the_first_to_write_wins(home: Path) -> None:
    lists = [_extract([*MATRIX, _row(f"Extra requirement {number}")])[0] for number in range(8)]
    barrier = threading.Barrier(len(lists))
    refs: list[object] = [None] * len(lists)

    def write(index: int) -> None:
        barrier.wait(timeout=30)
        refs[index] = rl.settle_first(home, home, lists[index])

    threads = [threading.Thread(target=write, args=(index,)) for index in range(len(lists))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    kinds = [ref.kind for ref in refs]  # type: ignore[union-attr]
    assert kinds.count("stored") == 1 and kinds.count("own") == len(lists) - 1
    winner = lists[kinds.index("stored")]
    assert rl.read_list(home, home, POSTING) == winner
    assert [path.name for path in rl.requirements_dir(home, home).iterdir()] == [rl.requirements_path(home, home, POSTING).name]  # no lock file, no temp file


def test_a_list_of_an_older_rules_version_is_moved_aside_and_kept(home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    old, _ids = _extract()
    rl.settle_first(home, home, old)
    path = rl.requirements_path(home, home, POSTING)
    before = path.read_bytes()
    monkeypatch.setattr(rl, "RULES_VERSION", "req-rules:2")
    assert rl.read_list(home, home, POSTING) is None  # extracted under other rules: not the list of these
    new, _ids = _extract([*MATRIX, _row("Terraform")])
    assert new.rules_version == "req-rules:2" and rl.settle_first(home, home, new).kind == "stored"
    assert rl.read_list(home, home, POSTING) == new
    aside = path.with_name(f"{path.stem}.req-rules-1.json")
    assert aside.read_bytes() == before  # never deleted
    # A row whose words did not change keeps its id under the new rules.
    assert set(old.ids()) <= set(new.ids())


# --- a later assessment ---------------------------------------------------------------------------------------------


def test_a_later_matrix_must_hold_exactly_the_listed_ids() -> None:
    listed = _extract()[0].rows
    first, second, third = (row.id for row in listed)
    rows = [{"id": row.id, "requirement": "the model's words", "class": "nice_to_have", "status": "met", "resume_evidence": []} for row in listed]
    checked = rl.check_listed([*rows, {"id": "elig-location", "requirement": "Remote in the US", "class": "hard", "status": "met", "resume_evidence": []}], listed)
    assert [row["requirement"] for row in checked] == ["8+ years of backend engineering", "Cassandra or MongoDB", "Helm", "Remote in the US"]
    assert [row["class"] for row in checked] == ["hard", "askable", "list_item", "hard"]
    assert checked[1]["alternatives"] == ["Cassandra", "MongoDB"] and "alternatives" not in checked[0]
    assert all("class_from" not in row for row in checked)
    # ``hard`` where the list says ``askable``: the candidate's own facts disclaim it. Kept, and said.
    disclaimed = rl.check_listed([rows[0], {**rows[1], "class": "hard"}, rows[2]], listed)
    assert (disclaimed[1]["class"], disclaimed[1]["class_from"]) == ("hard", "disclaimer")
    for wrong, said in (
        (rows[:2], f"1 is missing: {third}"),
        ([*rows, {**rows[0], "id": "req-ffffff"}], "1 is not in the list: req-ffffff"),
        ([*rows, rows[0]], f"1 is repeated: {first}"),
        ([rows[0], rows[2], {key: value for key, value in rows[1].items() if key != "id"}], f"1 is missing: {second}; 1 row carries no id"),
        ([*rows, {"id": "elig-region", "requirement": "x"}, {"id": "elig-region", "requirement": "y"}], "1 is repeated: elig-region"),
    ):
        with pytest.raises(FindJobsContractError) as refused:
            rl.check_listed(wrong, listed)
        message = str(refused.value)
        assert message.startswith("matrix must hold exactly the 3 listed requirement ids, each once; ") and said in message, message
        assert message.index(said) < 300  # what the model boundary feeds back on the one retry


def test_the_prompt_block_is_one_row_a_line() -> None:
    found = _extract()[0]
    lines = found.prompt_block().splitlines()
    assert len(lines) == 3 and lines[0] == f"{found.rows[0].id} | hard | 8+ years of backend engineering | Requirements: 8+ years of backend engineering"
    assert lines[1] == f"{found.rows[1].id} | askable | Cassandra or MongoDB (any one of: Cassandra, MongoDB) | Requirements: Cassandra or MongoDB"
