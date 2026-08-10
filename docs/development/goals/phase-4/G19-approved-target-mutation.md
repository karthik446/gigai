# G19 — Approved Target Mutation

- Status: Active — approved for implementation
- Depends on: G16 completion and terminal handoff, accepted S16-EVAL
  methodology, G18 completion and terminal handoff, and G22 completion audit
  and terminal handoff; consumes G04/G05/G06/G09 target, workpad, journal, and
  manifest authority
- Unblocks: G20 local `improve` and evaluator learning

## Outcome

Extend GigAI from workpad-only outputs to one narrowly bounded, explicitly
approved target effect. G19 must apply a reviewed, content-addressed document
edit to a bound local Git target only after a distinct target-effect
authorization. It records exact target before/after state, patch identity,
user-owned commit policy, and recoverable failure evidence.

G19 is an effect boundary, not a general agent executor. A proposal approval,
a successful Review Loop, an addressed artifact, a model response, or a
`write_workpad` choice from G22 is not by itself permission to mutate a target.
The target-effect authorization and the action-time target manifest are the
authority for the one mutation attempt.

## Contract gate

Before runtime implementation, read and cite:

- G16's completed Review Loop audit and terminal handoff;
- S16-EVAL's accepted corpus, judge bar, and mutation evidence;
- G18's completion audit and terminal handoff;
- G22's completion audit and terminal handoff; and
- the existing G04/G05/G06/G09 target, workpad, journal, and recovery rules.

The current G22 contract deliberately limits its effect choices to
`read_local` and `write_workpad`. Neither choice authorizes a target write.
The existing `gig-proposal` and `active-gig-version` resources also do not
silently become mutation authorizations. The accepted [G19 target-effect
contract amendment](../../evidence/phase-4/G19/target-effect-contract-amendment.md)
chooses a dedicated additive resource and settles the durable target-effect
authorization, mutation transition, patch identity, and target manifest shape.
Runtime code must conform to that amendment and must not invent the shape by
inference.

The contract decision must also settle whether the first target-effect record
contains, at minimum, the target identity, active proposal identity, operator
actor, effect kind, relative target path, source artifact identity, expected
before digest, expected after digest, authorization timestamp, cancellation
policy, and user-owned commit policy. A missing decision stops G19 before
runtime mutation code.

## First implementation boundary

The first implementation is intentionally narrow:

- one explicitly bound local Git target;
- one regular, non-symlink document file inside that target;
- one reviewed workpad artifact whose exact bytes are the proposed replacement;
- one operator-authorized target-effect record naming that file and its
  expected before/after digests; and
- no GigAI-created Git commit. The supported v1 commit policy is
  `leave_uncommitted`; the user owns any later commit.

Multi-file patches, arbitrary code execution, binary transformations, non-Git
targets, generated commands, package/tool execution, deployment, and automatic
commit creation are deferred until a later contract and Goal explicitly adopt
them. This scope still proves the general target-effect invariants without
pretending that arbitrary patch application is safe by default.

## In scope

- Revalidate the G22 active proposal, target binding, current Git identity,
  target path, and target-effect authorization immediately before mutation.
  A stale proposal, superseded proposal, changed target, missing authorization,
  or mismatched target identity fails closed.
- Require a completed Review Loop result and addressed artifact for the same
  proposal before a target effect can be authorized. The addressed artifact is
  an input artifact, not permission; the operator must separately approve the
  target effect.
- Validate the source artifact's exact bytes and digest, the relative target
  path, regular-file/non-symlink containment, expected before digest, expected
  after digest, and file-mode policy before exposing any target change.
- Refuse a dirty or ambiguous target baseline. v1 requires the bound Git
  target to have the exact recorded `HEAD` and no unapproved worktree/index
  changes at the target path; unrelated dirty paths also fail closed unless a
  later contract explicitly defines an allowlist.
- Capture a canonical target-before manifest containing repository identity,
  `HEAD`, index/worktree status, the authorized relative path, file mode, size,
  and exact content digest. Capture a matching target-after manifest after the
  effect and before terminal success.
- Stage the replacement inside an approved temporary location, validate the
  staged bytes and after digest, and expose the one-file change through an
  atomic replacement that preserves the declared mode. No shell, patch
  executable, arbitrary subprocess, provider, tool, or credential lookup is
  part of the v1 mutation path.
