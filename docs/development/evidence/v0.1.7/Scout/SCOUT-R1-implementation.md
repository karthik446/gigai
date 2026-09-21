# SCOUT R1 implementation handoff

Date: 2026-09-11  
Status: bounded immutable proposal-record lane implemented; shared registration remains an R4 integration step.

## Delivered

* `scout_proposal_records.py` adds the host-owned `scout-proposal-revision:1`
  record. It accepts only a complete result returned by the existing local
  proposal caller, re-resolves the exact discovery selector and explicitly
  purposed private selectors, and refuses if the sealed source lineage differs.
  Posting provenance contains the completed discovery Run/receipt/checkpoint
  and capture artifact; private input refs retain exact native record/revision
  and blob digest identities. The model never supplies these associations.
* Proposal records are immutable journal artifacts at
  `records/scout-proposals/{record_id}/revisions/{revision_id}.json`, published
  with the existing writer and `private_record_revised` transition. The reader
  requires the exact single authenticated publication, closed fields, digest
  and scope. The operation key is content-derived; an identical retry returns
  the original record without a transport call or extra write.
* `scout_discovery_job.py` is a read-only adapter over the accepted completed
  discovery posting resolver. It exposes a public acquisition row and does not
  create a second mutable job database. `scout_proposal_cli.py` is a callable
  entry for R4 registration; it performs local execution then host-owned
  revision recording and does not Tailor, apply, or register a scheduler.
* Native `experience_qa` revisions can now be explicitly selected with purpose
  `answer`; the host records the selected question IDs. No answer is inferred
  from a record kind, and G45/native IDs are never fabricated.

## Exact persistence shapes

The added records are strict host-owned values with schema IDs
`scout-proposal-revision:1`, `scout-answer-association:1`, and
`scout-proposal-discovery-job:1` (schema files are intentionally not put into
the shared registry by this lane). A proposal record binds `opportunity` to
the resolver's exact public refs, `assessment` to the validated model object
and digest, `input_revisions` to exact native blob refs and explicit purpose,
`answer_associations` to question IDs, `invocation` to actual Run/Goal/result
identity, and `method` to configured local target/digest. All proposal records
are private by default, including their raw assessment text.

The existing completed discovery packet remains the authority for considered
jobs, duplicate/acquisition failure/exclusion facts. This lane does not claim
that a new crawler or acquisition journal event exists; the integration patch
asks R4 to add one correctly named transition if acquisition results need
independent persistence beyond the existing packet.

## Verification

```text
time .venv/bin/pytest -q tests/test_scout_proposal_records.py::test_complete_assessment_is_an_immutable_revision_with_idempotent_replay
```

Result: `1 passed in 23.59s` (shell `23.723s`). The test uses the existing
disposable discovery/private fixture and injected synthetic transport, then
reads the committed proposal record and repeats the same operation key. A
separate closed-shape test passed during the initial file run; the first file
run exposed and fixed one mapping-proxy canonicalization bug and then passed
the positive case. Ruff check over all changed R1 Python files passed (`All
checks passed!`). Existing proposal execution evidence remains the source of
the invalid-output, interruption, local-permission, exact source-resolution,
and historical Run/Goal checks; this handoff does not rerun that ~53-second
compatibility lane. The final pure focused command selected the discovery-job
and closed-shape/answer-association tests: `4 passed in 0.13s` (shell
`0.271s`).

## Integration request and remaining limits

R4 should register the three new schema files, add their exact SHA256 entries
to the source inventory, and expose `run_saved_proposal` through the normal
CLI only after the supported proposal Run entry has selected real proposal
authority. The current invocation contract still requires a real G45
`ref_...` anchor; native-only proposal execution therefore remains refused by
the existing caller until R4 lands a versioned source-descriptor extension.
No fake legacy ref is created here. Daily scheduling, acquisition crawling,
local HTML/projection, answer editing, Tailor/document generation and
application linkage remain later R2/R3/R4 gates.
