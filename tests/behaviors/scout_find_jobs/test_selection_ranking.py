"""uat-bug-010 (UAT N13): the Jev ranking must reach the assess selection.

Root cause: ``market_acquisition.py`` ordered its candidates by Jev score
(``order_by_rank``) and then handed them to ``select_for_assessment``, which
ignores input order by design (dedupe -> per-company newest first ->
round-robin across companies by their newest posting). The assess cap went
to the newest postings, never the best Jev fits.

Three groups of tests:

1. the pure helper with scores: an older high-Jev posting beats a newer
   low-Jev one, across companies and inside one company;
2. no scores: the result is identical to the pre-fix algorithm (a frozen
   copy of it lives in this file as the reference);
3. the two call sites (acquire's selection and assess's recompute) agree,
   end to end: ``acquire_node`` then ``assess_node`` on the same run, and
   the ASSESSED postings are the high-Jev ones.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, replace
import json
from pathlib import Path
import random

import pytest

from gigai.canonical import canonical_json_bytes
from gigai.scout.find_jobs.contracts import (
    AcquireInput,
    AssessInput,
    ATSProvider,
    FindJobsConfig,
    ModelTarget,
    NodeContext,
    NotAssessedReason,
    PostingRow,
    SelectionReason,
    SelectionReasonCode,
    SelectionRule,
    SourceKind,
    SourceToggles,
)
from gigai.scout.find_jobs.filters import location_countries
from gigai.scout.find_jobs.jev_contracts import RankScore
from gigai.scout.find_jobs.market_acquisition import acquire_node
from gigai.scout.find_jobs.selection import (
    DEFAULT_PER_COMPANY_CAP,
    normalize_title,
    select_for_assessment,
)
from gigai.scout.proposal_execution import assess_node
from tests.behaviors.scout_find_jobs.test_assess_model_policy import _assess_fixture, _ScriptedBinding

FIXTURES = Path(__file__).parent / "fixtures"


@dataclass(frozen=True)
class _Row:
    normalized_url: str
    company: str
    title: str
    location: str = "Remote, United States"
    published_at: str | None = "2026-09-20T00:00:00Z"


@dataclass(frozen=True)
class _Score:
    normalized_url: str
    score: int | None


# --- 1. with scores ----------------------------------------------------------


def test_an_older_high_jev_posting_beats_a_newer_low_jev_one_across_companies() -> None:
    old_strong = _Row("https://boards.example/acme/1", "Acme", "Staff Engineer", published_at="2026-08-01T00:00:00Z")
    new_weak = _Row("https://boards.example/globex/1", "Globex", "Platform Engineer", published_at="2026-09-26T00:00:00Z")
    scores = (_Score(old_strong.normalized_url, 92), _Score(new_weak.normalized_url, 15))

    result = select_for_assessment([new_weak, old_strong], cap=1, rank_scores=scores)

    assert [row.normalized_url for row in result.selected] == [old_strong.normalized_url]
    assert result.dropped == {new_weak.normalized_url: "over_cap"}


def test_an_older_high_jev_posting_beats_a_newer_low_jev_one_inside_a_company() -> None:
    old_strong = _Row("https://boards.example/acme/1", "Acme", "Staff Engineer", published_at="2026-08-01T00:00:00Z")
    new_weak = _Row("https://boards.example/acme/2", "Acme", "Support Engineer", published_at="2026-09-26T00:00:00Z")
    scores = (_Score(old_strong.normalized_url, 92), _Score(new_weak.normalized_url, 15))

    result = select_for_assessment([new_weak, old_strong], cap=5, per_company=1, rank_scores=scores)

    assert [row.normalized_url for row in result.selected] == [old_strong.normalized_url]
    assert result.dropped == {new_weak.normalized_url: "company_cap"}


def test_companies_are_visited_by_their_best_score_and_rows_by_score_then_date() -> None:
    rows = [
        _Row("https://boards.example/a/1", "A", "Role 1", published_at="2026-09-25T00:00:00Z"),  # 40
        _Row("https://boards.example/a/2", "A", "Role 2", published_at="2026-09-01T00:00:00Z"),  # 70
        _Row("https://boards.example/a/3", "A", "Role 3", published_at="2026-09-10T00:00:00Z"),  # 70, newer than a/2
        _Row("https://boards.example/b/1", "B", "Role 1", published_at="2026-09-26T00:00:00Z"),  # 60
        _Row("https://boards.example/c/1", "C", "Role 1", published_at="2026-08-15T00:00:00Z"),  # 95
        _Row("https://boards.example/d/1", "D", "Role 1", published_at="2026-09-27T00:00:00Z"),  # unscored
    ]
    scores = (
        _Score("https://boards.example/a/1", 40),
        _Score("https://boards.example/a/2", 70),
        _Score("https://boards.example/a/3", 70),
        _Score("https://boards.example/b/1", 60),
        _Score("https://boards.example/c/1", 95),
        _Score("https://boards.example/d/1", None),
    )

    # cap 3, one pass of the round-robin: C (95), A (its best is 70), B (60).
    # Inside A the two 70s tie on score, so the newer one (a/3) leads.
    first_pass = select_for_assessment(rows, cap=3, rank_scores=scores)
    assert {row.normalized_url for row in first_pass.selected} == {
        "https://boards.example/c/1",
        "https://boards.example/a/3",
        "https://boards.example/b/1",
    }

    # cap 5: the unscored company is visited last in the first pass, then the
    # second pass takes A's second-best row (a/2); a/1 is past the company cap.
    wider = select_for_assessment(rows, cap=5, rank_scores=scores)
    assert {row.normalized_url for row in wider.selected} == {
        "https://boards.example/c/1",
        "https://boards.example/a/3",
        "https://boards.example/b/1",
        "https://boards.example/d/1",
        "https://boards.example/a/2",
    }
    assert wider.dropped == {"https://boards.example/a/1": "company_cap"}


def test_dedupe_still_keeps_the_newest_with_scores() -> None:
    older = _Row("https://boards.example/a/old", "Acme", "Senior Engineer", published_at="2026-09-01T00:00:00Z")
    newer = _Row("https://boards.example/a/new", "Acme", "senior engineer!", published_at="2026-09-15T00:00:00Z")
    scores = (_Score(older.normalized_url, 90), _Score(newer.normalized_url, 50))

    result = select_for_assessment([older, newer], cap=5, rank_scores=scores)

    assert [row.normalized_url for row in result.selected] == [newer.normalized_url]
    assert result.dropped == {older.normalized_url: "duplicate"}


def test_the_company_cap_and_round_robin_hold_with_scores() -> None:
    # One company owns the six best scores; it still contributes at most
    # DEFAULT_PER_COMPANY_CAP rows and every other company gets its turn.
    rows = [_Row(f"https://boards.example/big/{i}", "Big", f"Role {i}") for i in range(6)]
    rows += [_Row(f"https://boards.example/small{i}/1", f"Small {i}", "Role") for i in range(4)]
    scores = tuple(_Score(row.normalized_url, 99 - index) for index, row in enumerate(rows))

    result = select_for_assessment(rows, cap=5, rank_scores=scores)

    companies = Counter(row.company for row in result.selected)
    assert len(result.selected) == 5
    assert companies["Big"] == 1  # one row per company per pass; five companies fill the cap
    assert set(companies) == {"Big", "Small 0", "Small 1", "Small 2", "Small 3"}
    wide = select_for_assessment(rows, cap=10, rank_scores=scores)
    assert Counter(row.company for row in wide.selected)["Big"] == DEFAULT_PER_COMPANY_CAP


# --- 2. no scores = the pre-fix order, exactly --------------------------------


def _legacy_select(rows, *, cap: int, per_company: int = DEFAULT_PER_COMPANY_CAP):
    """``select_for_assessment`` as it was at 48673ae, copied verbatim as the reference."""

    def newest(row) -> str:
        published = row.published_at
        return published if isinstance(published, str) and published else ""

    def company_key(row) -> str:
        return " ".join((row.company or "").casefold().split())

    def dedupe_key(row):
        countries = location_countries(row.location or "")
        bucket = frozenset(countries) if countries else frozenset({"__unknown__"})
        return (company_key(row), normalize_title(row.title or ""), bucket)

    dropped: dict[str, str] = {}
    groups: dict[object, list] = {}
    for row in rows:
        groups.setdefault(dedupe_key(row), []).append(row)
    deduped = []
    for members in groups.values():
        ordered = sorted(members, key=newest, reverse=True)
        deduped.append(ordered[0])
        for loser in ordered[1:]:
            dropped[loser.normalized_url] = "duplicate"
    by_company: dict[str, list] = {}
    for row in deduped:
        by_company.setdefault(company_key(row), []).append(row)
    capped: dict[str, list] = {}
    for company, members in by_company.items():
        ordered = sorted(members, key=newest, reverse=True)
        capped[company] = ordered[:per_company]
        for loser in ordered[per_company:]:
            dropped[loser.normalized_url] = "company_cap"
    company_order = sorted(capped)
    company_order.sort(key=lambda name: newest(capped[name][0]), reverse=True)
    selected: list = []
    cap = max(cap, 0)
    round_index = 0
    while len(selected) < cap and any(capped.values()):
        progressed = False
        for company in company_order:
            if len(selected) >= cap:
                break
            queue = capped.get(company) or []
            if round_index < len(queue):
                selected.append(queue[round_index])
                progressed = True
        round_index += 1
        if not progressed:
            break
    selected_urls = {row.normalized_url for row in selected}
    for members in capped.values():
        for row in members:
            if row.normalized_url not in selected_urls and row.normalized_url not in dropped:
                dropped[row.normalized_url] = "over_cap"
    return tuple(row for row in rows if row.normalized_url in selected_urls), dropped


def _random_batch(seed: int) -> list[_Row]:
    rng = random.Random(seed)
    rows: list[_Row] = []
    for index in range(rng.randint(1, 60)):
        company = f"Company {rng.randint(0, 7)}"
        # Few titles and few dates on purpose: duplicates and date ties are
        # where an ordering change would show.
        title = rng.choice(["Software Engineer", "software engineer!", "Data Engineer", f"Engineer {rng.randint(0, 5)}"])
        published = rng.choice([None, "", "not-a-date", *(f"2026-09-{day:02d}T00:00:00Z" for day in (1, 5, 5, 12, 20))])
        location = rng.choice(["Remote, United States", "Bengaluru, India", "AMER", ""])
        rows.append(_Row(f"https://boards.example/{seed}/{index}", company, title, location, published))
    return rows


@pytest.mark.parametrize("seed", range(40))
def test_no_scores_is_the_pre_fix_selection_exactly(seed: int) -> None:
    rows = _random_batch(seed)
    for cap in (0, 1, 3, 10, 100):
        for per_company in (1, DEFAULT_PER_COMPANY_CAP, 5):
            expected_selected, expected_dropped = _legacy_select(rows, cap=cap, per_company=per_company)
            for scores in ((), tuple(_Score(row.normalized_url, None) for row in rows)):
                result = select_for_assessment(rows, cap=cap, per_company=per_company, rank_scores=scores)
                assert result.selected == expected_selected
                assert result.dropped == expected_dropped
            # The keyword is optional: every existing caller keeps working.
            default = select_for_assessment(rows, cap=cap, per_company=per_company)
            assert default.selected == expected_selected and default.dropped == expected_dropped


# --- 3. the two call sites agree, end to end ----------------------------------


def _posting(company: str, job: int, title: str, *, published_at: str) -> PostingRow:
    token = company.casefold().replace(" ", "-")
    url = f"https://boards.greenhouse.io/{token}/jobs/{job}"
    return PostingRow(
        url=url, normalized_url=url, provider=ATSProvider.GREENHOUSE, board_token=token,
        company=company, title=title, location="Denver, CO", published_at=published_at,
        content_sha256="sha256:" + f"{job:064x}", source_kind=SourceKind.ATS,
        query_key="software engineer", text=f"We need engineers for {title} at {company}.",
    )


def _ranked_batch() -> tuple[list[PostingRow], dict[str, int]]:
    """Eight role-matched postings: the three best Jev fits are the OLDEST ones."""

    rows = [
        _posting("Northwind", 1, "Software Engineer, Payments", published_at="2026-09-27T00:00:00Z"),
        _posting("Northwind", 2, "Software Engineer, Risk", published_at="2026-09-26T00:00:00Z"),
        _posting("Northwind", 3, "Software Engineer, Ledger", published_at="2026-09-25T00:00:00Z"),
        _posting("Contoso", 4, "Software Engineer", published_at="2026-09-24T00:00:00Z"),
        _posting("Contoso", 5, "software engineer!", published_at="2026-09-23T00:00:00Z"),  # duplicate of 4
        _posting("Fabrikam", 6, "Staff Software Engineer", published_at="2026-08-03T00:00:00Z"),
        _posting("Initech", 7, "Senior Software Engineer", published_at="2026-08-02T00:00:00Z"),
        _posting("Umbrella", 8, "Principal Software Engineer", published_at="2026-08-01T00:00:00Z"),
    ]
    scores = {1: 20, 2: 18, 3: 16, 4: 30, 5: 30, 6: 95, 7: 90, 8: 85}
    return rows, {row.normalized_url: scores[index + 1] for index, row in enumerate(rows)}


def _rank_score(row: PostingRow, score: int) -> RankScore:
    return RankScore(
        normalized_url=row.normalized_url, content_sha256=row.content_sha256 or "sha256:" + "0" * 64,
        fit="strong" if score >= 70 else "no", score=score, reasons=(), mismatch_flags=(),
        hidden_by_default=False, cost_usd="0", cached=True,
    )


class _Exa:
    def search(self, client, config, *, home_root=None):
        return ()


class _ATS:
    def list_board(self, client, provider, board_token, config):
        return ()


class _Watchlist:
    def add_to_watchlist(self, entry):
        return entry

    def active_entries(self):
        return ()


def _acquire_then_assess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, rows: list[PostingRow], scores: dict[str, int], cap: int
):
    fixture, target = _assess_fixture(tmp_path)
    resolved = fixture["resolved"]
    run_id = "run_00000000-0000-4000-8000-000000000a10"
    # assess_node reads its run directory under the ``target`` it is given
    # (the same layout ``test_assess_model_policy._run_assess`` writes);
    # acquire_node reads the sealed run input under the resolved workpad.
    # The same sealed input is written to both.
    run_dir = target / "runs" / run_id
    config = replace(
        FindJobsConfig.from_json(json.loads((FIXTURES / "fixture-find-jobs-config-v1.json").read_text())),
        sources=SourceToggles(exa=False, ats=False, hiringcafe=False),
        published_after="2000-01-01T00:00:00Z",
    )
    sealed_input = json.dumps({
        "schema_version": "scout-find-jobs-run-input:1",
        "config": config.to_json(),
        "config_digest": config.digest(),
        "selection_cap": cap,
        "selection_rule": "new_or_edited_role_match",
        "model_target": "ollama_local",
        "pinned_resume": fixture["pinned"].to_json(),
    })
    for sealed_dir in (run_dir / "sealed", resolved.path / "runs" / run_id / "sealed"):
        sealed_dir.mkdir(parents=True, exist_ok=True)
        (sealed_dir / "find-jobs-run-input.json").write_text(sealed_input)

    def fake_rank(candidates, **_kwargs):
        return tuple(_rank_score(row, scores[row.normalized_url]) for row in candidates if row.normalized_url in scores)

    monkeypatch.setattr("gigai.scout.find_jobs.market_acquisition._rank_candidates", fake_rank)

    def context(goal: str) -> NodeContext:
        return NodeContext(
            run_id=run_id, project_id=resolved.project_id, gig_id=fixture["gig_id"],
            graph_id="find-jobs:functional", graph_version=1, goal_slug=goal,
            manifest_digest="sha256:" + "a" * 64, operation_key=f"{goal}-{run_id}",
            target_observation_digest="sha256:" + "b" * 64,
            workpad_path=str(resolved.path), redeemed_consent_ref="consent",
            model_target=ModelTarget.OLLAMA_LOCAL,
        )

    acquired = acquire_node(
        context("acquire"),
        AcquireInput(config, "sha256:" + "c" * 64, None, tuple(rows), cap, SelectionRule.NEW_OR_EDITED_ROLE_MATCH),
        http_client=None, exa=_Exa(), ats=_ATS(), watchlist=_Watchlist(),
        home_root=fixture["home"], target=target,
    )
    (run_dir / "outputs").mkdir(parents=True, exist_ok=True)
    (run_dir / "outputs" / "acquire.json").write_bytes(canonical_json_bytes(acquired.to_json()))

    good = json.dumps({
        "matrix": [{"requirement": "Python", "resume_evidence": ["Built Python services"], "status": "met"}],
        "suggestions": [],
        "questions": [],
    })
    binding = _ScriptedBinding([good] * len(acquired.selected_postings))
    monkeypatch.setattr(
        "gigai.scout.proposal_execution.resolve_model_adapter",
        lambda config, adapter_target, **_kwargs: binding,
    )
    assessed = assess_node(
        context("assess"),
        AssessInput(
            acquire_batch_ref=str(run_dir / "outputs" / "acquire.json"),
            acquire_output_digest="sha256:" + "0" * 64,
            selected_postings=acquired.selected_postings,
            selection_cap=cap,
            selection_reasons=tuple(
                SelectionReason(posting.normalized_url, SelectionReasonCode.NEW) for posting in acquired.selected_postings
            ),
            pinned_resume=fixture["pinned"],
            target=str(target),
            model_target=ModelTarget.OLLAMA_LOCAL,
            answer_association_version="scout-answer-association:1",
        ),
        home_root=fixture["home"], target=target, config=fixture["config"],
    )
    return acquired, assessed


def test_the_assessed_postings_are_the_best_jev_fits_not_the_newest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows, scores = _ranked_batch()
    acquired, assessed = _acquire_then_assess(tmp_path, monkeypatch, rows=rows, scores=scores, cap=3)

    best = {row.normalized_url for row in rows if row.company in {"Fabrikam", "Initech", "Umbrella"}}
    # The symptom: what assess actually sent to the model.
    assert {item.posting.normalized_url for item in assessed.assessments} == best
    assert {item.normalized_url for item in acquired.selected_postings} == best


def test_acquire_and_the_assess_recompute_make_the_same_selection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    rows, scores = _ranked_batch()
    cap = 5
    acquired, assessed = _acquire_then_assess(tmp_path, monkeypatch, rows=rows, scores=scores, cap=cap)

    # One shared function, the same inputs: the pure helper over the batch
    # with the sealed scores is what both call sites must reproduce.
    expected = select_for_assessment(rows, cap=cap, rank_scores=acquired.rank_scores)
    expected_selected = {row.normalized_url for row in expected.selected}
    assert {item.normalized_url for item in acquired.selected_postings} == expected_selected
    assert {item.posting.normalized_url for item in assessed.assessments} == expected_selected

    # Fabrikam, Initech, Umbrella, Contoso (30) and Northwind's best (20).
    by_company = Counter(row.company for row in rows if row.normalized_url in expected_selected)
    assert by_company == {"Fabrikam": 1, "Initech": 1, "Umbrella": 1, "Contoso": 1, "Northwind": 1}

    # Assess labels every row acquire left behind with the reason acquire
    # itself dropped it for (duplicate, or over the cap / company cap).
    expected_reason = {
        url: NotAssessedReason.DUPLICATE if reason == "duplicate" else NotAssessedReason.OVER_CAP
        for url, reason in expected.dropped.items()
    }
    assert {row.posting.normalized_url: row.reason for row in assessed.not_assessed} == expected_reason
    acquire_counts = {item.reason: item.count for item in acquired.dropped_counts}
    assert acquire_counts == dict(Counter(expected_reason.values()))
