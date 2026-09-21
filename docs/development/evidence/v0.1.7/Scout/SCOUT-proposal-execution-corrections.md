# SCOUT proposal execution corrections

Date: 2026-09-11  
Scope: bounded corrections for the local proposal-assessment caller after
`SCOUT-proposal-execution-review.md`. This slice changes no local transport,
Scout renderer, application state, UI, provider, scheduler, or historical
.074/.075 source bytes.

## Delivered boundary

`execute_local_proposal` now fails closed unless the supplied Run and Goal are
authenticated from committed Run Details and the sealed Goal Graph belonging to
the resolved Gig. The authority check requires an active committed Run, a graph
member Goal whose committed detail is `running`, the graph digest pinned by Run
Details, a semantically valid graph, and the narrow accepted `write_workpad`
effect. The check runs before model resolution/source use, again immediately
before invocation, and again while holding the writer lock before publication;
foreign, unallocated, nonmember, terminal, or effect-incompatible IDs cannot
create invocation/result artifacts or transitions.

Generic G18 invocation recording has a narrow `commit_goal_transition=False`
path. It still builds and validates the existing v1/v2 invocation record and
returns its exact request/record/response artifacts, but leaves Goal
terminalization to the host caller. Proposal output validation therefore
precedes one host-owned `goal_completed` or `goal_failed` publication containing
the invocation evidence and private result together. Existing callers retain
the default G18 behavior and historical invocation contract.

Private sources now use an explicit closed `{purpose, selector}` association;
family alone never supplies purpose. Native `profile_preferences` is admitted
only as `preferences`, native `experience_qa` only as `experience`, and
committed imported resume/project/role/cover-letter references only as explicit
`experience`. The existing imported `job_description` Run-input contract is
not silently relabeled as an answer, so that unsupported semantic path refuses
before transport; answer-source support remains a separate source-contract
gate. Exact source bytes, source order, family identities, and digest checks
remain host-owned and authenticated under the existing writer snapshot.

## Verification

Commands run in this focused lane:

```text
$ .venv/bin/pytest -q tests/test_scout_proposal_execution.py
6 passed in 89.17s
$ .venv/bin/pytest -q tests/test_g18_model_execution.py
7 passed in 8.69s
$ ruff check src/gigai/model_execution.py src/gigai/scout_proposal_execution.py tests/test_scout_proposal_execution.py
All checks passed!
```

The six proposal tests include a real completed discovery fixture, a genuinely
allocated normal Run/Goal fixture whose IDs and graph bytes come from the Run
lifecycle, local synthetic transport, malformed model-lineage rejection with a
single host `goal_failed` after the fixture's start marker, local/hosted policy
refusals, changed discovery selection, foreign/nonmember authority refusals,
and incompatible private-purpose refusal. The disposable fixture publishes a
schema-valid active state after the deterministic worker exits solely to keep
the proposal call deterministic; it does not invent Run/Goal identifiers or
claim scheduler support for a proposal graph.

## Limits and remaining gates

This is not a scheduler integration or a first-class proposal-Goal graph. The
caller still requires an already allocated/started active Run/Goal and does not
allocate one, schedule one, reopen terminal history, or provide replay/dedup
semantics. An imported `job_description` remains an authenticated source but is
not an answer revision; a future answer contract must add its own closed native
or G45 source kind and purpose validator rather than relabeling this family.
Exact prompt replay remains deferred because the accepted invocation record
retains selected reference IDs/input digest rather than raw prompt bytes; the
host lineage and response artifact remain private and authenticated.

No claim is made about model semantic truth, proposal quality, UI/report
projection, daily scheduling, Tailor, resume generation, applications, or
outbound submission. A future scheduler caller must provide the approved
proposal graph/effect and invoke this helper only after its own normal Goal
allocation/start authority is committed.
