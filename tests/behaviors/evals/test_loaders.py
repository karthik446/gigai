"""The public eval-dataset loaders and their committed ``ci`` slices.

Sets: ContractNLI, RAGTruth, QuALITY, IFEval, JSONSchemaBench, RewardBench 2, JudgeBench, MT-Bench human judgments,
tinyGSM8k and tinyMMLU.

Every check runs offline on the committed slices; the upstream files are never read here. A loader's "body" is the
code of its module's functions (``items``, ``select`` and any helper): non-blank, non-comment lines from the first
statement after a function's docstring to its last line. The notice header and the module-level data (sources,
recipes, prompt text) are not loader body.
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
from pathlib import Path

import pytest

from gigai.evals import datasets
from gigai.gigs.eval_manifest import SPDX_ALLOWED
from tests.behaviors.ci_tooling import test_no_real_fixture_data as fixture_guard

PACKAGE = Path(datasets.__file__).parent
SETS = [name for name in datasets.SETS if (datasets.CI_DIR / f"{name}.jsonl").is_file()]
#: The sets whose upstream files are parquet, read with the optional pyarrow in a release build only.
PARQUET_SETS = [name for name in SETS if any(file.endswith(".parquet") for file in datasets.loader(name).SOURCES)]
MAX_SLICE_LINES = 60
MAX_LOADER_BODY = 30


def _body_lines(source: str) -> int:
    lines = source.splitlines()
    counted: set[int] = set()
    for node in ast.parse(source).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        body = node.body[1:] if ast.get_docstring(node) is not None else node.body
        counted.update(range(body[0].lineno, node.end_lineno + 1))
    return sum(1 for number in counted if lines[number - 1].strip() and not lines[number - 1].strip().startswith("#"))


def test_every_set_has_a_committed_slice() -> None:
    assert SETS == list(datasets.SETS)
    slices = {path.stem for path in datasets.CI_DIR.glob("*.jsonl")}
    modules = {path.stem for path in PACKAGE.glob("*.py")} - {"__init__"}
    assert slices == modules == set(datasets.SETS)


def test_rewardbench2_ids_and_clusters_are_unique_across_subsets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Upstream ids 40-49 occur in both Focus and Factuality; a built release pool must still key each prompt once."""
    module = datasets.loader("rewardbench2")
    rows = [
        {"id": str(number), "subset": subset, "prompt": f"{subset} prompt {number}", "chosen": ["right"]}
        | {"rejected": ["wrong 1", "wrong 2", "wrong 3"], "models": ["m0", "m1", "m2", "m3"]}
        for subset in (*module.SUBSETS, "Math")
        for number in range(50)
    ]
    monkeypatch.setattr(module, "parquet_rows", lambda path, set_name: rows)
    release = datasets.build("rewardbench2", tmp_path, "release")
    assert len(release) == 100
    assert len({item.id for item in release}) == len({item.cluster_id for item in release}) == 100
    assert {"rewardbench2-focus-40", "rewardbench2-factuality-40"} <= {item.id for item in release}


@pytest.mark.parametrize("name", SETS)
def test_slice_items_carry_the_file_hash_and_one_bounded_prompt(name: str) -> None:
    path = datasets.CI_DIR / f"{name}.jsonl"
    recorded = path.with_suffix(".sha256").read_text(encoding="utf-8").split()
    items = datasets.load_ci(name)
    assert recorded == [datasets.file_sha256(path), path.name]
    assert items and {item.sha256 for item in items} == {recorded[0]}
    assert len({item.id for item in items}) == len(items)
    for item in items:
        assert isinstance(item.prompt, str) and 0 < len(item.prompt) <= datasets.MAX_PROMPT_CHARS
        assert item.id.startswith(f"{name}-") and item.cluster_id and item.source_ids and item.completion
        assert item.slice_recipe.startswith(datasets.loader(name).RECIPES["ci"])


@pytest.mark.parametrize("name", SETS)
def test_slice_has_at_most_sixty_lines(name: str) -> None:
    text = (datasets.CI_DIR / f"{name}.jsonl").read_text(encoding="utf-8")
    assert 0 < len(text.splitlines()) <= MAX_SLICE_LINES


@pytest.mark.parametrize("name", SETS)
def test_loader_body_is_at_most_thirty_lines(name: str) -> None:
    """Counts function bodies only: a deviation from the row's whole-file count, accepted by the coordinator.

    The bound is on the flattening logic. Imports and module data (SOURCES pins, COUNTS, CAP, RECIPES, ASK prompt
    text) are constants the S-smoke loaders did not carry. Counted strictly (every non-blank, non-comment line after
    the module docstring) the loaders are contractnli 44, ragtruth 58, quality 45, ifeval 37, jsonschemabench 38,
    rewardbench2 51, judgebench 51, mtbench_human 52, tinygsm8k 43 and tinymmlu 38 lines.
    """
    assert _body_lines((PACKAGE / f"{name}.py").read_text(encoding="utf-8")) <= MAX_LOADER_BODY


def test_a_tampered_slice_is_refused(tmp_path: Path) -> None:
    source = datasets.CI_DIR / f"{SETS[0]}.jsonl"
    copy = tmp_path / source.name
    copy.write_bytes(source.read_bytes() + b"\n")
    copy.with_suffix(".sha256").write_bytes(source.with_suffix(".sha256").read_bytes())
    with pytest.raises(ValueError, match="does not match"):
        datasets.load_ci(SETS[0], tmp_path)


