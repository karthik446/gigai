"""0.1.11.2 RANK-B (scope B0b, size report 1c/1d): the list is ordered by rank and a batch is the top 50 BY RANK.

End outcomes on synthetic fixtures (rank scores are written the way the
background rank writes them, ``fit_fixtures.seed_rank``; the model is the
scripted fake):

(1) ASSESS = THE TOP 50 BY RANK. 60 not-assessed postings with scores: the ask
    names the 50 highest and ``more_after`` is 10, and the approved batch
    assesses exactly those. It used to be the newest 50 by posting date. Named
    ``jobs`` still assess what is named. ``scout new`` takes the same batch.
(2) THE LIST IS ORDERED BY RANK, best fit first: assessed postings first, then
    the not-assessed ones by rank score, then the ones not ranked yet ("not
    ranked yet"), in the search and in its SQL twin.
(3) RANKED LOW IS LISTED LOWER, NEVER HIDDEN (the operator's correction: the
    master may be missing real experience, and a re-rank must be able to lift
    the posting). A posting nothing assessed whose known rank is below
    ``fit.weak_fit_below_rank`` (50) is in every list, in rank order: after
    the other ranked postings not assessed yet, before the ones not ranked
    yet. Its row says ``ranked_low``, ``counts.ranked_low`` counts them, the
    terminal prints a plain "Ranked low (N)" line above them, and
    ``state=ranked_low`` is an optional filter that lists only them. An
    assessed posting with the same low rank keeps its place.
"""

from __future__ import annotations

from datetime import timedelta
import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.pipeline.store import PipelineStore, pipeline_path
from gigai.scout.quick_assess import read_quick_assessment

from tests.support.fit_fixtures import assess_one, matrix_answer, seed_rank
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job


