"""0.1.10.8 integration: the three packets' behaviour on ONE response, where their edits met in the same functions. Synthetic only.

U2 (0110-8-02) fetches a missing description on demand and counts it (``fetched_on_demand``, a failure's ``reason``); U3
(0110-8-08, -14) added the second batch (``reassessed``, the yes to ``stale_question``) and the progress lines; U4 (0110-8-05,
-11) added ``tag_pending`` and ``company_name`` to a row. Each packet tested its own side on the "assess new" batch; this is the
re-assess batch, which only exists in the merged tree, going through the same ``scout_new._assess``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gigai.scout import data_labels, posting_search, scout_new
from gigai.scout.quick_assess import read_quick_assessment

from tests.behaviors.scout_pipeline.test_description_on_demand import _Boards
from tests.behaviors.scout_pipeline.test_scout_new_uat import _old_run
from tests.support.greenhouse_fixtures import gh_job, gh_url, seed_greenhouse
from tests.support.posting_fixtures import NOW, TITLE_BOTH, build_postings_fixture, days_ago


def test_reassess_stale_fetches_a_missing_description_and_names_why_one_could_not_be_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    # Two postings an old run assessed (not new), neither with a stored description; the board has since removed one.
    seed_greenhouse(fx, "acme", [gh_job("acme", 401, TITLE_BOTH), gh_job("acme", 402, TITLE_BOTH)], seen_at=days_ago(20))
    kept, gone = gh_url("acme", 401), gh_url("acme", 402)
    _old_run(fx, [kept, gone])
    boards = _Boards(removed=(402,))
    boards.install(monkeypatch)
    calls = fx.base.model.calls
    lines: list[str] = []

    asked = scout_new.scout_new(fx.home_root, fx.target, now=NOW, peek=True)
    assert (asked["counts"]["to_assess"], asked["counts"]["only_stale"]) == (0, 2)  # type: ignore[index]
    assert boards.requests == [] and fx.base.model.calls == calls  # a question fetches nothing and calls no model

    done = scout_new.scout_new(fx.home_root, fx.target, now=NOW, peek=True, assess=False, reassess_stale=True, progress=lines.append)

    # U2 in U3's batch: one request per posting with no text, the count, and the named reason.
    assert sorted(boards.requests) == [f"boards-api.greenhouse.io/v1/boards/acme/jobs/{job_id}" for job_id in (401, 402)]
    assert done["assessed"] is None
    assert done["reassessed"] == {
        "requested": 2, "assessed": 1, "stopped": None, "fetched_on_demand": 1,
        "failed": [{"job_identity": gone, "profile_id": fx.default_profile_id, "error_code": "job_text_unavailable", "reason": "posting_removed"}],
    }
    assert fx.base.model.calls == calls + 1
    assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, kept) is not None  # the record
    assert read_quick_assessment(fx.home_root, fx.target, fx.default_profile_id, gone) is None
    # U3: the one that could not be re-assessed is still the stale question; the progress lines counted both.
    assert done["counts"]["only_stale"] == 1 and done["stale_question"]["to_reassess"] == 1  # type: ignore[index]
    assert lines[0].startswith("ranking: ") and lines[1].startswith("re-assessing 2 postings") and lines[-1] == "re-assessed 2 of 2"
    shown = scout_new.render(done)
    assert "Re-assessed 1 of 2. Fetched 1 missing description(s) first." in shown
    assert f"  not re-assessed (job_text_unavailable: posting_removed): {gone}" in shown
    data_labels.assert_not_mixed(scout_new.response_labels(done), what="scout new")

    # U4 next to U3 on a row, the same from the Jobs search (GET /api/postings): the company's name, the tag flag, the group.
    rows = {row["job_identity"]: row for row in posting_search.search_postings(fx.home_root, fx.target, now=NOW)["postings"]["rows"]}  # type: ignore[index]
    assert (rows[kept]["sort_group"], rows[kept]["stale_label"], rows[kept]["assessment_detail"]) == ("current", None, True)
    assert (rows[gone]["sort_group"], rows[gone]["stale_label"], rows[gone]["assessment_detail"]) == ("stale", "old assessment: older prompt", False)
    assert [row["job_identity"] for row in rows.values()] == [kept, gone]  # current before stale
    for row in rows.values():
        # 0110-10-03: ``company`` is the name; the board token is ``company_slug``.
        assert (row["company"], row["company_slug"], row["company_name"], row["tag_pending"]) == ("Acme", "acme", "Acme", False)
    listed = posting_search.render(posting_search.search_postings(fx.home_root, fx.target, now=NOW))
    assert "Acme: " in listed and "(old assessment: older prompt)" in listed