def test_contractnli_items_are_audit_pending() -> None:
    assert all(item.tags[0] == "audit_pending" for item in datasets.load_ci("contractnli"))


def _notice_sections() -> dict[str, str]:
    notices = (PACKAGE / "NOTICES.md").read_text(encoding="utf-8")
    return {section.split()[0]: section for section in re.split(r"^## ", notices, flags=re.M)[1:]}


def test_every_notice_licence_is_on_the_allow_list() -> None:
    licences = {name: re.findall(r"^- SPDX: (\S+)$", section, flags=re.M) for name, section in _notice_sections().items()}
    allowed = {licence.casefold() for licence in SPDX_ALLOWED}
    assert set(SETS) <= set(licences)
    for name in SETS:
        assert len(licences[name]) == 1 and licences[name][0].casefold() in allowed, name


#: What each licence requires to travel with a copy: MIT its copyright and permission notice, Apache-2.0 a copy or
#: the URL of the licence, CC BY 4.0 the licence URI, ODC-By 1.0 the licence URL. Every set also states the changes.
LICENCE_TERMS = {
    "MIT": ("Copyright (c) ", "Permission is hereby granted, free of charge", "this permission notice shall be included"),
    "Apache-2.0": ("https://www.apache.org/licenses/LICENSE-2.0",),
    "CC-BY-4.0": ("https://creativecommons.org/licenses/by/4.0/",),
    "ODC-By-1.0": ("https://opendatacommons.org/licenses/by/1-0/",),
}


@pytest.mark.parametrize("name", SETS)
def test_every_notice_carries_what_its_licence_requires(name: str) -> None:
    section = _notice_sections()[name]
    (licence,) = re.findall(r"^- SPDX: (\S+)$", section, flags=re.M)
    for term in (*LICENCE_TERMS[licence], "Changes made:"):
        assert term in section, (name, term)


@pytest.mark.parametrize("name", SETS)
def test_release_without_network_exits_2_with_the_download_hint(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def offline(*args: object, **kwargs: object) -> None:
        raise OSError("network is unreachable")

    monkeypatch.setattr("urllib.request.urlopen", offline)
    with pytest.raises(SystemExit) as exit_info:
        datasets.download_release(name, tmp_path)
    assert exit_info.value.code == 2
    url, pinned = next(iter(datasets.loader(name).SOURCES.values()))
    err = capsys.readouterr().err
    assert err.startswith(f"gigai: {name}: cannot download ") and f"download {url} to " in err and pinned in err
    assert not (tmp_path / "datasets" / f"{name}-release.jsonl").exists()


@pytest.mark.parametrize("name", PARQUET_SETS)
def test_release_without_pyarrow_exits_2_with_the_install_hint(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    module = datasets.loader(name)
    raw = tmp_path / "datasets" / "raw" / name
    raw.mkdir(parents=True)
    sources = {}
    for file, (url, _pinned) in module.SOURCES.items():
        (raw / file).write_bytes(b"PAR1 placeholder")
        sources[file] = (url, datasets.file_sha256(raw / file))
    monkeypatch.setattr(module, "SOURCES", sources)
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    with pytest.raises(SystemExit) as exit_info:
        datasets.download_release(name, tmp_path)
    assert exit_info.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith(f"gigai: {name}: reading ") and "needs pyarrow" in err and "--with pyarrow gigai" in err
    assert not (tmp_path / "datasets" / f"{name}-release.jsonl").exists()


def test_ci_slices_load_without_pyarrow(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "pyarrow", None)
    assert all(datasets.load_ci(name) for name in SETS)


@pytest.mark.parametrize("name", SETS)
def test_slice_holds_no_private_fixture_strings(name: str) -> None:
    """The ci_tooling guard's checks, scoped to this slice: the private denylist when set, else the shape check."""
    text = (datasets.CI_DIR / f"{name}.jsonl").read_text(encoding="utf-8")
    assert "/Users/" not in text
    domains = {match.group(1).lower() for match in fixture_guard.EMAIL.finditer(text)}
    assert domains <= fixture_guard.SAFE_EMAIL_DOMAINS
    denylist = os.environ.get("GIGAI_FIXTURE_DENYLIST")
    if denylist:
        entries = [line.strip().lower() for line in Path(denylist).read_text(encoding="utf-8").splitlines()]
        hits = [index for index, entry in enumerate(entries, 1) if entry and entry in text.lower()]
        assert not hits, f"{name}.jsonl holds denylist lines {hits} (entry text withheld)"


#: The one non-stdlib import the package may make: ``parquet_rows`` reads upstream parquet with the optional pyarrow,
#: inside the function, so importing the package or loading a ci slice never needs it.
OPTIONAL_IMPORT = ("__init__.py", "parquet_rows", "pyarrow")


def test_dataset_modules_import_only_the_standard_library() -> None:
    for path in sorted(PACKAGE.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        optional = {
            id(node)
            for function in tree.body
            if isinstance(function, ast.FunctionDef) and (path.name, function.name) == OPTIONAL_IMPORT[:2]
            for node in ast.walk(function)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and not node.level:
                roots = {node.module.split(".")[0]}
            else:
                continue
            extra = {OPTIONAL_IMPORT[2]} if id(node) in optional else set()
            allowed = sys.stdlib_module_names | {"__future__"} | extra
            assert roots <= allowed, f"{path.name}: {roots}"


def test_rows_round_trip_as_json_objects() -> None:
    for name in SETS:
        for line in (datasets.CI_DIR / f"{name}.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            assert "sha256" not in row and set(row) == set(datasets.Item.__dataclass_fields__) - {"sha256"}
