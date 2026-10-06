"""0.1.11.2 RANKVIS (4): a server start ranks what is unranked, seen through the server's own HTTP answers.

The local release check could not verify "ranking by itself after a restart": everything in its window was ranked
already. So this makes the unranked case: a synthetic home with postings of the last 7 days that nothing ranked, the
scripted model, and the REAL server started as ``gigai scout run`` starts it (``serve(..., background_refresh=True)``:
the pipeline runner's thread with the rank lane). Nothing else is done: no click, no ``POST /api/postings/rank``, no
sources update, no kick. THE OUTCOME is read where the Jobs page reads it: the ``ranking`` block of
``GET /api/postings`` goes from "0 of N" to "N of N" within 60 s, and the rows carry rank scores.

``tests/behaviors/scout_pipeline/test_rank_lane.py`` pins the same start through the store; this one goes through HTTP.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
import threading
import time

import httpx
import pytest

from gigai.scout.find_jobs.present_api import ScoutFindJobsBackend, serve
from gigai.scout.pipeline import rank_now
from gigai.scout.pipeline.settings import PIPELINE_ENV, pipeline_setting

from tests.behaviors.scout_pipeline.test_rank_lane import _fixture
from tests.support.pipeline_fixtures import set_pipeline_enabled
from tests.support.posting_fixtures import TITLE_SECOND_ONLY, lever_job

#: The start's own turn has this long (the ticket's bound); the fixture needs about a second.
WITHIN_SECONDS = 60.0


def _progress(body: dict[str, object]) -> tuple[int, int]:
    rows = body["ranking"]["by_profile"]  # type: ignore[index]
    return sum(item["ranked"] for item in rows), sum(item["total"] for item in rows)


def test_a_started_server_ranks_the_unranked_postings_by_itself_and_the_ranking_block_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(PIPELINE_ENV, raising=False)
    monkeypatch.setenv("GIGAI_SCOUT_AUTO_REFRESH", "0")  # no sources update by the server either
    monkeypatch.setenv("GIGAI_SCOUT_MODEL_TAGS", "0")
    fx, model = _fixture(tmp_path, monkeypatch)
    set_pipeline_enabled(fx.home_root, fx.target, False)
    assert pipeline_setting(fx.home_root, fx.target, environ={}).enabled is False  # the pipeline (tailoring) is off
    recent = datetime.now().astimezone() - timedelta(days=1)  # the server's lane reads the real clock
    jobs = [
        # Distinct text per posting: the score cache is keyed by a posting's content.
        lever_job("rs", n, title=TITLE_SECOND_ONLY, text=f"Posting number {n}: build reliable Python services, variant {n}.", created=recent - timedelta(minutes=n))
        for n in (1, 2, 3)
    ]
    fx.seed("rs", jobs, seen_at=recent)
    old = datetime.now().astimezone() - timedelta(days=20)
    fx.seed("old", [lever_job("old", 9, title=TITLE_SECOND_ONLY, text="An older posting: Go services.", created=old)], seen_at=old)
    assert model.rank_prompts == []

    # ONLY THE START: the server as `gigai scout run` serves it, with its background threads.
    server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0), background_refresh=True)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    seen: list[tuple[int, int]] = []
    requests: list[str] = []
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_address[1]}", timeout=30.0) as client:
            deadline = time.monotonic() + WITHIN_SECONDS
            while True:
                response = client.get("/api/postings", params={"limit": 200})
                requests.append("GET /api/postings")
                assert response.status_code == 200, response.text
                body = response.json()
                progress = _progress(body)
                if not seen or seen[-1] != progress:
                    seen.append(progress)
                if progress == (3, 3) or time.monotonic() > deadline:
                    break
                time.sleep(0.2)
        last = server.pipeline_runner._last_rank
    finally:
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
        assert rank_now.wait_for_jobs(timeout=30)

    # THE OUTCOME: with no click and no sources update, the ranking block reaches "3 of 3" and the rows have scores.
    assert seen[-1] == (3, 3), f"the ranking block after {WITHIN_SECONDS:.0f} s: {seen}; the lane's last turn: {last}"
    assert body["ranking"]["enabled"] is True and body["ranking"]["in_progress"] is False
    scores = {row["job_identity"]: row["rank_score"] for row in body["postings"]["rows"]}
    assert len(scores) == 4
    assert all(type(score) is int for job, score in scores.items() if "/rs/" in job), scores
    assert all(score is None for job, score in scores.items() if "/old/" in job), scores  # outside the window: never by the lane
    assert set(requests) == {"GET /api/postings"}  # the test only read: nothing asked the server to rank
    assert len(model.rank_prompts) == 1  # one call: the one profile these postings match
