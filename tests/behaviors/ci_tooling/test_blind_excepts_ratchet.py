"""uat-bug-021 prevention: no new unexplained blind ``except`` in Scout.

The defect this guards against: three failures on one fail-open path in
``market_acquisition`` (a ``ModuleNotFoundError`` and two
``WorkpadConflictError``) were each swallowed by ``except Exception`` with no
recorded reason, so Jev scoring was skipped on every real run for three weeks
and nothing said why.

The rule, for every ``.py`` under ``src/gigai/scout``: an ``except:``,
``except Exception`` or ``except BaseException`` (alone or inside a tuple)
must either

* say why on the ``except`` line: ``# noqa: BLE001 - <reason>`` (the reason
  is required; a bare ``# noqa: BLE001`` does not count), or
* be listed in ``blind_excepts_baseline.json``, the ones that were already
  there when this test landed.

The baseline is a ratchet. It maps a file to ``"<function>:<n>"`` entries:
the enclosing function (``Class.method``, ``outer.inner``, ``<module>``) and
the 1-based ordinal of the unannotated blind except within it, in source
order. No line numbers, so unrelated edits do not churn it. Because an
ordinal cannot tell which of several handlers in one function is the new one,
a function over its allowance is reported with every candidate line. An entry
that no longer matches a handler fails too, so the baseline only shrinks.

Regenerate (only to shrink it, or when the coordinator re-baselines at
commit); without the flag the same command only prints the counts:

    GIGAI_REGEN_BLIND_EXCEPT_BASELINE=1 \\
        uv run --locked --extra test python tests/behaviors/ci_tooling/test_blind_excepts_ratchet.py
"""

from __future__ import annotations

import ast
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
import io
import json
import os
from pathlib import Path
import re
import tokenize

REPO_ROOT = Path(__file__).resolve().parents[3]
SCOPE = "src/gigai/scout"
BASELINE_PATH = Path(__file__).with_name("blind_excepts_baseline.json")
REGEN_FLAG = "GIGAI_REGEN_BLIND_EXCEPT_BASELINE"
RULE = (
    "catch the specific exception, or say why everything is swallowed on the "
    "except line: `# noqa: BLE001 - <reason>`"
)
_BLIND = frozenset({"Exception", "BaseException"})
_SKIPPED_DIRECTORIES = frozenset({"__pycache__", "node_modules"})
_NOQA = re.compile(r"#\s*noqa\s*:(?P<codes>[^-]*)(?:-(?P<reason>.*))?$", re.IGNORECASE)


@dataclass(frozen=True)
class BlindExcept:
    path: str
    line: int
    function: str
    caught: str
    reason: str | None
    noqa_without_reason: bool

    @property
    def annotated(self) -> bool:
        return self.reason is not None


# ---------------------------------------------------------------------------
# Finding them
# ---------------------------------------------------------------------------


def load_sources(repo_root: Path) -> dict[str, str]:
    """Every ``.py`` under ``src/gigai/scout``, keyed by its posix path
    relative to the repo root."""

    sources: dict[str, str] = {}
    for path in sorted((repo_root / SCOPE).rglob("*.py")):
        relative = path.relative_to(repo_root)
        if _SKIPPED_DIRECTORIES.intersection(relative.parts):
            continue
        sources[relative.as_posix()] = path.read_text(encoding="utf-8")
    return sources


def _caught(handler: ast.ExceptHandler) -> str | None:
    """How a blind handler reads (``except Exception``), or ``None`` when the
    handler names only specific exceptions."""

    if handler.type is None:
        return "except:"
    members = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    for member in members:
        name = member.id if isinstance(member, ast.Name) else getattr(member, "attr", None)
        if name in _BLIND:
            return f"except {ast.unparse(handler.type)}"
    return None


