# v0.2.0 gig-workbench roadmap: content review r0 (coordinator, 2026-09-23)

File: docs/development/v0.2.0/roadmaps/v0.2.0-gig-workbench-roadmap.md (511 lines).

Holds up: Status line; D-A..D-G recorded, not reopened; D-B cited to S21's amendment; the Workstream 0 evidence was re-verified (setup.py:47-48/:199, credentials.py:55-73/:63-67/:11, private_transfer.py:102, exa_client.py:38/66-73, runbook :151/:162); .env read for mode and names only; the W0 packet breakdown is concrete; the 5 open questions are carried and not decided.

Send back (r1):
1. MAJOR wrong count (W1 + Change log). "A fresh grep finds only 9 lines": the pattern `from .scout import` misses `from .scout.<mod> import`. Coordinator recount: `grep -c 'from \.scout\|from gigai\.scout' src/gigai/*.py` → 36 lines in 10 files. S21 §7 reconciles 36 import lines + 9 string/docstring hits = S16's 45 strict rows. "34 distinct sites" is S16's internal inconsistency (its open question 2 says 34, its inventory 45), flagged earlier; don't cite it as a count. The brief's "36 / 45" was right. The W1 "done" grep needs the correct pattern.
2. MAJOR wrong premise, in every "Pull into 0.1.9?": v0.1.9 is NOT "spike research". It is wave 1 (release/CI/git workflow + test lanes, done) plus fix requests from the v0.1.8 UAT; S16/S17 implementation is deferred until find-jobs works live (.orchestrator/decisions.log). The spikes are already-written research docs. Redo each pull-in reason on that basis. W0's real tradeoff: it removes the manual `export EXA_API_KEY` the operator hits in UAT (runbook :151/:162), against adding an implementation packet while UAT runs.
3. Minor: the dependency diagram draws 4/5/6 under 3; the table says "after 1". Make them agree.
4. Minor: W0 "done" runs the directory `tests/behaviors/secrets/`; list the exact test files (the operator's test-budget rule).
5. Minor: S21 is still being revised; cite S21 by section heading, not line numbers.
