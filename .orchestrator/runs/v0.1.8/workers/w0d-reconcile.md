# w0d — Reconcile roadmap + Amendment 02 (Revision 2)

state: done

## Files owned and changed

- `docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md` (rewritten, 192 lines)
- `docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md` (rewritten, 260 lines)

## D10 operator scope decision — applied

Recorded verbatim as D10 in the amendment (top-level section, before D1-D9) and
at the top of the roadmap. Applied as:

1. **Milestone M1 "usable"** added to the roadmap's Goal table, ahead of the
   installed-wheel/release proof. Every DAG row tagged `[M1]` or `[after-M1]`.
2. **Cut list** added (roadmap, "Cut for M1"): HiringCafe sitemaps deferred
   (unproven board-token yield; M1 uses Exa + ATS clients only), receipt
   fields beyond what `present`/continuation need deferred, model-quality
   evaluation deferred. Consent validator, model-boundary network check, and
   scheduler effect/status check explicitly marked **not cut** (enforced by
   existing code, not ceremony).
3. **0.2.0 generalization ledger** added (roadmap, table): UI consent source,
   `network_public_acquisition`/`network_hosted_model` effect names, present
   API + UI shell, per-gig config file, graph binding, and the contracts
   module's generic/Scout-specific split — each with 0.1.8 form and what gig
   #2 would need. Record only, nothing built.

## Finding disposition

