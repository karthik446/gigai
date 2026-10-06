"""tools/ci_select_tests.py: a push's changed paths -> the test files that import what changed.

Synthetic repository under tmp_path; no git, no network."""

from __future__ import annotations

from pathlib import Path

from tools import ci_select_tests


def _write(root: Path, rel: str, text: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _repo(tmp_path: Path) -> ci_select_tests.Graph:
    _write(tmp_path, "src/gigai/__init__.py")
    _write(tmp_path, "src/gigai/store.py", "VALUE = 1\n")
    _write(tmp_path, "src/gigai/reader.py", "from .store import VALUE\n")
    _write(tmp_path, "src/gigai/other.py", "OTHER = 2\n")
    _write(tmp_path, "src/gigai/data/prompt.md", "words\n")
    _write(tmp_path, "src/gigai/loader.py", "TEXT = open('data/prompt.md').read()\n")
    _write(tmp_path, "tests/__init__.py")
    _write(tmp_path, "tests/support/__init__.py")
    _write(tmp_path, "tests/support/helper.py", "from gigai import store\n")
    _write(tmp_path, "tests/test_store.py", "def test_a():\n    from gigai.store import VALUE\n")
    _write(tmp_path, "tests/test_via_helper.py", "from tests.support import helper\n")
    _write(tmp_path, "tests/test_reader.py", "from gigai import reader\n")
    _write(tmp_path, "tests/test_loader.py", "from gigai import loader\n")
    _write(tmp_path, "tests/test_other.py", "from gigai import other\n")
    return ci_select_tests.Graph(tmp_path)


def test_a_source_module_selects_the_tests_that_import_it_directly_and_through_a_helper(tmp_path: Path) -> None:
    graph = _repo(tmp_path)

    selected = graph.select(["src/gigai/store.py"], "direct")

    assert selected == ["tests/test_store.py", "tests/test_via_helper.py"]  # test_reader imports a module that imports it: one hop only


def test_a_changed_test_selects_itself_and_docs_select_nothing(tmp_path: Path) -> None:
    graph = _repo(tmp_path)

    assert graph.select(["tests/test_other.py", "docs/x.md", "README.md"], "direct") == ["tests/test_other.py"]


def test_a_data_file_selects_the_tests_of_the_module_that_reads_it(tmp_path: Path) -> None:
    graph = _repo(tmp_path)

    assert graph.select(["src/gigai/data/prompt.md"], "direct") == ["tests/test_loader.py"]


def test_a_change_no_test_can_be_mapped_to_selects_everything_and_the_tool_runs_nothing(tmp_path: Path) -> None:
    graph = _repo(tmp_path)

    assert graph.select(["uv.lock"], "direct") == ci_select_tests.ALL
    assert graph.select(["pyproject.toml", "src/gigai/other.py"], "direct") == ci_select_tests.ALL


def test_tests_are_counted_by_their_functions(tmp_path: Path) -> None:
    _write(tmp_path, "t.py", "def test_a():\n    pass\n\nasync def test_b():\n    pass\n\ndef helper():\n    pass\n")

    assert ci_select_tests.count_tests(tmp_path / "t.py") == 2


def test_the_pr_and_release_lane_runs_no_shard_browser_gate_or_macos_job() -> None:
    """0.1.11.1: PR CI and the pre-check run the cut-down lane; the heavy jobs are `full` only."""

    import pytest

    path = Path(__file__).resolve().parents[3] / ".github/workflows/pull_request.yaml"
    if not path.is_file():
        pytest.skip("workflows are excluded from the offline container build context")
    text = path.read_text(encoding="utf-8")

    def job(name: str) -> str:
        """The first lines of the job: its `needs` and `if`."""

        return text.split(f"\n  {name}:\n", 1)[1][:400]

    for name in ("source-matrix", "ui", "macos-smoke", "operator-home"):
        assert "(inputs.profile || 'pr') == 'full'" in job(name) or "inputs.profile == 'full'" in job(name), name
    for name in ("fast-tests", "core-flow"):
        assert "(inputs.profile || 'pr') != 'full'" in job(name), name
    assert "github.event_name == 'pull_request'" in job("changed-tests")
    assert "make test-changed" in text and "make unit-tests" in text
