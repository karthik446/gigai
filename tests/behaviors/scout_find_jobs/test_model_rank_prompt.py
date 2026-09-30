"""SCOPE-ADD-3 B1: the rank-v1 prompt, the strict answer schema, ordering and the sealed shape.

Pure functions only (no model, no files): ``scout/find_jobs/model_rank.py``.
"""

from __future__ import annotations

import json

import pytest

from gigai.scout.find_jobs.model_rank import (
    PROMPT_VERSION,
    RANK_SCHEMA_VERSION,
    BatchResult,
    BatchUsage,
    RankAnswerError,
    RankedPosting,
    RankResult,
    ordering_key,
    ranked_order,
    render_rank_prompt,
    validate_rank_answer,
)

_LINES = [
    "p0 | Senior Backend Engineer @ acme | lvl=senior | loc=Remote [US] | yrs=5+ | req=Python, AWS",
    "p1 | Staff Engineer @ beta | lvl=staff | loc=London [GB] | yrs=? | req=Go (req=full) | flags=onsite",
]
_CANDIDATE = "CANDIDATE: level=staff; 9+ yrs; synthetic\nskills: Python, Go\ntargets: Staff Engineer\ncountries: US\nneeds visa sponsorship: yes\nlocation: unknown"

_GOLDEN = """You are pre-ranking job postings for ONE candidate so the best ones get a full assessment first.
Score every posting 0-100 for how likely a full resume-vs-requirements assessment would find it a match.

Scoring guide:
- 80-100: level and title fit the candidate's targets, most required skills/tech appear in the candidate's skills, no blocker.
- 50-79: plausible fit with gaps (some key tech missing, domain unfamiliar, level one step off).
- 20-49: weak fit (core stack or domain mostly absent, or level clearly off).
- 0-19: a blocker applies or the role is a different job family.

Blockers are ONLY hard facts stated in the posting line that rule the candidate out: flags=no_sponsor when the candidate needs sponsorship, flags=clearance, flags=citizen, a location/countries list that excludes the candidate's countries. Do not invent blockers; leave the list empty when none apply. Missing skills are NOT blockers.

Posting lines are: id | title @ company | lvl | loc [countries] | yrs=min years | req=skills/tech from the requirements section | flags=hard-constraint hints.

CANDIDATE: level=staff; 9+ yrs; synthetic
skills: Python, Go
targets: Staff Engineer
countries: US
needs visa sponsorship: yes
location: unknown

POSTINGS (2):
p0 | Senior Backend Engineer @ acme | lvl=senior | loc=Remote [US] | yrs=5+ | req=Python, AWS
p1 | Staff Engineer @ beta | lvl=staff | loc=London [GB] | yrs=? | req=Go (req=full) | flags=onsite

Answer with ONLY a JSON array, no prose, no code fences, one object per posting, every id exactly once:
[{"posting_id": "p000", "score": 0, "reasons": ["<=12 words", "<=12 words"], "blockers": []}]
reasons: at most 2 short strings. blockers: strings, [] when none."""


def test_prompt_golden() -> None:
    assert PROMPT_VERSION == "rank-v1"
    assert render_rank_prompt(_LINES, _CANDIDATE) == _GOLDEN


def test_retry_prompt_feeds_the_error_back_bounded() -> None:
    prompt = render_rank_prompt(_LINES, _CANDIDATE, "p1: score must be an integer 0-100" + "x" * 500)
    assert prompt.startswith(_GOLDEN)
    tail = prompt[len(_GOLDEN):]
    assert tail.startswith("\n\nYour previous answer was rejected: p1: score must be an integer 0-100")
    assert tail.endswith("You cannot see that answer. Produce a fresh, complete answer following the format exactly.")
    assert len(tail) < 300 + 200


def _item(pid: str, score: object = 50, reasons: object = None, blockers: object = None, **extra: object) -> dict:
    return {"posting_id": pid, "score": score, "reasons": ["ok"] if reasons is None else reasons,
            "blockers": [] if blockers is None else blockers, **extra}


def test_valid_answer_is_returned_in_id_order() -> None:
    text = json.dumps([_item("p1", 10, blockers=["no_sponsor"]), _item("p0", 90)])
    items = validate_rank_answer(text, ["p0", "p1"])
    assert [item["posting_id"] for item in items] == ["p0", "p1"]
    assert items[1]["blockers"] == ["no_sponsor"]


def test_fenced_or_prose_wrapped_array_is_accepted() -> None:
    body = json.dumps([_item("p0"), _item("p1")])
    assert len(validate_rank_answer(f"```json\n{body}\n```", ["p0", "p1"])) == 2
    assert len(validate_rank_answer(f"Here you go:\n{body}\nDone.", ["p0", "p1"])) == 2


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        ("no json here", "not a JSON array"),
        (json.dumps({"posting_id": "p0"}), "top level is not an array"),
        (json.dumps([_item("p0"), "p1"]), "item 1 is not an object"),
        (json.dumps([_item("p0", why="x"), _item("p1")]), "unknown keys ['why']"),
        (json.dumps([_item("p0"), _item("p9")]), "unknown posting_id 'p9'"),
        (json.dumps([_item("p0"), _item("p0")]), "posting_id p0 appears twice"),
        (json.dumps([_item("p0", 101), _item("p1")]), "p0: score must be an integer 0-100"),
        (json.dumps([_item("p0", 50.5), _item("p1")]), "p0: score must be an integer 0-100"),
        (json.dumps([_item("p0", True), _item("p1")]), "p0: score must be an integer 0-100"),
        (json.dumps([_item("p0", reasons=["a", "b", "c"]), _item("p1")]), "p0: reasons must be a list of at most 2 strings"),
        (json.dumps([_item("p0", blockers="none"), _item("p1")]), "p0: blockers must be a list of strings"),
        (json.dumps([_item("p0")]), "1 of 2 posting_ids missing (e.g. ['p1'])"),
    ],
)
def test_invalid_answers_name_the_problem(answer: str, message: str) -> None:
    with pytest.raises(RankAnswerError) as error:
        validate_rank_answer(answer, ["p0", "p1"])
    assert message in str(error.value)


