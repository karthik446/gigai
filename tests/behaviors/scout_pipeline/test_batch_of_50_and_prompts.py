"""0110-10-11 (the small UAT findings on 0.1.10.9, and the operator's rule): end outcomes on synthetic fixtures.

THE RULE: every batch offer and every batch command acts on the NEWEST 50 at a
time, never more. The offer says the real total and the 50.

(1) ``gigai scout new --no-assess`` never prompts: in a real terminal (a pty)
    it prints the offers and exits 0. It used to stop at "N have only an old
    assessment; re-assess? [y/N]:" and wait.
(2) The stale re-assess offer is the newest 50 by posting date, says the total
    and "N more after these 50", and ``reassess_stale`` assesses at most 50 a
    call; the assess-new offer, its low-rank question, "Assess these", the run's
    "Assess all new" queue and ``--process`` have the same cap.
(3) A run left at a prompt (Ctrl-C, or the end of input) does not consume
    "new": the next run measures from the anchor it found.
(4) 0.1.10.11 NA (the NEWUSER blocker): an asking call WITHOUT a terminal
    (``gigai scout new --json``, or no terminal at all) is a PREVIEW too. It
    used to move the "new since" mark as soon as its reply was built, so the
    documented next step, a bare ``gigai scout new --yes --json``, found
    "Nothing new" and assessed nothing. The mark moves when the question is
    answered: the yes, the no (``--no-assess``, what ``question.no`` names),
    never the ask.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from gigai.cli import cli
from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs.api import assess_all as assess_all_api
from gigai.scout.pipeline.store import PipelineStore, pipeline_path
from gigai.scout.quick_assess import read_quick_assessment

from tests.behaviors.scout_pipeline.test_scout_new_uat import _old_run
from tests.support.fit_fixtures import seed_rank
from tests.support.pipeline_fixtures import assess_base
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job
from tests.support.pty_cli import CTRL_C, CTRL_D, run_cli_in_pty

needs_pty = pytest.mark.skipif(sys.platform == "win32", reason="a pty is POSIX only")
#: How long a terminal run may take before it counts as waiting for an answer nobody gives (a run takes 2 to 5 s; a loaded CI box more).
_WAIT = 120


def _new(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return scout_new.scout_new(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _anchor(fx: PostingsFixture) -> str | None:
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        found = store.anchor()
        return None if found is None else found.last_checked_at
    finally:
        store.close()


def _terminal_scene(fx: PostingsFixture) -> tuple[datetime, str]:
    """What a CLI process (the real clock) reads: an anchor two days back, two new postings since, three with only an old assessment."""

    now = datetime.now(UTC)
    fx.seed("aged", [lever_job("aged", n, created=now - timedelta(days=6)) for n in (1, 2, 3)], seen_at=now - timedelta(days=5))
    _old_run(fx, [job_url("aged", n) for n in (1, 2, 3)])
    anchor = scout_new.mark_all_seen(fx.home_root, fx.target, now=now - timedelta(days=2))["last_checked_at"]
    fx.seed("fresh", [lever_job("fresh", n, created=now - timedelta(days=1)) for n in (1, 2)], seen_at=now - timedelta(days=1))
    return now, str(anchor)


def _peek(fx: PostingsFixture) -> dict[str, object]:
    """A look from a process with no terminal that moves nothing: what the NEXT run would measure from."""

    done = subprocess.run(
        [sys.executable, "-c", "import sys; from gigai.cli import cli; sys.exit(cli())", *fx.cli("--peek", "--json")],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return json.loads(done.stdout)


# --- (1) --no-assess never prompts ------------------------------------------------------------


@needs_pty
def test_no_assess_never_prompts_in_a_terminal_and_prints_the_offer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _terminal_scene(fx)

    run = run_cli_in_pty(fx.cli("--no-assess"), timeout=_WAIT)

    # It ended by itself (nobody answered anything), with the offer and its counts on the terminal.
    assert not run.timed_out, f"scout new --no-assess was still waiting in the terminal after {_WAIT} s:\n{run.output}"
    assert run.exit_code == 0, run.output
    assert "[y/N]" not in run.output, run.output
    assert "3 have only an old assessment; re-assess? ~3 calls" in run.output
    assert "Yes: gigai scout new --reassess-stale --since " in run.output
    assert "2 new postings since " in run.output

    # Without a terminal: the same offer, the same exit.
    plain = subprocess.run(
        [sys.executable, "-c", "import sys; from gigai.cli import cli; sys.exit(cli())", *fx.cli("--no-assess", "--peek")],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=120, check=False,
    )
    assert plain.returncode == 0, plain.stdout + plain.stderr
    assert "3 have only an old assessment; re-assess? ~3 calls" in plain.stdout and "[y/N]" not in plain.stdout


# --- (3) an abandoned prompt does not consume "new" -------------------------------------------


@needs_pty
def test_a_run_left_at_the_prompt_does_not_move_the_new_since_anchor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _now, original = _terminal_scene(fx)
    assert _anchor(fx) == original

    # Ctrl-C at "Assess them? [y/N]".
    left = run_cli_in_pty(fx.cli(), steps=[("[y/N]", CTRL_C)], timeout=_WAIT)
    assert not left.timed_out and left.exit_code == 1, left.output
    assert "2 new postings" in left.output and "Assess them?" in left.output and "Aborted" in left.output
    assert _anchor(fx) == original
    after = _peek(fx)
    assert (after["since"], after["since_source"], after["counts"]["new"]) == (original, "anchor", 2)  # type: ignore[index]

    # The end of input at the prompt (Ctrl-D): the same.
    ended = run_cli_in_pty(fx.cli(), steps=[("[y/N]", CTRL_D)], timeout=_WAIT)
    assert not ended.timed_out and ended.exit_code == 1, ended.output
    assert _anchor(fx) == original
    assert _peek(fx)["since"] == original

    # Answered (no to both questions): the run did its work, and now the anchor moves.
    answered = run_cli_in_pty(fx.cli(), steps=[("~2 calls [y/N]", b"n\n"), ("~3 calls [y/N]", b"n\n")], timeout=_WAIT)
    assert not answered.timed_out and answered.exit_code == 0, answered.output
    assert "2 new postings since " in answered.output
    moved = _anchor(fx)
    assert moved is not None and moved > original
    later = _peek(fx)
    assert (later["since"], later["status"], later["counts"]["new"]) == (moved, "nothing_new", 0)  # type: ignore[index]


def test_a_call_that_holds_the_anchor_moves_nothing_until_it_is_settled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("fresh", [lever_job("fresh", n) for n in (1, 2)], seen_at=days_ago(1))
    original = scout_new.mark_all_seen(fx.home_root, fx.target, now=days_ago(2))["last_checked_at"]

    held = _new(fx, advance=False)
    assert held["anchor"] == {"last_checked_at": original, "advances": False} and _anchor(fx) == original
    assert _new(fx, advance=False, now=NOW + timedelta(hours=1))["since"] == original  # nothing was consumed

    assert scout_new.settle_anchor(fx.home_root, fx.target, str(held["checked_at"])) == held["checked_at"]
    assert _anchor(fx) == held["checked_at"]
    assert scout_new.settle_anchor(fx.home_root, fx.target, str(original)) == held["checked_at"]  # never back
    # A plain call still moves it by itself, as before.
    plain = _new(fx, now=NOW + timedelta(hours=2))
    assert plain["anchor"]["advances"] is True and _anchor(fx) == plain["checked_at"]  # type: ignore[index]


# --- (4) an asking call without a terminal is a preview (0.1.10.11 NA) -------------------------


def _cli_json(fx: PostingsFixture, *args: str) -> dict[str, object]:
    """``gigai scout new ARGS --json`` with no terminal: what an agent runs. The real clock, like every CLI process."""

    result = CliRunner().invoke(cli, [*fx.cli(*args), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout)


def _jobs(response: dict[str, object]) -> list[str]:
    return sorted(row["job_identity"] for row in response["postings"]["rows"])  # type: ignore[index]


def _options(command: object) -> list[str]:
    """The options of a command a reply names (``gigai scout new --yes --since ...``), to run it as written."""

    words = str(command).split()
    assert words[:3] == ["gigai", "scout", "new"], command
    return words[3:]


def _assessed_jobs(fx: PostingsFixture, slug: str, numbers: tuple[int, ...]) -> list[str]:
    urls = [job_url(slug, n) for n in numbers]
    return sorted(url for url in urls if read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, url) is not None)


@pytest.mark.parametrize("scene", ["first_run", "after_an_earlier_check"])
def test_a_bare_yes_after_an_asking_call_assesses_exactly_what_the_ask_showed(
    scene: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    if scene == "first_run":
        # The NEWUSER run: no check was ever made, so "new" is the last 7 days.
        now = datetime.now(UTC)
        fx.seed("fresh", [lever_job("fresh", n, created=now - timedelta(days=1)) for n in (1, 2)], seen_at=now - timedelta(days=1))
        original, source = None, "first_use_7_days"
    else:
        _now, original = _terminal_scene(fx)  # and three postings with only an old assessment: never the plain yes's
        source = "anchor"
    fresh = [job_url("fresh", 1), job_url("fresh", 2)]
    calls = fx.base.model.calls

    asked = _cli_json(fx)

    assert (asked["status"], asked["since_source"], asked["counts"]["to_assess"]) == ("ask", source, 2)  # type: ignore[index]
    assert _jobs(asked) == fresh and asked["question"]["text"].startswith("2 new postings")  # type: ignore[index]
    assert fx.base.model.calls == calls  # asking calls no model

    # The next step as start.md and the shipped agent skill write it: a BARE --yes, no --since.
    yes = _cli_json(fx, "--yes")

    assert yes["status"] == "new" and yes["question"] is None, yes["message"]
    assert yes["assessed"] is not None, yes["message"]
    assert (yes["assessed"]["requested"], yes["assessed"]["assessed"], yes["assessed"]["failed"]) == (2, 2, [])  # type: ignore[index]
    assert fx.base.model.calls == calls + 2
    assert _jobs(yes) == _jobs(asked) and yes["since_source"] == source
    if original is not None:
        assert yes["since"] == asked["since"] == original  # measured from the mark the ask left where it was
    assert _assessed_jobs(fx, "fresh", (1, 2)) == fresh
    assert all(row["state"] != "not_assessed" for row in yes["postings"]["rows"])  # type: ignore[index]
    # The yes answered the question: now the mark moves, and the same call again has nothing left to ask.
    assert yes["anchor"]["advances"] is True and _anchor(fx) == yes["checked_at"]  # type: ignore[index]
    after = _cli_json(fx)
    assert (after["status"], after["question"], after["counts"]["new"]) == ("nothing_new", None, 0)  # type: ignore[index]
    assert fx.base.model.calls == calls + 2


def test_an_asking_call_is_a_preview_asked_twice_it_shows_the_same_postings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _now, original = _terminal_scene(fx)

    first = _cli_json(fx)
    text = CliRunner().invoke(cli, fx.cli())  # no terminal and no --json: the same preview, as text
    second = _cli_json(fx)

    assert text.exit_code == 0 and "2 new postings since " in text.output and "Assess them?" in text.output, text.output
    for reply in (first, second):
        assert (reply["status"], reply["since"], reply["since_source"]) == ("ask", original, "anchor")
        assert (reply["counts"]["new"], reply["counts"]["to_assess"]) == (2, 2)  # type: ignore[index]
        assert reply["anchor"] == {"last_checked_at": original, "advances": False}  # the reply says so itself
    assert _jobs(first) == _jobs(second) == [job_url("fresh", 1), job_url("fresh", 2)]
    assert first["question"] == second["question"]
    assert _anchor(fx) == original


def test_no_assess_is_an_answer_it_moves_the_mark(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _now, original = _terminal_scene(fx)
    calls = fx.base.model.calls

    looked = _cli_json(fx, "--no-assess")  # the user chose to just look

    assert (looked["status"], looked["question"], looked["counts"]["new"]) == ("new", None, 2)  # type: ignore[index]
    assert looked["anchor"] == {"last_checked_at": original, "advances": True}
    assert _anchor(fx) == looked["checked_at"] and str(looked["checked_at"]) > original
    after = _cli_json(fx)
    assert (after["status"], after["since"], after["counts"]["new"]) == ("nothing_new", looked["checked_at"], 0)  # type: ignore[index]
    assert fx.base.model.calls == calls


def test_the_explicit_no_of_an_asking_call_moves_the_mark_and_the_ask_before_it_did_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _now, original = _terminal_scene(fx)
    calls = fx.base.model.calls

    asked = _cli_json(fx)

    # The explicit no, as the reply names it: --no-assess for the same window (the API: {"assess": false, "since": ...}).
    no = asked["question"]["no"]  # type: ignore[index]
    assert no["cli"] == f"gigai scout new --no-assess --since {original}"
    assert no["api"] == {"method": "POST", "path": "/api/new", "body": {"assess": False, "since": original}}
    assert _anchor(fx) == original  # the question is still open: nothing moved

    declined = _cli_json(fx, *_options(no["cli"]))

    assert (declined["status"], declined["question"], declined["assessed"]) == ("new", None, None)
    assert _jobs(declined) == _jobs(asked) and declined["anchor"]["advances"] is True  # type: ignore[index]
    assert _anchor(fx) == declined["checked_at"]
    after = _cli_json(fx)
    assert (after["status"], after["counts"]["new"]) == ("nothing_new", 0)  # type: ignore[index]
    assert fx.base.model.calls == calls and _assessed_jobs(fx, "fresh", (1, 2)) == []


def test_the_yes_command_an_asking_call_names_still_assesses_them(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    _now, original = _terminal_scene(fx)
    calls = fx.base.model.calls

    asked = _cli_json(fx)
    command = asked["question"]["yes"]["cli"]  # type: ignore[index]
    assert command == f"gigai scout new --yes --since {original}"

    yes = _cli_json(fx, *_options(command))

    assert (yes["assessed"]["requested"], yes["assessed"]["assessed"], yes["assessed"]["failed"]) == (2, 2, [])  # type: ignore[index]
    assert fx.base.model.calls == calls + 2 and _jobs(yes) == _jobs(asked)
    assert _anchor(fx) == yes["checked_at"]
    assert _cli_json(fx)["status"] == "nothing_new"


def test_only_the_assess_new_question_makes_a_reply_a_preview(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("fresh", [lever_job("fresh", n) for n in (1, 2)], seen_at=days_ago(1))
    original = scout_new.mark_all_seen(fx.home_root, fx.target, now=days_ago(2))["last_checked_at"]

    # The one definition: a reply that carries the question (status "ask") is a preview; any other reply is not.
    assert scout_new.is_preview({"status": "ask"}) is True
    assert [scout_new.is_preview({"status": status}) for status in ("new", "nothing_new")] == [False, False]

    asked = _new(fx)
    assert scout_new.is_preview(asked) and asked["anchor"]["advances"] is False and _anchor(fx) == original  # type: ignore[index]
    # Never moved by --peek, --profile or --process either, asked or not (as before).
    for kwargs in ({"peek": True}, {"profile_id": fx.default_profile_id}, {"peek": True, "assess": False}):
        assert _new(fx, **kwargs)["anchor"]["advances"] is False and _anchor(fx) == original  # type: ignore[index]
    # A reply with nothing to ask moves it, as before: here every new posting is assessed already.
    _new(fx, peek=True, assess=True)
    plain = _new(fx, now=NOW + timedelta(hours=1))
    assert (plain["status"], plain["question"], plain["counts"]["new"]) == ("new", None, 2)  # type: ignore[index]
    assert not scout_new.is_preview(plain) and plain["anchor"]["advances"] is True  # type: ignore[index]
    assert _anchor(fx) == plain["checked_at"]


# --- (2) the newest 50 at a time --------------------------------------------------------------


def test_the_rule_is_50() -> None:
    assert scout_new.BATCH_LIMIT == 50


def test_newest_is_the_day_it_went_up_and_first_seen_when_the_board_gives_none() -> None:
    def row(job: str, published: str | None, seen: str) -> SimpleNamespace:
        return SimpleNamespace(job=job, published_at=published, first_seen=seen)

    posted = row("https://x.test/a", "2026-09-01T00:00:00.000000Z", "2026-10-01T00:00:00.000000Z")
    undated = row("https://x.test/b", None, "2026-09-20T00:00:00.000000Z")
    assert scout_new.batch_date(posted) == "2026-09-01T00:00:00.000000Z"  # type: ignore[arg-type]  # an old posting Scout saw late is old
    assert scout_new.batch_date(undated) == "2026-09-20T00:00:00.000000Z"  # type: ignore[arg-type]
    pairs = [(item.job, "p") for item in (posted, undated)]
    dates = {(item.job, "p"): scout_new.batch_date(item) for item in (posted, undated)}  # type: ignore[arg-type]
    assert scout_new.newest_batch(pairs, dates, limit=1) == ([(undated.job, "p")], 1)
    assert scout_new.newest_batch(pairs, dates) == ([(undated.job, "p"), (posted.job, "p")], 0)


def test_the_stale_offer_is_the_newest_50_of_all_and_a_yes_re_assesses_50_never_more(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    # 53 postings an old run assessed. All first seen at one moment; posted a minute apart, number 53 the newest.
    numbers = list(range(1, 54))
    fx.seed(
        "aged", [lever_job("aged", n, created=days_ago(21) + timedelta(minutes=n)) for n in numbers], seen_at=days_ago(20),
    )
    _old_run(fx, [job_url("aged", n) for n in numbers])
    assess_base(fx.base)  # one assess call on record: 30 tokens, what the estimate is made from
    calls = fx.base.model.calls

    asked = _new(fx, peek=True)

    stale = asked["stale_question"]
    assert asked["counts"]["only_stale"] == 53  # type: ignore[index]
    assert stale["text"] == "53 have only an old assessment; re-assess the top 50 by rank of 53? ~50 calls, ~2k tokens (3 more after these 50)"  # type: ignore[index]
    assert (stale["to_reassess"], stale["batch"], stale["more_after"]) == (53, 50, 3)  # type: ignore[index]
    assert stale["estimate"]["calls"] == 50 and stale["estimate"]["tokens"] == 50 * 30  # type: ignore[index]
    assert fx.base.model.calls == calls  # an offer calls no model
    shown = scout_new.render(asked)
    assert "re-assess the top 50 by rank of 53? ~50 calls" in shown and "(3 more after these 50)" in shown

    done = _new(fx, peek=True, assess=False, reassess_stale=True)

    assert fx.base.model.calls == calls + 50
    again = done["reassessed"]
    assert (again["requested"], again["assessed"], again["failed"], again["more_after"]) == (50, 50, [], 3)  # type: ignore[index]
    assert again["next"]["cli"] == f"gigai scout new --reassess-stale --since {done['since']}"  # type: ignore[index]
    # The three left are the three OLDEST postings: the newest 50 went first.
    for n in numbers:
        current = read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, job_url("aged", n))
        assert (current is not None) == (n > 3), n
    left = done["stale_question"]
    assert (left["to_reassess"], left["batch"], left["more_after"]) == (3, 3, 0)  # type: ignore[index]
    assert left["text"] == "3 have only an old assessment; re-assess? ~3 calls, ~90 tokens"  # type: ignore[index]
    assert "3 more with only an old assessment: 50 at a time. Next: gigai scout new --reassess-stale --since " in scout_new.render(done)

    rest = _new(fx, peek=True, assess=False, reassess_stale=True)
    assert rest["reassessed"]["requested"] == 3 and "more_after" not in rest["reassessed"] and rest["stale_question"] is None  # type: ignore[index,operator]
    assert fx.base.model.calls == calls + 53


def test_the_assess_new_yes_and_its_low_rank_question_are_the_newest_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout_new, "BATCH_LIMIT", 3)  # the same rule with a small number: the 50 itself is pinned above
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    high, low = list(range(1, 6)), list(range(6, 11))
    fx.seed("fresh", [lever_job("fresh", n, created=days_ago(2) + timedelta(minutes=n)) for n in high + low], seen_at=days_ago(1))
    seed_rank(fx, monkeypatch, {job_url("fresh", n): 80 for n in high} | {job_url("fresh", n): 20 for n in low})
    calls = fx.base.model.calls

    asked = _new(fx, peek=True)

    question, low_question = asked["question"], asked["low_rank_question"]
    assert (question["to_assess"], question["batch"], question["more_after"], question["low_rank_skipped"]) == (5, 3, 2, 5)  # type: ignore[index]
    assert question["estimate"]["calls"] == 3  # type: ignore[index]
    assert str(question["text"]).startswith("10 new postings")  # type: ignore[index]
    assert "Assess the top 3 by rank of 5 not assessed yet (5 low-ranked ones are a separate question)? ~3 calls" in str(question["text"])  # type: ignore[index]
    assert str(question["text"]).endswith("(2 more after these 3)")  # type: ignore[index]
    assert (low_question["skipped"], low_question["batch"], low_question["more_after"]) == (5, 3, 2)  # type: ignore[index]
    assert low_question["text"] == "5 low-ranked ones are skipped (rank below 50); assess the top 3 by rank of those too? ~3 calls (2 more after these 3)"  # type: ignore[index]

    yes = _new(fx, peek=True, assess=True)

    assert fx.base.model.calls == calls + 3
    assert (yes["assessed"]["requested"], yes["assessed"]["more_after"]) == (3, 2)  # type: ignore[index]
    assert yes["assessed"]["next"]["cli"] == f"gigai scout new --yes --since {yes['since']}"  # type: ignore[index]
    assessed = {n for n in high + low if read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, job_url("fresh", n))}
    assert assessed == {3, 4, 5}  # the newest three of the ones ranked 50 or more
    assert "2 more not assessed yet: 50 at a time. Next: gigai scout new --yes --since " in scout_new.render(yes)

    _new(fx, peek=True, assess=True)  # the next batch: the two left
    assert fx.base.model.calls == calls + 5
    # The low-ranked ones are still their own question, and its yes is a batch too: the newest three of them.
    both = _new(fx, peek=True, assess=True, include_low_rank=True)
    assert fx.base.model.calls == calls + 8 and both["assessed"]["more_after"] == 2  # type: ignore[index]
    assert both["assessed"]["next"]["cli"] == f"gigai scout new --yes --include-low-rank --since {both['since']}"  # type: ignore[index]
    assessed = {n for n in low if read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, job_url("fresh", n))}
    assert assessed == {8, 9, 10}


def test_one_call_with_both_yeses_still_makes_at_most_one_batch_of_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout_new, "BATCH_LIMIT", 3)
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("aged", [lever_job("aged", n, created=days_ago(21) + timedelta(minutes=n)) for n in (1, 2, 3)], seen_at=days_ago(20))
    _old_run(fx, [job_url("aged", n) for n in (1, 2, 3)])
    fx.seed("fresh", [lever_job("fresh", n, created=days_ago(2) + timedelta(minutes=n)) for n in (1, 2)], seen_at=days_ago(1))
    calls = fx.base.model.calls

    both = _new(fx, peek=True, assess=True, reassess_stale=True)

    # --yes --reassess-stale: the two new ones first, then ONE old assessment in what is left of the three.
    assert fx.base.model.calls == calls + 3
    assert both["assessed"]["requested"] == 2 and both["reassessed"]["requested"] == 1  # type: ignore[index]
    assert both["reassessed"]["more_after"] == 2 and both["stale_question"]["to_reassess"] == 2  # type: ignore[index]
    assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, job_url("aged", 3)) is not None  # the newest old one


def test_assess_these_is_the_newest_batch_and_says_the_total(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scout_new, "BATCH_LIMIT", 3)
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    numbers = list(range(1, 6))
    fx.seed("fresh", [lever_job("fresh", n, created=days_ago(2) + timedelta(minutes=n)) for n in numbers], seen_at=days_ago(1))
    jobs = [job_url("fresh", n) for n in numbers]
    calls = fx.base.model.calls

    asked = posting_search.assess_these(fx.home_root, fx.target, jobs=jobs, now=NOW)

    assert asked["status"] == "ask" and fx.base.model.calls == calls
    question = asked["question"]
    assert (question["to_assess"], question["batch"], question["more_after"], question["estimate"]["calls"]) == (5, 3, 2, 3)  # type: ignore[index]
    assert str(question["text"]).startswith("Assess the top 3 by rank of 5 postings") and str(question["text"]).endswith("(2 more after these 3)")  # type: ignore[index]
    assert (asked["counts"]["to_assess"], asked["counts"]["batch"], asked["counts"]["more_after"]) == (5, 3, 2)  # type: ignore[index]

    done = posting_search.assess_these(fx.home_root, fx.target, jobs=jobs, approve=True, now=NOW)

    assert fx.base.model.calls == calls + 3
    assert done["assessed"]["requested"] == 3 and done["approval"]["jobs"] == 3  # type: ignore[index]
    assert {n for n in numbers if read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, job_url("fresh", n))} == {3, 4, 5}
    assert "2 more not assessed yet: 50 at a time. Run the same command again for the next 50." in posting_search.render(done)
    # The same call again: the two left, and then nothing.
    rest = posting_search.assess_these(fx.home_root, fx.target, jobs=jobs, approve=True, now=NOW)
    assert rest["assessed"]["requested"] == 2 and rest["counts"]["more_after"] == 0 and fx.base.model.calls == calls + 5  # type: ignore[index]


def test_assess_all_new_takes_the_newest_50_of_its_queue_in_the_queues_order() -> None:
    queue = [SimpleNamespace(normalized_url=f"https://x.test/{n}") for n in range(1, 61)]
    # Number 60 is the newest; 5 and 6 have no date.
    published = {item.normalized_url: None if n in (5, 6) else f"2026-09-{1 + n // 3:02d}T00:{n:02d}:00Z" for n, item in enumerate(queue, start=1)}

    batch, later = assess_all_api.newest_queue(queue, published)

    assert (len(batch), later) == (50, 10)
    left = [item.normalized_url.rsplit("/", 1)[1] for item in queue if item not in batch]
    assert left == ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]  # the ten oldest; an undated one is not "newest"
    assert batch == [item for item in queue if item in batch]  # the grid's order is kept inside the batch
    assert assess_all_api.newest_queue(queue[:50], published) == (queue[:50], 0)


def test_the_pipeline_offer_says_the_50_and_process_runs_at_most_50_steps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from gigai.scout.pipeline import runner, triggers

    asked: dict[str, object] = {}

    def run_once(home_root, target, **kwargs):  # noqa: ANN001, ANN003, ANN202
        asked.update(kwargs)
        return SimpleNamespace(to_json=lambda: {"state": "idle", "steps": []})

    monkeypatch.setattr(runner, "run_once", run_once)
    monkeypatch.setattr(triggers, "approve_all", lambda *args, **kwargs: [])
    scout_new.process_waiting(tmp_path, tmp_path)
    assert asked["max_steps"] == 50

    def offer(count: int) -> dict[str, object]:
        steps = [SimpleNamespace(state="ready", profile_id="p", job=f"https://x.test/{n}", approval_id=None, name="tailor") for n in range(count)]
        found = scout_new._pipeline_offer(SimpleNamespace(steps=lambda: steps))  # type: ignore[arg-type]
        assert found is not None
        return found

    assert offer(50)["text"] == "50 waiting, process now? ~50 calls" and "batch" not in offer(50)
    many = offer(60)
    assert many["text"] == "60 waiting, process the next 50 of 60 steps now? ~60 calls in all, at most 50 a run"
    assert (many["steps"], many["batch"]) == (60, 50)
