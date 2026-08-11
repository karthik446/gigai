# G21 — Recurring and Comparative Gigs

- Status: Complete; contract amendment accepted; implementation and closeout
  evidence accepted
- Depends on: G13 and G14 sealed Run/sequential scheduler evidence, and the
  G20 completion audit and terminal handoff; consumes existing G15/G16 review
  artifacts without changing their authority
- Unblocks: G24 alpha-readiness planning after G23 completion; does not
  authorize a daemon, OS scheduler, or background provider activity

## Outcome

G21 makes one approved Gig explicitly repeatable as a sequence of operator-
triggered occurrences. Each occurrence receives its own durable identity,
exact reference snapshot, Run identity, terminal outcome, and optional
comparison against an explicitly named prior occurrence. A repeated Run is a
new Run over a new immutable input snapshot; it is never a mutation of an old
Run and never a silent continuation of prior state.

The first G21 surface supports manually triggered daily, weekly, and monthly
examples. An external trigger or operator supplies the occurrence slot and
starts the Run. GigAI records the occurrence and snapshot before execution,
uses the existing G13/G14 Run path, and records a comparison only when the two
occurrences are explicitly comparable.

G21 makes missed or unavailable occurrences visible and recoverable through an
explicit operator/reconciliation action. It does not install a daemon, cron
entry, calendar integration, or background worker. Scheduling automation,
retry policy, and unattended provider activity remain later contracts.

## Contract gate

Before runtime implementation, G21 must read and cite:

