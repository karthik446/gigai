"""0.1.11.2 RANKUI (4): the Jobs page's "Rank now" and "Re-rank latest 100" (``POST /api/postings/rank``).

End outcomes, on synthetic postings and the scripted model, through a real
in-process server (``serve()`` with the real backend, no background runner):

(1) RANK NOW. A home whose in-window postings are not ranked: the page's read
    (``{}``) and the list (``GET /api/postings``) both say "0 of N ranked";
    the ask (``{"mode": "unranked"}``) names the postings and the calls and
    calls no model; the yes answers 202 at once, the job ranks them all, and
    the read and the list then say "N of N" and the rows have rank scores.
(2) RE-RANK LATEST 100. 120 ranked postings: the ask says 100 postings and 2
    calls BEFORE anything runs (no model call); the yes makes exactly 2 calls
    of 50, the newest 100 get the model's NEW score and the 20 oldest keep
    theirs.
(3) THE DAILY CAP. With 99 of the day's 100 rank calls used, "Re-rank latest
    100" (2 calls) is refused: the ask says so, the yes answers 409
    ``rank_daily_cap`` and no model call is made.
(4) THE OFF SWITCH. ``rank.enabled`` false: the read says ranking is off and
    how to turn it on, and a yes answers 409 ``rank_disabled``; no model call.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import re
import threading
from types import SimpleNamespace

import httpx
import pytest

from gigai.adapters.port import InvocationResult, NormalizedUsage
from gigai.scout.find_jobs.api.server import ScoutFindJobsBackend, serve
from gigai.scout.find_jobs.refresh_tick import settings_path
from gigai.scout.pipeline import rank_now, triggers
from gigai.scout.pipeline.store import PipelineStore, pipeline_path

from tests.support.answers_stories_fixtures import config as fixture_config
from tests.support.pipeline_fixtures import MARKERS, PipelineModel, install_model
from tests.support.posting_fixtures import TITLE_SECOND_ONLY, PostingsFixture, build_postings_fixture, lever_job

_LINE = re.compile(r"^(p\d+) \| ", re.MULTILINE)
ROUTE = "/api/postings/rank"


class RankModel(PipelineModel):
    """The scripted model: every posting of a rank prompt gets ``score``. Every rank prompt is kept."""

    def __init__(self) -> None:
        super().__init__()
        self.current = SimpleNamespace(target=SimpleNamespace(model="fixture-model"))
        self.rank_prompts: list[str] = []
        self.score = 70

    def answer(self, prompt: str) -> InvocationResult:
        if "\nPOSTINGS (" not in prompt:
            return super().answer(prompt)
        self.rank_prompts.append(prompt)
        block = prompt.split("\nPOSTINGS (", 1)[1].split("\n\nAnswer with ONLY", 1)[0]
        items = [{"posting_id": pid, "score": self.score, "reasons": ["fits"], "blockers": []} for pid in _LINE.findall(block)]
        return InvocationResult(
            status="success", output_text=json.dumps(items), resolved_model="fixture-model", raw_usage={},
            normalized_usage=NormalizedUsage(100, 10, 110), cost_status="unavailable",
        )


class _Served:
    def __init__(self, fx: PostingsFixture) -> None:
        self.server = serve(backend=ScoutFindJobsBackend(home_root=fx.home_root, target=fx.target), bind=("127.0.0.1", 0))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.client = httpx.Client(base_url=f"http://127.0.0.1:{self.server.server_address[1]}", timeout=120.0)

    def __enter__(self) -> httpx.Client:
        self.thread.start()
        return self.client

    def __exit__(self, *exc: object) -> None:
        assert rank_now.wait_for_jobs(timeout=60)
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(10)


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, count: int) -> tuple[PostingsFixture, RankModel]:
    """``count`` postings only the SECOND profile matches, posted in the last day (number 1 the newest), none ranked."""

    fx = build_postings_fixture(tmp_path, monkeypatch, deleted=False)
    model = RankModel()
    install_model(monkeypatch, model)
    monkeypatch.setattr("gigai.config.load_config", fixture_config)  # the rank cache is keyed by the configured model
    now = datetime.now(UTC)
    jobs = [
        # Distinct text per posting: the score cache is keyed by a posting's content.
        lever_job("rn", n, title=TITLE_SECOND_ONLY, text=f"Posting number {n}: build reliable Python services, variant {n}.", created=now - timedelta(hours=1, minutes=n))
        for n in range(1, count + 1)
    ]
    fx.seed("rn", jobs, seen_at=now - timedelta(minutes=30))
    return fx, model


def _progress(body: dict[str, object], profile_id: str) -> tuple[int, int]:
    (item,) = [entry for entry in body["ranking"]["by_profile"] if entry["profile_id"] == profile_id]  # type: ignore[index]
    return item["ranked"], item["total"]


def _scores(fx: PostingsFixture) -> dict[int, int | None]:
    store = PipelineStore(pipeline_path(fx.home_root, fx.target))
    try:
        return {int(row.job.rsplit("-", 1)[1]): row.rank_score for row in store.postings(profile_id=fx.second_profile_id)}
    finally:
        store.close()


def _read(client: httpx.Client, body: dict[str, object] | None = None) -> dict[str, object]:
    response = client.post(ROUTE, json=body or {})
    assert response.status_code == 200, response.text
    return response.json()


def test_rank_now_ranks_the_unranked_postings_and_the_page_reads_ranked_x_of_y(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, model = _fixture(tmp_path, monkeypatch, 60)
    owner = fx.second_profile_id
    with _Served(fx) as client:
        # Not ranked, and the page can see it: the read and the list say 0 of 60.
        before = _read(client)
        assert (before["schema_version"], before["enabled"], before["how_to_enable"], before["job"], before["plan"]) == ("scout-rank-now:1", True, None, None, None)
        assert _progress(before, owner) == (0, 60) and before["ranking"]["in_progress"] is True  # type: ignore[index]
        listed = client.get(f"/api/postings?profile_id={owner}&limit=200").json()
        assert _progress(listed, owner) == (0, 60)
        assert all(row["rank_score"] is None and "not ranked yet" in row["score_text"] for row in listed["postings"]["rows"])

        # The ask: the count and the cost, no model call, nothing started.
        asked = _read(client, {"mode": "unranked"})
        plan = asked["plan"]
        assert (plan["mode"], plan["postings"], plan["calls"], plan["allowed"], plan["refusal"]) == ("unranked", 60, 2, True, None)  # type: ignore[index]
        assert plan["by_profile"] == [{"profile_id": owner, "postings": 60, "calls": 2}]  # type: ignore[index]
        assert asked["started"] is False and asked["job"] is None and model.rank_prompts == []

        # The yes: 202 at once, a job.
        started = client.post(ROUTE, json={"mode": "unranked", "approve": True})
        assert started.status_code == 202, started.text
        job = started.json()["job"]
        assert started.json()["started"] is True and (job["mode"], job["state"], job["postings"], job["planned_calls"]) == ("unranked", "running", 60, 2)
        assert rank_now.wait_for_jobs(timeout=60)

        # The end outcome: every posting is ranked, in 2 calls, and the read and the list say 60 of 60.
        after = _read(client)
        done = after["job"]
        assert (done["job_id"], done["state"], done["outcome"], done["calls"], done["ranked"]) == (job["job_id"], "done", "ran", 2, 60)  # type: ignore[index]
        assert _progress(after, owner) == (60, 60) and after["ranking"]["in_progress"] is False  # type: ignore[index]
        assert after["calls_today"]["used"] == 2 and len(model.rank_prompts) == 2  # type: ignore[index]
        listed = client.get(f"/api/postings?profile_id={owner}&limit=200").json()
        assert _progress(listed, owner) == (60, 60)
        assert [row["rank_score"] for row in listed["postings"]["rows"]] == [70] * 60
        assert all("rank 70" in row["score_text"] for row in listed["postings"]["rows"])

        # Nothing is left: a second click starts nothing and says so; no call.
        again = _read(client, {"mode": "unranked", "approve": True})
        assert (again["started"], again["plan"]["refusal"], again["plan"]["postings"]) == (False, "nothing_to_rank", 0)  # type: ignore[index]
        assert len(model.rank_prompts) == 2
        # System data only.
        assert all(marker not in json.dumps(after) for marker in MARKERS)

        # What is refused.
        for payload, status, code in (
            ({"mode": "everything"}, 422, "invalid_value"), ({"approve": True}, 422, "invalid_value"),
            ({"mode": "latest", "approve": "yes"}, 422, "wrong_type"), ({"mode": "latest", "bogus": 1}, 422, "unknown_key"),
        ):
            refused = client.post(ROUTE, json=payload)
            assert refused.status_code == status and refused.json()["error"]["code"] == code, refused.text
        assert client.post(ROUTE, json={"mode": "unranked", "approve": True}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_re_rank_latest_100_shows_the_cost_first_and_re_ranks_the_newest_100_in_two_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fx, model = _fixture(tmp_path, monkeypatch, 120)
    owner = fx.second_profile_id
    with _Served(fx) as client:
        assert client.post(ROUTE, json={"mode": "unranked", "approve": True}).status_code == 202
        assert rank_now.wait_for_jobs(timeout=60)
        assert _scores(fx) == {n: 70 for n in range(1, 121)} and len(model.rank_prompts) == 3  # 50 + 50 + 20
        assert _read(client, {"mode": "unranked"})["plan"]["refusal"] == "nothing_to_rank"  # type: ignore[index]

        # The model would now answer another score. THE COST FIRST: 100 postings, 2 calls, and no model call for the ask.
        model.score = 91
        asked = _read(client, {"mode": "latest"})
        plan = asked["plan"]
        assert (plan["mode"], plan["postings"], plan["calls"], plan["max_calls"], plan["batch_size"]) == ("latest", 100, 2, 2, 50)  # type: ignore[index]
        assert (plan["allowed"], plan["refusal"], plan["calls_left_today"]) == (True, None, 97)  # type: ignore[index]
        assert asked["started"] is False and len(model.rank_prompts) == 3 and _scores(fx) == {n: 70 for n in range(1, 121)}

        started = client.post(ROUTE, json={"mode": "latest", "approve": True})
        assert started.status_code == 202 and started.json()["started"] is True, started.text
        assert rank_now.wait_for_jobs(timeout=60)

        done = _read(client)["job"]
        assert (done["mode"], done["state"], done["outcome"], done["calls"], done["ranked"], done["postings"]) == ("latest", "done", "ran", 2, 100, 100)  # type: ignore[index]
        # Exactly 2 calls of 50, and they re-ranked postings that already had a score.
        assert len(model.rank_prompts) == 5 and [len(_LINE.findall(prompt.split("\nPOSTINGS (", 1)[1])) for prompt in model.rank_prompts[3:]] == [50, 50]
        scores = _scores(fx)
        assert {n for n, score in scores.items() if score == 91} == set(range(1, 101))  # the newest 100 (number 1 is the newest)
        assert {n for n, score in scores.items() if score == 70} == set(range(101, 121))  # the 20 oldest keep their score
        assert _read(client)["calls_today"]["used"] == 5  # type: ignore[index]
        listed = client.get(f"/api/postings?profile_id={owner}&limit=200").json()
        assert sorted(row["rank_score"] for row in listed["postings"]["rows"]) == [70] * 20 + [91] * 100


def test_re_rank_is_refused_beyond_the_daily_cap_and_with_ranking_off_and_no_model_call_is_made(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fx, model = _fixture(tmp_path, monkeypatch, 60)
    with _Served(fx) as client:
        # 99 of the day's 100 rank calls are used: re-ranking 60 postings needs 2.
        assert triggers.spend_rank_calls(fx.home_root, fx.target, 99)["used"] == 99
        asked = _read(client, {"mode": "latest"})
        plan = asked["plan"]
        assert (plan["postings"], plan["calls"], plan["calls_left_today"], plan["allowed"], plan["refusal"]) == (60, 2, 1, False, "rank_daily_cap")  # type: ignore[index]
        refused = client.post(ROUTE, json={"mode": "latest", "approve": True})
        assert refused.status_code == 409 and refused.json()["error"]["code"] == "rank_daily_cap", refused.text
        assert "needs 2" in refused.json()["error"]["message"] and "1 of 100" in refused.json()["error"]["message"]
        assert model.rank_prompts == [] and _read(client)["job"] is None and _read(client)["calls_today"]["used"] == 99  # type: ignore[index]
        # The same at the lane itself: all or nothing, no call.
        direct = rank_now.rerank_latest(fx.home_root, fx.target, config=fixture_config(fx.home_root), busy=lambda: None)
        assert (direct["state"], direct["reason"], direct["calls"]) == ("waiting", "daily_cap_reached", 0) and model.rank_prompts == []
        # "Rank now" ranks what the day has left: one call (50 postings), then it waits for tomorrow.
        assert _read(client, {"mode": "unranked"})["plan"]["allowed"] is True  # type: ignore[index]
        assert client.post(ROUTE, json={"mode": "unranked", "approve": True}).status_code == 202
        assert rank_now.wait_for_jobs(timeout=60)
        waited = _read(client)
        assert (waited["job"]["outcome"], waited["job"]["reason"], waited["job"]["calls"], waited["job"]["ranked"]) == ("waiting", "daily_cap_reached", 1, 50)  # type: ignore[index]
        assert _progress(waited, fx.second_profile_id) == (50, 60) and waited["calls_today"]["reached"] is True  # type: ignore[index]
        assert _read(client, {"mode": "unranked"})["plan"]["refusal"] == "rank_daily_cap"  # type: ignore[index]
        assert client.post(ROUTE, json={"mode": "unranked", "approve": True}).status_code == 409 and len(model.rank_prompts) == 1

        # The off switch: the page is told, and a yes is refused; no model call.
        path = settings_path(fx.home_root, fx.target)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"schema_version": "scout-settings:1", "rank": {"enabled": False}}), encoding="utf-8")
        off = _read(client, {"mode": "latest"})
        assert (off["enabled"], off["source"], off["plan"]["allowed"], off["plan"]["refusal"]) == (False, "setting", False, "rank_disabled")  # type: ignore[index]
        assert off["how_to_enable"] == rank_now.HOW_TO_ENABLE and '"rank": {"enabled": true}' in str(off["how_to_enable"])
        assert off["ranking"]["enabled"] is False  # type: ignore[index]
        for mode in ("unranked", "latest"):
            refused = client.post(ROUTE, json={"mode": mode, "approve": True})
            assert refused.status_code == 409 and refused.json()["error"]["code"] == "rank_disabled", refused.text
        assert len(model.rank_prompts) == 1
