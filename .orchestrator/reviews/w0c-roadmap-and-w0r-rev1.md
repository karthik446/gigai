# Coordinator review: new roadmap (W0c) + Amendment 02 Revision 1 (W0r), 2026-09-22 23:40

Verdict: **both are close, not dispatchable yet.** Rev 1 fixed B1–B3 and G1–G4 correctly (4/4 new citations accurate), but it dropped content and has one logic error. The roadmap has naming drift and 5 DAG holes. One reconcile pass should fix both docs together (a single owner, so they can't drift again).

## Amendment 02 Rev 1
R1 **Dropped content (regression).** Rev 0 had the node receipt fields (`producer{…}`, `usage{…, cost_status, measured}`), the aggregate precedence `interrupted > failed > blocked > cancelled > running > pending > succeeded` fixing the "any complete ⇒ succeeded" bug (`scout_report_readers.py` ~332), the watchlist JSON shape, and the open-risks table. Rev 1 says only "preserve P3 defaults" (0 matches). Restore them.
R2 **Logic error in D6.** "N and the selected identities are sealed in the Run input", but the selected postings come from `acquire`, which runs after the input is sealed. Fix: the Run input seals N + the selection rule; the selected identities are recorded in `AssessInput` and the assess node receipt.
R3 **The watchlist has no storage home.** It's the "sole new persisted record", but where does it live (journal record kind? workpad file?) and who writes it (A-5)? Specify the path/kind, reusing the journal/`_publish` pattern (`private_records.py:326`).
R4 **Integration's run.py changes have no owned test.** UI consent, network effects, newest-resume resolution and input sealing are checked only by the existing `test_g14_scheduler.py`, which is read-only. Add a new `tests/behaviors/scout_find_jobs/test_run_seam.py` to Integration.
R5 **B's new behavior has no owned test either.** Hosted policy and the cap in `scout_proposal_execution.py` are checked only by existing read-only tests. Add a new `tests/behaviors/scout_find_jobs/test_assess_model_policy.py` to B.

## Roadmap (W0c)
P1 **Names drift from the amendment:** `scout_find_jobs_exa.py`/`_hiringcafe`/`_ats`/`_urls` vs the amendment's `scout_exa_client.py`, `scout_hiringcafe_sitemaps.py`, `scout_ats_board_clients.py`, `scout_url_change_detection.py`. The config path is `.gigai/find-jobs.json` here vs `<target_root>/find-jobs.json` there. The amendment wins.
P2 **Fixtures have no owner.** "The coordinator publishes fixtures", but the coordinator doesn't write repo files. The fixtures are contract examples, so I-0 owns them (e.g. `tests/behaviors/scout_find_jobs/fixtures/*.json`), validated against the dataclasses in I-0's test.
P3 **Wave 3 "the coordinator binds real callables" has no owner.** Needs an I-3 item (binding in `scout_materialization.py`, same owner as I-1, sequential after A-6/B/C-2).
P4 **Fake parallelism.** A-6 needs the A-1..A-5 client signatures, and C-4 needs C-2's HTTP routes. Neither is frozen. Either freeze the client signatures and the API routes/JSON in I-0, or move A-6 and C-4 to a later sub-wave. Prefer freezing them in I-0; that's the point of wave 1a.
P5 **D7 resume resolution isn't in any row.** Put it in I-2 explicitly, with its test (R4).
P6 **Routing:** A-5 watchlist persistence touches authenticated journal writes, so Luna medium, not Haiku.
P7 After R4/R5, I-2's and B-2's acceptance commands must include the new owned tests.

## OK as is
Old-roadmap diff = banner only (the other hunks predate tonight). README edits in the two spots only. Gates kept/dropped are sensible. Waves put `make test` at wave end in a TEST tab.

## Pending operator input
"Tool first vs GigAI showcase" (asked 23:20) may add a "usable by the operator" milestone and cut ceremony; fold it into the same pass.