- Persist ordered journal transitions for authorization, preparation,
  exposure, verification, refusal, cancellation, failure, rollback, and
  terminal completion. A terminal mutation record is immutable and names the
  before/after manifests and patch identity.
- Define recovery for interruption at every boundary. Before exposure, cleanup
  leaves the target unchanged; after exposure, recovery either verifies the
  exact expected after state and commits the terminal record or restores the
  exact before bytes and records `rolled_back`; ambiguous state blocks and
  requires operator inspection.
- Make a repeated authorized application idempotent when the target already
  matches the recorded after digest. A divergent target, changed authorization,
  or different patch identity is refused rather than overwritten.
- Keep target mutation separate from G18 provider execution and G17 capability
  installation. G19 may consume committed workpad artifacts but does not call a
  provider, activate a capability, acquire credentials, or run a tool in v1.
- Verify the boundary through a disposable document-edit Gig, fresh-wheel
  replay, target/workpad before-and-after manifests, interruption fixtures,
  mutation tests, and a completion audit/terminal handoff.

## Out of scope

- Treating G22 proposal approval, `write_workpad`, a model response, a Review
  Loop report, or an addressed artifact as implicit target permission.
- Multi-file or arbitrary unified-diff application, source-code execution,
  generated shell commands, arbitrary subprocesses, editors, formatters,
  package managers, capability execution, or provider/tool invocation.
- Non-Git target mutation, deployment, remote workspaces, network filesystems,
  remote hosting, background workers, queues, schedules, retries, fallback, or
  target synchronization.
- Automatic Git commits, pushes, branch creation, merge operations, history
  rewriting, or a GigAI-owned commit policy. v1 leaves the approved change
  uncommitted for the user to inspect and commit.
- Mutation without an action-time clean baseline, exact target identity,
  explicit target-effect authorization, and an operator actor.
- Reusing SQLite, browser state, model output, an in-memory approval, or a
  stale target manifest as mutation authority.
- Learning, recurring improvement, comparative history, or automatic proposal
  revision. G20 and G21 own those behaviors.

## State and authority contract

The target-effect lifecycle is separate from the G22 interview and G16 Review
Loop states:

```text
proposal_approved
        |
        v
effect_authorized -> prepared -> exposed -> verified -> applied
       |              |          |          |
       v              v          v          +--> rolled_back
     refused        failed     cancelled
                                  |
                                  +--> blocked (ambiguous recovery)
```

The exact serialized state names and journal transitions are adopted by the
accepted additive contract amendment. The following rules are non-negotiable:

1. `proposal_approved` is necessary but not sufficient. Only an explicit
   operator target-effect authorization for the exact proposal, target, path,
   source artifact, and expected before digest may enter `prepared`.
2. The action-time target manifest is authoritative for the mutation attempt.
   A stale or dirty target, changed Git identity, path escape, symlink, mode
   mismatch, or digest mismatch refuses before exposure.
3. `prepared` contains validated staging and no target-visible change.
   `exposed` means the target replacement occurred but final verification and
   terminal journaling are incomplete; interruption at this state is never
   reported as success by inference.
4. `verified` requires the exact expected after digest, path, mode, Git
   identity, and target manifest. Only then may G19 record `applied`.
5. `failed`, `cancelled`, `refused`, `rolled_back`, and `blocked` are terminal
   for the attempt. No terminal mutation record transitions to retry, fallback,
   another target, or another patch automatically.
6. If the target already has the exact expected after state and the same
   authorization/patch identity, replay may return the prior terminal result
   without a second write. Any divergence requires refusal and operator review.
7. The target never becomes a source of hidden model context. G19 consumes only
   the explicitly authorized path and artifact; it does not expand the read or
   write set from filesystem discovery.

## Acceptance criteria

1. Before runtime implementation, G19 cites the completed G16, S16-EVAL, G18,
   and G22 audits/handoffs and records an accepted additive contract amendment
   for target-effect authorization, mutation states, patch identity, and
   target before/after evidence. No existing resource changes meaning by
   inference.
2. A valid target-effect authorization is bound to one active proposal, one
   Gig/project, one Git target identity, one relative regular-file path, one
   source artifact digest, one expected before digest, one expected after
   digest, one operator actor, and the v1 `leave_uncommitted` commit policy.
   Missing, stale, duplicated, or cross-Gig authorization fails closed.
