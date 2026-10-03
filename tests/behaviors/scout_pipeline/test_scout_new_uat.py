"""0.1.10.8 U3: the read-model / ``scout new`` UAT tickets, end outcomes on synthetic fixtures.

``0110-8-01`` The best tag prefers a profile with a CURRENT assessment, and
    ``to_assess`` counts the postings no profile has assessed. Neither moves
    while the background rank fills in another profile's scores.
``0110-8-04`` The grid orders current verdicts, then stale ones, then the
    not-assessed; inside a group by verdict, rank score, share of
    requirements met, recency. The score column says the verdict, "N of M"
    and the rank. ``scout new`` and the Jobs search order the same way.
``0110-8-08`` Postings that have only an old assessment are their OWN
    question, with the count and the estimate; ``--yes`` assesses the new
    ones only, ``--reassess-stale`` the old ones. A stale row is labelled.
``0110-8-12`` A tailored posting keeps its verdict state and stays under the
    profile that tailored it.
``0110-8-13`` A run's prompt version and constraints digest survive the
    import; a run sealed before versions had names is named as such.
``0110-8-14`` A batch prints progress lines to stderr (stdout stays JSON) and
    the response carries ranked/total per profile.

No response variant mixes posting text with the user's own
(``data_labels.assert_not_mixed``).
"""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
from types import SimpleNamespace

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes, parse_json_bytes
from gigai.cli import cli
from gigai.scout import data_labels, posting_search, postings, run_history, scout_new
from gigai.scout.assessment_core import ASSESS_PROMPT_VERSION
from gigai.scout.find_jobs.assess_contracts import AssessJobInput, AssessResumeInput
from gigai.scout.find_jobs.contracts import AssessOutput
from gigai.scout.find_jobs.model_rank import _hex, cache_dir, cache_key, prefs_digest
from gigai.scout.find_jobs.rank_digest import resume_digest
from gigai.scout.find_jobs.rank_run import rank_prefs
from gigai.scout.pipeline.store import PipelineStore, RunAssessment, pipeline_path
from gigai.scout.tailored_resume import TailorRequest, run_tailored_resume

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import RESUME, assess_base, assessment, config, resolved_job
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job

_OLD_RUN = "run_20260930T100000Z"
_PINNED = {"record_id": "rec_0001", "revision_id": "rev_0002", "content_sha256": "sha256:" + "9" * 64}
_CONSTRAINTS = "sha256:" + "c" * 64


