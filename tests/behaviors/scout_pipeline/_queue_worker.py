"""A second (third, ...) process for the pipeline queue tests; run as a script, never collected.

    python _queue_worker.py crash <db> <lane>
        claim one step in <lane> with a 600 s lease, print the claim as JSON, then die
        holding it (``os._exit``): only the dead-owner rule can give the step back.
    python _queue_worker.py hold <db> <lane> <count> <release>
        claim <count> steps in <lane>, print them, wait until the file <release> exists,
        finish them, exit.
    python _queue_worker.py try <db> <lane>
        one claim in <lane>; print it (``null`` when there was no room) and finish it.
    python _queue_worker.py race <db> <logdir> <label> <threads>
        <threads> threads claim and run steps until every step is done. Each run is
        logged (``<logdir>/<label>-<i>.jsonl``: job, name, lane, monotonic start/end)
        and marked with an exclusive-create file, so a step run twice is caught
        outside the database too.

Synthetic only: the "model call" is a short sleep.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import time

from gigai.scout.pipeline.store import MODEL_STEPS, PipelineStore, StepMetrics

#: A safety net only: the tests assert the exit status, never a duration.
_GIVE_UP_SECONDS = 120.0


def _digest(*parts: str) -> str:
    return "sha256:" + hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def _claim_json(claim) -> str:
    return json.dumps(None if claim is None else {"job": claim.job, "name": claim.name, "lane": claim.lane, "owner": claim.owner})


def _finish(store: PipelineStore, claim) -> None:
    digest = claim.input_digest or _digest(claim.job, claim.name, "inputs")
    store.finish(claim, input_digest=digest, output_digest=_digest(claim.job, claim.name, "output"))


def crash(db: str, lane: str) -> None:
    store = PipelineStore(Path(db), lease_seconds=600.0)
    claim = store.claim(worker="crash", lanes=[lane])
    print(_claim_json(claim), flush=True)
    os._exit(0)


def hold(db: str, lane: str, count: str, release: str) -> None:
    store = PipelineStore(Path(db), lease_seconds=600.0)
    claims = [store.claim(worker=f"hold{i}", lanes=[lane]) for i in range(int(count))]
    print(json.dumps([json.loads(_claim_json(claim)) for claim in claims]), flush=True)
    deadline = time.monotonic() + _GIVE_UP_SECONDS
    while not Path(release).exists():
        if time.monotonic() > deadline:
            sys.exit(3)
        time.sleep(0.01)
    for claim in claims:
        if claim is not None:
            _finish(store, claim)


def try_once(db: str, lane: str) -> None:
    store = PipelineStore(Path(db), lease_seconds=600.0)
    claim = store.claim(worker="try", lanes=[lane])
    print(_claim_json(claim), flush=True)
    if claim is not None:
        _finish(store, claim)


def race(db: str, logdir: str, label: str, threads: str) -> None:
    store = PipelineStore(Path(db), lease_seconds=600.0)
    root = Path(logdir)
    failures: list[str] = []

    def work(index: int) -> None:
        worker = f"{label}t{index}"
        deadline = time.monotonic() + _GIVE_UP_SECONDS
        with (root / f"{worker}.jsonl").open("a", encoding="utf-8") as log:
            while True:
                if time.monotonic() > deadline:
                    failures.append(f"{worker}: gave up")
                    return
                claim = store.claim(worker=worker)
                if claim is None:
                    counts = store.counts()
                    if not any(counts.get(state) for state in ("blocked", "ready", "running")):
                        return
                    time.sleep(0.002)
                    continue
                marker = root / "ran" / f"{hashlib.sha256(claim.job.encode()).hexdigest()[:16]}-{claim.name}"
                try:
                    os.close(os.open(marker, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
                except FileExistsError:
                    failures.append(f"{worker}: {claim.job} {claim.name} ran twice")
                started = time.monotonic()
                time.sleep(0.01 if claim.name in MODEL_STEPS else 0.002)
                ended = time.monotonic()
                log.write(json.dumps({"job": claim.job, "name": claim.name, "lane": claim.lane, "owner": claim.owner, "start": started, "end": ended}) + "\n")
                log.flush()
                digest = claim.input_digest or _digest(claim.job, claim.name, "inputs")
                outcome = store.finish(
                    claim,
                    input_digest=digest,
                    output_digest=_digest(claim.job, claim.name, "output"),
                    metrics=StepMetrics(adapter="fixture", model="fixture-model", input_tokens=10, output_tokens=5),
                )
                if outcome != "done":
                    failures.append(f"{worker}: {claim.job} {claim.name} finished {outcome}")

    pool = [threading.Thread(target=work, args=(index,)) for index in range(int(threads))]
    for thread in pool:
        thread.start()
    for thread in pool:
        thread.join()
    if failures:
        print("\n".join(failures), file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    command, *args = sys.argv[1:]
    {"crash": crash, "hold": hold, "try": try_once, "race": race}[command](*args)
