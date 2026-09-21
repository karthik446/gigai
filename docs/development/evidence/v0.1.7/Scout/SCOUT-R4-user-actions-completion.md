# SCOUT R4 user-actions completion

Date: 2026-09-11 (America/Denver)  
Status: bounded synthetic user-action journey demonstrated; no live model,
provider, scheduler, UI, outbound submission, or private-user-data acceptance.
This report supersedes the earlier saved/rejected-only result described in
`SCOUT-R4-full-journey.md` while preserving that document as historical
evidence.

## Delivered boundary

The journal-backed R4 path now exposes three explicit user actions and one
bounded acquisition adapter:

* `gigai scout-answer save` reads one committed native `experience_qa`
  revision, changes exactly the selected question, and writes a new immutable
  native revision. The answer carries `user_reported` provenance and an empty
  source-ref list; it is not inferred from a model response. Parent revision,
  record identity, Gig and target are authenticated by the existing native
  record service. The copied `data/scout/gig.py answer` command maps the same
  operation and requires `--confirm`.
* `gigai scout-documents final-select` reads each requested committed document
  revision before writing selection. It requires an actual completed Tailor
  Run, Goal, invocation and output digest. `record_final_selection` reads and
  authenticates `runs/<run_id>/scout-tailor/result.json`, checks that each
  selected `(kind, record, revision, content digest)` tuple is in that result,
  and persists additive `scout-document-selection:2` metadata. The model does
  not provide selection provenance. The copied wrapper has matching
  `final-select` arguments and strict confirmation.
* Application `document_refs` now accept a closed versioned `scout_document`
  shape in addition to legacy imported references. Each Scout ref contains
  document kind/record/revision/content digest, opportunity/snapshot and
  authenticated Tailor run/goal/invocation identities. Publication re-reads
  the exact document bytes and Tailor result under the committed workpad;
  `applied` remains an explicit confirmed event, and saved/applied/correction
  replay preserves immutable source records. Existing applications without a
  Tailor document and legacy imported refs remain on their old path.
* `run_bounded_public_import` classifies already-acquired public rows into
  considered, duplicates, acquisition failures, and acquisition exclusions
  until a wall-time bound. It returns processed/next-index/stop reason and
  does no network fetching, crawling, scheduling, private matching, or
  journaling. A caller still must submit accepted public captures through the
  authenticated discovery journal protocol; this helper is progress plumbing,
  not posting authority.

Public acquisition and private assessment remain separate: a captured job's
public title/employer/source state can be considered, failed, duplicated or
excluded by acquisition, while salary minimum, sponsorship, geography and
experience fit remain private proposal data. A report and the application
history are local authenticated outputs; no public export or automatic action
is performed.

## Synthetic end-to-end journey

`tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey`
uses one disposable approved test Gig and normal journal lifecycle:

1. Native experience and profile revisions are created. The test invokes the
   public answer command against a real missing question and uses the returned
   committed record/revision selector in a proposal Run.
2. A completed discovery fixture is resolved through the existing authenticated
   posting resolver. A genuine allocated proposal Run uses an injected local
   transport and persists a proposal revision containing answer association.
3. A changed committed profile revision drives a second proposal Run. The old
   proposal's exact input revisions remain unchanged and the new revision is
   different, demonstrating reassessment rather than overwrite.
4. A genuine Tailor Run uses a deterministic injected transport to produce a
   bounded `.075` resume and cover-letter bundle. R2 persists immutable document
   revisions/checks and a host-owned Tailor result.
5. The public final-select command consumes those exact persisted document
   identities and the actual Tailor result identities. No `goal_synthetic` or
   `inv_synthetic` values are supplied. The application service then records a
   saved event, an explicitly confirmed applied event, an exact replay, and a
   rejected correction, each carrying the strict Scout document refs.
6. R3 rebuilds the local projection, publishes a private local HTML report,
   and a fresh resolved-workpad session reads the same report and application
   history. Document/proposal bytes remain immutable throughout.