3. G19 refuses target mutation unless the source proposal is active and the
   same proposal has a completed Review Loop result with a valid addressed
   artifact. Review output alone never authorizes mutation.
4. A clean-target fixture captures a canonical target-before manifest and
   applies exactly the authorized one-file replacement. The target-after
   manifest proves the exact path, mode, size, bytes, digest, Git identity,
   and declared worktree delta; no other target path changes.
5. Dirty-target, changed-HEAD, changed-before-digest, missing-file,
   symlink/path-traversal, mode-mismatch, source-digest, after-digest, and
   cross-target fixtures refuse before target exposure with deterministic
   rejection codes.
6. The preparation boundary validates staged bytes and all target policy before
   exposure. A failpoint before exposure leaves the target and Git state
   byte-identical to the before manifest.
7. An interruption after exposure is recoverable. Recovery either verifies the
   exact expected after state and records `applied`, restores the exact before
   manifest and records `rolled_back`, or records `blocked` for ambiguity; it
   never reports success from an incomplete journal.
8. Cancellation before exposure produces a terminal `cancelled` record without
   target change. Cancellation after exposure follows the same verify/restore/
   block rule and cannot create a second attempt automatically.
9. Replaying the same authorized mutation against the exact after state is
   idempotent and produces no second write or Git commit. A different patch,
   proposal, target, actor policy, or expected digest is refused.
10. G19 creates no Git commit, push, branch, merge, provider call, credential
    lookup, capability execution, arbitrary subprocess, network request, or
    background activity. Offline process guards and target/workpad manifests
    prove no effect outside the authorized file and required workpad evidence.
11. Mutation tests kill removal of the separate authorization gate, active
    proposal revalidation, dirty-target refusal, path containment, source and
    before/after digest checks, staging boundary, atomic exposure, recovery
    decision, and user-owned commit policy.
12. A fresh installed wheel replays the clean document-edit fixture and every
    refusal/recovery fixture from local bytes without a source checkout,
    provider credentials, network, target repository state, or test-module
    imports.
13. Completion evidence includes the accepted contract amendment and hashes,
    state/transition table, target-effect and patch corpus, sanitized before/
    after manifests, refusal/cancellation/partial-write/recovery records,
    mutation report, installed-wheel replay, completion audit, and terminal
    handoff. The audit explicitly states that G20 learning and G21 recurrence
    remain absent.

## Verification and evidence

- Contract vectors prove the additive target-effect authorization, patch
  identity, target manifest, and mutation state resources without changing
  existing schema hashes or authority semantics.
- Disposable Git-target fixtures exercise clean application, dirty refusal,
  changed HEAD, path/symlink rejection, source drift, mode policy, exact
  before/after manifests, and the `leave_uncommitted` policy.
- Failure-point fixtures interrupt preparation, exposure, verification, and
  terminal journaling; recovery compares exact target bytes and manifests.
- Public-boundary tests exercise the G19 lifecycle through the installed CLI or
  approved command surface rather than calling a private helper alone.
- Static import and process-guard checks prove that mutation does not invoke
  providers, credentials, capabilities, shells, arbitrary subprocesses,
  networks, or background workers.
- Mutation tests remove each load-bearing guard and require the corresponding
  negative fixture to fail.
- Evidence lives under `docs/development/evidence/phase-4/G19/` and includes a
  completion audit and terminal handoff before G20 begins.

## Stop boundary

Stop before implementation if G16, S16-EVAL, G18, or G22 completion evidence is
missing, if G22's approved proposal cannot be distinguished from a separate
target-effect authorization, or if the additive contract cannot represent the
target identity, patch identity, before/after manifests, mutation states, and
recovery outcomes.

Stop before exposure if the target is dirty or ambiguous, the active proposal
or target identity changed, the source or expected bytes do not match, the
authorized path is not a regular non-symlink file, or the clean before
manifest cannot be captured.

Stop and block if an interruption can leave an unexplained target delta, if
recovery would need to guess between before and after, if a terminal record can
be rewritten, if a replay can write twice, or if G19 needs a provider, tool,
credential, shell, network, automatic commit, target discovery, fallback, or
background worker. Do not begin G20 or G21 behavior inside G19.