def _new(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return scout_new.scout_new(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _search(fx: PostingsFixture, **kwargs: object) -> dict[str, object]:
    kwargs.setdefault("now", NOW)
    return posting_search.search_postings(fx.home_root, fx.target, **kwargs)  # type: ignore[arg-type]


def _rows(response: dict[str, object]) -> list[dict[str, object]]:
    return response["postings"]["rows"]  # type: ignore[index,return-value]


def _store(fx: PostingsFixture) -> PipelineStore:
    return PipelineStore(pipeline_path(fx.home_root, fx.target))


def _seed_rank(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, profile_id: str, scores: dict[str, int]) -> None:
    """What the background rank does: a score in the home's rank cache for ``profile_id``, written as the ranker writes it."""

    monkeypatch.setattr("gigai.config.load_config", fixture_config)
    done = postings.refresh(fx.home_root, fx.target, now=NOW)
    view = next(item for item in done.profiles if item.profile_id == profile_id)
    prefs = rank_prefs(view.config)  # type: ignore[arg-type]
    model = postings.rank_model_key(fx.home_root, fx.target)
    assert model is not None
    store = _store(fx)
    try:
        digests = {row.job: row.listing_digest for row in store.postings(profile_id=profile_id, live=False)}
    finally:
        store.close()
    cache_dir(fx.home_root).mkdir(parents=True, exist_ok=True)
    for job, score in scores.items():
        key = cache_key(
            content_sha256=digests[job], resume_digest_sha256=_hex({"resume_digest": resume_digest(RESUME, prefs)}),
            prefs_sha256=prefs_digest(prefs), model=model,
        )
        (cache_dir(fx.home_root) / f"{key}.json").write_text(json.dumps({"score": score, "reasons": [], "blockers": []}), encoding="utf-8")


def _assess(fx: PostingsFixture, job: str, answer: str, *, profile_id: str | None = None) -> None:
    """One posting assessed from its stored text ("Assess these"), the scripted model answering ``answer``."""

    fx.base.model.assessed = answer
    done = posting_search.assess_these(fx.home_root, fx.target, jobs=[job], profile_id=profile_id, approve=True, now=NOW)
    assert done["assessed"] == {"requested": 1, "assessed": 1, "failed": [], "stopped": None}, done["assessed"]
    fx.base.model.assessed = assessment(met=2)


def _one_requirement() -> str:
    rows = [{"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]}]
    return json.dumps({"verdict": "matched_above_threshold", "matrix": rows, "suggestions": [], "questions": [], "not_a_match_reason": None})


def _needs_answers() -> str:
    rows = [
        {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
        {"requirement": "Terraform", "class": "askable", "status": "unmet", "resume_evidence": []},
    ]
    questions = [{"question_id": "tooling:terraform", "question": "Have you used Terraform in production?", "requirement": "Terraform"}]
    return json.dumps({"verdict": "pending_user_answers", "matrix": rows, "suggestions": [], "not_a_match_reason": None, "questions": questions})


def _not_a_match() -> str:
    rows = [
        {"requirement": "5+ years of Python", "class": "hard", "status": "met", "resume_evidence": ["six years"]},
        {"requirement": "An active security clearance", "class": "hard", "status": "unmet", "resume_evidence": []},
    ]
    return json.dumps({
        "verdict": "not_a_match", "matrix": rows, "suggestions": [], "questions": [],
        "not_a_match_reason": "An active security clearance is required and the resume shows none.",
    })


def _old_run(fx: PostingsFixture, jobs: list[str], *, profile_id: str | None = None, met: int = 3) -> None:
    """``jobs`` as an old find-jobs run assessed them (imported rows: an older prompt, no detail in the quick store)."""

    owner = profile_id or fx.default_profile_id
    store = _store(fx)
    try:
        written = store.import_run(_OLD_RUN, owner, [
            RunAssessment(
                run_id=_OLD_RUN, job=job, profile_id=owner, state="matched", assessed_at="2026-09-30T10:00:00.000000Z",
                reqs_met=met, reqs_total=3, open_questions=0, listing_digest=None, prompt_version="assess-prompt-v4",
                constraints_digest=_CONSTRAINTS, bank_digest=None, profile_revision=1, profile_digest=None, pinned_record=None,
                pinned_revision=None, pinned_digest=None, model_target="codex_cli", adapter="codex_cli",
            )
            for job in jobs
        ])
    finally:
        store.close()
    assert written == len(jobs)


def _fail_for(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch, marker: str) -> None:
    """The scripted model answers nonsense for a posting whose prompt holds ``marker`` (a failed assessment)."""

    real = fx.base.model.answer

    def answer(prompt: str):
        result = real(prompt)
        return replace(result, output_text="this is not an assessment") if marker in prompt else result

    monkeypatch.setattr(fx.base.model, "answer", answer)


# --- 0110-8-01 ------------------------------------------------------------------------------


def test_01_the_best_tag_stays_with_the_profile_that_has_a_current_assessment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    job = job_url("acme", 1)
    fx.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))  # both active profiles match it
    _assess(fx, job, assessment(met=2), profile_id=fx.second_profile_id)  # assessed for profile B only

    before = _new(fx, peek=True)
    row = _rows(before)[0]
    assert (row["profile_id"], row["state"], row["stale_reason"]) == (fx.second_profile_id, "matched", None)
    assert before["counts"]["to_assess"] == 0 and before["question"] is None  # type: ignore[index]

    # The background rank scores the posting for profile A (the default), where nothing is assessed: higher than B's.
    _seed_rank(fx, monkeypatch, fx.default_profile_id, {job: 99})

    after = _new(fx, peek=True)
    row = _rows(after)[0]
    assert [(item["profile_id"], item["rank_score"]) for item in row["profiles"]] == [  # type: ignore[union-attr]
        (fx.second_profile_id, None), (fx.default_profile_id, 99),
    ]
    assert (row["profile_id"], row["state"]) == (fx.second_profile_id, "matched")  # not flipped to A's unassessed row
    assert after["counts"]["to_assess"] == 0 and after["question"] is None and after["status"] == "new"  # type: ignore[index]


def test_01_to_assess_after_a_full_yes_is_the_failures_only_and_does_not_move_while_the_rank_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    fx.seed("acme", [lever_job("acme", n) for n in range(1, 7)], seen_at=days_ago(1))
    _fail_for(fx, monkeypatch, "Posting 3:")

    yes = _new(fx, assess=True)

    assert yes["assessed"]["requested"] == 6 and yes["assessed"]["assessed"] == 5  # type: ignore[index]
    assert [item["job_identity"] for item in yes["assessed"]["failed"]] == [job_url("acme", 3)]  # type: ignore[index,union-attr]
    since = str(yes["since"])
    first = _new(fx, peek=True, since=since)
    assert first["counts"]["to_assess"] == 1  # type: ignore[index]

    # The background rank now works through the OTHER profile: every posting gets a higher score there.
    jobs = [job_url("acme", n) for n in range(1, 7)]
    seen = []
    for done in (2, 4, 6):
        _seed_rank(fx, monkeypatch, fx.second_profile_id, {job: 90 + index for index, job in enumerate(jobs[:done])})
        peek = _new(fx, peek=True, since=since)
        seen.append(peek["counts"]["to_assess"])  # type: ignore[index]
        assessed = [row for row in _rows(peek) if row["job_identity"] != job_url("acme", 3)]
        assert {(row["profile_id"], row["state"]) for row in assessed} == {(fx.default_profile_id, "matched")}
        assert peek["question"]["to_assess"] == 1  # type: ignore[index]
    assert seen == [1, 1, 1]  # the failure only, whatever the rank has done so far


# --- 0110-8-04 ------------------------------------------------------------------------------


def _mixed(fx: PostingsFixture, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Seven postings: three current verdicts, a 1-of-1, an old run's 3 of 3, and two never assessed."""

    fx.seed("mix", [lever_job("mix", n) for n in range(1, 8)], seen_at=days_ago(1))
    jobs = {name: job_url("mix", n) for n, name in enumerate(("matched", "one_of_one", "answers", "old", "ranked", "unranked", "no"), start=1)}
    _assess(fx, jobs["matched"], assessment(met=2), profile_id=fx.default_profile_id)
    _assess(fx, jobs["one_of_one"], _one_requirement(), profile_id=fx.default_profile_id)
    _assess(fx, jobs["answers"], _needs_answers(), profile_id=fx.default_profile_id)
    _assess(fx, jobs["no"], _not_a_match(), profile_id=fx.default_profile_id)
    _old_run(fx, [jobs["old"]])
    _seed_rank(fx, monkeypatch, fx.default_profile_id, {
        jobs["matched"]: 60, jobs["one_of_one"]: 40, jobs["answers"]: 90, jobs["old"]: 95, jobs["ranked"]: 97, jobs["no"]: 99,
    })
    return jobs


def test_04_the_grid_orders_current_then_stale_then_not_assessed_and_says_n_of_m(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    jobs = _mixed(fx, monkeypatch)
    names = {url: name for name, url in jobs.items()}
    expected = ["matched", "one_of_one", "answers", "no", "old", "ranked", "unranked"]

    grid = _new(fx, assess=False, peek=True)
    listed = _search(fx)

    for response in (grid, listed):
        rows = _rows(response)
        assert [names[row["job_identity"]] for row in rows] == expected  # type: ignore[index]
        groups = [row["sort_group"] for row in rows]
        assert groups == ["current"] * 4 + ["stale"] + ["not_assessed"] * 2
        # No stale row above a current one, and no rank-only row between assessed rows.
        order = {"current": 0, "stale": 1, "not_assessed": 2}
        assert [order[group] for group in groups] == sorted(order[group] for group in groups)  # type: ignore[index]
        by_name = {names[row["job_identity"]]: row for row in rows}  # type: ignore[index]
        assert by_name["one_of_one"]["score_text"] == "Matched · 1 of 1 requirements · rank 40"
        assert by_name["matched"]["score_text"] == "Matched · 2 of 2 requirements · rank 60"
        assert by_name["answers"]["score_text"] == "Needs your answers · 1 of 2 requirements · rank 90"
        assert by_name["no"]["score_text"] == "Not a match · 1 of 2 requirements · rank 99"
        assert by_name["old"]["score_text"] == "Matched (old assessment: older prompt) · 3 of 3 requirements · rank 95"
        assert by_name["ranked"]["score_text"] == "rank 97 · not assessed"
        assert by_name["unranked"]["score_text"] == "not ranked yet · not assessed"
        # Compatibility: the old numbers are still there, and the rank score always is.
        assert (by_name["old"]["score"], by_name["old"]["score_kind"], by_name["old"]["rank_score"]) == (100, "assessment", 95)
        assert all(row["stale_reason"] is None for row in rows if row["sort_group"] == "current")

    # The terminal says the same: never a bare percent.
    text = CliRunner().invoke(cli, fx.cli("--peek", "--no-assess"))
    assert text.exit_code == 0, text.output
    flat = " ".join(text.output.split())
    assert "1 of 1" in flat and "% of requirements met" not in flat and "old assessment: older prompt" in flat
    listing = CliRunner().invoke(cli, ["scout", "jobs", "list", "--home", str(fx.home_root), "--target", str(fx.target)])
    assert listing.exit_code == 0 and "1 of 1 requirements" in listing.output and "% of requirements met" not in listing.output, listing.output

    # Nothing new: the postings that still need attention are in the same order (the not-a-match is not one of them).
    scout_new.mark_all_seen(fx.home_root, fx.target, now=NOW)
    nothing = _new(fx, peek=True, now=NOW.replace(hour=16))
    assert nothing["status"] == "nothing_new"
    assert [names[row["job_identity"]] for row in _rows(nothing)] == [name for name in expected if name != "no"]  # type: ignore[index]


# --- 0110-8-08 ------------------------------------------------------------------------------


def test_08_only_old_assessments_are_their_own_question_and_yes_assesses_the_new_ones_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("aged", [lever_job("aged", n) for n in (1, 2, 3)], seen_at=days_ago(20))  # not new
    fx.seed("fresh", [lever_job("fresh", n) for n in (1, 2)], seen_at=days_ago(1))
    old = [job_url("aged", n) for n in (1, 2, 3)]
    _old_run(fx, old)
    assess_base(fx.base)  # one assess call on record: 10 in + 20 out tokens, what the estimate is made from
    calls = fx.base.model.calls

    asked = _new(fx, peek=True)

    assert asked["status"] == "ask" and asked["question"]["kind"] == "assess_new" and asked["question"]["to_assess"] == 2  # type: ignore[index]
    stale = asked["stale_question"]
    assert stale["kind"] == "reassess_stale" and stale["to_reassess"] == 3  # type: ignore[index]
    assert stale["by_profile"] == [{"profile_id": fx.default_profile_id, "count": 3}]  # type: ignore[index]
    assert stale["estimate"] == {"calls": 3, "tokens": 90, "seconds": stale["estimate"]["seconds"], "cost": None, "basis_calls": 1}  # type: ignore[index]
    assert stale["text"] == "3 have only an old assessment; re-assess? ~3 calls, ~90 tokens"  # type: ignore[index]
    assert stale["yes"]["cli"] == f"gigai scout new --reassess-stale --since {asked['since']}"  # type: ignore[index]
    assert stale["yes"]["api"] == {  # type: ignore[index]
        "method": "POST", "path": "/api/new", "body": {"assess": False, "reassess_stale": True, "since": asked["since"]},
    }
    assert asked["counts"]["to_assess"] == 2 and asked["counts"]["only_stale"] == 3  # type: ignore[index]
    assert fx.base.model.calls == calls  # two questions, no model call

    # --yes approves the NEW question only.
    yes = _new(fx, assess=True, peek=True)
    assert yes["assessed"] == {"requested": 2, "assessed": 2, "failed": [], "stopped": None} and yes["reassessed"] is None
    assert fx.base.model.calls == calls + 2
    assert yes["stale_question"]["to_reassess"] == 3 and yes["counts"]["only_stale"] == 3  # type: ignore[index]

    # A stale row is labelled, never a bare 100: a run's assessment has no detail here, and the row says so.
    listed = {row["job_identity"]: row for row in _rows(_search(fx))}
    row = listed[old[0]]
    assert (row["sort_group"], row["stale_reason"], row["assessment_detail"]) == ("stale", "older_prompt", False)
    assert row["stale_label"] == "old assessment: older prompt"
    assert row["assessment"] == {"verdict": None, "met": 3, "requirements": 3, "percent": 100, "assessed_at": "2026-09-30T10:00:00.000000Z"}
    assert row["score_text"] == "Matched (old assessment: older prompt) · 3 of 3 requirements · not ranked yet"

    # --reassess-stale is the yes to the other question.
    again = _new(fx, assess=False, reassess_stale=True, peek=True)
    assert again["reassessed"] == {"requested": 3, "assessed": 3, "failed": [], "stopped": None}
    assert fx.base.model.calls == calls + 5
    assert again["stale_question"] is None and again["counts"]["only_stale"] == 0  # type: ignore[index]
    listed = {row["job_identity"]: row for row in _rows(_search(fx))}
    assert all((listed[job]["sort_group"], listed[job]["assessment_detail"]) == ("current", True) for job in old)

    # No variant mixes posting text with the user's own.
    for response in (asked, yes, again):
        data_labels.assert_not_mixed(set(scout_new.response_labels(response)), what="scout new")
        assert set(scout_new.response_labels(response)) == {data_labels.PUBLIC_UNTRUSTED}


def test_08_the_cli_flags_and_the_terminal_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("aged", [lever_job("aged", n) for n in (1, 2)], seen_at=days_ago(20))
    fx.seed("fresh", [lever_job("fresh", 1)], seen_at=days_ago(1))
    _old_run(fx, [job_url("aged", n) for n in (1, 2)])
    calls = fx.base.model.calls

    text = CliRunner().invoke(cli, fx.cli("--peek"))
    assert text.exit_code == 0, text.output
    assert "2 have only an old assessment; re-assess? ~2 calls" in text.output
    assert "Yes: gigai scout new --reassess-stale --since" in text.output

    yes = CliRunner().invoke(cli, [*fx.cli("--yes", "--peek"), "--json"])
    assert yes.exit_code == 0, yes.output
    answer = json.loads(yes.stdout)
    assert answer["assessed"]["assessed"] == 1 and answer["reassessed"] is None and fx.base.model.calls == calls + 1

    stale = CliRunner().invoke(cli, [*fx.cli("--reassess-stale", "--peek"), "--json"])
    assert stale.exit_code == 0, stale.output
    answer = json.loads(stale.stdout)
    assert answer["reassessed"]["assessed"] == 2 and answer["stale_question"] is None and fx.base.model.calls == calls + 3
    assert "re-assessed 2 of 2" in stale.stderr

    refused = CliRunner().invoke(cli, [*fx.cli("--reassess-stale", "--yours"), "--json"])
    assert refused.exit_code == 1 and json.loads(refused.stdout)["error"]["code"] == "invalid_value"
    assert "--reassess-stale" in CliRunner().invoke(cli, ["scout", "new", "--help"]).output


# --- 0110-8-12 ------------------------------------------------------------------------------


def test_12_a_tailored_posting_keeps_its_verdict_and_shows_under_the_profile_that_tailored_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    job = job_url("wheel", 1)
    fx.seed("wheel", [lever_job("wheel", 1)], seen_at=days_ago(1))
    _assess(fx, job, assessment(met=2), profile_id=fx.default_profile_id)
    run_tailored_resume(
        TailorRequest(job=AssessJobInput(job_url=job), resume=AssessResumeInput(profile_id=fx.default_profile_id)),
        home_root=fx.home_root, target=fx.target, config=config(fx.home_root), resolved_job=resolved_job(job),
    )
    # The other profile ranks the posting higher, and has a current assessment of its own.
    _assess(fx, job, assessment(met=2), profile_id=fx.second_profile_id)
    _seed_rank(fx, monkeypatch, fx.second_profile_id, {job: 98})
    _seed_rank(fx, monkeypatch, fx.default_profile_id, {job: 96})

    for response in (_new(fx, peek=True), _search(fx)):
        row = _rows(response)[0]
        assert (row["profile_id"], row["state"], row["tailored"]) == (fx.default_profile_id, "matched", True)
        assert row["score_text"] == "Matched · 2 of 2 requirements · rank 96 · resume tailored"
        assert [item["profile_id"] for item in row["profiles"]] == [fx.default_profile_id, fx.second_profile_id]  # type: ignore[union-attr]
    # The state filter still finds it, by the verdict and by "tailored".
    assert [row["job_identity"] for row in _rows(_search(fx, states=["tailored"]))] == [job]
    assert [row["job_identity"] for row in _rows(_search(fx, states=["matched"]))] == [job]
    # And it still needs attention when nothing is new.
    scout_new.mark_all_seen(fx.home_root, fx.target, now=NOW)
    nothing = _new(fx, peek=True, now=NOW.replace(hour=16))
    assert [(row["job_identity"], row["tailored"]) for row in _rows(nothing)] == [(job, True)]


# --- 0110-8-13 ------------------------------------------------------------------------------


def _sealed_output(*, with_basis: bool) -> dict[str, object]:
    url = job_url("acme", 1)
    identity = {"normalized_url": url, "url": url, "content_sha256": "sha256:" + "1" * 64, "role_match": True}
    value: dict[str, object] = {
        "schema_version": "scout-find-jobs-assess-output:1", "selected_postings": [identity], "pinned_resume": _PINNED,
        "target": "targets/acme", "selection_cap": 10, "selection_rule": "new_or_edited_role_match",
        "candidate_rows": [{
            "posting": {
                "url": url, "normalized_url": url, "provider": "lever", "board_token": "acme", "company": "Acme",
                "title": "Staff AI Engineer", "location": "Remote", "published_at": None, "content_sha256": "sha256:" + "1" * 64,
                "source_kind": "ats", "query_key": "staff-ai-engineer",
            },
            "outcome": "new",
        }],
        "assessments": [{
            "posting": identity, "suggestions": [], "proposal_revision_ref": None, "verdict": "matched_above_threshold", "questions": [],
            "matrix": [{"requirement": "Python", "resume_evidence": ["six years"], "status": "met"}],
        }],
        "not_assessed": [], "proposal_revision_refs": [], "model_target": "codex_cli",
        "producer": {"callable": "scout.find_jobs.assess", "version": "1", "actor": "scout-assess", "model_target": "codex_cli", "adapter": "codex_cli"},
        "usage": None, "failures": [],
    }
    if with_basis:
        value.update({"prompt_version": ASSESS_PROMPT_VERSION, "constraints_digest": _CONSTRAINTS})
    return value


def _run_evidence(output: AssessOutput):
    return SimpleNamespace(
        run_id=_OLD_RUN, started_at="2026-09-30T10:00:00Z", terminal=True, run_input=SimpleNamespace(profile_ref=None),
        assess_output=output,
    )


def test_13_the_run_import_keeps_the_prompt_version_and_the_constraints_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # A run that sealed its basis: both survive the run's own bytes (written, read back, imported).
    sealed = AssessOutput.from_json(parse_json_bytes(canonical_json_bytes(AssessOutput.from_json(_sealed_output(with_basis=True)).to_json())))
    _profile, rows, _skipped, dropped = run_history.run_rows(_OLD_RUN, _run_evidence(sealed))
    assert (rows[0].prompt_version, rows[0].constraints_digest, dropped) == (ASSESS_PROMPT_VERSION, _CONSTRAINTS, 0)
    assert run_history.basis_json(rows[0])["prompt_version"] == ASSESS_PROMPT_VERSION
    assert run_history.basis_json(rows[0])["prompt_sealed"] is True

    # A run sealed before the assess prompt had a version (before 0.1.10.4) seals none: the import names that prompt.
    legacy = AssessOutput.from_json(_sealed_output(with_basis=False))
    assert legacy.prompt_version is None
    _profile, rows, _skipped, dropped = run_history.run_rows(_OLD_RUN, _run_evidence(legacy))
    assert (rows[0].prompt_version, rows[0].constraints_digest, dropped) == ("assess-prompt-pre-v4", None, 0)
    basis = run_history.basis_json(rows[0])
    assert (basis["prompt_version"], basis["prompt_sealed"], basis["constraints_digest"]) == ("assess-prompt-pre-v4", False, None)

    # A row imported by 0.1.10.7 (the column is empty) reads the same way, and the grid shows it.
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("acme", [lever_job("acme", 1)], seen_at=days_ago(1))
    store = _store(fx)
    try:
        store.import_run(_OLD_RUN, fx.default_profile_id, [
            RunAssessment(
                run_id=_OLD_RUN, job=job_url("acme", 1), profile_id=fx.default_profile_id, state="matched",
                assessed_at="2026-09-30T10:00:00.000000Z", reqs_met=3, reqs_total=3, open_questions=0, listing_digest=None,
                prompt_version=None, constraints_digest=None, bank_digest=None, profile_revision=None, profile_digest=None,
                pinned_record=None, pinned_revision=None, pinned_digest=None, model_target="codex_cli", adapter="codex_cli",
            )
        ])
    finally:
        store.close()
    row = _rows(_search(fx))[0]
    assert row["assessment_basis"]["prompt_version"] == "assess-prompt-pre-v4" and row["assessment_basis"]["prompt_sealed"] is False  # type: ignore[index]
    assert row["stale_reason"] == "older_prompt"  # as before: nothing made before the fence is current


# --- 0110-8-14 and progress / ETA -----------------------------------------------------------


def test_14_a_batch_of_twelve_prints_rising_progress_to_stderr_and_stdout_stays_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("dozen", [lever_job("dozen", n) for n in range(1, 13)], seen_at=days_ago(1))
    jobs = [job_url("dozen", n) for n in range(1, 13)]
    _seed_rank(fx, monkeypatch, fx.default_profile_id, {job: 70 for job in jobs[:5]})
    assess_base(fx.base)  # one assess call on record: the averages the first ETA is made from

    result = CliRunner().invoke(cli, [*fx.cli("--yes"), "--json"])

    assert result.exit_code == 0, result.output
    response = json.loads(result.stdout)  # stdout is the JSON alone
    assert response["assessed"] == {"requested": 12, "assessed": 12, "failed": [], "stopped": None}
    lines = result.stderr.splitlines()
    assert lines[0].startswith("ranking: default 5 of 12 ranked"), lines
    assert re.fullmatch(r"assessing 12 postings, \d+ at a time( · ~.+)?", lines[1]), lines
    done = [int(match.group(1)) for line in lines if (match := re.fullmatch(r"assessed (\d+) of 12(?: · .+ left)?", line))]
    assert len(done) >= 3 and done == sorted(set(done)) and done[-1] == 12, lines  # rising, never repeated, to the end
    assert all("assess" in line or "rank" in line for line in lines), lines  # nothing else on stderr
    # The response says how far the rank is, per profile.
    assert response["ranking"] == {
        "enabled": True, "in_progress": True,
        "by_profile": [
            {"profile_id": fx.default_profile_id, "ranked": 5, "total": 12},
            {"profile_id": fx.second_profile_id, "ranked": 0, "total": 12},
        ],
    }
    data_labels.assert_not_mixed(set(scout_new.response_labels(response)), what="scout new")

    # The terminal form says it too, and a peek while the rank runs explains the moving numbers.
    text = CliRunner().invoke(cli, fx.cli("--peek"))
    assert text.exit_code == 0 and "Ranking in progress: default 5 of 12 ranked" in text.stdout, text.output


def test_14_the_eta_uses_the_recorded_average_first_and_the_batch_pace_once_it_has_one() -> None:
    clock = [0.0]
    lines: list[str] = []
    progress = scout_new.BatchProgress(333, lines.append, average_seconds=18.0, concurrency=4, clock=lambda: clock[0], every_seconds=0)

    progress.start()
    assert lines == ["assessing 333 postings, 4 at a time · ~25 min"]  # 333 x 18 s / 4 at a time
    clock[0] = 9.0
    progress.done()
    assert lines[-1] == "assessed 1 of 333 · ~25 min left"  # one result is no pace yet: still the recorded average
    for _ in range(119):
        clock[0] += 5.0
        progress.done()
    assert lines[-1] == "assessed 120 of 333 · ~18 min left"  # 604 s for 120: the batch's own pace now
    for _ in range(213):
        clock[0] += 5.0
        progress.done()
    assert lines[-1] == "assessed 333 of 333"
    counts = [int(match.group(1)) for line in lines if (match := re.match(r"assessed (\d+) of", line))]
    assert counts == list(range(1, 334))

    # A long batch is never silent, and never a line per posting: at most one every 10 seconds, and always the last.
    quiet: list[str] = []
    clock[0] = 0.0
    slow = scout_new.BatchProgress(100, quiet.append, average_seconds=None, concurrency=1, clock=lambda: clock[0])
    slow.start()
    for _ in range(100):
        clock[0] += 3.0
        slow.done()
    assert quiet[0] == "assessing 100 postings, 1 at a time" and quiet[-1] == "assessed 100 of 100"
    assert 20 <= len(quiet) <= 32 and all(" left" in line for line in quiet[2:-1])
