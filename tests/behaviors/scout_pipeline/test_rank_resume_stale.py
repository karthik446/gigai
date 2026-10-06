"""0.1.11.2 RANKORDER (3): a changed master offers a re-rank (``ranking.stale_resume``).

A rank score is stored per resume digest (``model_rank.cache_key``), so a
resume that changed finds no stored score: the postings read "not ranked
yet". The read model remembers which resume the last scores were read for
(``postings._Facts.note_rank_basis``) and ``ranking.stale_resume`` says when
the profile's resume is another one now. End outcomes, on synthetic postings:

(1) Ranked against the resume the profile has: not stale, for either profile.
(2) The default profile's resume is replaced (a master that gained Rust):
    its postings read "not ranked yet", ``ranking.stale_resume`` is true for
    that profile only, in GET /api/postings' block, ``scout new``'s and the
    rank route's (``rank_now.status``), and NO model call was made for it.
(3) One posting ranked against the new resume: the offer is gone.
(4) A profile nothing ever ranked is never stale, whatever its resume does.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.private_records import create_record, import_reference
from gigai.scout import posting_search, scout_new
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.pipeline import rank_now
from gigai.scout.profile_records import write_profile

from tests.support.fit_fixtures import seed_rank
from tests.support.pipeline_fixtures import RESUME
from tests.support.posting_fixtures import NOW, PostingsFixture, build_postings_fixture, days_ago, job_url, lever_job
from tests.support.scout_profile_fixtures import uuids

#: The master after it gained real experience the ranker reads (a skill of its vocabulary).
NEW_RESUME = RESUME + "Rust, Kafka, Terraform\n"


def _replace_resume(fx: PostingsFixture, tmp_path: Path, profile_id: str, text: str, seed: int = 90) -> None:
    """Pin ``profile_id`` to a new resume record holding ``text`` (what saving a changed master does to a profile)."""

    gig = fx.base.gig
    path = tmp_path / f"resume-changed-{seed}.md"
    path.write_bytes(text.encode("utf-8"))
    digest = digest_imported_bytes(path.read_bytes())
    imported = import_reference(
        home_root=fx.home_root, requested_target=fx.target, gig_id=gig.created.gig_id, kind="resume", source=path,
        operation_key=f"scout-resume-add:{path.name}:{digest}", uuid_factory=uuids(seed),
    )
    record = create_record(
        home_root=fx.home_root, requested_target=fx.target, gig_id=gig.created.gig_id, kind="imported_reference",
        content_family="g45_reference", content_id=imported.item_id, actor={"kind": "operator", "id": "local-user"},
        origin="imported", operation_key=f"scout-resume-record:{imported.item_id}", uuid_factory=uuids(seed + 1),
    )
    write_profile(gig.resolved, profile_id=profile_id, resume_ref=PinnedResume(record.record_id, record.revision_id, digest))


def _search(fx: PostingsFixture, profile_id: str | None = None) -> dict[str, object]:
    return posting_search.search_postings(fx.home_root, fx.target, now=NOW, profile_ids=[profile_id or fx.default_profile_id])


def _stale(ranking: object) -> tuple[bool, dict[str, bool]]:
    return ranking["stale_resume"], {item["profile_id"]: item["stale_resume"] for item in ranking["by_profile"]}  # type: ignore[index]


def _score_texts(response: dict[str, object]) -> list[str]:
    return [str(row["score_text"]) for row in response["postings"]["rows"]]  # type: ignore[index]


def test_a_changed_master_is_said_until_a_posting_is_ranked_against_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    default, second = fx.default_profile_id, fx.second_profile_id
    numbers = [1, 2, 3]
    fx.seed("st", [lever_job("st", n, created=days_ago(2)) for n in numbers], seen_at=days_ago(1))
    scores = {job_url("st", 1): 80, job_url("st", 2): 40, job_url("st", 3): 30}
    seed_rank(fx, monkeypatch, scores)

    # (1) ranked against the resume the profile has.
    ranked = _search(fx)
    assert _score_texts(ranked) == ["rank 80 · not assessed", "rank 40 · not assessed", "rank 30 · not assessed"]
    assert _stale(ranked["ranking"]) == (False, {default: False, second: False})

    # (2) the master changes: no stored score is for this resume, and the page is told so. No model call.
    calls = fx.base.model.calls
    _replace_resume(fx, tmp_path, default, NEW_RESUME)
    changed = _search(fx)
    assert _score_texts(changed) == ["not ranked yet · not assessed"] * 3
    assert _stale(changed["ranking"]) == (True, {default: True, second: False})
    assert changed["counts"]["ranked_low"] == 0 and len(changed["postings"]["rows"]) == 3  # type: ignore[index]  # still listed
    new = scout_new.scout_new(fx.home_root, fx.target, peek=True, assess=False, now=NOW)
    assert _stale(new["ranking"]) == (True, {default: True, second: False})
    ask = rank_now.status(fx.home_root, fx.target, mode="latest", now=lambda: NOW)
    assert _stale(ask["ranking"])[0] is True and ask["plan"]["mode"] == "latest" and ask["job"] is None  # type: ignore[index]
    assert _stale(_search(fx)["ranking"])[0] is True  # it stays said, read after read
    assert fx.base.model.calls == calls

    # (3) a re-rank against the new resume lifts a posting the old one ranked low, and the offer is gone.
    seed_rank(fx, monkeypatch, {job_url("st", 3): 85}, resume=NEW_RESUME)
    again = _search(fx)
    assert _score_texts(again)[0] == "rank 85 · not assessed" and again["postings"]["rows"][0]["job_identity"] == job_url("st", 3)  # type: ignore[index]
    assert _stale(again["ranking"]) == (False, {default: False, second: False})


def test_a_profile_never_ranked_is_never_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    fx.seed("nv", [lever_job("nv", n, created=days_ago(2)) for n in (1, 2)], seen_at=days_ago(1))

    assert _stale(_search(fx)["ranking"])[0] is False
    _replace_resume(fx, tmp_path, fx.default_profile_id, NEW_RESUME)
    after = _search(fx)
    assert _stale(after["ranking"]) == (False, {fx.default_profile_id: False, fx.second_profile_id: False})
    assert _score_texts(after) == ["not ranked yet · not assessed"] * 2