| Finding | Disposition |
| --- | --- |
| R1 (dropped node-receipt fields, aggregate precedence) | Restored in amendment's new "Node receipt and aggregate status" section. Could not recover Rev-0's literal text (file is untracked, no git history, no copy in `.orchestrator/reviews/w0-amendment-02.md` beyond the finding description) — rewrote from the actual current code: `_goal_front_matter` (`run.py:3564-3584`), `_ZERO_USAGE` (`run.py:129-136`), `_usage` (`model_execution.py:463-481`), confirming `producer{callable,version,actor,model_target,adapter}` and `usage.measured` are **new** additive fields (no existing code has them), and confirming the aggregate-status bug is real in two places: `_terminal_status` (`run.py:3491-3496`, only checks failed/blocked, defaults running/pending to succeeded) and `_run_rows` (`scout_report_readers.py:315-331`, sets succeeded the moment any goal is complete, checked before failed/running). |
| R2 (D6 logic error) | Fixed: D6 now reads "the Run input seals N + the selection rule; acquire resolves candidates; selected posting identities go in AssessInput and the assess node receipt." Roadmap's DAG and Frozen-choices table carry the same fix, labeled "Selection sealing (D6/R2)". |
| R3 (watchlist storage home) | Added "Watchlist storage" section: `records/scout-watchlist/{watchlist_id}.json`, written by A-5 via the existing `_publish` pattern (`private_records.py:246-326`, same pattern `import_reference` uses), idempotent key `scout_watchlist:{provider}:{board_token}`. Full JSON shape given (`schema_version: "scout-watchlist:1"`, `watchlist_id`, `provider`, `board_token`, `company`, `state`, `first_seen{source_kind,source_url,query_key,batch_id,observed_at}`). |
| R4 (Integration run.py seam untested) | Added `tests/behaviors/scout_find_jobs/test_run_seam.py` to Integration's owned files and to I-2's row and acceptance command in both docs. |
| R5 (B's new behavior untested) | Added `tests/behaviors/scout_find_jobs/test_assess_model_policy.py` to B's owned files and to B-2's row and acceptance command in both docs. |
| P1 (naming drift) | Roadmap now uses the amendment's exact names throughout: `scout_exa_client.py`, `scout_hiringcafe_sitemaps.py`, `scout_ats_board_clients.py`, `scout_url_change_detection.py`, `scout_watchlist.py`, `scout_market_acquisition.py`, and config path `<target_root>/find-jobs.json`. Verified by diffing the `scout_*.py` name sets extracted from both docs — identical. |
| P2 (fixture ownership) | Roadmap's fixtures table now has an explicit "Owner" column; all fixtures owned by I-0. Added a line: fixture files live at `tests/behaviors/scout_find_jobs/fixtures/*.json`, one file per fixture name, validated against `test_contracts.py` dataclasses. |
| P3 (unowned "bind real callables") | Added I-3 row: `scout_materialization.py`, same owner as I-1, sequential after A-6/B-2/C-2, wave 3, no new file. |
| P4 (fake parallelism) | I-0's row now explicitly freezes the A-1..A-5 client call signatures/return shapes and the present API's routes + request/response JSON, in the same pass as the dataclasses, so A-6 and C-4 depend only on frozen interfaces. |
| P5 (D7 resume resolution unowned) | I-2's row and the "Frozen choices" table now explicitly state I-2 owns newest-committed-resume resolution (D7). |
| P6 (A-5 routing) | A-5 routed to `gpt-5.6-luna medium` (authenticated journal write), not Haiku. |
| P7 (acceptance commands) | I-2's command includes `test_run_seam.py`; B-2's command includes `test_assess_model_policy.py`. The amendment's packet-table commands and the roadmap's Wave-3 rerun commands both updated to match. |

## Ownership audit

Extracted every `.py`/`.json`/`.jsx`/`.js` path from the roadmap's DAG table
programmatically (16 rows). One duplicate: `src/gigai/scout_materialization.py`,
owned by both I-1 and I-3 — intentional and explicitly labeled "same owner as
I-1" in both the DAG row and the ownership-audit prose, matching the review's
P3 instruction verbatim ("same owner as I-1"). No other path appears twice.
Cross-checked amendment vs. roadmap `scout_*.py` name sets: identical (19 names
each, diffed with `sort -u`).

## Acceptance command outputs

```
$ grep -c -E 'interrupted > failed|cost_status|scout-watchlist:1' docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
6

$ grep -n -E 'scout_find_jobs_(exa|hiringcafe|ats|urls)|\.gigai/find-jobs' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md
(no output, exit 1)

$ grep -c -E 'M1|0\.2\.0 generalization ledger' docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md
35

$ wc -l docs/development/v0.1.8/roadmaps/v0.1.8-find-jobs-functional-roadmap.md docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
192 roadmaps/v0.1.8-find-jobs-functional-roadmap.md
260 phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md
```

All four acceptance checks pass.

## READ vs EXECUTED

**READ:** `.orchestrator/reviews/w0c-roadmap-and-w0r-rev1.md`,
`.orchestrator/reviews/w0-amendment-02.md`,
`.orchestrator/workers/spec-w0-amendment-02.txt`,
`.orchestrator/workers/w0-amendment-02.md`; both owned docs in full (pre-edit);
`git status`/`git log` on the amendment path (untracked, no history — Rev 0
text is not recoverable from git); source files:
`src/gigai/scout_report_readers.py:295-345`,
`src/gigai/run.py:100-145,1600-1630,2187-2213,280-303,218-237,2313-2400,3491-3521,3560-3600,3700-3730`,
`src/gigai/model_execution.py:440-484`,
`src/gigai/private_records.py:246-335,300-335`,
`src/gigai/scout_acquisition_records.py:1-10,56-99`, and a `watchlist` grep
across `src/gigai/*.py` (no existing watchlist code — confirmed genuinely new).

**EXECUTED:** read-only `grep`/`rg`, `sed`, `wc -l`, `git status`/`git log`
(read-only), a local Python script counting/diffing table cell contents for
the ownership audit, and the four acceptance `grep`/`wc` commands above. No
git stash/reset/clean/add/commit; no source/test/schema/UI file touched; no
network, provider, or model calls; no tests executed. The only writes were to
the two owned docs and this handoff file.

## Question for coordinator

None. Both docs are internally consistent, cite only real code, and pass all
four acceptance greps under the 260-line cap.
