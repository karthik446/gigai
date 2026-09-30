"""ci-shard-suite: the CI shards are a partition of the test files.

CI runs the source suite as 4 jobs per OS x Python; tests/conftest.py keeps,
in job k, only the test files tests/support/sharding.py assigns to shard k.
A file in no shard would silently never run in CI, and a file in two would
run twice, so the partition is checked here over the real test files with
the same functions the hook calls. Pure: no process, no environment change
(the hook takes the environment as an argument), so it stays in the
fast_unit lane.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests import conftest
from tests.support import sharding

SHARDS = 4
TESTS_ROOT = sharding.REPO_ROOT / "tests"
WORKFLOW = sharding.REPO_ROOT / ".github" / "workflows" / "pull_request.yaml"


def _test_files() -> list[Path]:
    # pytest's default python_files patterns; pyproject.toml sets none.
    found = set(TESTS_ROOT.rglob("test_*.py")) | set(TESTS_ROOT.rglob("*_test.py"))
    return sorted(found)


def _items(paths) -> list[SimpleNamespace]:
    return [SimpleNamespace(path=path) for path in paths]


class _RecordingConfig:
    """The two things the hook reads from pytest's config."""

    def __init__(self) -> None:
        self.rootpath = sharding.REPO_ROOT
        self.deselected: list = []
        self.hook = SimpleNamespace(pytest_deselected=self._record)

    def _record(self, *, items) -> None:
        self.deselected.extend(items)


def _environ(shard: object, shards: object = SHARDS) -> dict[str, str]:
    return {sharding.SHARDS_ENV: str(shards), sharding.SHARD_ENV: str(shard)}


def test_every_test_file_lands_in_exactly_one_of_four_shards() -> None:
    files = _test_files()
    assert len(files) > 100, "the test files were not found"

    kept_by_shard: dict[int, list[Path]] = {}
    for shard in range(1, SHARDS + 1):
        kept, deselected = sharding.split_items(
            _items(files), shard=shard, shards=SHARDS
        )
        kept_by_shard[shard] = [item.path for item in kept]
        assert len(kept) + len(deselected) == len(files)

    union = [path for kept in kept_by_shard.values() for path in kept]
    assert len(union) == len(set(union)), "a test file is in more than one shard"
    assert set(union) == set(files), "a test file is in no shard"
    assert all(kept_by_shard.values()), "a shard has no test files"


def test_the_shard_is_the_sha1_of_the_repo_relative_posix_path() -> None:
    for path in _test_files():
        key = path.relative_to(sharding.REPO_ROOT).as_posix()
        assert sharding.repo_relative(path) == key
        expected = int(hashlib.sha1(key.encode("utf-8")).hexdigest(), 16) % SHARDS
        assert sharding.shard_of(key, SHARDS) == expected + 1
        assert sharding.in_shard(key, expected + 1, SHARDS)


def test_known_files_stay_in_their_shard() -> None:
    # Pinned: a change of hash or key moves files between CI shards, which
    # hides or exposes order dependence. That must be a deliberate edit here.
    assert sharding.shard_of("tests/scenarios/test_harness_timing.py", 4) == 1
    assert sharding.shard_of("tests/behaviors/ci_tooling/test_ci_changes.py", 4) == 2
    assert sharding.shard_of("tests/api_e2e/test_rank_journey.py", 4) == 2
    assert sharding.shard_of("tests/behaviors/ci_tooling/test_shard_partition.py", 4) == 4


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {sharding.SHARDS_ENV: "4"},
        {sharding.SHARD_ENV: "2"},
        # Not validated either: with one variable unset nothing is sharded.
        {sharding.SHARD_ENV: "not-a-number"},
    ],
)
def test_the_hook_changes_nothing_unless_both_variables_are_set(environ) -> None:
    config = _RecordingConfig()
    items = _items(_test_files())
    before = list(items)

    conftest._apply_shard(config, items, environ)

    assert items == before
    assert config.deselected == []


@pytest.mark.parametrize("shard", range(1, SHARDS + 1))
def test_the_hook_keeps_its_shard_and_reports_the_rest_deselected(shard) -> None:
    config = _RecordingConfig()
    files = _test_files()
    items = _items(files)

    conftest._apply_shard(config, items, _environ(shard))

    kept = [item.path for item in items]
    deselected = [item.path for item in config.deselected]
    assert kept == [
        path
        for path in files
        if sharding.shard_of(sharding.repo_relative(path), SHARDS) == shard
    ]
    assert sorted(kept + deselected) == files


def test_one_shard_of_one_keeps_every_file() -> None:
    config = _RecordingConfig()
    items = _items(_test_files())
    before = list(items)

    conftest._apply_shard(config, items, _environ(1, shards=1))

    assert items == before
    assert config.deselected == []


@pytest.mark.parametrize(
    ("environ", "named"),
    [
        (_environ(1, shards="four"), sharding.SHARDS_ENV),
        (_environ(1, shards=0), sharding.SHARDS_ENV),
        (_environ(1, shards=-4), sharding.SHARDS_ENV),
        (_environ(1, shards=""), sharding.SHARDS_ENV),
        (_environ("two"), sharding.SHARD_ENV),
        (_environ("1.5"), sharding.SHARD_ENV),
        (_environ(""), sharding.SHARD_ENV),
        (_environ(0), sharding.SHARD_ENV),
        (_environ(5), sharding.SHARD_ENV),
        (_environ(-1), sharding.SHARD_ENV),
    ],
)
def test_bad_shard_values_are_a_usage_error(environ, named) -> None:
    config = _RecordingConfig()
    items = _items(_test_files())
    before = list(items)

    with pytest.raises(pytest.UsageError) as raised:
        conftest._apply_shard(config, items, environ)

    assert named in str(raised.value)
    assert items == before
    assert config.deselected == []


def test_a_test_file_outside_the_repository_is_a_usage_error() -> None:
    config = _RecordingConfig()
    items = _items([Path(sharding.REPO_ROOT.anchor) / "elsewhere" / "test_x.py"])

    with pytest.raises(pytest.UsageError):
        conftest._apply_shard(config, items, _environ(1))


def test_the_workflow_sets_the_variables_the_hook_reads() -> None:
    # A renamed variable on either side would leave both unset for the hook:
    # every shard would then run the whole suite, green and four times over.
    if not WORKFLOW.is_file():
        pytest.skip(".github/workflows is excluded from the offline container build context")
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert f"      {sharding.SHARDS_ENV}: ${{{{ matrix.shards }}}}\n" in workflow
    assert f"      {sharding.SHARD_ENV}: ${{{{ matrix.shard }}}}\n" in workflow