def _posting(pid: str, score: int | None, blockers: tuple[str, ...] = (), **kw: object) -> RankedPosting:
    return RankedPosting(pid, f"https://jobs.example/{pid}", "sha256:" + pid, score, ("r",) if score is not None else (),
                         blockers, "b000" if score is not None else None, **kw)  # type: ignore[arg-type]


def test_blockers_demote_but_never_hide() -> None:
    postings = (
        _posting("p0", 95, ("no_sponsor",)),
        _posting("p1", 40),
        _posting("p2", None, unscored_reason="call_budget"),
        _posting("p3", 70),
        _posting("p4", 10, ("clearance",)),
    )
    result = _result(postings, ())
    order = [item.posting_id for item in ranked_order(result)]
    assert order == ["p3", "p1", "p2", "p0", "p4"]
    assert postings[0].demoted and not postings[3].demoted
    assert ordering_key(postings[0], 0)[0] > ordering_key(postings[2], 2)[0] > ordering_key(postings[1], 1)[0]


def test_equal_scores_keep_input_order() -> None:
    postings = (_posting("p0", 60), _posting("p1", 60))
    assert [item.posting_id for item in ranked_order(_result(postings, ()))] == ["p0", "p1"]


def _result(postings: tuple[RankedPosting, ...], batches: tuple[BatchResult, ...]) -> RankResult:
    return RankResult(
        model_target="codex_cli", configured_target="codex-default", model="default", resolved_model="gpt-test",
        effort="low", effort_applied="low", batch_size=50, concurrency=8, max_calls=40, max_tokens=None,
        postings=postings, batches=batches, status="partial", fail_open_reason="1 of 5 postings unscored", seconds=1.5,
    )


def test_sealed_rank_json_shape_round_trips() -> None:
    scored = _posting("p0", 80)
    cached = RankedPosting("p1", "https://jobs.example/p1", "sha256:p1", 30, ("stack gap",), (), "cache", True)
    unscored = _posting("p2", None, unscored_reason="invalid: 1 of 1 posting_ids missing")
    batches = (
        BatchResult("cache", ("p1",), "cache", True, 0, 0.0, postings=(cached,)),
        BatchResult("b000", ("p0", "p2"), "model", False, 2, 3.25, BatchUsage(100, 20, 120, 60), "bad", split=True,
                    resolved_model="gpt-test"),
        BatchResult("b000a", ("p0",), "model", True, 1, 1.0, BatchUsage(50, 10, 60, None), split_from="b000",
                    resolved_model="gpt-test", postings=(scored,)),
        BatchResult("b000b", ("p2",), "model", False, 1, 1.0, BatchUsage(40, 5, None, None), "missing", split_from="b000",
                    postings=(unscored,)),
    )
    result = _result((scored, cached, unscored), batches)
    sealed = result.to_json()

    assert list(sealed) == [
        "schema_version", "prompt_version", "digest_version", "model_target", "configured_target", "model",
        "resolved_model", "effort", "effort_applied", "batch_size", "concurrency", "max_calls", "max_tokens",
        "status", "fail_open_reason", "totals", "postings", "batches",
    ]
    assert sealed["schema_version"] == RANK_SCHEMA_VERSION
    assert (sealed["prompt_version"], sealed["digest_version"]) == ("rank-v1", "digest-v4")
    assert sealed["totals"] == {
        "postings": 3, "scored": 2, "unscored": 1, "cached": 1, "demoted": 0, "calls": 4, "batches": 3,
        "valid_batches": 1, "input_tokens": 190, "output_tokens": 35, "total_tokens": 180, "cached_input_tokens": 60,
        "seconds": 1.5,
    }
    assert sealed["postings"][0] == {
        "posting_id": "p0", "normalized_url": "https://jobs.example/p0", "content_sha256": "sha256:p0", "score": 80,
        "reasons": ["r"], "blockers": [], "demoted": False, "batch_id": "b000", "cached": False, "unscored_reason": None,
    }
    assert sealed["batches"][1] == {
        "batch_id": "b000", "posting_ids": ["p0", "p2"], "source": "model", "valid": False, "attempts": 2,
        "seconds": 3.25, "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "cached_input_tokens": 60},
        "error": "bad", "split": True, "split_from": None, "resolved_model": "gpt-test",
    }
    assert json.loads(json.dumps(sealed)) == sealed
    again = RankResult.from_json(json.loads(json.dumps(sealed)))
    assert again.to_json() == sealed
    assert again.batches[0].postings == (cached,)


def test_from_json_refuses_another_schema() -> None:
    with pytest.raises(ValueError):
        RankResult.from_json({"schema_version": "scout-rank:0"})
