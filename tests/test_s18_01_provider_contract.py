from __future__ import annotations

from copy import deepcopy

from research.s18_01.matrix import (
    CANDIDATE_FAMILIES,
    CANDIDATE_MATRIX,
    COMMON_FIELDS,
    REPLAY_STABLE_FIELDS,
    REPLAY_VARIABLE_FIELDS,
    build_replay_fixture,
    validate_matrix,
)


def test_matrix_covers_all_candidates_without_support_claims() -> None:
    validate_matrix()
    assert set(CANDIDATE_MATRIX) == set(CANDIDATE_FAMILIES)
    assert all(set(COMMON_FIELDS) <= set(entry) for entry in CANDIDATE_MATRIX.values())
    assert all(entry["decision"] != "supported" for entry in CANDIDATE_MATRIX.values())


def test_replay_fixture_has_stable_and_variable_boundaries() -> None:
    fixture = build_replay_fixture()
    assert fixture["stable_digest"]
    assert set(fixture["variable"]) == set(REPLAY_VARIABLE_FIELDS)
    assert set(REPLAY_STABLE_FIELDS).isdisjoint(REPLAY_VARIABLE_FIELDS)
    assert fixture["result"]["cost_status"] == "unavailable"
    assert "secret" not in repr(fixture).lower()


def test_matrix_mutations_are_rejected() -> None:
    missing = deepcopy(CANDIDATE_MATRIX)
    del missing["anthropic_api"]["finish"]
    try:
        validate_matrix(missing)
    except ValueError as error:
        assert "missing common fields" in str(error)
    else:
        raise AssertionError("missing common field mutation was not caught")

    support_claim = deepcopy(CANDIDATE_MATRIX)
    support_claim["codex_cli"]["decision"] = "supported"
    try:
        validate_matrix(support_claim)
    except ValueError as error:
        assert "advertised as supported" in str(error)
    else:
        raise AssertionError("support-claim mutation was not caught")
