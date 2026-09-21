# SCOUT R4 integration handoff

> **Current status:** this earlier handoff is retained as historical evidence.
> The completed synthetic report/application journey is documented in
> [SCOUT-R4-full-journey.md](SCOUT-R4-full-journey.md); its results supersede
> the open downstream-API statements below.

Date: 2026-09-11 (America/Denver)  
Lane: shared R4 integration / R1 caller completion  
Status: a real proposal-to-local-Tailor journey is executable with synthetic
inputs and an injected local transport; the complete report/application
journey remains intentionally open behind the R2/R3 continuation APIs.

## Delivered shared path

The existing sealed Run entry now has one explicit `TailorRunRequest` path in
`gigai.run.launch_run`. It resolves `tailor-application` from the approved
Graph Set, validates the exact one-Goal local write effect and identified
`ollama_local` target, seals the selector-only request, allocates a genuine
Run, and starts the Goal through the existing journal lifecycle. The Tailor
request contains only the authenticated discovery selector, private source
selectors with host-owned purposes, optional saved proposal identity, output
choices, target identity/bounds, selected Graph digest, and local permission;
posting/private bytes are resolved later from the same Gig's committed
journal. No Tailor is inferred from a proposal or reviewer role, and no
application transition is performed.

After start, the caller hydrates the completed discovery posting and selected
private revisions using the existing pinned resolvers. It converts those exact
bytes into R2's `TailorSelection`, builds the existing Tailor request, and
invokes `run_model_invocation` through the configured local target with
`commit_goal_transition=False`; the host, rather than the model, owns source
identities, target identity, invocation evidence and result lineage. The
model response must be a complete bounded `.075` tailoring bundle. The caller
then records the non-terminal invocation evidence, each immutable R2 document
revision, a private Tailor result artifact, one Goal-completion transition,
and the owning Run terminal transition. Failed/truncated/invalid output is a
failed Run and is not a successful document revision.

The R1 `proposal` command continues to use the supported
`proposal-assessment` Graph and `ProposalRunRequest`; the copied Scout
`gig.py` now offers explicit `proposal`, `tailor`, and `report generate/status`
subcommands, each requiring direct `--confirm`. The installed `gigai` CLI
also registers `proposal`, `tailor`, and the R3 `scout-report` group. These
commands do not auto-apply, submit, or export private artifacts.

## Authority and compatibility

- Selection authority is the committed Graph Set descriptor and exact graph
  digest. A selector alias or graph title does not authorize an operation.
- Run/Goal status and terminal transitions remain journal authority; the
  Tailor branch does not reactivate a completed Run or manufacture a started
  record. Existing deterministic and provider-review branches are unchanged.
- Existing v1 model-invocation records remain readable. Native/discovery
  source handles and their closed host-owned descriptors use the additive v3
  invocation schema; their durable identities are retained in the selected
  source lineage and journal evidence, not fabricated `ref_` records.
- Immutable resolver identities may contain nested mapping proxies. The R4
  caller materializes them to ordinary canonical JSON values before hashing
  descriptors or recording R2 lineage, preserving exact identity without
  weakening validation.
- The local adapter remains the only transport binding for this path. It
  checks local endpoint/model identity before prompt transmission and closes
  its owned HTTP client on success or failure. This is trusted-runtime
  identity and loopback policy, not an operating-system no-egress guarantee.

## Verification

Commands run in the current dirty worktree (synthetic fixtures only):

```text
time .venv/bin/pytest -q tests/test_scout_r4_journey.py
2 passed, 1 skipped in 39.68s

ruff check src/gigai/run.py src/gigai/cli.py \
  src/gigai/data/scout/gig.py tests/test_scout_r4_journey.py
All checks passed

time .venv/bin/python tools/verify_installed_schemas.py
verified 72 installed GigAI schemas (0.03s)

.venv/bin/gigai --help | rg 'proposal|tailor|scout-report'
proposal, tailor, and scout-report are registered

.venv/bin/python src/gigai/data/scout/gig.py --help | rg 'proposal|tailor|report'
proposal, tailor, and report are exposed by the copied wrapper
```

An earlier compatibility check in this lane also ran the supported proposal
entry and one R2 revision test together with the journey (`3 passed, 1
skipped in 35.49s`); the final command above reran the strengthened proposal
then Tailor journey and malformed-output evidence case.

The one skipped test is deliberately named full downstream Tailor/report/
application acceptance: R2 final-selection and R3 report/application APIs
remain peer-owned continuation work. The passing journey is a real Run and
Goal allocation with normal discovery/private fixture transitions, but its
model call uses an injected deterministic transport; it is not live Ollama or
provider evidence.

## Integration checklist

| Lane | Current state | R4 handoff / remaining gate |
| --- | --- | --- |
| R0 | Complete bounded receipt/lineage reader and shared interface sheet | Carry targeted tamper/alias/extra-Goal/journal-conflict regressions into grouped release review. |
| R1 | Proposal Run, immutable proposal revisions, answer/source-purpose validation, reassessment primitives and explicit CLI entry | Daily acquisition progress/deadline, answer UX and broader proposal history remain to be wired. |
| R2 | Tailor selection, local execution helper, exact document revision/final-selection journal services and readers | Connect final selection to user review and ensure the shared dispatcher uses the reviewed R2 APIs. |
| R3 | Journal-derived opportunity/proposal/document/run/evidence readers and local report publisher | Register report/projection/application surfaces in the final shared index/report command journey; no canned rows. |
| R4 | Shared schema registry/runtime invocation v3, proposal/tailor CLI entry, actual proposal-to-Tailor Run path | Complete one supported end-to-end report/application journey after peer APIs land; no API authority is fabricated here. |

## Explicit limits and next work

The current path does not schedule daily acquisition, provide a UI, answer
questions, select final documents, publish a report automatically, or record
an application. The smallest next integration is to consume the R2
authenticated final-selection service and R3 reader/report service in the
same committed snapshot, then add one real grouped journey that records an
explicit user-selected final document and an explicit application event.
Only after that should the copied wrapper expose those review/final/report
actions; this lane deliberately leaves the downstream actions non-automatic.
