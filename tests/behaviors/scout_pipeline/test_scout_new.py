"""0.1.10.7 M3a: ``gigai scout new`` (``scout/scout_new.py``), end outcomes on synthetic fixtures.

(a) Two active profiles and a deleted one: each new posting is listed once,
    tagged with the active profiles it matches, never the deleted one.
(b) The anchor: first use is the last 7 days; a plain call moves it after the
    response is built; ``--profile``, ``--peek`` do not; Mark all seen does;
    a call that fails after the response is built does not.
(c) Asking: new postings give the structured question (count, estimate) and
    no model call; ``--yes`` assesses with the fixture model and the grid then
    has scores; nothing new gives "Nothing new" and the top 10.
(d) Labels: NO response variant carries both labels. The default response
    holds posting text and nothing the user wrote; the user's own evidence of
    what matches is the separate ``yours`` call, which holds no posting text.
    A recruiter email in a posting passes (public); an email in the user's
    own evidence is removed by the outbound check.
"""

from __future__ import annotations

import json
from pathlib import Path

from click.testing import CliRunner
import pytest

from gigai.cli import cli
from gigai.scout import data_labels, scout_new
from gigai.scout.outbound_check import REDACTIONS_KEY, TOKENS, redact_payload
from gigai.scout.pipeline.steps import enqueue_job
from gigai.scout.pipeline.store import PipelineStore, pipeline_path

from tests.support.pipeline_fixtures import EMAIL_SHAPE, MARKERS
from tests.support.posting_fixtures import (
    NOW,
    RECRUITER_EMAIL,
    SECOND_LABEL,
    TITLE_SECOND_ONLY,
    PostingsFixture,
    build_postings_fixture,
    days_ago,
    job_url,
    lever_job,
    posting_text,
)

_OWN_EMAIL = "zed.private@own-mail.example"


