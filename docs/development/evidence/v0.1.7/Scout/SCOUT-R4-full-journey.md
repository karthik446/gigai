# SCOUT R4 full synthetic journey

> **Superseded user-action extension:** the later
> [SCOUT-R4-user-actions-completion.md](SCOUT-R4-user-actions-completion.md)
> adds the authenticated answer, final-selection, Scout-document application,
> correction/retry, bounded public-import, and fresh-session checks below.
> The saved/rejected-only limitation in this historical report is retained as
> evidence of the earlier run and is not the current status.

Date: 2026-09-11 (America/Denver)  
Status: demonstrated bounded end-to-end service journey; synthetic and
offline only.  This is an implementation handoff, not live Ollama/provider,
UI, scheduler, or outbound-application acceptance.

## What is now real

`tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey`
drives one disposable Gig through the supported journal-backed services:

1. The normal fixture creates native experience/preferences revisions and a
   committed completed discovery packet. The actual posting resolver supplies
   the posting selector; no posting bytes or fake G45 reference are supplied by
   the test.
2. An allocated proposal Run is launched through `launch_run` with the
   `proposal-assessment` graph, explicit local target, posting selector and
   host-owned private selectors. An injected deterministic transport returns a
   bounded structured assessment. The resulting immutable proposal revision
   is read back and contains the selected answer association.
3. A new committed profile revision is written through `update_native_record`.
   A second real proposal Run selects that revision. Both proposal revisions
   remain readable and their `input_revisions` differ, demonstrating
   reassessment without rewriting the earlier claim.
4. A real Tailor Run selects the second proposal and returns a complete
   synthetic `.075` resume/cover-letter bundle through a separate injected
   transport. R2's authenticated document revision writer persists both
   documents and their generated checks.
5. `record_final_selection` writes an explicit selection, then
   `record_application` writes an explicit `saved` event followed by a
   `rejected` correction. There is no automatic Tailor, final selection,
   application, or external submission transition.
6. `rebuild_projection` reconstructs the local query projection from journal
   readers. `publish_report` emits a private local HTML generation; a fresh
   `resolve_workpad` session reads it with `read_current_report` and reports
   `stale: false`. The test also invokes public `scout-report status` and
   `application history` through `CliRunner` against that same workpad.

At the time of this historical run, the application-event schema redeemed
imported native document references rather than `records/scout-documents`
revisions. The later user-action extension adds a strict versioned
`scout_document` reference and authenticates each revision against the exact
Tailor result before application publication; no document identity is
invented.

## Durable paths and authority

The discovery packet and receipt are authenticated under the committed
posting resolver. Native inputs are immutable records under
`records/<record-id>/revisions/<revision-id>.json`; proposal revisions are
under `records/scout-proposals/<record-id>/revisions/<revision-id>.json`.
Tailor documents use
`records/scout-documents/<record-id>/revisions/<revision-id>/record.json` with
the sibling document bytes. Final selections use
`records/scout-documents/selections/<opportunity>_<snapshot>.json`.
Application events and their operation receipts stay under the existing
`records/applications/` journal contract. Report metadata is
`reports/scout/current.json`, selecting a generation under
`reports/scout/generations/`.

Journal entries, committed bytes and their artifact references remain the
authority. `state.sqlite` is rebuilt by `rebuild_projection` and is not a
second mutable truth. The small shared journal changes required to make this
journey authenticate its actual committed snapshot are deliberately bounded:
mechanically attach artifact references for transition artifacts, permit
version-aware reads of mutable run-details and graph manifests at the pinned
head, include the graph-set manifest needed by resolver authentication, and
exclude non-self-authenticating handoff metadata from this snapshot prefix.
Immutable records retain the existing exactly-one-publisher rule.

## Verification

Synthetic fixtures and injected `httpx` transports only; no model/network
call was made. Exact focused command and result:

```text
time .venv/bin/pytest -q tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey
1 passed in 68.32s (0:01:08)
```

The test verifies proposal and Tailor Run allocation, generated proposal and
document bytes, document checks, explicit final selection, application
correction, projection rows, report sections (`Proposal content`,
`Application history`, `Documents and checks`) and fresh-session report
reload. It is not a semantic-quality guarantee for a language model: the
transport fixture supplies the known bounded response. Existing malformed
Tailor and proposal-to-Tailor tests remain in the focused file and are not
described as live runtime proof.

## Remaining product gates

| Area | State after this journey | Explicit next gate |
| --- | --- | --- |
| Discovery acquisition | Existing committed public fixture is consumed | Bounded deadline/progress, duplicate and acquisition-failure UX around the public import protocol; no crawler or installed scheduler |
| Proposal answers | Answer association and revision-sensitive reassessment are journaled | User-facing answer/question round-trip and richer reassessment UX |
| Tailor documents | Real local Tailor Run, immutable docs/checks and explicit final selection | Closed application-event reference kind for Scout documents, then user review wiring |
| Report/projection | Journal-derived projection and clean local HTML reload work | Broader session/rebuild and legacy compatibility review; no remote assets or deployed API |
| Application | Explicit saved event and correction are readable; no automatic transition | Keep outbound submission and any applied transition behind separate explicit approval |
| Scheduler/UI | Not part of this lane | Daily overlap lease, soft-stop/hard-timeout recovery, and UI/final-selection flows |

The exact application-to-document link and public final-selection/saved-answer
CLI commands are intentionally not claimed here: the current closed service
APIs are exercised directly, while report/status and application/history CLI
mapping is covered. R0 receipt tamper/alias/extra-Goal/journal-conflict tests
remain for the later grouped release review and were not rerun in this
focused journey. No historical `.074`/`.075` source identity or legacy
invocation bytes were changed.
