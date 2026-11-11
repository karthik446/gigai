"""Public eval datasets: a committed ``ci`` slice per set, and a ``release`` slice built on demand under ``{data}``.

Each set has one loader module (``contractnli``, ``ragtruth``, ``quality``, ``ifeval``) defining:

- ``SOURCES``: ``{file name: (url, sha256)}``, the pinned upstream files the loader reads;
- ``RECIPES``: ``{suite: text}``, how the suite's subset is drawn (``seed = 20261109``);
- ``items(raw)``: every upstream item, flattened to one prompt string (``raw`` is the folder holding ``SOURCES``);
- ``select(items, suite, rng)``: the suite's subset, drawn with ``rng = random.Random(SEED)``.

A ``ci`` slice lives in ``tests/fixtures/evals/ci/<set>.jsonl`` next to ``<set>.sha256``. Its rows carry a canned
completion, so a ``ci`` run makes 0 model calls. ``release`` slices are never committed: ``download_release``
builds one under ``{data}/datasets/`` from the upstream files, downloading them only when they are absent. Licences
and attribution are in ``NOTICES.md`` next to this file. The module is stdlib only; a loader module is imported the
first time its set is used.
"""

from __future__ import annotations

import hashlib
import json
import random
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import asdict, dataclass, field, replace
from importlib import import_module
from pathlib import Path
from types import ModuleType
from typing import Any, TypeVar

SEED = 20261109
#: Longest flattened prompt an item may carry, in characters.
MAX_PROMPT_CHARS = 60_000
#: The sets with a loader module, in the order the docs list them.
SETS = ("contractnli", "ragtruth", "quality", "ifeval")
#: Where the committed ``ci`` slices live (a source checkout; they are not shipped in the wheel).
CI_DIR = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "evals" / "ci"
#: Appended to every ``ci`` recipe: committed fixtures must pass tests/behaviors/ci_tooling/test_no_real_fixture_data.py.
CI_FILTER = (
    " Before sampling, items whose text holds an e-mail address outside example.com/example.test or a macOS"
    " home-folder path are dropped."
)
# The pattern test_no_real_fixture_data.py applies to every fixture file.
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_SAFE_EMAIL_DOMAINS = {"example.test", "example.com"}

T = TypeVar("T")


@dataclass(frozen=True)
class Item:
    """One eval item: a flattened prompt, the gold answer, and where it came from."""

    id: str
    prompt: str
    expected: Any
    cluster_id: str
    #: Canned completion a ``ci`` run scores instead of calling a model.
    completion: str = ""
    #: Upstream ids, enough to find the item in the source files.
    source_ids: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    slice_recipe: str = ""
    #: sha256 of the slice file the item was read from; set on load, never written.
    sha256: str = ""

    def __post_init__(self) -> None:
        if len(self.prompt) > MAX_PROMPT_CHARS:
            raise ValueError(f"{self.id}: prompt is {len(self.prompt)} chars, over {MAX_PROMPT_CHARS}")


def loader(set_name: str) -> ModuleType:
    """The loader module of ``set_name``."""
    if set_name not in SETS:
        raise ValueError(f"unknown dataset {set_name!r}; known: {', '.join(SETS)}")
    return import_module(f"{__name__}.{set_name}")


def length_band(chars: int) -> str:
    """The prompt-length band of a document of ``chars`` characters (the RC1 stratification bands)."""
    if chars < 8_000:
        return "<8k"
    if chars < 24_000:
        return "8-24k"
    return "24-60k" if chars < MAX_PROMPT_CHARS else ">60k"


def stratified(
    values: Iterable[T], key: Callable[[T], str], counts: dict[str, int | None], rng: random.Random
) -> list[T]:
    """``counts[k]`` values drawn without replacement from those whose ``key`` is ``k`` (``None``: all of them).

    Groups are drawn in ``counts`` order, keeping ``values`` order inside a group, so callers sort first.
    """
    groups: dict[str, list[T]] = {}
    for value in values:
        groups.setdefault(key(value), []).append(value)
    picked: list[T] = []
    for name, count in counts.items():
        pool = groups.get(name, [])
        if count is not None and count > len(pool):
            raise ValueError(f"recipe draws {count} from {name!r}, which has {len(pool)}")
        picked += pool if count is None else rng.sample(pool, count)
    return picked