def _new(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return scout_new.scout_new(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _anchor(fx: PostingsFixture):
    path = pipeline_path(fx.home_root, fx.target)
    if not path.is_file():
        return None
    store = PipelineStore(path)
    try:
        return store.anchor()
    finally:
        store.close()


def _cli(fx: PostingsFixture, *args: str) -> dict[str, object]:
    result = CliRunner().invoke(cli, [*fx.cli(*args), "--json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _rows(response: dict[str, object]) -> list[dict[str, object]]:
    return response["postings"]["rows"]  # type: ignore[index,return-value]


#: Words only the user's own text holds in these fixtures (the model's cited evidence, the stored answer).
_PRIVATE_WORDS = ("six years", "Two years on GCP", "ask my old lead", "Story bank")
#: Words only a posting holds.
_POSTING_WORDS = ("inference services", "Staff AI Engineer", "acme", "Terraform", "Remote - United States")


def _assert_labels(response: dict[str, object]) -> None:
    """(d): one response never mixes. The default response: posting text, nothing the user wrote. ``yours``: the reverse."""

    labels = set(scout_new.response_labels(response))
    data_labels.assert_not_mixed(labels, what="scout new")
    scout_new.check_response(response)
    dumped = json.dumps(response)
    if response["schema_version"] == scout_new.YOURS_SCHEMA_VERSION:
        assert labels == {data_labels.USER_PRIVATE} and set(response) >= {"evidence", "_labels"} and "postings" not in response
        # A posting is named by its link only: the link is taken out before looking for its words.
        bare = dumped
        for item in response["evidence"]:  # type: ignore[union-attr]
            bare = bare.replace(item["job_identity"], "")
        for word in _POSTING_WORDS:
            assert word not in bare, word
    else:
        assert labels == {data_labels.PUBLIC_UNTRUSTED} and "yours" not in response and "evidence" not in response
        for word in _PRIVATE_WORDS:
            assert word not in dumped, word
        assert all("matches" not in row for row in response["postings"]["rows"])  # type: ignore[index]
    assert "Old Search" not in dumped  # the deleted profile's tag
    for marker in MARKERS:  # the resume's contact header never leaves
        assert marker not in dumped


def _yours(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return scout_new.scout_new_yours(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _seed_week(fx: PostingsFixture) -> None:
    fx.seed(
        "acme",
        [
            lever_job("acme", 1, text=posting_text(1, extra=f" Questions? Write to {RECRUITER_EMAIL}."), salary=True),
            lever_job("acme", 2, title=TITLE_SECOND_ONLY),
        ],
        seen_at=days_ago(1),
    )
    fx.seed("old", [lever_job("old", 1)], seen_at=days_ago(20))


def test_a_each_new_posting_once_with_its_profile_tags_and_never_the_deleted_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    _seed_week(fx)

    response = _cli(fx)

    assert response["schema_version"] == "scout-new:1" and response["status"] == "ask"
    rows = _rows(response)
    assert sorted(row["job_identity"] for row in rows) == [job_url("acme", 1), job_url("acme", 2)]  # each once; the 20-day-old one is not new
    by_job = {row["job_identity"]: row for row in rows}
    both, second_only = by_job[job_url("acme", 1)], by_job[job_url("acme", 2)]
    assert [item["profile_id"] for item in both["profiles"]] == [fx.default_profile_id, fx.second_profile_id]  # type: ignore[union-attr]
    assert both["profile_id"] == fx.default_profile_id
    assert [item["profile_id"] for item in second_only["profiles"]] == [fx.second_profile_id]  # type: ignore[union-attr]
    assert fx.deleted_profile_id is not None and fx.deleted_profile_id not in json.dumps(response)
    tags = response["profiles"]
    assert [(item["profile_id"], item["label"]) for item in tags] == [(fx.default_profile_id, "default"), (fx.second_profile_id, SECOND_LABEL)]  # type: ignore[union-attr]
    # Each profile's resume used (ids), and the grid's details column.
    assert all(set(item["resume"]) == {"record_id", "revision_id"} for item in tags)  # type: ignore[union-attr]
    assert (both["company"], both["title"], both["work_mode"], both["salary"]) == ("acme", "Staff AI Engineer", "remote", "USD 180,000-220,000 per year")
    assert response["counts"] == {
        "new": 2, "to_assess": 2, "shown": 2,
        "by_profile": [{"profile_id": fx.default_profile_id, "new": 1}, {"profile_id": fx.second_profile_id, "new": 2}],
    }
    _assert_labels(response)

    # --profile shows that profile's own row, still with every tag.
    filtered = _cli(fx, "--profile", fx.second_profile_id, "--since", str(response["since"]))
    assert {row["profile_id"] for row in _rows(filtered)} == {fx.second_profile_id} and len(_rows(filtered)) == 2
    tagged = next(row for row in _rows(filtered) if row["job_identity"] == job_url("acme", 1))
    assert [item["profile_id"] for item in tagged["profiles"]] == [fx.default_profile_id, fx.second_profile_id]  # type: ignore[union-attr]
    # A deleted profile cannot be asked for: it is hidden.
    refused = CliRunner().invoke(cli, [*fx.cli("--profile", fx.deleted_profile_id), "--json"])
    assert refused.exit_code == 1 and json.loads(refused.output)["error"]["code"] == "profile_not_found"


def test_b_the_anchor_rules(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    _seed_week(fx)
    assert _anchor(fx) is None

    # --peek, --profile: first use is the last 7 days, and neither moves the anchor.
    peek = _new(fx, peek=True)
    assert (peek["since"], peek["since_source"]) == ("2026-09-26T15:00:00.000000Z", "first_use_7_days")
    assert peek["counts"]["new"] == 2 and peek["anchor"] == {"last_checked_at": None, "advances": False}  # type: ignore[index]
    assert _anchor(fx) is None
    filtered = _new(fx, profile_id=fx.second_profile_id)
    assert filtered["anchor"]["advances"] is False and _anchor(fx) is None  # type: ignore[index]

    # A call that fails after the response is built does not move it.
    def broken(response: object) -> None:
        raise data_labels.LabelError("forced after the build")

    with monkeypatch.context() as patched:
        patched.setattr(scout_new, "check_response", broken)
        with pytest.raises(data_labels.LabelError):
            _new(fx)
    assert _anchor(fx) is None

    # The plain call moves it, to the time it read the postings; the response still says what "new" was measured from.
    plain = _new(fx)
    assert plain["since_source"] == "first_use_7_days" and plain["counts"]["new"] == 2  # type: ignore[index]
    assert plain["anchor"] == {"last_checked_at": None, "advances": True}
    anchor = _anchor(fx)
    assert anchor is not None and (anchor.last_checked_at, anchor.set_by) == ("2026-10-03T15:00:00.000000Z", "scout_new")

    # A posting first seen after the anchor is new to the next call; a peek sees it and leaves it new.
    later = NOW.replace(hour=20)
    fx.seed("late", [lever_job("late", 1)], seen_at=NOW.replace(hour=18))
    for _ in range(2):
        looked = _new(fx, peek=True, now=later)
        assert (looked["since"], looked["since_source"], looked["counts"]["new"]) == ("2026-10-03T15:00:00.000000Z", "anchor", 1)  # type: ignore[index]
    assert _anchor(fx) == anchor
    seen = _new(fx, now=later)
    assert [row["job_identity"] for row in _rows(seen)] == [job_url("late", 1)]
    assert _anchor(fx).last_checked_at == "2026-10-03T20:00:00.000000Z"  # type: ignore[union-attr]
    assert _new(fx, now=later.replace(minute=5))["status"] == "nothing_new"
    # The yours call never moves it either.
    before = _anchor(fx)
    _yours(fx, now=later.replace(minute=20))
    assert _anchor(fx) == before

    # Mark all seen moves the same anchor; it never moves back.
    marked = scout_new.mark_all_seen(fx.home_root, fx.target, now=later.replace(minute=30))
    assert marked == {
        "schema_version": "scout-new-seen:1", "previous": "2026-10-03T20:05:00.000000Z",
        "last_checked_at": "2026-10-03T20:30:00.000000Z", "set_by": "mark_all_seen",
    }
    assert scout_new.mark_all_seen(fx.home_root, fx.target, now=NOW)["last_checked_at"] == "2026-10-03T20:30:00.000000Z"

    # The CLI's --peek, --profile and --yours leave it too.
    for args in (("--peek",), ("--profile", fx.second_profile_id), ("--yours",)):
        _cli(fx, *args)
        assert _anchor(fx).last_checked_at == "2026-10-03T20:30:00.000000Z"  # type: ignore[union-attr]


def test_c_new_postings_are_asked_about_and_assessed_only_on_a_yes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    _seed_week(fx)
    # A waiting pipeline job is offered, never started.
    from tests.support.pipeline_fixtures import assess_base

    assess_base(fx.base)
    enqueue_job(fx.default_profile_id, "https://jobs.example.test/acme/staff-ai-engineer", home_root=fx.home_root, target=fx.target)
    calls = fx.base.model.calls

    asked = _cli(fx)

    assert asked["status"] == "ask" and fx.base.model.calls == calls  # the question, and no model call
    question = asked["question"]
    assert question["kind"] == "assess_new" and (question["new"], question["to_assess"]) == (2, 2)  # type: ignore[index]
    assert question["by_profile"] == [  # type: ignore[index]
        {"profile_id": fx.default_profile_id, "count": 1}, {"profile_id": fx.second_profile_id, "count": 1},
    ]
    # The estimate comes from the recorded calls (call_metrics.estimate): one assess call is on record, 10 in + 20 out tokens.
    assert question["estimate"] == {"calls": 2, "tokens": 60, "seconds": question["estimate"]["seconds"], "cost": None, "basis_calls": 1}  # type: ignore[index]
    assert question["yes"]["cli"] == f"gigai scout new --yes --since {asked['since']}"  # type: ignore[index]
    sentence = question["text"]  # type: ignore[index]
    assert question["no"]["cli"] == f"gigai scout new --no-assess --since {asked['since']}"  # type: ignore[index]
    assert sentence == f"2 new postings across 2 profiles (default 1, {SECOND_LABEL} 1). Assess them? ~2 calls, ~60 tokens"
    assert all(row["score"] is None and row["assessment"] is None and row["needs_tailoring"] is None for row in _rows(asked))
    assert asked["pipeline"] == {
        "waiting": 1, "awaiting_approval": 0, "approvals": [], "est_calls": 2, "command": "gigai scout new --process",
        "text": "1 waiting, process now? ~2 calls",
    }
    assert asked["processed"] is None
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    assert {step.state for step in store.steps()} == {"ready", "blocked"}  # offered, not started
    store.close()
    _assert_labels(asked)

    # The yes: assessed with the fixture model (one call per posting, each for its best profile), and the grid has scores.
    fx.base.model.assess_prompts.clear()
    yes = _cli(fx, "--yes", "--since", str(asked["since"]))

    assert yes["status"] == "new" and yes["question"] is None
    assert yes["assessed"] == {"requested": 2, "assessed": 2, "failed": [], "stopped": None, "fetched_on_demand": 0}
    assert len(fx.base.model.assess_prompts) == 2
    for row in _rows(yes):
        assert (row["score"], row["score_kind"], row["state"]) == (100, "assessment", "matched")
        assert row["assessment"]["verdict"] == "matched_above_threshold" and row["needs_tailoring"] is False  # type: ignore[index]
    assert {row["profile_id"] for row in _rows(yes)} == {fx.default_profile_id, fx.second_profile_id}
    _assert_labels(yes)
    # "What matches" is the separate call, and the response says how to make it for the same postings.
    assert yes["yours_hint"]["available"] == 2 and yes["yours_hint"]["cli"] == f"gigai scout new --yours --since {yes['since']}"  # type: ignore[index]
    assert yes["yours_hint"]["api"] == {"method": "GET", "path": f"/api/new/yours?since={yes['since']}"}  # type: ignore[index]
    mine = _cli(fx, "--yours", "--since", str(yes["since"]))
    assert mine["schema_version"] == "scout-new-yours:1" and mine["status"] == "new"
    assert sorted(item["job_identity"] for item in mine["evidence"]) == [job_url("acme", 1), job_url("acme", 2)]  # type: ignore[union-attr]
    assert all(item["lines"] == ["six years", "Two years on GCP"] for item in mine["evidence"])  # type: ignore[union-attr]
    _assert_labels(mine)

    # Nothing new now: the sentence and the postings that still need attention, best score first.
    nothing = _cli(fx)
    assert nothing["status"] == "nothing_new" and nothing["question"] is None and nothing["counts"]["new"] == 0  # type: ignore[index]
    assert str(nothing["message"]).startswith("Nothing new since your last check (") and str(nothing["message"]).endswith("still need your attention:")
    scores = [row["score"] for row in _rows(nothing)]
    assert scores[:2] == [100, 100] and scores[2:] == [None]  # the two assessed ones, then the unassessed 20-day-old one
    assert fx.base.model.calls == calls + 2
    _assert_labels(nothing)

    # The terminal table: four columns, the profile tags, the offer.
    text = CliRunner().invoke(cli, fx.cli("--peek"))
    assert text.exit_code == 0, text.output
    assert "Details" in text.output and "Needs tailoring?" in text.output and "Open questions" in text.output
    assert f"[default, {SECOND_LABEL}]" in text.output and "100% of requirements met" in text.output
    assert "What matches, from your own resume and answers (a separate call): gigai scout new --yours --since" in text.output
    assert "six years" not in text.output  # the user's own evidence is never printed next to posting text
    own = CliRunner().invoke(cli, fx.cli("--yours"))
    assert own.exit_code == 0 and "+ six years" in own.output and "Staff AI Engineer" not in own.output, own.output
    both = CliRunner().invoke(cli, [*fx.cli("--yours", "--yes"), "--json"])
    assert both.exit_code == 1 and json.loads(both.output)["error"]["code"] == "invalid_value"
    assert "Pipeline: 1 waiting, process now? ~2 calls Run: gigai scout new --process" in text.output


def test_c_the_no_is_the_grid_with_rank_only_and_the_top_ten_is_ten(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("many", [lever_job("many", n) for n in range(54)], seen_at=days_ago(1))

    no = _new(fx, assess=False)
    assert no["status"] == "new" and no["question"] is None and no["assessed"] is None and fx.base.model.calls == 0
    # 54 new: all are counted (and would be asked about), the 50 with the best score are listed.
    assert no["counts"] == {**no["counts"], "new": 54, "to_assess": 54, "shown": 50} and len(_rows(no)) == scout_new.NEW_ROWS_LIMIT == 50  # type: ignore[dict-item]
    assert str(no["message"]).endswith("Showing the 50 with the best score.")

    nothing = _new(fx, now=NOW.replace(hour=16))
    assert nothing["status"] == "nothing_new" and len(_rows(nothing)) == scout_new.ATTENTION_LIMIT == 10
    assert str(nothing["message"]).endswith("Top 10 that still need your attention:")
    assert len({row["job_identity"] for row in _rows(nothing)}) == 10


def test_d_a_recruiter_email_in_a_posting_passes_and_an_email_in_the_users_own_text_is_removed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    _seed_week(fx)
    # The model cites the user's own answer as evidence, and that answer holds an email address.
    matrix = [
        {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": [f"Story bank cloud:gcp: ask my old lead, {_OWN_EMAIL}"]},
        {"requirement": "Terraform", "class": "askable", "status": "unmet", "resume_evidence": []},
    ]
    fx.base.model.assessed = json.dumps({
        "verdict": "pending_user_answers", "matrix": matrix, "suggestions": [], "not_a_match_reason": None,
        "questions": [{"question_id": "tooling:terraform", "question": "Have you used Terraform in production?", "requirement": "Terraform"}],
    })

    response = _new(fx, assess=True)

    assert response["assessed"] == {"requested": 2, "assessed": 2, "failed": [], "stopped": None, "fetched_on_demand": 0}
    _assert_labels(response)
    row = next(item for item in _rows(response) if item["job_identity"] == job_url("acme", 1))
    assert RECRUITER_EMAIL in row["description"]  # type: ignore[operator]
    assert row["unmet"] == ["Terraform"] and row["needs_tailoring"] is True and row["state"] == "needs_answers"
    assert row["open_questions"] == [{"question_id": "tooling:terraform", "question": "Have you used Terraform in production?"}]
    # The question is the question: no answer of the user's is quoted with it, and none of the user's evidence is here.
    assert "two years on gcp" not in json.dumps(response).lower() and _OWN_EMAIL not in json.dumps(response)

    sent = redact_payload(response)  # what every API response and the CLI's output pass through (B)
    assert sent is response and REDACTIONS_KEY not in sent  # nothing contact-shaped outside the posting's own text
    assert RECRUITER_EMAIL in json.dumps(sent["postings"])  # the posting's contact line is public: what the user applies with

    # The separate call holds the user's own evidence, and its email address is removed on the way out.
    mine = _yours(fx, since=str(response["since"]))
    _assert_labels(mine)
    assert len(mine["evidence"]) == 2 and all(_OWN_EMAIL in item["lines"][0] for item in mine["evidence"])  # type: ignore[arg-type,union-attr]
    assert RECRUITER_EMAIL not in json.dumps(mine)
    checked = redact_payload(mine)
    assert checked[REDACTIONS_KEY] == {"email": 2} and _OWN_EMAIL not in json.dumps(checked) and TOKENS["email"] in json.dumps(checked)
    assert EMAIL_SHAPE.findall(json.dumps(checked)) == []
    _assert_labels(checked)

    # The CLI prints the checked forms.
    printed = _cli(fx, "--peek")
    assert RECRUITER_EMAIL in json.dumps(printed["postings"]) and _OWN_EMAIL not in json.dumps(printed) and REDACTIONS_KEY not in printed
    own = _cli(fx, "--yours")
    assert own[REDACTIONS_KEY] == {"email": 2} and _OWN_EMAIL not in json.dumps(own)


def test_d_no_response_variant_carries_both_labels(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    _seed_week(fx)
    variants: list[dict[str, object]] = []
    variants.append(_new(fx, peek=True))                                    # ask
    variants.append(_yours(fx))                                             # yours, nothing assessed yet
    variants.append(_new(fx, assess=False, peek=True))                      # the no: rank only
    variants.append(_new(fx, profile_id=fx.second_profile_id))              # one profile
    variants.append(_new(fx, assess=True))                                  # the yes: assessed
    variants.append(_yours(fx, since=str(variants[-1]["since"])))           # yours, with evidence
    variants.append(_yours(fx, profile_id=fx.second_profile_id, since=str(variants[-2]["since"])))
    variants.append(_new(fx, now=NOW.replace(hour=16)))                     # nothing new: the top 10
    variants.append(_yours(fx, now=NOW.replace(hour=17)))                   # yours for the top 10
    assert [item["status"] for item in variants] == ["ask", "new", "new", "ask", "new", "new", "new", "nothing_new", "nothing_new"]
    assert len(variants[5]["evidence"]) == 2 and len(variants[8]["evidence"]) == 2  # type: ignore[arg-type]
    for response in variants:
        labels = set(scout_new.response_labels(response))
        assert labels in ({data_labels.PUBLIC_UNTRUSTED}, {data_labels.USER_PRIVATE}), response["schema_version"]
        assert not data_labels.mixes_private_with_untrusted(labels)
        _assert_labels(response)
    # And the check itself refuses a response that would carry both.
    mixed = {**variants[4], "evidence": variants[5]["evidence"], "_labels": variants[5]["_labels"]}
    with pytest.raises(data_labels.LabelError, match="scout new mixes public-untrusted text with user-private text"):
        scout_new.check_response(mixed)