def _search(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("profile_ids", [fx.default_profile_id])
    return posting_search.search_postings(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _these(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    kwargs.setdefault("profile_id", fx.default_profile_id)
    return posting_search.assess_these(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _rows(response: dict[str, object]) -> list[dict[str, object]]:
    return response["postings"]["rows"]  # type: ignore[index,return-value]


def _numbers(response: dict[str, object]) -> list[int]:
    return [int(str(row["job_identity"]).rsplit("-", 1)[1]) for row in _rows(response)]


def _assessed(fx: PostingsFixture, slug: str, numbers: range | list[int]) -> set[int]:
    return {n for n in numbers if read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, job_url(slug, n)) is not None}


# --- (1) the batch is the top 50 by rank ------------------------------------------------------


def _score(n: int) -> int:
    """60 scores from 50 to 100 (so none is below the assess threshold); the ten lowest are postings 1 to 10."""

    return 50 + n * 5 // 6


def test_the_ask_names_the_50_highest_ranked_of_60_and_the_approved_batch_assesses_exactly_those(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    numbers = range(1, 61)
    # Posting 1 is the NEWEST and has the LOWEST score: the newest 50 (1 to 50) and the top 50 by rank (11 to 60) differ.
    fx.seed("rk", [lever_job("rk", n, created=days_ago(2) - timedelta(minutes=n)) for n in numbers], seen_at=days_ago(1))
    seed_rank(fx, monkeypatch, {job_url("rk", n): _score(n) for n in numbers})
    assert max(_score(n) for n in range(1, 11)) < min(_score(n) for n in range(11, 61))
    top = set(range(11, 61))
    calls = fx.base.model.calls

    asked = _these(fx)

    assert asked["status"] == "ask" and fx.base.model.calls == calls  # nothing is assessed without approval
    question = asked["question"]
    assert (question["to_assess"], question["batch"], question["more_after"], question["low_rank_skipped"]) == (60, 50, 10, 0)  # type: ignore[index]
    assert set(_numbers(asked)) == top and len(_rows(asked)) == 50  # the 50 the ask names: the highest ranked
    assert str(question["text"]).startswith("Assess the top 50 by rank of 60 postings")  # type: ignore[index]
    assert str(question["text"]).endswith("(10 more after these 50)")  # type: ignore[index]
    # How far the background rank is, so a batch taken while it runs is known to be the top of a half-ranked list.
    (progress,) = [item for item in asked["ranking"]["by_profile"] if item["profile_id"] == fx.default_profile_id]  # type: ignore[index]
    assert (progress["ranked"], progress["total"]) == (60, 60)

    done = _these(fx, approve=True)

    assert fx.base.model.calls == calls + 50
    assert (done["assessed"]["requested"], done["assessed"]["assessed"], done["approval"]["jobs"]) == (50, 50, 50)  # type: ignore[index]
    assert done["counts"]["more_after"] == 10  # type: ignore[index]
    assert _assessed(fx, "rk", numbers) == top  # exactly the 50 highest ranked; the ten lowest wait

    # The same call again takes what is left; named jobs assess what is named, whatever their rank among the rest.
    named = _these(fx, jobs=[job_url("rk", 2), job_url("rk", 7)], approve=True)
    assert named["assessed"]["requested"] == 2 and _assessed(fx, "rk", range(1, 11)) == {2, 7}  # type: ignore[index]
    rest = _these(fx, approve=True)
    assert rest["assessed"]["requested"] == 8 and rest["counts"]["more_after"] == 0  # type: ignore[index]
    assert _assessed(fx, "rk", numbers) == set(numbers)


def test_a_posting_not_ranked_yet_comes_after_the_ranked_ones_in_a_batch_the_newest_first() -> None:
    pairs = [(f"https://jobs.example/{name}", "p") for name in ("a", "b", "c", "d", "e")]
    a, b, c, d, e = pairs
    ranks = {a: 60, b: None, c: 90, d: None, e: 60}
    dates = {a: "2026-10-01", b: "2026-10-02", c: "2026-09-01", d: "2026-10-03", e: "2026-10-02"}

    assert scout_new.top_ranked_batch(pairs, ranks, dates) == ([c, e, a, d, b], 0)  # 90; 60 (the newer first); then no rank, newest first
    assert scout_new.top_ranked_batch(pairs, ranks, dates, limit=2) == ([c, e], 3)
    # With nothing ranked it is the newest batch, as before ranking came back.
    assert scout_new.top_ranked_batch(pairs, {}, dates) == scout_new.newest_batch(pairs, dates)


def test_scout_new_yes_assesses_the_top_ranked_new_postings_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout_new, "BATCH_LIMIT", 3)  # the same rule with a small number
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    numbers = list(range(1, 6))
    # Posting 5 is the newest and ranked lowest; 1, 2, 3 are the top three by rank.
    fx.seed("fresh", [lever_job("fresh", n, created=days_ago(2) + timedelta(minutes=n)) for n in numbers], seen_at=days_ago(1))
    seed_rank(fx, monkeypatch, {job_url("fresh", n): 100 - 10 * n for n in numbers})

    asked = scout_new.scout_new(fx.home_root, fx.target, peek=True, profile_id=fx.default_profile_id, now=NOW)
    text = str(asked["question"]["text"])  # type: ignore[index]

    yes = scout_new.scout_new(fx.home_root, fx.target, peek=True, assess=True, profile_id=fx.default_profile_id, now=NOW)
    assert (yes["assessed"]["requested"], yes["assessed"]["more_after"]) == (3, 2)  # type: ignore[index]
    assert _assessed(fx, "fresh", numbers) == {1, 2, 3}
    assert "Assess the top 3 by rank of 5 not assessed yet? ~3 calls" in text


def test_the_ranking_line_is_said_only_while_the_rank_runs() -> None:
    running = {"enabled": True, "in_progress": True, "by_profile": [{"profile_id": "a", "ranked": 100, "total": 150}, {"profile_id": "b", "ranked": 20, "total": 23}]}
    assert posting_search.ranking_line(running) == (
        "Ranking is still running: 120 of 173 ranked. The order, and the top 50 by rank, are of what is ranked so far; "
        "a posting not ranked yet comes after the ranked ones."
    )
    assert posting_search.ranking_line({**running, "in_progress": False}) is None and posting_search.ranking_line(None) is None


# --- (2) the order, (3) ranked low is listed lower, never hidden -------------------------------

#: name -> (posting number, rank score or None). Seeded so that the day a posting went up says nothing about its rank.
_SCENE = {"r70": (1, 70), "r90": (2, 90), "r50": (3, 50), "r49": (4, 49), "r20": (5, 20), "unranked_a": (6, None), "unranked_b": (7, None)}


def _scene(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Seven not-assessed postings around the weak-fit rank (50), and one ASSESSED posting whose rank is low (20)."""

    fx.seed("ord", [lever_job("ord", n, created=days_ago(3) + timedelta(minutes=n)) for n in range(1, 9)], seen_at=days_ago(1))
    jobs = {name: job_url("ord", n) for name, (n, _rank) in _SCENE.items()} | {"assessed_r20": job_url("ord", 8)}
    # Four requirement rows: a real match (fewer than 4 is a thin posting, listed last: test_thin_posting.py).
    met = [("5+ years of Python", "hard", "met"), ("Kubernetes", "hard", "met"), ("Postgres", "hard", "met"), ("AWS", "hard", "met")]
    assess_one(fx, jobs["assessed_r20"], matrix_answer(met))
    seed_rank(fx, monkeypatch, {jobs[name]: score for name, (_n, score) in _SCENE.items() if score is not None} | {jobs["assessed_r20"]: 20})
    return jobs


def _names(response: dict[str, object], jobs: dict[str, str]) -> list[str]:
    by_url = {url: name for name, url in jobs.items()}
    return [by_url[str(row["job_identity"])] for row in _rows(response)]


_ALL = ["assessed_r20", "r90", "r70", "r50", "r49", "r20", "unranked_a", "unranked_b"]


def _line_of(output: str, text: str) -> int:
    (found,) = [number for number, line in enumerate(output.splitlines()) if text in line]
    return found


def test_the_list_is_ordered_by_rank_and_low_ranked_unassessed_postings_are_listed_lower_never_hidden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = _scene(fx, monkeypatch)

    listed = _search(fx)

    # EVERY posting is listed. Best fit first: the assessed one, then by rank score, so the two ranked below 50 come
    # after the other ranked ones; the ones not ranked yet last.
    assert _names(listed, jobs) == _ALL
    counts = listed["counts"]
    assert (counts["matched"], counts["shown"], counts["ranked_low"], counts["weak_fit"]) == (8, 8, 2, 0)  # type: ignore[index]
    assert counts["by_state"] == {"matched": 1, "not_assessed": 7}  # type: ignore[index]
    by_name = dict(zip(_names(listed, jobs), _rows(listed)))
    # Which rows the "Ranked low (2)" divider stands above: only the not-assessed ones with a KNOWN rank below 50.
    assert [name for name in _ALL if by_name[name]["ranked_low"]] == ["r49", "r20"]
    assert [(by_name[name]["state"], by_name[name]["score_text"]) for name in ("r49", "r20")] == [
        ("not_assessed", "rank 49 · not assessed"), ("not_assessed", "rank 20 · not assessed"),
    ]
    assert by_name["r90"]["score_text"] == "rank 90 · not assessed"
    # Not ranked yet: after the ranked ones, and it says so. Nothing says its rank is low.
    assert by_name["unranked_a"]["score_text"] == "not ranked yet · not assessed"
    # An ASSESSED posting keeps today's rules, whatever its rank: Matched, listed first.
    assert (by_name["assessed_r20"]["state"], by_name["assessed_r20"]["rank_score"]) == ("matched", 20)

    # The optional filter lists only them, by rank; "Not assessed" lists all seven, in the one order.
    low = _search(fx, states=["ranked_low"])
    assert _names(low, jobs) == ["r49", "r20"] and low["counts"]["matched"] == 2 and low["counts"]["ranked_low"] == 2  # type: ignore[index]
    assert _names(_search(fx, states=["not_assessed"]), jobs) == _ALL[1:]
    assert _search(fx, states=["assessed"])["counts"]["ranked_low"] == 0  # type: ignore[index]  # the count is of the rows listed
    # The other order (the day the posting went up) holds every posting too.
    assert sorted(_names(_search(fx, sort="newest_posted"), jobs)) == sorted(_ALL)
    # A page of the list is a slice of the same order: the ranked-low ones are on the page their rank puts them on.
    assert _names(_search(fx, limit=3, offset=3), jobs) == ["r50", "r49", "r20"]

    # The order's two halves agree: ``scout_new.order_key`` and its SQL twin (``pipeline.store._POSTING_ORDER``).
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        by_url = {url: name for name, url in jobs.items()}
        in_sql = [by_url[row.job] for row in store.postings_by_score(states=["not_assessed", "matched"], profile_id=fx.default_profile_id, limit=50)]
        rows = store.postings(profile_id=fx.default_profile_id)
    finally:
        store.close()
    by_key = [by_url[row.job] for _group, row in scout_new.in_order(([row], row) for row in rows)]
    assert in_sql == by_key == _ALL

    # "Assess these" still asks about them separately (the 0.1.10 assess threshold), never drops them.
    asked = _these(fx)
    assert (asked["question"]["to_assess"], asked["question"]["low_rank_skipped"]) == (5, 2)  # type: ignore[index]

    # The terminal: every posting, a plain "Ranked low (2)" line above the two, "Not ranked yet" below them.
    home = ["--home", str(fx.home_root), "--target", str(fx.target), "--profile", fx.default_profile_id]
    plain = CliRunner().invoke(cli, ["scout", "jobs", "list", *home])
    assert plain.exit_code == 0, plain.output
    assert "8 posting(s) match" in plain.output and "not listed" not in plain.output
    order = [_line_of(plain.output, text) for text in (jobs["r50"], "Ranked low (2)", jobs["r49"], jobs["r20"], "Not ranked yet", jobs["unranked_a"])]
    assert order == sorted(order) and plain.output.splitlines()[order[1]] == "Ranked low (2)", plain.output
    as_json = CliRunner().invoke(cli, ["scout", "jobs", "list", "--json", *home])
    assert [row["ranked_low"] for row in json.loads(as_json.output)["postings"]["rows"]] == [False, False, False, False, True, True, False, False]
    shown = CliRunner().invoke(cli, ["scout", "jobs", "list", "--state", "ranked_low", *home])
    assert shown.exit_code == 0 and jobs["r49"] in shown.output and jobs["r20"] in shown.output and jobs["r90"] not in shown.output, shown.output
    # In another order the ranked-low rows are spread through the list: no divider, and they are listed all the same.
    newest = posting_search.render(_search(fx, sort="newest_posted"))
    assert "Ranked low" not in newest and jobs["r20"] in newest and jobs["r49"] in newest

    # A ranked-low posting is a row like any other: named, it is assessed (the low-rank yes), and then reads assessed.
    done = _these(fx, jobs=[jobs["r20"]], approve=True, include_low_rank=True)
    assert done["assessed"]["assessed"] == 1  # type: ignore[index]
    after = _search(fx)
    assert after["counts"]["ranked_low"] == 1 and len(_rows(after)) == 8  # type: ignore[index]
    assert next(row for row in _rows(after) if row["job_identity"] == jobs["r20"])["state"] != "not_assessed"


def test_ranked_low_follows_the_weak_fit_rank_setting_and_never_hides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = _scene(fx, monkeypatch)
    path = settings_path(fx.home_root, fx.target)
    path.parent.mkdir(parents=True, exist_ok=True)

    def low(response: dict[str, object]) -> list[str]:
        return [name for name, row in zip(_names(response, jobs), _rows(response)) if row["ranked_low"]]

    path.write_text(json.dumps({"schema_version": "scout-settings:1", "fit": {"weak_fit_below_rank": 30}}), encoding="utf-8")
    relaxed = _search(fx)
    assert relaxed["counts"]["ranked_low"] == 1 and low(relaxed) == ["r20"] and _names(relaxed, jobs) == _ALL  # type: ignore[index]

    path.write_text(json.dumps({"schema_version": "scout-settings:1", "fit": {"weak_fit_below_rank": 0}}), encoding="utf-8")
    off = _search(fx)  # 0 switches the rule off: no row is ranked low; the order is the rank's all the same
    assert off["counts"]["ranked_low"] == 0 and low(off) == [] and _names(off, jobs) == _ALL  # type: ignore[index]


def test_scout_new_lists_the_ranked_low_postings_under_a_divider_and_marks_them_in_the_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    numbers = [1, 2, 3, 4, 5]
    scores = {1: 90, 2: 60, 3: 49, 4: 10}  # 5 is not ranked yet
    fx.seed("nw", [lever_job("nw", n, title=f"Staff AI Engineer {n}", created=days_ago(2) + timedelta(minutes=n)) for n in numbers], seen_at=days_ago(1))
    seed_rank(fx, monkeypatch, {job_url("nw", n): score for n, score in scores.items()})

    response = scout_new.scout_new(fx.home_root, fx.target, peek=True, assess=False, profile_id=fx.default_profile_id, now=NOW)

    assert _numbers(response) == numbers  # all five, in rank order: nothing is left out
    assert [row["ranked_low"] for row in _rows(response)] == [False, False, True, True, False]
    assert (response["counts"]["new"], response["counts"]["shown"], response["counts"]["ranked_low"]) == (5, 5, 2)  # type: ignore[index]
    table = scout_new.render(response).splitlines()
    order = [next(i for i, line in enumerate(table) if text in line) for text in ("Staff AI Engineer 2", "Ranked low (2)", "Staff AI Engineer 3", "Staff AI Engineer 4", "Not ranked yet", "Staff AI Engineer 5")]
    assert order == sorted(order) and table[order[1]] == "Ranked low (2)", "\n".join(table)

    shown = CliRunner().invoke(cli, [*fx.cli("--no-assess", "--peek", "--json", "--profile", fx.default_profile_id)])
    assert shown.exit_code == 0, shown.output
    rows = json.loads(shown.output)["postings"]["rows"]
    assert [(int(str(row["job_identity"]).rsplit("-", 1)[1]), row["ranked_low"]) for row in rows] == [(1, False), (2, False), (3, True), (4, True), (5, False)]


def test_ranking_progress_counts_only_what_the_rank_lane_will_rank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The lane ranks postings that went up in the last 7 days: an older one with no score is never "still to rank"."""

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    # Three that went up 2 days ago (one of them ranked), and two that went up 10 days ago and will never be ranked.
    fx.seed(
        "win", [lever_job("win", n, created=days_ago(2)) for n in (1, 2, 3)] + [lever_job("win", n, created=days_ago(10)) for n in (4, 5)],
        seen_at=days_ago(1),
    )
    seed_rank(fx, monkeypatch, {job_url("win", 1): 80})

    def progress(response: dict[str, object]) -> tuple[int, int]:
        (item,) = [entry for entry in response["ranking"]["by_profile"] if entry["profile_id"] == fx.default_profile_id]  # type: ignore[index]
        return item["ranked"], item["total"]

    listed = _search(fx)
    assert progress(listed) == (1, 3) and listed["ranking"]["window_days"] == 7  # type: ignore[index]
    assert len(_rows(listed)) == 5  # the old ones are still listed, "not ranked yet"; they are only not counted as waiting
    assert progress(_these(fx)) == (1, 3)

    # Everything inside the window ranked: nothing is left for the lane, although two old postings have no score.
    seed_rank(fx, monkeypatch, {job_url("win", 2): 70, job_url("win", 3): 60})
    done = _search(fx)
    assert progress(done) == (3, 3)
    assert not any(item["ranked"] < item["total"] for item in done["ranking"]["by_profile"] if item["profile_id"] == fx.default_profile_id)  # type: ignore[index]
    assert [row["score_text"] for row in _rows(done)][-2:] == ["not ranked yet · not assessed"] * 2