- [G13's completion audit](../../evidence/phase-3/G13/completion-audit.md) and
  [terminal handoff](../../evidence/phase-3/G13/terminal-handoff.md) for the
  sealed Run authority, fresh Run identity, target-preserving deterministic
  execution, and no automatic retry boundary;
- [G14's completion audit](../../evidence/phase-3/G14/completion-audit.md) and
  [terminal handoff](../../evidence/phase-3/G14/terminal-handoff.md) for the
  sequential Goal Graph scheduler and its rejection of unsupported parallel,
  recovery, and scheduling policy;
- [G20's completion audit](../../evidence/phase-5/G20/completion-audit.md) and
  [terminal handoff](../../evidence/phase-5/G20/terminal-handoff.md) for the
  rule that prior evidence may inform later work but does not silently grant
  authority or modify approved history; and
- [G15's completion audit](../../evidence/phase-3/G15/completion-audit.md),
  [G15 terminal handoff](../../evidence/phase-3/G15/terminal-handoff.md),
  [G16's completion audit](../../evidence/phase-3/G16/completion-audit.md),
  and [G16 terminal handoff](../../evidence/phase-3/G16/terminal-handoff.md)
  when defining snapshot and comparison references. G21 must reuse their
  artifact identity rules and must not reinterpret a Review Report as an
  occurrence.

Before runtime code, an accepted additive amendment must settle the exact
serialized shape and authority for the following. The recommended v1 design
is two dedicated resources, because an occurrence is a new identity that binds
a slot to a Run, while a comparison is derived evidence between two Runs:

1. `gig-occurrence.schema.json` records the Gig/version, canonical occurrence
   slot, trigger actor, immutable reference-snapshot artifact, prior occurrence
   reference, linked Run, state, outcome, and explicit missed/unavailable
   reason. It is not a schedule daemon state or a second active-version
   pointer.
2. `gig-comparison.schema.json` records the current and prior occurrence IDs,
   both Run IDs and Gig versions, both snapshot/output identities, comparison
   method/version, result, and evidence. It never selects a winner, changes a
   Gig, or makes the prior output authoritative.
3. Existing `run-manifest.schema.json`, `run-details.schema.json`,
   `active-gig-version.schema.json`, and `gig-proposal.schema.json` remain
   semantically unchanged unless the amendment proves an additive field is
   required. The existing Run remains the execution authority; the occurrence
   only binds one explicit Run to one repeat slot.
4. The amendment must define the exact packaged-resource baseline and preserve
   all existing schema bytes, hashes, and vectors. The current baseline is 25
   resources; the recommended two-resource amendment would make it 27.

The amendment must also settle calendar semantics without smuggling in a
scheduler: v1 stores an operator/external-trigger supplied canonical
`occurrence_key`, declared cadence (`daily`, `weekly`, or `monthly`), and an
optional exact `scheduled_for` timestamp. G21 validates uniqueness and format;
it does not calculate future calendar occurrences or claim that a missing
trigger is automatically discovered.

## First implementation boundary

The first implementation is intentionally narrow:

- one existing approved Gig version at a time;
- one explicit occurrence record for one supplied slot;
- one immutable Review Bundle/reference-snapshot artifact per occurrence;
- one linked Run ID, allocated through the existing G13/G14 Run path;
- one optional comparison to the immediately named prior occurrence; and
- one manual or test-harness trigger path with no long-lived process.

The first fixtures cover a daily market-state report, a weekly screener, and a
monthly spreadsheet analysis. They use local deterministic inputs and the
existing workpad-only Run boundary. A provider-backed recurring example is
not required for G21 and cannot be implied by the fixture names.

## In scope

- Define a canonical occurrence identity containing `gig_id`, the selected
  approved `gig_version`, cadence, occurrence key, trigger actor, and exact
  reference-snapshot artifact identity. The same Gig/slot cannot create two
  occurrence identities.
- Persist an immutable reference snapshot before the Run starts. The snapshot
  must be a canonical, content-addressed Review Bundle or explicitly amended
  snapshot artifact; its bytes, digest, size, bundle/contract identity, and
  reference set are revalidated at Run time.
- Link an occurrence to at most one Run. A successful repeat allocates a fresh
  `run_id`, preserves the selected Gig version, and records the occurrence
  identity in the occurrence/Run evidence without changing the active pointer.
- Reuse G13/G14's sealed preparation and sequential execution path. A failed
  preparation leaves no apparently runnable occurrence; an interrupted Run
  remains an explicit terminal/interrupted state and is never automatically
  relaunched.
- Define explicit occurrence states and closed terminal outcomes. The
  recommended state sequence is:

  ```text
  declared -> triggered -> snapshot_verified -> run_prepared
           -> run_terminal -> compared -> closed
  ```

  The terminal refusal/outcome states are `blocked`, `skipped`, `cancelled`,
  `unavailable`, `failed`, and `missed`. `compared` is allowed only when a
  comparison is requested and its inputs are valid; otherwise a completed Run
  may close with `comparison: not_requested`.
- Make missed-occurrence handling explicit. An operator or a deterministic
  reconciliation command may mark an expected slot `missed`, `skipped`, or
  `unavailable` with a reason and actor. No timer, daemon, or hidden wall-clock
  process may create that state in v1.
- Compare only explicitly compatible occurrences. The comparison must bind
  both Run IDs, Gig versions, review-contract/Goal Graph identities, reference
  snapshot digests, output artifact digests, and comparison-method version.
  If the inputs differ in a way the method cannot normalize, the result is
  `incomparable` or `blocked`, never a fabricated delta.
- Preserve prior outputs as read-only references. A comparison may report
  `changed`, `unchanged`, `incomparable`, or `blocked`; it cannot overwrite a
  prior Run, promote a prior output to authority, or select which output is
  correct.
- Make repeated operator requests idempotent. Replaying the same occurrence
  request returns the existing occurrence/Run result. A changed snapshot,
  Gig version, contract digest, or occurrence key refuses rather than creating
  a second Run for the same slot.
- Produce sanitized evidence for the occurrence, snapshot, comparison, missed
  state, and terminal Run transitions. Evidence must preserve exact local
  digests and identities without shipping credentials, ambient paths, or
  unredacted private content.

## Out of scope

- Any daemon, cron/systemd/launchd integration, calendar subscription, OS
  scheduler, background worker, or automatic discovery of missed occurrences.
- Automatic retries, fallback, catch-up Runs, overlapping occurrences, or
  concurrency/parallelism policy. A later Goal must define those explicitly.
- Provider/API/CLI execution, credential acquisition, network access, or a
  claim that a daily/weekly/monthly fixture proves live provider scheduling.
  G21 consumes the existing Run boundary and local deterministic fixtures.
- Changes to `active-gig-version.json`, Gig proposal approval, active-version
  selection, G19 target-effect authority, G23 portability, or G20's learning
  and improvement authority.
- Automatic Gig improvement from comparisons. A comparison may become a
  cited G20 learning observation only through G20's explicit provenance and
  evidence gates; G21 does not publish an improvement proposal itself.
- Cross-Gig comparisons, retroactive rewriting of prior Runs, comparison of
  outputs without exact identities, or treating a prior output as a baseline
  authority merely because it is newer or older.
- Automatic schedule calculation, timezone database management, holidays,
  daylight-saving policy, or a general-purpose recurrence language. v1 accepts
  a canonical external occurrence key and records the supplied timestamp.
- Alpha, beta, PyPI, or public-release declaration. Release readiness belongs
  to a later release-lane Goal.

## State and authority contract

The occurrence state machine is closed at terminal outcomes. No terminal
state transitions to a new Run, retry, fallback, or different Gig version:

```text
declared -> triggered -> snapshot_verified -> run_prepared
                                      |             |
                                      v             v
                                  blocked       run_terminal
                                                    |
                             +----------------------+------------------+
                             |                                         |
                         compared                                  closed
                             |
                             +-----------------------> closed

declared/triggered/snapshot_verified/run_prepared
  -> skipped | cancelled | unavailable | failed | missed
```

The following rules are normative:

1. The active-version pointer and workpad journal remain the sole authority
   for which Gig version is approved. An occurrence selects an approved
   version; it cannot advance or replace that pointer.
2. An occurrence key is unique within a Gig and declared recurrence profile.
   Replaying the same key is an idempotent read of the existing occurrence,
   not permission to allocate another Run.
3. A reference snapshot is immutable and must be verified before preparation.
   A changed digest, missing object, symlink, or contract mismatch blocks the
   occurrence before Run execution.
4. A Run ID is fresh per successful occurrence, while the selected Gig version
   and exact Goal Graph remain pinned to the Run. Prior Runs are never reused
   as the current Run's execution identity.
5. A comparison is evidence about two named occurrences. It is not review
   adjudication, evaluator consensus, improvement approval, or target authority.
6. Missed, skipped, cancelled, unavailable, and failed states require an
   explicit reason, matching outcome, and outcome actor. G21 never silently converts absence of a Run into
   success or silently catches up a missed slot.
7. A comparison failure or incompatibility does not rewrite either occurrence.
   It records `incomparable`/`blocked` evidence and leaves both Runs intact.
8. G20 may later consume a comparison as evidence only through its existing
   learning-record and improvement gates. G21 cannot mutate a Gig from a
   comparison result.

## Acceptance criteria

1. **Contract gate.** G21 cites G13, G14, and G20 completion evidence and
   records an accepted additive amendment settling the occurrence resource,
   comparison resource, canonical occurrence key/cadence shape, snapshot
   binding, Run linkage, comparison semantics, missed-state policy, and
   25-to-27 resource baseline (or a documented alternative with equal
   authority clarity). No existing schema meaning changes by inference.
2. **Occurrence contract.** A canonical occurrence record binds one Gig,
   approved Gig version, cadence, occurrence key, trigger actor, and snapshot
   artifact. Duplicate Gig/slot requests are rejected or idempotently replayed;
   they never allocate a second occurrence identity.
3. **Snapshot integrity.** The occurrence refuses missing, redirected,
   symlinked, changed, noncanonical, or contract-mismatched reference snapshot
   bytes before Run preparation. The exact reference set and digest are
   preserved in evidence.
4. **Run linkage.** A valid manually triggered occurrence produces one fresh
   Run ID through the existing G13/G14 path, records the same approved Gig
   version and Goal Graph identity, and leaves the active pointer and prior
   Runs unchanged.
5. **Occurrence lifecycle.** Fixtures cover every non-terminal transition,
   every terminal outcome, interruption before Run preparation, interruption
   after Run preparation, and replay after terminalization. Terminal states
   have no outgoing retry/fallback transition.
6. **Missed occurrence.** A deterministic operator/reconciliation fixture
   records `missed`, `skipped`, and `unavailable` with explicit reason,
   matching outcome, and outcome actor. The schema enforces these refusal
   invariants, as well as requiring a comparison reference for `compared`.
   No background timer or scheduler process is involved, and no missing Run is
   reported as successful.
7. **Comparison integrity.** A valid prior/current pair produces a comparison
   bound to both occurrence IDs, Run IDs, Gig versions, snapshot digests,
   output digests, and method version. The result is deterministic and cannot
   select a winner or mutate either input.
8. **Incomparability.** Changed contract identity, incompatible snapshot
   shapes, missing output, or missing prior evidence produces `incomparable` or
   `blocked` with a deterministic reason, not a fabricated comparison.
9. **Fixture corpus.** Daily market-state, weekly screener, and monthly
   spreadsheet fixtures each produce separate Runs and preserve prior
   snapshots and outputs. At least one fixture exercises a changed reference
   snapshot and one exercises a missing capability/input.
10. **No automatic recurrence.** Static/import/process checks and a test
    harness prove G21 does not install or start a daemon, cron/calendar
    integration, background worker, network request, credential lookup,
    provider call, target mutation, or automatic retry.
11. **Authority preservation.** Repeated Runs do not rewrite the active Gig,
    prior Run manifests, prior outputs, or G20 learning records. A comparison
    cannot advance a Gig version or create an improvement proposal implicitly.
12. **Mutation coverage.** Mutation tests catch removal of occurrence-key
    uniqueness, snapshot digest verification, Run-linkage binding, terminal
    no-retry behavior, comparison input binding, and incomparability refusal.
    A report's existence is not evidence of coverage.
13. **Installed replay.** A freshly built wheel replays the manual daily,
    weekly, and monthly occurrence fixtures from local bytes in a disposable
    home without a source checkout, daemon, network, or provider credentials.
14. **Closeout evidence.** Evidence under
    `docs/development/evidence/phase-5/G21/` includes the accepted amendment,
    occurrence/comparison corpus, lifecycle and missed-state records, mutation
    report, installed replay, completion audit, and terminal handoff. The
    handoff names the later scheduler/release decisions that remain absent.

## Verification and evidence

- Contract vectors prove the additive resource count and unchanged prior
  schema hashes/canonical vectors.
- Lifecycle fixtures exercise occurrence identity, snapshot sealing, Run
  linkage, idempotent replay, interruption, terminal outcomes, and explicit
  missed handling.
- Comparison fixtures independently reconstruct both input identities and
  verify that a comparison cannot be produced from a digest-only or mismatched
  prior output.
- Static effect checks prove no scheduler, network, credential, provider,
  target, or background-process path exists in G21's implementation.
- Mutation tests remove each load-bearing guard and require the corresponding
  negative fixture to fail.
- Fresh-wheel evidence replays the three domain-shaped local fixtures without
  relying on the source checkout.

Evidence belongs under `docs/development/evidence/phase-5/G21/`. Raw private
references, credentials, ambient paths, and model outputs do not ship as
evidence.

## Stop boundary

Stop before runtime implementation if the amendment cannot define occurrence
identity, exact snapshot binding, one-Run linkage, terminal/no-retry states,
comparison authority, explicit missed-state handling, and the schema/resource
baseline without inference.

Stop before adding any automated trigger if the implementation would require a
daemon, OS scheduler, calendar integration, background worker, retry policy,
catch-up behavior, or concurrency policy. Those require a later contract.

Stop before comparison success if either input lacks an exact occurrence, Run,
Gig-version, snapshot, output, or comparison-method identity. Record
`incomparable` or `blocked` instead.

Stop before connecting G21 to G20 improvement if a comparison would bypass
G20's provenance/evidence gates or create a new active version implicitly.
