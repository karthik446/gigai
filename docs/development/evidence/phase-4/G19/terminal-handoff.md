# G19 Terminal Handoff — G20

- Status: G19 complete and accepted
- Handoff date: 2026-08-10
- Next goal: G20 — Local `improve` and evaluator learning
- Required evidence: [G19 completion audit](completion-audit.md), [accepted amendment](target-effect-contract-amendment.md), [mutation report](mutation-report.md), and [installed replay](installed-replay.md)

## Handoff verdict

G19 establishes the first controlled target-effect boundary. A target write is
possible only through a durable `target-effect` authorization bound to one
active proposal, one addressed Review Loop artifact, one operator, one Git
target, one regular file, exact before/after digests, and the
`leave_uncommitted` policy. The target remains user-owned and uncommitted.

The lifecycle is closed at:

```text
effect_authorized -> prepared -> exposed -> verified -> applied
```

with terminal `refused`, `failed`, `cancelled`, `rolled_back`, and `blocked`
states. There is no automatic retry, fallback, second target, or second patch
path. Recovery compares exact target state before deciding whether to apply,
roll back, or block for operator inspection.

## G20 start condition

G20 is startable only when it cites this handoff and the completion audit, and
when its own goal contract explicitly preserves these boundaries:

1. G20 may consume completed target-effect records and user-owned outcomes,
   but it may not infer mutation authority from a proposal approval, model
   response, addressed artifact, or `write_workpad` choice.
2. Any proposed change to a Gig's review contract, rubric, verifier, recovery,
   or parallelism must remain a proposal until its own operator approval and
   contract gate are satisfied.
3. Learning inputs must preserve provenance and distinguish observed target
   outcomes from evaluator judgments, feedback, and accepted outcomes.
4. G20 must not add provider calls, capability execution, automatic commits,
   recurring jobs, or multi-file effects by inference from G19.
5. G20 must define its own acceptance corpus, judge/calibration evidence, and
   recovery/rollback behavior before implementation changes a shipped Gig.

## Explicit non-handoff

The following are deliberately not handed to G20 as supported behavior:

- arbitrary or multi-file patch application;
- remote, non-Git, or network filesystem mutation;
- Git commit, push, merge, branch, or history rewrite;
- provider/model/tool execution as a mutation mechanism;
- credentials or capability installation;
- automatic retries, fallback, or background recurrence; and
- automatic learning or proposal revision.

Those require separate contract decisions and acceptance evidence.