def publishable(item: Item) -> bool:
    """Whether ``item`` may sit in a committed fixture: no ``/Users/`` path, no e-mail outside example domains."""
    text = json.dumps([item.prompt, item.expected, item.completion, item.source_ids], ensure_ascii=False)
    if "/Users/" in text:
        return False
    return all(match.group(1).lower() in _SAFE_EMAIL_DOMAINS for match in _EMAIL.finditer(text))


def build(set_name: str, raw: Path, suite: str) -> list[Item]:
    """The ``suite`` slice of ``set_name``, drawn from the upstream files in ``raw``."""
    module = loader(set_name)
    pool = module.items(Path(raw))
    recipe = module.RECIPES[suite]
    if suite == "ci":
        pool = [item for item in pool if publishable(item)]
        recipe += CI_FILTER
    chosen = module.select(pool, suite, random.Random(SEED))
    return [replace(item, slice_recipe=recipe) for item in chosen]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _sidecar(path: Path) -> Path:
    return path.with_suffix(".sha256")


def write_slice(items: Sequence[Item], path: Path) -> str:
    """Write ``items`` to ``path`` (one JSON object per line) and its ``.sha256`` sidecar; return the digest."""
    lines = []
    for item in items:
        row = asdict(item)
        del row["sha256"]
        lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
    digest = file_sha256(path)
    _sidecar(path).write_text(f"{digest}  {path.name}\n", encoding="utf-8")
    return digest


def read_slice(path: Path) -> list[Item]:
    """The items of the slice at ``path``, after checking it against its ``.sha256`` sidecar."""
    digest = file_sha256(path)
    recorded = _sidecar(path).read_text(encoding="utf-8").split()[0]
    if digest != recorded:
        raise ValueError(f"{path}: sha256 {digest} does not match {_sidecar(path).name} ({recorded})")
    items = []
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        items.append(Item(**{**row, "tags": tuple(row["tags"]), "sha256": digest}))
    return items


def load_ci(set_name: str, directory: Path | None = None) -> list[Item]:
    """The committed ``ci`` slice of ``set_name`` (from ``directory``, default ``CI_DIR``)."""
    loader(set_name)
    return read_slice(Path(directory or CI_DIR) / f"{set_name}.jsonl")


def _unavailable(message: str) -> SystemExit:
    sys.stderr.write(f"gigai: {message}\n")
    return SystemExit(2)


def download_release(set_name: str, data_dir: Path, *, timeout: float = 60.0) -> Path:
    """Build ``{data}/datasets/<set>-release.jsonl`` and return its path.

    The upstream files live in ``{data}/datasets/raw/<set>/``; an absent one is downloaded once. No network, a
    failed download, or bytes that do not match the pinned sha256 exit 2 with the URL and the path to fill by hand.
    """
    module = loader(set_name)
    raw = Path(data_dir) / "datasets" / "raw" / set_name
    for name, (url, pinned) in module.SOURCES.items():
        target = raw / name
        hint = f"download {url} to {target} (sha256 {pinned}) and run again"
        if not target.is_file():
            try:
                _download(url, target, timeout)
            except OSError as error:  # URLError, timeouts and refused connections are all OSError
                raise _unavailable(f"{set_name}: cannot download {name} ({error}); {hint}") from error
        if file_sha256(target) != pinned:
            raise _unavailable(f"{set_name}: {target} does not match the pinned sha256; {hint}")
    out = Path(data_dir) / "datasets" / f"{set_name}-release.jsonl"
    write_slice(build(set_name, raw, "release"), out)
    return out


def _download(url: str, target: Path, timeout: float) -> None:
    from urllib.request import urlopen

    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f"{target.name}.part")
    with urlopen(url, timeout=timeout) as response, partial.open("wb") as handle:
        for block in iter(lambda: response.read(1 << 20), b""):
            handle.write(block)
    partial.replace(target)
