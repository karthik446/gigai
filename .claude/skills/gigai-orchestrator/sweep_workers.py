#!/usr/bin/env python3
"""Close idle, finished Orca workers and finished coordinator TEST/LOCAL tabs.

Usage: ORCH_RUN=<run> [IDLE_MIN=5] sweep_workers.py [--dry-run]

- A worker whose dispatch has settled (succeeded/failed/completed/cancelled) and
  whose terminal has produced no output for IDLE_MIN minutes is released and its
  tab is closed. worker-release alone leaves Claude worker tabs open.
- A worker still in progress is never closed. If it has been silent for 30+
  minutes, it's reported as STALLED so the coordinator can look.
- A TEST/LOCAL tab (created by run_visible.sh) is closed once its preview shows
  "=== EXIT" and it has been idle for IDLE_MIN minutes.
Terminals this Run didn't start are never touched.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
import sys

RUN = os.environ.get("ORCH_RUN") or sys.exit("set ORCH_RUN to the Run id")
IDLE = dt.timedelta(minutes=float(os.environ.get("IDLE_MIN", "5")))
STALL = dt.timedelta(minutes=30)
DRY = "--dry-run" in sys.argv
SETTLED = {"succeeded", "failed", "completed", "cancelled", "settled"}


def orca(*args: str) -> dict:
    out = subprocess.run(["orca", *args, "--json"], capture_output=True, text=True)
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"code": "bad_json", "message": out.stderr[:200]}}


def idle_for(handle: str) -> tuple[dt.timedelta | None, str]:
    info = (orca("terminal", "show", "--terminal", handle).get("result") or {})
    term = info.get("terminal", info)
    last, preview = term.get("lastOutputAt"), term.get("preview") or ""
    if not last:
        return None, preview
    if isinstance(last, (int, float)) or str(last).isdigit():
        value = float(last)
        when = dt.datetime.fromtimestamp(value / 1000 if value > 1e11 else value, dt.timezone.utc)
    else:
        when = dt.datetime.fromisoformat(str(last).replace("Z", "+00:00"))
    return dt.datetime.now(dt.timezone.utc) - when, preview


def close(handle: str, why: str) -> None:
    print(f"close {handle[-12:]}  ({why})")
    if not DRY:
        orca("terminal", "close", "--terminal", handle, "--tab")


# Workers and TEST/LOCAL tabs live in the active version worktree, not the orchestrator home.
HOME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
WORKTREE = os.environ.get("GIGAI_WORKTREE") or open(os.path.join(HOME, "orchestrator", "active-worktree")).read().strip()
live = {t["handle"]: t for t in (orca("terminal", "list", "--worktree", f"path:{WORKTREE}").get("result") or {}).get("terminals", [])}
workers = (orca("orchestration", "worker-list", "--run", RUN).get("result") or {}).get("workers", [])


def is_settled(w: dict) -> bool:
    stage = (w.get("projection") or {}).get("stage") or {}
    status = {w.get("dispatchStatus"), w.get("workerState"), (w.get("projection") or {}).get("outcome"), stage.get("dispatch")}
    return bool(status & SETTLED)


mine = {w.get("agentTerminalHandle") for w in workers}
busy = {w.get("agentTerminalHandle") for w in workers if not is_settled(w)}
for w in workers:
    handle = w.get("agentTerminalHandle")
    if handle not in live:
        continue  # already closed; nothing to do
    idle, _ = idle_for(handle)
    if not is_settled(w):
        if idle is not None and idle >= STALL:
            print(f"STALLED? {w['dispatchId']} silent {int(idle.total_seconds() // 60)}m (not closed)")
        continue
    if handle in busy:
        continue  # the terminal was reused by a worker that's still in progress
    if idle is not None and idle >= IDLE:
        print(f"release {w['dispatchId']}")
        if not DRY:
            orca("orchestration", "worker-release", "--dispatch", w["dispatchId"])
        close(handle, f"settled, idle {int(idle.total_seconds() // 60)}m")
        live.pop(handle, None)

for handle, t in list(live.items()):
    title = t.get("title") or ""
    if handle in mine or not title.startswith(("TEST ", "LOCAL ")):
        continue
    idle, preview = idle_for(handle)
    if idle is not None and idle >= IDLE and "=== EXIT" in preview:
        close(handle, f"finished tab '{title[:40]}', idle {int(idle.total_seconds() // 60)}m")
