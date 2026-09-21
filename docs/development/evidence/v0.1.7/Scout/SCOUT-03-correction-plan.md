# SCOUT-03 — Bounded correction sequence

**Status:** Second C1 combined suite passed: 782 tests and 104 subtests, one
intentional live-model skip. Fresh Claude C1 review accepted; native-record
implementation is now under its own independent review. The [parallel delivery plan](parallel-delivery-plan.md)
supersedes this document's serial implementation sequencing, not its acceptance
criteria. C3 integration remains open.  
**Owner:** Astra coordinator; bounded implementation workers, then fresh
Claude review and independent verification.  
**Authority:** Existing approved SCOUT-03 implementation scope; no new product
approval is required to correct omissions from that scope.

## Current evidence and disposition

[Claude's review](SCOUT-03-review.md) rejects the partial implementation.
Astra read it fully and independently confirmed the missing native content,
archive/default/override/tool-binding/Plan-selection paths, unlocked receipt
and parent checks, UUID-sort revision ordering, and working-file projection.
[The recovered full suite](SCOUT-03-full-suite-recovery.md) passes 769 tests
and 104 subtests with one intentional live-model skip. That regression result
does not close missing acceptance behavior. Astra read Luna's completed
[adversarial report](SCOUT-03-verification.md): it reproduces two successful
null-parent concurrent creates, broken matching-key receipt replay, uncaught
post-commit projection failure, and silent replacement of a malformed database.
These concrete failures are mandatory C1 regression cases. Native/default/
archive and tool/Plan gaps remain C2/C3, not waived by the passing suite.

Review clarifications:

- Five SCOUT-03 schemas were added (44 to 49), not six as one review sentence
  says. The inventory total itself is correct.
- Migration legitimately replaces the old `.gitignore`; immutable record,
  source, receipt and revision publication must still be no-clobber.
- Projection status belongs in the operation result and disposable cursor;
  do not rewrite an immutable committed receipt to mark a later rebuild.
- Preserve ignored drafts; refuse ambiguous collisions without deleting or
  relocating user files. Collision refusal itself is not data loss.
- Provider privacy checks must follow known typed private-record provenance at
  every supported ingress. Fix real private-reference/typed-input bypasses,
  including known imported content copied through generic input routes where
  its provenance can be identified. Do not claim an OS sandbox or universal
  detection of arbitrary external-agent copies/transformations of private text.
- The review's tests and environment-flake observations are reviewer evidence,
  not source-only evidence. Its full-suite-pending statement is superseded by
  the coordinator's later completed run. Retain the original report unchanged.
- Luna's missing external Plan/Run observations describe SCOUT-04, not an extra
  C1 requirement. C3 must connect the accepted selected-input foundation and
  private ingress checks; it must not implement the deferred external executor.
- A `--no-deps` wheel check with missing `questionary` is an incomplete CLI
  verification setup, not proof of an undeclared dependency. Installed CLI
  acceptance still needs the normal declared dependencies in a disposable
  environment. Resource-only verification does not satisfy that gate.

## C1 — Make existing storage trustworthy

First correction, deliberately narrower than the full goal. Own only storage,
layout, journal and database consistency plus their tests; do not claim native
profiles, tool approval or Plan selection are complete yet.

1. Validate every path component and canonical ID; authenticate exact marker,
   import, receipt and revision bytes against committed journal publication,
   not a Git subject string or schema-valid working file. Check record/snapshot
   IDs, canonical path, media, size and digest agree. Selection and content read
   must reject tampered, uncommitted, foreign, missing or redirected evidence.
2. Put operation-key lookup, normalized-payload comparison, equivalent-import
   deduplication and parent CAS inside the journal writer critical section.
   Normalize without generated revision/record IDs defeating same-request
   replay. Return the ORIGINAL committed IDs/receipt on an identical retry.
   Include all semantic request fields (actor/origin/media/relationships etc.)
   in conflict identity. Different keys cannot bypass content idempotency.
3. Follow the authenticated parent chain/committed ordering, never lexical
   UUID ordering. Reject branches/cycles/broken parents and append with
   no-clobber publication. Stale concurrent updates yield one winner and one
   typed conflict with no duplicate/overwritten authoritative artifact.
4. Rebuild from validated committed records/receipts at a pinned journal HEAD.
   Use a closed schema/cursor, preserve G22 trace, and coordinate ALL trace
   paths including direct `persist_trace` and long-lived HTTP connections.
   Replacing the database inode while a connection remains open is not solved
   merely by adding a lock around the later INSERT. Prefer safe in-place
   managed-table transactions if appropriate; never execute supplied SQL.
5. Distinguish absent database from corrupt/unrecognized state; never silently
   erase malformed trace/unknown tables. Scout projection corruption can be
   rebuilt only where authoritative history suffices; legacy trace loss must
   refuse with an actionable recovery diagnostic.
6. Return committed plus `projection_pending` after a post-commit view failure.
   Safe retry/rebuild must repair without another application event. Cursor
   mismatch repairs or reports staleness. Atomically publish scoped context
   with coherent HEAD/schema metadata using bounded unique staging paths.
7. Make layout migration recoverable across marker/ignore publication failure,
   preserving v1 history and ignored drafts. Validate new roots and exact
   committed layout/ignore binding without following parent symlinks.

Required new regressions: non-monotonic UUID revisions; identical create/update
retry and changed-payload conflicts; concurrent same-parent and equivalent
imports; forged/uncommitted receipts/revisions/markers; altered snapshot and
parent symlink/traversal; interrupted journal/migration and projection failure;
legacy-first/Scout-first rebuilds and direct G22/HTTP trace concurrency;
unknown/corrupt database and cursor/content divergence. Test source bytes and
commit count, not only returned status. Keep the original SCOUT-02 guarantees.

## C2 — Deliver the actual record and CRUD behavior

After C1 settles, build on its validated operation service:

- Strict kind-specific native profile/preference, experience question/answer,
  supplied source/artifact and selected-conversation content with provenance,
  bounded values, explicit question states and uncertainty. Use canonical
  native blob/`docs/` paths; never duplicate G45 imported bytes.
- Complete agent-readable CRUD: create, update with expected parent, archive,
  list, exact read, and explicit rebuild/context commands. Archive preserves
  exact historical selections; no hard-delete promise.
- Explicit saved-default versus task-only override semantics. A task override
  cannot update the default or another task's context. Questions, typed
  relationships, artifact backlinks and current work must be real persisted
  metadata, not empty arrays or record-kind labels standing in for content.
- Selected conversation import takes only supplied bounded excerpts/summaries,
  marks which it is and its source/selection, and never reads ambient chat or
  follows a session locator. Plain listing stays metadata-only.
- Tight new schema patterns, complete golden/inventory fixtures, readable code
  and clean scoped lint. Existing accepted schemas/IDs remain unchanged.

Prove a fresh process recovers preferences and an unanswered experience
question, saves an answer, keeps an older revision readable, applies a task
override, and archives without changing old selections. Exercise CLI and
library paths, not only synthetic revision wrappers.

## C3 — Bind tools and exact inputs end to end

After C2 settles:

- Implement approved Gig-owned tool mutation binding with actual inventory,
  Gig/version/entry/tool bytes/effects/agent origin checked before dispatch
  and publication, and recorded in receipts. Keep direct built-ins distinct;
  reject unapproved/changed/extra executable bytes and broadened effects.
  Scaffold shipping remains SCOUT-05, not an excuse to omit this service seam.
- Normalize explicit G45 IDs plus Scout record/revision selections into one
  pinned input set without erasing explicit revisions. Wire into the supported
  Plan boundary and revalidate before Run allocation. Never mutate an older
  sealed Plan after a saved preference/answer changes.
- Enforce known private provenance at Plan/Run/provider ingress, including
  typed review inputs and supported alternate callers. No new provider grant
  is inferred from import, read, selection or an agent envelope. Keep the old
  invocation protocol's forbidden-key boundary unchanged.
- Guard clean package/export against private record/context/report material;
  test it explicitly. Full private transfer is still SCOUT-11.
- Prove installed CLI/resources and the fresh-session candidate/posting/answer
  input-selection story. External Plan/Run execution is still SCOUT-04 and
  consumes this shared selection resolver later.

## Verification and coordination

C1 task `task_06d965422f18`, dispatch `ctx_0532891e1307`, reached
`ready/input_accepted` after both review and verification completed. The fresh
terminal `term_a91a17c6-90e2-4684-8e96-c593a349c52f` was launched with
`codex --model gpt-5.6-terra --approve-for-me -c model_reasoning_effort="high"`.
No existing user-owned Terra terminal was changed. The previous Luna verifier
was released with `retained/external_terminal`, no process action; its report
was read and its completion acknowledged before C1 began.

That C1 worker has now completed and was released with
`retained/external_terminal`, no process action. The coordinator read its
[handoff](SCOUT-03-C1-implementation.md) and then reproduced
[five remaining failures](SCOUT-03-C1-coordinator-verification.md). C2 is not
cleared to start. A report-only independent Claude C1 review is active as task
`task_38e35bb47331`, dispatch `ctx_0b3a8bd0519f`; consolidate its findings with
the coordinator tests before the next runtime correction dispatch.

That independent review is now complete and its terminal was released with
transcript captured. The coordinator added two corrected recovery probes and
consolidated the review disposition in the
[second-pass brief](SCOUT-03-C1-second-pass.md). Terra task `task_33a635107c45`,
dispatch `ctx_28cc3ba4652c`, is active in the same explicitly approved-mode
Terra terminal. C2 task `task_d8d515fa409f` is explicitly blocked pending C1
acceptance; no C2 source writer has started.

The second Terra pass has now completed. Its reported 11 acceptance, 47 wider
C1 and 13 G22 tests are recorded in the
[handoff](SCOUT-03-C1-second-implementation.md). Completion was confirmed,
released and acknowledged. The coordinator's
[combined suite](SCOUT-03-C1-combined-verification.md) is running in background
session `75389`; no repeated polling, per the user. Fresh independent review
and verification remain before C1 acceptance. C2 is still blocked.

Each correction gets an explicit file-ownership brief and evidence handoff.
Luna's report-only [C2 native record design](SCOUT-03-C2-record-contracts.md)
is complete, with coordinator implementation clarifications appended. The
bounded C2 implementation task `task_d8d515fa409f` is queued behind C1; no C2
runtime worker has started. The coordinator will inspect C1's settled handoff
before dispatching that task.
The coordinator also prepared [C3 integration notes](SCOUT-03-C3-integration-notes.md)
against untouched capability/scheduler/package surfaces. Neither note is
implementation or acceptance evidence, and neither changed C1-owned source.
One shared-source writer at a time. Workers finish their focused tests and
report before the coordinator inspects their code; no duplicate full suites.
After C1–C3, run the full offline suite, isolated wheel verification, fresh
Claude re-review and independent adversarial verification. All original
SCOUT-03 acceptance rows must be satisfied or explicitly remain incomplete.

Do not edit the approved G44 baseline, private user `.gigai/`, configuration,
unrelated dirty work, or old evidence to make results look successful. No
provider calls, new init/default batch, external executor, UI server, hooks,
Dolt, commit, or release publication is authorized by these corrections.
