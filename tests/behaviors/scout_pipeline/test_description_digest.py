"""0.1.10.8 U2 (0110-8-06): ONE digest rule for the assessed text and the row's current digest. Synthetic only.

A Greenhouse posting whose description is readable only as stale text (the listing's ``updated_at`` moved after the description was
cached) is assessed from that text. Its row's "current" digest used to be the digest of the TITLE alone (a row with no description
hashes ``title`` only), which can never equal the digest of ``title + text``: the assessment read ``posting_changed`` the moment it
was stored, and re-assessing did not clear it. The end outcome asserted here is the row's ``stale_code``, not the digests.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.scout import postings, scout_new
from gigai.scout.assessment_basis import posting_sha256
from gigai.scout.pipeline.store import PipelineStore, pipeline_path

from tests.support.greenhouse_fixtures import UPDATED, UPDATED_LATER, gh_job, gh_url, posting_text, seed_greenhouse
from tests.support.posting_fixtures import NOW, TITLE_BOTH, PostingsFixture, build_postings_fixture, days_ago


def _rows(fx: PostingsFixture, job: str):
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        return [row for row in store.postings() if row.job == job]
    finally:
        store.close()


def _assess_new(fx: PostingsFixture) -> dict[str, object]:
    return scout_new.scout_new(fx.home_root, fx.target, now=NOW, assess=True)  # type: ignore[arg-type]


def test_a_greenhouse_posting_assessed_from_its_stale_description_is_current_at_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    job = gh_job("acme", 101, TITLE_BOTH, updated_at=UPDATED_LATER)
    # The description was cached at UPDATED; the board lists the posting as updated at UPDATED_LATER: only a stale read has the text.
    seed_greenhouse(fx, "acme", [job], seen_at=days_ago(1), details={101: (posting_text(101), UPDATED)})

    done = _assess_new(fx)

    assert done["assessed"]["assessed"] == 1 and not done["assessed"]["failed"]  # type: ignore[index]
    rows = _rows(fx, gh_url("acme", 101))
    assert rows and all(row.state != "not_assessed" for row in rows if row.profile_id == fx.default_profile_id)
    assert [row.stale_code for row in rows] == [None] * len(rows)  # not posting_changed, for any profile row


def test_the_stale_flag_clears_with_no_model_call_and_a_real_change_still_reads_changed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    job = gh_job("acme", 101, TITLE_BOTH, updated_at=UPDATED_LATER)
    seed_greenhouse(fx, "acme", [job], seen_at=days_ago(1), details={101: (posting_text(101), UPDATED)})
    _assess_new(fx)
    calls = fx.base.model.calls

    # The table a 0.1.10.7 home holds: built under the old match version, its text-less row a "known" digest, the assessment posting_changed.
    with monkeypatch.context() as old:
        old.setattr(postings, "MATCH_VERSION", "posting-match:1")
        postings.refresh(fx.home_root, fx.target, now=NOW)
    db = sqlite3.connect(pipeline_path(fx.home_root, fx.target))
    db.execute(
        "UPDATE posting SET listing_known = 1, listing_digest = ?, stale_code = 'posting_changed'", (digest_imported_bytes(TITLE_BOTH.encode("utf-8")),)
    )
    db.commit()
    db.close()
    assert "posting_changed" in {row.stale_code for row in _rows(fx, gh_url("acme", 101))}

    # The next ordinary read rebuilds the rows once and the flag is gone: no model call, no re-assessment, nothing stored changed.
    postings.refresh(fx.home_root, fx.target, now=NOW)
    assert [row.stale_code for row in _rows(fx, gh_url("acme", 101))] == [None, None]
    assert fx.base.model.calls == calls  # no model call to clear it

    # The description is fetched again and the posting REALLY changed: that one is posting_changed.
    changed = posting_text(101, extra=" Now also requires Rust.")
    seed_greenhouse(fx, "acme", [job], seen_at=days_ago(0.5), details={101: (changed, UPDATED_LATER)})
    postings.refresh(fx.home_root, fx.target, now=NOW, force=True)
    # 0.1.11.9: the job has ONE assessment, so every role that tags it reads the same "posting changed" (until then
    # only the role that had assessed it had a row to say so).
    changed_rows = _rows(fx, gh_url("acme", 101))
    assert {row.profile_id for row in changed_rows} == {fx.default_profile_id, fx.second_profile_id}
    assert [(row.state != "not_assessed", row.stale_code) for row in changed_rows] == [(True, "posting_changed")] * 2
    assert len({row.assessed_at for row in changed_rows}) == 1  # one assessment, read by both tags
    assert fx.base.model.calls == calls


def test_a_fresh_assessment_of_a_current_description_stays_current(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch)
    job = gh_job("acme", 102, TITLE_BOTH, updated_at=UPDATED)
    seed_greenhouse(fx, "acme", [job], seen_at=days_ago(1), details={102: (posting_text(102), UPDATED)})  # marker == updated_at: strict text

    _assess_new(fx)
    assert [row.stale_code for row in _rows(fx, gh_url("acme", 102))] == [None, None]

    # The listing's updated_at moves with no new description (the strict read loses the text): still current, never a false change.
    seed_greenhouse(fx, "acme", [gh_job("acme", 102, TITLE_BOTH, updated_at=UPDATED_LATER)], seen_at=days_ago(0.5), details={102: (posting_text(102), UPDATED)})
    postings.refresh(fx.home_root, fx.target, now=NOW, force=True)
    assert [row.stale_code for row in _rows(fx, gh_url("acme", 102))] == [None, None]


def test_one_digest_rule_for_the_assessed_text_and_the_row() -> None:
    from gigai.scout.find_jobs.job_state import quick_assessment_fact  # noqa: F401 - the other reader of the rule
    from gigai.scout.find_jobs.ats_board_clients import _greenhouse_row

    title, text = TITLE_BOTH, posting_text(7)
    job = gh_job("acme", 7, title)
    row = _greenhouse_row(job, title, str(job["absolute_url"]), text, "acme")
    assert row.content_sha256 == posting_sha256(title, row.text or "")
    assert posting_sha256(title, text) == digest_imported_bytes("\n".join((title, text)).encode("utf-8"))