The synthetic transport is a test seam, not live Ollama evidence or semantic
model-quality proof. The no-confirm answer and final-select tests refuse before
workpad resolution; cross-Gig, tampered-byte, legacy-reference and document
reader negatives remain covered by the R2/application focused suites rather
than being represented by fabricated provenance in this journey.

## Exact verification

Commands were run in the dirty worktree with no provider/network/model call:

```text
time .venv/bin/pytest -q tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey
1 passed in 105.27s (0:01:45)

time .venv/bin/pytest -q tests/test_scout_discovery_job.py tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey
5 passed in 105.27s (0:01:45)

time .venv/bin/pytest -q tests/test_scout_r4_journey.py tests/test_scout_discovery_job.py tests/test_scout_r2_document_records.py tests/test_scout_r3_report.py tests/test_scout09_application_events.py
27 passed in 168.86s (0:02:48)

time .venv/bin/pytest -q tests/test_scout_r4_journey.py::test_r4_explicit_answer_and_document_actions_require_confirmation tests/test_scout_discovery_job.py
5 passed in 0.15s

ruff check src/gigai/scout_discovery_job.py src/gigai/scout_document_records.py src/gigai/application_events.py src/gigai/scout_documents_cli.py src/gigai/scout_answer_cli.py src/gigai/data/scout/gig.py tests/test_scout_discovery_job.py tests/test_scout_r4_journey.py
All checks passed!

time .venv/bin/python tools/verify_installed_schemas.py
verified 73 installed GigAI schemas (0.03s)

.venv/bin/python src/gigai/data/scout/gig.py --help | rg 'answer|final-select|proposal|tailor|report'
copied wrapper lists answer, final-select, proposal, tailor and report

.venv/bin/gigai --help | rg 'scout-answer|scout-documents|application|scout-report'
installed CLI lists scout-answer, scout-documents, application and scout-report
```

The first development invocation used an intentionally wrong synthetic
question identifier and refused with `answer_question_missing` in 15.12s;
the identifier was corrected to the fixture's real `leadership-01`, and the
passing timings above are the accepted evidence. The schema inventory is
additive: the application-event digest changed for the closed Scout ref shape
and `scout-document-selection-v2.schema.json` is registered; historical v1
resource bytes remain unchanged.

After the final CLI output-only adjustment, a fresh isolated journey rerun
also exposed an existing shared dirty-worktree issue before any Scout action:
the fixture's committed `capmanifest_...0072.json` is published by the
historical `scout_source_materialized` transition, while the current frozen
`journal.py` mutable-manifest reader accepts only `capability_review_decided`.
That run therefore refused at native fixture setup with
`mutable capability manifest has an invalid publishing transition`; this
cannot be corrected in this lane because `journal.py` is shared/frozen. The
27-test focused run immediately before that presentation-only adjustment
passed, and the new output code only rereads the same persisted v2 selection;
the exact regression is reported rather than hidden.

The shared frozen `journal.py` subsequently changed independently to accept
the one-publisher `scout_source_materialized` bootstrap transition while
retaining reviewed replacement checks. A fresh rerun against that current
worktree then passed both the complete journey and explicit confirmation-gate
test in 153.17s; this report does not attribute or include that shared change.

## Remaining bounded gates

| Area | Demonstrated here | Still open |
| --- | --- | --- |
| Proposal reassessment | Immutable old/new input revisions and answer association | Richer question generation/answer UX and bounded acquisition persistence |
| Tailor documents | Authenticated generated revisions, checks, v2 selection | UI review and final document editing; no auto selection |
| Application | Explicit saved/applied/rejected correction with exact Scout refs and replay | No outbound submission; application remains a local intent/history event |
| Discovery import | Deadline/progress and visibility classification for supplied public rows | Journal-backed public acquisition writer/crawler integration and scheduler/lease |
| Reports | Projection rebuild and fresh local HTML read | Broader session/rebuild review and future UI |

No new API, scheduler, crawler, hosted model path, or private-data export was
introduced. The copied wrapper and installed CLI expose the actions, but this
evidence does not claim release-wide CLI/UI acceptance or live local-runtime
quality; R0 receipt regressions and remaining release-graph gates belong in
the later grouped review.
