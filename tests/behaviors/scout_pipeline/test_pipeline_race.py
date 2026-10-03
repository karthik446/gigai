"""PL1: the design prototype (``proto_queue.py``) as a real race between processes.

20 synthetic jobs x 4 steps. One process claims ``job00``'s tailor step with a
600 s lease and dies holding it; then 2 processes x 4 threads race for every
step. Asserted: all 80 steps done, each run exactly once (in the database and
by an exclusive-create marker per step), the per-lane caps and the total of
model calls held across both processes (from the runs' own intervals), and
the crashed step reclaimed from its dead owner (its lease was far from
expiry, so only the dead-owner rule can have given it back).

No wall-clock assertion: the workers stop when every step is done, and a
lane's peak is only ever checked against its cap (an interval is logged
strictly inside the claim that allowed it, so it can never exceed the cap).
"""

from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from gigai.scout.pipeline.store import LANE_CAPS, MODEL_TOTAL_CAP, STEPS, PipelineStore

_WORKER = Path(__file__).with_name("_queue_worker.py")
_JOBS = [f"https://jobs.example.test/acme/{index:02d}" for index in range(20)]


def _digest(*parts: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _peak(intervals: list[tuple[float, float]]) -> int:
    events = sorted([(start, 1) for start, _ in intervals] + [(end, -1) for _, end in intervals], key=lambda item: (item[0], item[1]))
    best = current = 0
    for _, delta in events:
        current += delta
        best = max(best, current)
    return best


def test_two_processes_run_every_step_once_within_the_lane_caps_and_reclaim_a_crashed_holder(tmp_path: Path) -> None:
    db = tmp_path / "pipeline" / "pipeline.sqlite"
    store = PipelineStore(db)
    for index, job in enumerate(_JOBS):
        # Six jobs tailor on the local model (cap 1), the rest on claude (cap 2); re-assess on codex (cap 2).
        lane = "ollama" if index >= 14 else "claude_cli"
        assert store.enqueue(
            "profile_race", job, "tailor", input_digest=_digest(job, "resume-r1", "answers-r1"), trigger="process_now",
            lane=lane, model_target=lane, downstream_lanes={"reassess": ("codex_cli", "codex_cli")},
        ) == "enqueued"

    crashed = subprocess.run(
        [sys.executable, str(_WORKER), "crash", str(db), "claude_cli"], capture_output=True, text=True, timeout=120, check=True
    )
    held = json.loads(crashed.stdout)
    assert (held["job"], held["name"]) == (_JOBS[0], "tailor")
    assert store.step("profile_race", _JOBS[0], "tailor").state == "running"  # still held by the dead process

    logs = tmp_path / "logs"
    (logs / "ran").mkdir(parents=True)
    racers = [
        subprocess.Popen([sys.executable, str(_WORKER), "race", str(db), str(logs), f"p{index}", "4"], stderr=subprocess.PIPE, text=True)
        for index in range(2)
    ]
    for racer in racers:
        _, stderr = racer.communicate(timeout=180)
        assert racer.returncode == 0, stderr

    expected = len(_JOBS) * len(STEPS)
    assert store.counts() == {"done": expected}
    ok = [run for run in store.runs() if run.outcome == "ok"]
    per_step = defaultdict(int)
    for run in ok:
        per_step[(run.job, run.name)] += 1
    assert len(per_step) == expected and set(per_step.values()) == {1}  # 80 of 80, none twice
    assert len(list((logs / "ran").iterdir())) == expected

    lines = [json.loads(line) for path in sorted(logs.glob("*.jsonl")) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == expected
    owners = {line["owner"].split(":")[0] for line in lines}
    assert len(owners) == 2  # both processes ran steps
    by_lane: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for line in lines:
        by_lane[line["lane"]].append((line["start"], line["end"]))
    for lane, intervals in by_lane.items():
        assert _peak(intervals) <= LANE_CAPS[lane], lane
    model = [interval for lane, intervals in by_lane.items() if lane != "local" for interval in intervals]
    assert _peak(model) <= MODEL_TOTAL_CAP
    assert {lane: len(intervals) for lane, intervals in by_lane.items()} == {"claude_cli": 14, "ollama": 6, "codex_cli": 20, "local": 40}

    # The crashed step: reclaimed from its dead owner and run once, by a racer.
    crashed_step = store.step("profile_race", _JOBS[0], "tailor")
    assert crashed_step.attempts == 2
    attempts = [run for run in store.runs(job=_JOBS[0]) if run.name == "tailor"]
    assert [run.outcome for run in attempts] == ["interrupted", "ok"]
    assert attempts[0].owner == held["owner"] and attempts[1].owner.split(":")[0] != held["owner"].split(":")[0]
    assert sum(1 for step in store.steps() if step.attempts > 1) == 1