def _handlers(node: ast.AST, scope: tuple[str, ...]) -> Iterator[tuple[ast.ExceptHandler, str]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            yield from _handlers(child, (*scope, child.name))
            continue
        if isinstance(child, ast.ExceptHandler):
            yield child, ".".join(scope) or "<module>"
        yield from _handlers(child, scope)


def _comment_on(line: str) -> str:
    """The comment on one source line, read with the tokenizer so a ``#``
    inside a string is not mistaken for one."""

    comment = ""
    try:
        for token in tokenize.generate_tokens(io.StringIO(line).readline):
            if token.type == tokenize.COMMENT:
                comment = token.string
    except (tokenize.TokenError, SyntaxError, IndentationError):
        # `except (` continued on the next line: what was read so far stands.
        pass
    return comment


def _annotation(line: str) -> tuple[str | None, bool]:
    """``(reason, noqa without a reason)`` for one ``except`` line."""

    match = _NOQA.search(_comment_on(line.strip()))
    if match is None or "BLE001" not in re.split(r"[\s,]+", match.group("codes").strip()):
        return None, False
    reason = (match.group("reason") or "").strip()
    return (reason, False) if reason else (None, True)


def find_blind_excepts(path: str, source: str) -> list[BlindExcept]:
    lines = source.splitlines()
    found: list[BlindExcept] = []
    for handler, function in _handlers(ast.parse(source, filename=path), ()):
        caught = _caught(handler)
        if caught is None:
            continue
        reason, noqa_without_reason = _annotation(lines[handler.lineno - 1])
        found.append(
            BlindExcept(path, handler.lineno, function, caught, reason, noqa_without_reason)
        )
    return sorted(found, key=lambda entry: entry.line)


def scan(sources: Mapping[str, str]) -> list[BlindExcept]:
    return [
        entry for path in sorted(sources) for entry in find_blind_excepts(path, sources[path])
    ]


# ---------------------------------------------------------------------------
# The ratchet
# ---------------------------------------------------------------------------


def _unannotated_by_function(found: Sequence[BlindExcept]) -> dict[tuple[str, str], list[BlindExcept]]:
    grouped: dict[tuple[str, str], list[BlindExcept]] = {}
    for entry in found:
        if not entry.annotated:
            grouped.setdefault((entry.path, entry.function), []).append(entry)
    return grouped


def build_baseline(found: Sequence[BlindExcept]) -> dict[str, list[str]]:
    baseline: dict[str, list[str]] = {}
    for (path, function), entries in sorted(_unannotated_by_function(found).items()):
        baseline.setdefault(path, []).extend(
            f"{function}:{ordinal}" for ordinal in range(1, len(entries) + 1)
        )
    return baseline


def _describe(entry: BlindExcept) -> str:
    note = " (its `# noqa: BLE001` gives no reason)" if entry.noqa_without_reason else ""
    return f"{entry.path}:{entry.line}: `{entry.caught}` in `{entry.function}`{note}"


def new_blind_excepts(
    found: Sequence[BlindExcept], baseline: Mapping[str, Sequence[str]]
) -> list[str]:
    problems: list[str] = []
    in_source_order = sorted(
        _unannotated_by_function(found).items(), key=lambda item: (item[0][0], item[1][0].line)
    )
    for (path, function), entries in in_source_order:
        allowed = set(baseline.get(path, ()))
        expected = [f"{function}:{ordinal}" for ordinal in range(1, len(entries) + 1)]
        if all(key in allowed for key in expected):
            continue
        listed = sum(1 for key in allowed if key.rsplit(":", 1)[0] == function)
        if listed == 0:
            problems.extend(
                f"{_describe(entry)} is a new blind except: {RULE}" for entry in entries
            )
            continue
        problems.append(
            f"{path}: `{function}` has {len(entries)} unannotated blind excepts but the "
            f"baseline lists {listed}; one of these is new, or the baseline entries for "
            f"`{function}` are not numbered 1..{listed}: "
            + "; ".join(_describe(entry) for entry in entries)
            + f". {RULE[0].upper()}{RULE[1:]}"
        )
    return problems


def stale_baseline_entries(
    found: Sequence[BlindExcept], baseline: Mapping[str, Sequence[str]]
) -> list[str]:
    current = build_baseline(found)
    problems: list[str] = []
    for path in sorted(baseline):
        entries = list(baseline[path])
        existing = set(current.get(path, ()))
        for key, count in sorted(Counter(entries).items()):
            if count > 1:
                problems.append(f"{path}: baseline entry `{key}` is listed {count} times")
            if key not in existing:
                problems.append(
                    f"{path}: baseline entry `{key}` no longer matches an unannotated blind "
                    f"except; remove it from {BASELINE_PATH.name} (the baseline only shrinks)"
                )
        if not entries:
            problems.append(f"{path}: baseline lists the file with no entries; remove the key")
    return problems


def counts(found: Sequence[BlindExcept], baseline: Mapping[str, Sequence[str]]) -> str:
    annotated = sum(1 for entry in found if entry.annotated)
    by_file = Counter(entry.path for entry in found)
    unannotated = Counter(entry.path for entry in found if not entry.annotated)
    lines = [
        f"blind excepts under {SCOPE}: {len(found)} in {len(by_file)} files",
        f"  annotated `# noqa: BLE001 - <reason>`: {annotated}",
        f"  unannotated: {len(found) - annotated}",
        f"  baseline entries: {sum(len(entries) for entries in baseline.values())} "
        f"in {len(baseline)} files",
        "  by file (top 10: total / annotated / unannotated):",
    ]
    for path, total in sorted(by_file.items(), key=lambda item: (-item[1], item[0]))[:10]:
        lines.append(
            f"    {total:3d} / {total - unannotated[path]:3d} / {unannotated[path]:3d}  {path}"
        )
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _live() -> tuple[tuple[BlindExcept, ...], dict[str, list[str]]]:
    found = tuple(scan(load_sources(REPO_ROOT)))
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return found, baseline


def _failure(problems: Sequence[str], what: str) -> str:
    return (
        f"{len(problems)} {what} (uat-bug-021: a blind except hid a failing import "
        "for three weeks):\n  " + "\n  ".join(problems)
    )


# ---------------------------------------------------------------------------
# The live tree
# ---------------------------------------------------------------------------


def test_scout_has_no_new_unannotated_blind_except() -> None:
    found, baseline = _live()
    print(counts(found, baseline))
    assert len(found) > 20, f"found only {len(found)} blind excepts under {SCOPE}"

    problems = new_blind_excepts(found, baseline)

    assert not problems, _failure(problems, "blind except(s) neither annotated nor baselined")


def test_the_baseline_lists_only_blind_excepts_that_still_exist() -> None:
    found, baseline = _live()

    problems = stale_baseline_entries(found, baseline)

    assert not problems, _failure(problems, "stale baseline entries")


def test_the_baseline_file_is_in_its_generated_order() -> None:
    _, baseline = _live()

    assert list(baseline) == sorted(baseline)
    assert all(isinstance(entries, list) for entries in baseline.values())
    assert all(
        re.fullmatch(r".+:[1-9][0-9]*", key) for entries in baseline.values() for key in entries
    )


# ---------------------------------------------------------------------------
# The rule itself, on in-memory sources
# ---------------------------------------------------------------------------

_PATH = "src/gigai/scout/find_jobs/market_acquisition.py"
_UAT_BUG_021 = (
    "def _read_resume_text_for_rank(resolved):\n"
    "    try:\n"
    "        from ..private_records import read_record\n"
    "        return read_record(resolved, 'resume')\n"
    "    except Exception:\n"
    "        return None\n"
)


def test_the_uat_bug_021_handler_fails_with_its_file_line_and_the_rule() -> None:
    found = find_blind_excepts(_PATH, _UAT_BUG_021)

    assert new_blind_excepts(found, {}) == [
        "src/gigai/scout/find_jobs/market_acquisition.py:5: `except Exception` in "
        "`_read_resume_text_for_rank` is a new blind except: catch the specific "
        "exception, or say why everything is swallowed on the except line: "
        "`# noqa: BLE001 - <reason>`"
    ]


def test_every_blind_shape_is_found_and_a_specific_except_is_not() -> None:
    source = (
        "try:\n    pass\nexcept:\n    pass\n"
        "try:\n    pass\nexcept BaseException as exc:\n    pass\n"
        "try:\n    pass\nexcept (ValueError, Exception):\n    pass\n"
        "try:\n    pass\nexcept builtins.Exception:\n    pass\n"
        "try:\n    pass\nexcept (OSError, ValueError):\n    pass\n"
        "try:\n    pass\nexcept ModuleNotFoundError:\n    pass\n"
    )

    found = find_blind_excepts(_PATH, source)

    assert [(entry.line, entry.caught, entry.function) for entry in found] == [
        (3, "except:", "<module>"),
        (7, "except BaseException", "<module>"),
        (11, "except (ValueError, Exception)", "<module>"),
        (15, "except builtins.Exception", "<module>"),
    ]


def test_an_annotation_needs_a_reason() -> None:
    source = (
        "def score():\n"
        "    try:\n        pass\n"
        "    except Exception as exc:  # noqa: BLE001 - fail open: Jev never fails a run\n"
        "        pass\n"
        "    try:\n        pass\n"
        "    except Exception:  # noqa: BLE001\n"
        "        pass\n"
        "    try:\n        pass\n"
        "    except Exception:  # noqa: BLE001 -\n"
        "        pass\n"
        "    try:\n        pass\n"
        "    except Exception:  # noqa: E722 - the wrong rule\n"
        "        pass\n"
        "    try:\n        pass\n"
        "    except Exception:  # noqa: E722, BLE001 - recorded, then re-raised\n"
        "        pass\n"
        "    try:\n        x = '# noqa: BLE001 - in a string'\n"
        "    except Exception: x = '# noqa: BLE001 - in a string'\n"
    )

    found = find_blind_excepts(_PATH, source)

    assert [(entry.line, entry.reason, entry.noqa_without_reason) for entry in found] == [
        (4, "fail open: Jev never fails a run", False),
        (8, None, True),
        (12, None, True),
        (16, None, False),
        (20, "recorded, then re-raised", False),
        (24, None, False),
    ]
    assert new_blind_excepts(found, {})[0] == (
        "src/gigai/scout/find_jobs/market_acquisition.py:8: `except Exception` in `score` "
        "(its `# noqa: BLE001` gives no reason) is a new blind except: catch the specific "
        "exception, or say why everything is swallowed on the except line: "
        "`# noqa: BLE001 - <reason>`"
    )


def test_the_key_is_the_enclosing_function_and_an_ordinal() -> None:
    source = (
        "try:\n    pass\nexcept Exception:\n    pass\n"
        "class Reader:\n"
        "    def read(self):\n"
        "        try:\n            pass\n"
        "        except Exception:\n            pass\n"
        "        def inner():\n"
        "            try:\n                pass\n"
        "            except Exception:\n                pass\n"
        "        try:\n            pass\n"
        "        except Exception:  # noqa: BLE001 - explained, so it takes no ordinal\n"
        "            pass\n"
        "        try:\n            pass\n"
        "        except Exception:\n            pass\n"
    )

    assert build_baseline(find_blind_excepts(_PATH, source)) == {
        _PATH: ["<module>:1", "Reader.read:1", "Reader.read:2", "Reader.read.inner:1"]
    }


def test_a_baselined_handler_passes_and_moving_it_does_not_churn_the_baseline() -> None:
    baseline = build_baseline(find_blind_excepts(_PATH, _UAT_BUG_021))
    moved = "import json\n\n\nVALUE = json.dumps({})\n\n\n" + _UAT_BUG_021

    found = find_blind_excepts(_PATH, moved)

    assert baseline == {_PATH: ["_read_resume_text_for_rank:1"]}
    assert [entry.line for entry in found] == [11]
    assert new_blind_excepts(found, baseline) == []
    assert stale_baseline_entries(found, baseline) == []


def test_a_second_blind_except_in_a_baselined_function_names_every_candidate() -> None:
    baseline = build_baseline(find_blind_excepts(_PATH, _UAT_BUG_021))
    grown = _UAT_BUG_021 + (
        "    try:\n"
        "        return resolved.read()\n"
        "    except Exception:\n"
        "        return None\n"
    )

    problems = new_blind_excepts(find_blind_excepts(_PATH, grown), baseline)

    assert problems == [
        "src/gigai/scout/find_jobs/market_acquisition.py: `_read_resume_text_for_rank` has "
        "2 unannotated blind excepts but the baseline lists 1; one of these is new, or the "
        "baseline entries for `_read_resume_text_for_rank` are not numbered 1..1: "
        "src/gigai/scout/find_jobs/market_acquisition.py:5: `except Exception` in "
        "`_read_resume_text_for_rank`; "
        "src/gigai/scout/find_jobs/market_acquisition.py:9: `except Exception` in "
        "`_read_resume_text_for_rank`. Catch the specific exception, or say why everything "
        "is swallowed on the except line: `# noqa: BLE001 - <reason>`"
    ]


def test_a_fixed_handler_must_leave_the_baseline() -> None:
    baseline = {
        _PATH: ["_read_resume_text_for_rank:1", "_read_resume_text_for_rank:2"],
        "src/gigai/scout/removed.py": ["gone:1"],
    }

    problems = stale_baseline_entries(find_blind_excepts(_PATH, _UAT_BUG_021), baseline)

    assert problems == [
        "src/gigai/scout/find_jobs/market_acquisition.py: baseline entry "
        "`_read_resume_text_for_rank:2` no longer matches an unannotated blind except; "
        "remove it from blind_excepts_baseline.json (the baseline only shrinks)",
        "src/gigai/scout/removed.py: baseline entry `gone:1` no longer matches an "
        "unannotated blind except; remove it from blind_excepts_baseline.json "
        "(the baseline only shrinks)",
    ]


if __name__ == "__main__":
    scanned = scan(load_sources(REPO_ROOT))
    if os.environ.get(REGEN_FLAG) == "1":
        BASELINE_PATH.write_text(
            json.dumps(build_baseline(scanned), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"wrote {BASELINE_PATH.relative_to(REPO_ROOT)}")
    else:
        print(f"{REGEN_FLAG} is not 1: counts only, {BASELINE_PATH.name} not written")
    recorded = (
        json.loads(BASELINE_PATH.read_text(encoding="utf-8")) if BASELINE_PATH.is_file() else {}
    )
    print(counts(scanned, recorded))
    for problem in (*new_blind_excepts(scanned, recorded), *stale_baseline_entries(scanned, recorded)):
        print(f"  {problem}")
