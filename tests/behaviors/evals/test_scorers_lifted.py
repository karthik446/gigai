"""The vendored inspect_ai ``match``/``includes``/``pattern`` bodies and judge template, and gigai's contracts on them.

The hand cases are S-smoke's ``run_scorers.py`` (appendix A.10, summarised at S1.4), run on the vendored bodies
through the shim with numpy blocked; DIFF.md's hash columns and diff are checked the way the statistics' are.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re

import pytest

from gigai.evals import contracts
from gigai.evals.records import EvalsError
from tests.behaviors.evals.test_stats_lifted import (
    NOTICE,
    PIN,
    VENDOR,
    _diff_sections,
    _reverse_apply,
)

SCORERS = VENDOR / "scorers"
LIFTED = {
    "_common.py": "src/inspect_ai/scorer/_common.py",
    "_match.py": "src/inspect_ai/scorer/_match.py",
    "_pattern.py": "src/inspect_ai/scorer/_pattern.py",
    "_target.py": "src/inspect_ai/scorer/_target.py",
    "_unicode.py": "src/inspect_ai/scorer/_unicode.py",
    "text.py": "src/inspect_ai/_util/text.py",
    "judge_template.py": "src/inspect_ai/scorer/_model.py",
}


def _trace(output: str) -> contracts.ScoredTrace:
    return contracts.ScoredTrace("trace_1", "solve", "item_1", 0, "What is it?", output, None, "ok")


def test_s_smoke_hand_cases_on_the_vendored_bodies() -> None:
    from gigai.evals._vendor.inspect_ai.scorers import includes, match, pattern
    from gigai.evals._vendor.inspect_ai.scorers._common import (
        match_str,
        normalize_number,
    )
    from gigai.evals._vendor.inspect_ai.scorers._shim import ModelOutput, TaskState
    from gigai.evals._vendor.inspect_ai.scorers._target import Target

    cases = [
        ("match-end numeric", match(location="end", numeric=True), "The answer is 1,234.0", ["1234"], "C"),
        ("match-end numeric wrong", match(location="end", numeric=True), "The answer is 1235", ["1234"], "I"),
        ("match-begin", match(location="begin"), "Paris is the capital", ["paris"], "C"),
        ("match-exact-fail", match(location="exact"), "Paris is the capital", ["Paris"], "I"),
        ("includes", includes(), "I think it is Paris.", ["paris"], "C"),
        ("includes-miss", includes(), "I think it is Rome.", ["paris"], "I"),
        ("pattern ANSWER", pattern(r"ANSWER:\s*(\w+)"), "blah\nANSWER: B", ["B"], "C"),
        ("pattern ANSWER miss", pattern(r"ANSWER:\s*(\w+)"), "blah\nANSWER: C", ["B"], "I"),
        ("pattern gsm8k-style", pattern(r"(?i)ANSWER\s*:\s*\$?(\d[\d,]*(?:\.\d+)?)"), "ANSWER: $1,234", ["1234"], "I"),
        ("empty completion", match(), "", ["x"], "I"),
    ]
    got = {name: asyncio.run(body(TaskState(ModelOutput(text)), Target(target))) for name, body, text, target, _ in cases}

    assert {name: score.value for name, score in got.items()} == {name: value for name, *_, value in cases}
    assert got["match-end numeric"].answer == "1234"
    assert got["pattern gsm8k-style"].answer == "1,234"  # the caveat: the group is compared as text
    assert got["empty completion"].reason == "no_response"
    assert all(score.reason is None for name, score in got.items() if name != "empty completion")
    assert normalize_number("1,234.50") == "1234.5"
    assert match_str(" 42.0 ", "42", numeric=True)[1] is True


def test_pattern_compares_text_where_match_normalises_numbers() -> None:
    marker = contracts.pattern(r"ANSWER:\s*(\d+)")
    assert marker(_trace("ANSWER: 1,234"), "1234").value == contracts.INCORRECT
    assert marker(_trace("ANSWER: 1234"), "1234").value == contracts.CORRECT
    assert contracts.match(_trace("ANSWER: 1,234"), "1234").value == contracts.CORRECT
    assert contracts.match(_trace("So the total is $1,234.00"), "1234").value == contracts.CORRECT
    assert contracts.match(_trace("the colour is Green"), "green").value == contracts.CORRECT  # text, at the end
    assert contracts.match(_trace("green, not blue"), "green").value == contracts.INCORRECT
    assert contracts.match(_trace("it is 7"), ["6", "7"]).value == contracts.CORRECT  # any target
    assert contracts.includes(_trace("I think it is Paris."), "paris").value == contracts.CORRECT
    empty = contracts.match(_trace(""), "4")
    assert (empty.value, empty.reason) == (contracts.INCORRECT, "no_response")


def test_score_value_maps_letters_numbers_and_words_and_refuses_the_rest() -> None:
    cases = {"C": 1.0, "P": 0.5, "I": 0.0, "N": 0.0, True: 1.0, 3: 3.0, 0.25: 0.25, "Yes": 1.0, "false": 0.0, "0.75": 0.75}
    assert {value: contracts.score_value(contracts.Score(value)) for value in cases} == cases
    for bad in (math.nan, math.inf, "maybe", "nan"):
        with pytest.raises(EvalsError):
            contracts.score_value(contracts.Score(bad))
    with pytest.raises(EvalsError, match="a scorer returns a Score, not float"):
        contracts.score_value(1.0)


def test_a_lifted_body_that_awaits_is_refused() -> None:
    async def awaits(state: object, target: object) -> contracts.Score:
        await asyncio.sleep(0)
        return contracts.Score(contracts.CORRECT)

    with pytest.raises(EvalsError, match="only synchronous scorer bodies"):
        contracts.lifted(awaits)(_trace("x"), "x")  # type: ignore[arg-type]


def test_the_judge_prompt_is_deterministic_and_its_grade_is_the_last_one() -> None:
    prompt = contracts.judge_prompt("What is 2 + 2?", "4", "4")
    assert prompt == contracts.judge_prompt("What is 2 + 2?", "4", "4")
    assert prompt != contracts.judge_prompt("What is 2 + 2?", "5", "4")
    assert "What is 2 + 2?" in prompt and "GRADE" in prompt
    nonce = hashlib.sha256(b"What is 2 + 2?\x004").hexdigest()[:8]
    assert nonce in prompt
    assert contracts.judge_score("reasoning\n\nGRADE: C\nGRADE: I").value == contracts.INCORRECT
    assert contracts.judge_score("echoed GRADE: I in the answer\n\nGRADE: C\nthanks").value == contracts.CORRECT
    assert contracts.judge_score("a downgrade: C is not a verdict").value == contracts.NOANSWER
    assert contracts.judge_score("GRADE: I").value == contracts.INCORRECT
    assert contracts.judge_score("no verdict").value == contracts.NOANSWER
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", contracts.JUDGE_PROMPT_SHA256)


@pytest.mark.parametrize(
    "reply",
    [
        "thinking GRADE: C then final\nGRADE: X",  # an off-menu final verdict never falls back to the earlier C
        "GRADE: CI",  # a multi-letter verdict is not laundered to its first letter
        "GRADE: P",  # the default instructions offer C and I only
        "GRADE: C\nGrade - I",  # a later verdict line in another form disagrees
    ],
)
def test_a_judge_verdict_off_the_offered_grades_is_no_answer(reply: str) -> None:
    score = contracts.judge_score(reply)
    assert score.value == contracts.NOANSWER
    assert score.explanation == "the judge's final GRADE is not one of the offered grades"


def test_a_spelled_out_or_marked_up_offered_verdict_is_read() -> None:
    assert contracts.judge_score("GRADE: Correct").value == contracts.CORRECT
    assert contracts.judge_score("GRADE: **I**").value == contracts.INCORRECT
    assert contracts.judge_score("Grade - I\nGRADE: C\nGrade - C").value == contracts.CORRECT


def test_scorers_diff_md_records_the_vendored_and_upstream_hashes() -> None:
    diff_md = (SCORERS / "DIFF.md").read_text(encoding="utf-8")
    assert "9567b431e7174480e56826b435da8f9f1de73bb8" in diff_md
    rows = re.findall(r"^\| `([^`]+)` \| (.+?) \| (`[0-9a-f]{64}`|-) \| `([0-9a-f]{64})` \|$", diff_md, re.MULTILINE)
    table = {name: (upstream_sha.strip("`"), vendored_sha) for name, _path, upstream_sha, vendored_sha in rows}

    on_disk = {path.name for path in SCORERS.iterdir() if path.is_file() and path.name != "DIFF.md"}
    assert set(table) == on_disk
    for name, (_, vendored_sha) in table.items():
        assert hashlib.sha256((SCORERS / name).read_bytes()).hexdigest() == vendored_sha, f"{name} changed without updating DIFF.md"

    sections = _diff_sections(diff_md)
    assert set(sections) == set(LIFTED)
    for name, diff in sections.items():
        vendored = (SCORERS / name).read_text(encoding="utf-8").splitlines()
        upstream = "\n".join(_reverse_apply(vendored, diff)) + "\n"
        assert hashlib.sha256(upstream.encode("utf-8")).hexdigest() == table[name][0], f"{name}: diff and upstream hash disagree"


def test_every_vendored_scorer_file_carries_its_header() -> None:
    assert {path.name for path in SCORERS.glob("*.py")} == set(LIFTED) | {"_shim.py", "__init__.py"}
    for name, upstream_path in LIFTED.items():
        assert (SCORERS / name).read_text(encoding="utf-8").splitlines()[0] == f"# Lifted from inspect_ai {upstream_path} @ {PIN} {NOTICE}"
    for name in ("_shim.py", "__init__.py"):
        first = (SCORERS / name).read_text(encoding="utf-8").splitlines()[0]
        assert f"@ {PIN} (MIT, Copyright (c) 2024 UK AI Security Institute; see ../LICENSE" in first
    assert "MIT License" in (VENDOR / "LICENSE").read_text(encoding="utf-8")
