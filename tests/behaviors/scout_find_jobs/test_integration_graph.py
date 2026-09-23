"""I-1: the sealed ``find-jobs:functional:1`` graph and widened graph-set ceiling.

Amendment 02 (P2-FREEZE-04) D11 and the find-jobs-functional roadmap's I-1
row require ``scout_materialization.py`` to author one linear
acquire -> assess -> present graph bound to the three
``scout.find_jobs.*`` capabilities, and to widen only this graph's
descriptor ceiling (plus the Gig-wide ``shared_policy``, which is the only
ceiling ``graph_set.py`` enforces) so the existing validators accept it,
while every other Scout graph's compiled bytes stay unchanged.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime
from unittest import mock

import pytest

import gigai.scout.materialization as scout_materialization
from gigai.canonical import canonical_json_bytes, canonical_json_digest, digest_imported_bytes, parse_json_bytes
from gigai.graph_set import validate_graph_set
from gigai.scout.find_jobs.contracts import (
    ACQUIRE_CAPABILITY,
    ACQUIRE_DECLARED_EFFECTS,
    ASSESS_CAPABILITY,
    ASSESS_DECLARED_EFFECTS,
    ModelTarget,
    PRESENT_CAPABILITY,
    PRESENT_DECLARED_EFFECTS,
)
from gigai.scout.materialization import _FIND_JOBS_FUNCTIONAL_SELECTOR, _compiled_snapshot
from gigai.scout.template import scout_source_files
from gigai.validators import validate_goal_graph, validate_serialized_contract


_GIG_ID = "gig_00000000-0000-4000-8000-000000000099"


class _FrozenDatetime(datetime):
    """Freeze ``datetime.now`` so two ``_compiled_snapshot`` calls agree."""

    _fixed = datetime(2026, 9, 23, 12, 0, 0, tzinfo=UTC)

    @classmethod
    def now(cls, tz=None):  # noqa: D102 - trivial override
        return cls._fixed


def _replay_uuid_factory(seed_sequence: list[uuid.UUID]):
    iterator = iter(seed_sequence)
    return lambda: next(iterator)


@pytest.fixture(scope="module")
def _seed_uuids() -> list[uuid.UUID]:
    return [uuid.uuid4() for _ in range(200)]


def _snapshot(uuid_factory) -> tuple[dict[str, bytes], tuple[str, ...]]:
    source = dict(scout_source_files())
    with mock.patch.object(scout_materialization, "datetime", _FrozenDatetime):
        return _compiled_snapshot(gig_id=_GIG_ID, source=source, uuid_factory=uuid_factory)


def _graph_set_definition(compiled: dict[str, bytes]) -> dict[str, object]:
    definition = parse_json_bytes(compiled["compiled/first-graph-set-definition.json"])
    assert isinstance(definition, dict)
    return definition


def _find_jobs_functional_descriptor(definition: dict[str, object]) -> dict[str, object]:
    for descriptor in definition["graphs"]:
        if descriptor["graph_id"] == _FIND_JOBS_FUNCTIONAL_SELECTOR:
            return descriptor
    raise AssertionError("find-jobs-functional descriptor is missing")


def _sealed_graph_set(definition: dict[str, object]) -> dict[str, object]:
    """Add the fields ``propose_graph_set_offline`` would seal at approval time."""

    return {
        "schema_version": "1.0",
        "graph_set_id": "graph_set_00000000-0000-4000-8000-000000000001",
        "gig_id": definition["gig_id"],
        "graphs": definition["graphs"],
        "shared_policy": definition["shared_policy"],
        "created_at": "2026-09-23T12:00:00Z",
        "created_by": {"kind": "operator", "id": "local-user"},
    }


def test_find_jobs_functional_goal_graph_is_schema_and_semantically_valid(_seed_uuids: list[uuid.UUID]) -> None:
    compiled, _goal_ids = _snapshot(_replay_uuid_factory(_seed_uuids))
    definition = _graph_set_definition(compiled)
    descriptor = _find_jobs_functional_descriptor(definition)
    graph_bytes = compiled[f"compiled/{_FIND_JOBS_FUNCTIONAL_SELECTOR}/goal-graph.json"]
    graph = parse_json_bytes(graph_bytes)
    assert isinstance(graph, dict)

    schema_report = validate_serialized_contract("goal-graph.schema.json", graph_bytes)
    assert schema_report.valid, schema_report.findings

    semantic_report = validate_goal_graph(graph)
    assert semantic_report.valid, semantic_report.findings

    assert graph["gig_id"] == _GIG_ID
    assert graph["failure_policy"] == "fail_gig"
    assert graph["aggregate_budget"]["max_parallel_goals"] == 1

    slugs = [goal["slug"] for goal in graph["goals"]]
    assert slugs == ["acquire", "assess", "present"]

    goals_by_slug = {goal["slug"]: goal for goal in graph["goals"]}
    assert goals_by_slug["acquire"]["executor"]["capability"] == ACQUIRE_CAPABILITY
    assert goals_by_slug["assess"]["executor"]["capability"] == ASSESS_CAPABILITY
    assert goals_by_slug["present"]["executor"]["capability"] == PRESENT_CAPABILITY
    for slug in ("acquire", "assess", "present"):
        assert goals_by_slug[slug]["executor"]["kind"] == "local_capability"
        assert goals_by_slug[slug]["activation"] == "automatic"
        assert goals_by_slug[slug]["outcomes"] == ["COMPLETE"]

    assert goals_by_slug["acquire"]["effects"] == sorted(ACQUIRE_DECLARED_EFFECTS)
    assert goals_by_slug["assess"]["effects"] == sorted(ASSESS_DECLARED_EFFECTS)
    assert goals_by_slug["present"]["effects"] == sorted(PRESENT_DECLARED_EFFECTS)

    # Exactly two automatic COMPLETE dependency edges: acquire->assess->present.
    assert len(graph["edges"]) == 2
    acquire_id = goals_by_slug["acquire"]["goal_id"]
    assess_id = goals_by_slug["assess"]["goal_id"]
    present_id = goals_by_slug["present"]["goal_id"]
    edge_pairs = {(edge["from_goal_id"], edge["to_goal_id"]) for edge in graph["edges"]}
    assert edge_pairs == {(acquire_id, assess_id), (assess_id, present_id)}
    for edge in graph["edges"]:
        assert edge["kind"] == "dependency"
        assert edge["automatic"] is True
        assert edge["on_outcomes"] == ["COMPLETE"]

    assert graph["entry_goal_ids"] == [acquire_id]
    assert graph["terminal_goal_ids"] == [present_id]

    # The descriptor's goal_graph artifact ref matches the compiled bytes.
    assert descriptor["goal_graph"]["content_sha256"] == digest_imported_bytes(graph_bytes)
    assert descriptor["goal_graph"]["size_bytes"] == len(graph_bytes)


def test_find_jobs_functional_descriptor_widens_only_this_graph(_seed_uuids: list[uuid.UUID]) -> None:
    compiled, _goal_ids = _snapshot(_replay_uuid_factory(_seed_uuids))
    definition = _graph_set_definition(compiled)
    descriptor = _find_jobs_functional_descriptor(definition)

    assert descriptor["effect_policy"] == sorted(ACQUIRE_DECLARED_EFFECTS | ASSESS_DECLARED_EFFECTS | PRESENT_DECLARED_EFFECTS)
    assert descriptor["capability_requirements"] == sorted({ACQUIRE_CAPABILITY, ASSESS_CAPABILITY, PRESENT_CAPABILITY})
    assert descriptor["provider_eligibility"] == {
        "providers": sorted({ModelTarget.OLLAMA_LOCAL.value, ModelTarget.CODEX_CLI.value, ModelTarget.OPENROUTER_API.value})
    }

    # Every other descriptor keeps today's narrow ceiling, unwidened.
    other_descriptors = [g for g in definition["graphs"] if g["graph_id"] != _FIND_JOBS_FUNCTIONAL_SELECTOR]
    assert len(other_descriptors) == 6
    for other in other_descriptors:
        assert other["effect_policy"] == ["write_workpad"]
        assert other["capability_requirements"] == ["gigai.offline"]
        assert other["provider_eligibility"] == {"providers": ["deterministic"]}

    # The shared_policy is the Gig-wide ceiling graph_set.py actually checks;
    # it must be widened to admit the new graph while still covering every
    # existing (unwidened) descriptor.
    shared = definition["shared_policy"]
    assert set(descriptor["effect_policy"]).issubset(set(shared["effects"]))
    assert set(descriptor["capability_requirements"]).issubset(set(shared["required_capability_ids"]))
    assert set(descriptor["provider_eligibility"]["providers"]).issubset(set(shared["provider_eligibility"]["providers"]))
    for other in other_descriptors:
        assert set(other["effect_policy"]).issubset(set(shared["effects"]))
        assert set(other["capability_requirements"]).issubset(set(shared["required_capability_ids"]))


def test_graph_set_validator_accepts_the_widened_definition(_seed_uuids: list[uuid.UUID]) -> None:
    compiled, _goal_ids = _snapshot(_replay_uuid_factory(_seed_uuids))
    definition = _graph_set_definition(compiled)
    graph_set = _sealed_graph_set(definition)

    report = validate_graph_set(canonical_json_bytes(graph_set))
    assert report.valid, report.findings


def test_new_goal_ids_are_appended_for_the_capability_manifest(_seed_uuids: list[uuid.UUID]) -> None:
    compiled, goal_ids = _snapshot(_replay_uuid_factory(_seed_uuids))
    definition = _graph_set_definition(compiled)
    graph_bytes = compiled[f"compiled/{_FIND_JOBS_FUNCTIONAL_SELECTOR}/goal-graph.json"]
    graph = parse_json_bytes(graph_bytes)
    functional_goal_ids = {goal["goal_id"] for goal in graph["goals"]}
    assert functional_goal_ids.issubset(set(goal_ids))
    assert len(goal_ids) == len(set(goal_ids))


def _deterministic_uuid4_sequence(seed: int, count: int) -> list[uuid.UUID]:
    """A reproducible UUIDv4 sequence, independent of git history or paths.

    ``generate_entity_id`` requires real UUIDv4-shaped values, so a plain
    counter or namespace UUID will not do; seeding ``random.Random`` and
    forcing the version nibble keeps the sequence exactly reproducible while
    satisfying that shape check.
    """

    rng = random.Random(seed)
    return [uuid.UUID(int=rng.getrandbits(128), version=4) for _ in range(count)]


# Pinned pre-I-1 golden digests for every compiled artifact that I-1 declared
# must stay byte-identical (all six pre-existing Scout graphs, plus the two
# Gig-wide compiled files that do not carry the new graph): everything under
# ``compiled/`` except the ``find-jobs-functional`` graph's own artifacts and
# ``compiled/first-graph-set-definition.json`` (which legitimately gains one
# more descriptor and is checked separately below).
#
# Provenance: this is not a fresh claim of "byte-identical" — it is the
# pinned result of the ORIGINAL git-history-diffing regression proof
# (`test_existing_scout_graphs_are_byte_identical_to_pre_change_output`,
# I-1's `.orchestrator/runs/v0.1.8/workers/i1-graph.md`) re-run with this
# same fixed, deterministic UUID sequence and cross-checked byte-for-byte
# against `git show HEAD:src/gigai/scout_materialization.py` (the pre-reorg
# commit) during the 2026-09-23 `src/gigai/scout/` package re-org
# (`.orchestrator/workers/scout-reorg.md`). That cross-check confirmed these
# digests equal the pre-I-1 baseline; pinning them here removes the git/path
# dependency the original proof carried, per the operator's 2026-09-23
# reorg-follow-up decision to convert this test rather than retire it.
_PINNED_UUID_SEED = 20260923
_PINNED_UUID_COUNT = 200
_PINNED_UNCHANGED_ARTIFACT_DIGESTS = {
    "compiled/creation-manifest.json": "sha256:3a903f815f6675864e3f74803ee97b6a57dade956b3c27fe447e248ed5a49a37",
    "compiled/gig.md": "sha256:edcf7600f7a7d9842d8e22f7e508e7c54ada7824c512ac471014905ac0041e74",
    "compiled/find-jobs/completion_evidence_contract.json": "sha256:7aba854974c7a0350333fb7868220d760a8c6246617cac1b50f1009e46f7e64f",
    "compiled/find-jobs/evaluation_contract.json": "sha256:a74fe34131b81aee03bdcc62a3d3ed0872c47f8c88aac332e5cf6ab316b3e3b1",
    "compiled/find-jobs/goal-contract.md": "sha256:1aba2493f8d70178b43355ced6383b6eb6e1c45c1a32c8747e4c0c730c91b8c7",
    "compiled/find-jobs/goal-graph.json": "sha256:0b8487c03cf31fea3c5b9d7116174410574738ea91c29c192695c2c8627c198d",
    "compiled/find-jobs/input_contract.json": "sha256:e9c038a2c50f7f6696b26fff9da569e224cdd704034a04e34a0b2db342f308fc",
    "compiled/find-jobs/output_contract.json": "sha256:ceca9172552e04b3233e26234d2f411e003f006a8973a4981c4eb724674bed2c",
    "compiled/find-jobs/permitted_reference_contract.json": "sha256:83f2fbe69b86c6b3c2dc68be59622776d1bc75a238aea6b1a165234e8ee594c3",
    "compiled/find-jobs/review-contract.json": "sha256:98d5b200b865382f540756a695793562c2bf2135f69589d00c7eaac9b50973ab",
    "compiled/prepare-interview/completion_evidence_contract.json": "sha256:842328d107e2ced481b6e108023653394d156cb52565f4e7c5b3f51a6697408f",
    "compiled/prepare-interview/evaluation_contract.json": "sha256:a74fe34131b81aee03bdcc62a3d3ed0872c47f8c88aac332e5cf6ab316b3e3b1",
    "compiled/prepare-interview/goal-contract.md": "sha256:8ffb10410ea92f8630a010b1f4daaa4a54c333ff26d434c8c34cd899f751bb9f",
    "compiled/prepare-interview/goal-graph.json": "sha256:fe07925aa0432020463578e3a8e89d31405a034551c2741a648bff2ef20c44b6",
    "compiled/prepare-interview/input_contract.json": "sha256:87735398dac9a90cd69c1b11ff7c16dd66f2e64d8f398cc6f9d7a3bd00909551",
    "compiled/prepare-interview/output_contract.json": "sha256:d7c523023eb732b2e74d9a17925668def17841cf8a68b49e412b549343e04ff5",
    "compiled/prepare-interview/permitted_reference_contract.json": "sha256:83f2fbe69b86c6b3c2dc68be59622776d1bc75a238aea6b1a165234e8ee594c3",
    "compiled/prepare-interview/review-contract.json": "sha256:545e8dd2a89bc6ddff7ff8c68178e790c3f78bfc006085db02acb9380ccb0edc",
    "compiled/proposal-assessment/completion_evidence_contract.json": "sha256:ded195e9d760b93da97506814913da82703b0ccdb27e0869a94a9d768ca78de1",
    "compiled/proposal-assessment/evaluation_contract.json": "sha256:a74fe34131b81aee03bdcc62a3d3ed0872c47f8c88aac332e5cf6ab316b3e3b1",
    "compiled/proposal-assessment/goal-contract.md": "sha256:f2c7c6e0a40517b14523c6aa4c2ed462ac3bbf2bce23d88736ec8f5868360dba",
    "compiled/proposal-assessment/goal-graph.json": "sha256:9e0a61a98f2fa0f0795d8b38de781c923fd935f6203e4faa0675c296bbd9ee6e",
    "compiled/proposal-assessment/input_contract.json": "sha256:03c11bf784ffd1277b2250c822c9d20a24a53e529eb3c334c67901b2adb42649",
    "compiled/proposal-assessment/output_contract.json": "sha256:9e819c5201373ff30bc532a9d705120d583e0bd1b01666d96aa999f80769756a",
    "compiled/proposal-assessment/permitted_reference_contract.json": "sha256:83f2fbe69b86c6b3c2dc68be59622776d1bc75a238aea6b1a165234e8ee594c3",
    "compiled/proposal-assessment/review-contract.json": "sha256:ca813d442dd2f478efec1e789f1a1c4d2d47eeb73fd69db554f56216b0859bc6",
    "compiled/record-application/completion_evidence_contract.json": "sha256:cc31a199ffb437fd1903ec6d735237343b18955e61d3f3c2d54f20d71408dde0",
    "compiled/record-application/evaluation_contract.json": "sha256:a74fe34131b81aee03bdcc62a3d3ed0872c47f8c88aac332e5cf6ab316b3e3b1",
    "compiled/record-application/goal-contract.md": "sha256:69b1d8aa2ab7a3c9d7c934aa3d6978514323e2f2d077c3aadf26a8ab5b1cf9d9",
    "compiled/record-application/goal-graph.json": "sha256:72d5493e79e75fecf47d0e8f9ed605b0b0291f4b45f9246e8b63e0f63fac83d2",
    "compiled/record-application/input_contract.json": "sha256:114866287090a364ef6bb0718464895653e161ddedac196f235d1d368ccee1da",
    "compiled/record-application/output_contract.json": "sha256:e63c6681fa14605c6a96d5e3e3cc670198f81b58dac1f96358991d9f7091bb8b",
    "compiled/record-application/permitted_reference_contract.json": "sha256:83f2fbe69b86c6b3c2dc68be59622776d1bc75a238aea6b1a165234e8ee594c3",
    "compiled/record-application/review-contract.json": "sha256:ae5ae95617c674265441582b676042a249bce16ec0d14cd5367eff56e5a973a5",
    "compiled/research-role/completion_evidence_contract.json": "sha256:4617986b047a93a0e631bfa072b8baf0b006268ecd7047e7cc55d320a85c9a1f",
    "compiled/research-role/evaluation_contract.json": "sha256:a74fe34131b81aee03bdcc62a3d3ed0872c47f8c88aac332e5cf6ab316b3e3b1",
    "compiled/research-role/goal-contract.md": "sha256:aa665c5739a0a883ab3d73068294e6f663553948292bb189b99814cf5cd4974d",
    "compiled/research-role/goal-graph.json": "sha256:883e3f7ebd9ea5442f6456dca3fa25ea9c8fae6cc4549b111623fdcad020e393",
    "compiled/research-role/input_contract.json": "sha256:a1dc0c920a7d1306e93312bd60af8ecbbb1bca8d735c1b164ee0b568dfb09e84",
    "compiled/research-role/output_contract.json": "sha256:8a45c91eb165a92f9aa42f7db89b7e02052ca5af9451ab1ef285994f7a14e8fa",
    "compiled/research-role/permitted_reference_contract.json": "sha256:83f2fbe69b86c6b3c2dc68be59622776d1bc75a238aea6b1a165234e8ee594c3",
    "compiled/research-role/review-contract.json": "sha256:5a951bade1d3d898d45879a712ebd457e7379ad7f869829e71fc5290ca752e22",
    "compiled/tailor-application/completion_evidence_contract.json": "sha256:dd535786c6af289a117e52dd6d987a9a6a773730509ba7116cc17bdb90e8f6f6",
    "compiled/tailor-application/evaluation_contract.json": "sha256:a74fe34131b81aee03bdcc62a3d3ed0872c47f8c88aac332e5cf6ab316b3e3b1",
    "compiled/tailor-application/goal-contract.md": "sha256:216aa989ff9d9081f5608b5b782843db2e10676f170effad1eaf6a927d79e01f",
    "compiled/tailor-application/goal-graph.json": "sha256:ca53bf661ef709facbfa26fe2dda780030100dd25eaddba2b0e7d17e3e502c49",
    "compiled/tailor-application/input_contract.json": "sha256:9e0fd7c6afa7115fcb9b4f6047368775247203d87cb75b1e6e1087c4723ac973",
    "compiled/tailor-application/output_contract.json": "sha256:2b7c0a9e28d48274867f172401fc8ead1fcb0f8d69af3a20bd1e90b07eb418c5",
    "compiled/tailor-application/permitted_reference_contract.json": "sha256:83f2fbe69b86c6b3c2dc68be59622776d1bc75a238aea6b1a165234e8ee594c3",
    "compiled/tailor-application/review-contract.json": "sha256:56c16b6f9d5995a1c5b0dad837ce8dedfe9d9062b8cce7fe5047de79916e8015",
}

# Same provenance as above: the six pre-existing descriptors' canonical
# identity within the compiled graph-set definition (order preserved,
# `find-jobs-functional` appended last), and the definition's Gig-wide
# fields that must not shift when a graph is added.
_PINNED_OTHER_DESCRIPTORS_DIGEST = "sha256:5949961a8f5707eb4c45e9ee2ff07588a5b4712bfc883d3ddf1d5477dc948747"
_PINNED_DEFINITION_FIELD_DIGESTS = {
    "gig_document": "sha256:1433808f409ef30c15d8241dd1b78db0ab1403cc70a11ed66c060714260a97df",
    "creation_manifest": "sha256:798131d88225fa1c5cc50bd4b9c9daa3878a45bcb2e2348fab3d7ccd91bf29ee",
}
_PINNED_DEFINITION_LITERAL_FIELDS = {
    "name": "scout",
    "commission": "Prepare the bundled Scout candidate for direct operator approval; no Run or tool execution is authorized.",
    "schema_version": "1.0",
    "gig_id": _GIG_ID,
}


def test_existing_scout_graphs_match_pinned_pre_i1_digests() -> None:
    """The six pre-existing Scout graphs still match their pinned pre-I-1 digests.

    This replaces a prior git-history-diffing version of this test
    (``test_existing_scout_graphs_are_byte_identical_to_pre_change_output``)
    that shelled out to ``git show HEAD:src/gigai/scout_materialization.py``
    and re-ran that historical module under an isolated name. That mechanism
    is inherently tied to one specific commit and cannot survive a module
    rename (it broke immediately when ``src/gigai/scout/`` was physically
    reorganized on 2026-09-23; see ``.orchestrator/workers/scout-reorg.md``).
    Its green run before that move already proved these six graphs' compiled
    artifacts are byte-identical to pre-I-1 HEAD, so the digests pinned below
    ARE that verified baseline — carried forward with no git or path
    dependency, per the operator's decision to pin rather than retire this
    protection.
    """

    uuid_factory = _replay_uuid_factory(_deterministic_uuid4_sequence(_PINNED_UUID_SEED, _PINNED_UUID_COUNT))
    compiled, _goal_ids = _snapshot(uuid_factory)

    unchanged_keys = {
        key for key in compiled
        if not key.startswith(f"{_FIND_JOBS_FUNCTIONAL_SELECTOR}/")
        and not key.startswith(f"compiled/{_FIND_JOBS_FUNCTIONAL_SELECTOR}/")
        and key != "compiled/first-graph-set-definition.json"
    }
    assert unchanged_keys == set(_PINNED_UNCHANGED_ARTIFACT_DIGESTS), (
        unchanged_keys ^ set(_PINNED_UNCHANGED_ARTIFACT_DIGESTS)
    )
    mismatches = {
        key: digest_imported_bytes(compiled[key])
        for key in unchanged_keys
        if digest_imported_bytes(compiled[key]) != _PINNED_UNCHANGED_ARTIFACT_DIGESTS[key]
    }
    assert mismatches == {}, f"pinned pre-I-1 digest guarantee violated for: {mismatches}"

    definition = _graph_set_definition(compiled)
    other_descriptors = [g for g in definition["graphs"] if g["graph_id"] != _FIND_JOBS_FUNCTIONAL_SELECTOR]
    assert canonical_json_digest(other_descriptors) == _PINNED_OTHER_DESCRIPTORS_DIGEST
    # find-jobs-functional is appended last; the six original descriptors keep their order.
    assert [g["graph_id"] for g in definition["graphs"][:-1]] == [g["graph_id"] for g in other_descriptors]
    assert definition["graphs"][-1]["graph_id"] == _FIND_JOBS_FUNCTIONAL_SELECTOR

    for field, expected_digest in _PINNED_DEFINITION_FIELD_DIGESTS.items():
        assert canonical_json_digest(definition[field]) == expected_digest
    for field, expected_value in _PINNED_DEFINITION_LITERAL_FIELDS.items():
        assert definition[field] == expected_value
