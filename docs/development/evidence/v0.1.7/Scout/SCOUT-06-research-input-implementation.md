# SCOUT-06 exact completed-research reuse foundation

Date: 2026-09-10. This is the Terra-owned read-only resolver foundation. It
does not wire a new caller or input union, and it does not claim consumer
availability or end-to-end reuse.

## Delivered

`src/gigai/scout_research_inputs.py` provides:

- `resolve_research_input(resolved, snapshot, raw)` for the closed selector
  `{family: "scout_research", run_id, receipt_id, output_kind: "research"}`;
- `revalidate_research_input(resolved, snapshot, sealed)` for exact replay of a
  previously normalized selection; and
- `ResearchInputError.code` diagnostics for invalid, missing, foreign,
  incomplete, forged, unsupported, and changed historical evidence.

The resolver consumes only the caller's already writer-locked
`JournalSnapshot`. It authenticates v2 Plan, Run, checkpoint, and succeeded
terminal receipt records; requires one unique terminal receipt; binds the exact
receipt output tuple to exactly one committed checkpoint; checks every
Markdown, generic sidecar, domain sidecar, supporting artifact, Plan, Run,
checkpoint, graph, and contract reference; and invokes only the fixed packaged
`scout-role-research:2` validator. It never scans for latest, follows the
active Gig pointer, imports Gig code, reads mutable working bytes, creates a
native record, or re-imports G45 content.

The normalized envelope is deliberately explicit and closed:

```text
{
  family, run_id, receipt_id, output_kind,
  project_id, gig_id, gig_version, run_plan_id,
  run_plan_ref, run_ref, receipt_ref,
  checkpoint_id, checkpoint_ref,
  selected_graph: {
    selected_graph_id, goal_graph_id, graph_version,
    graph_selector, graph_ref
  },
  selected_inputs,
  output: {
    kind, markdown, sidecar, domain_sidecar,
    supporting_artifacts: [{artifact_id, ref}, ...]
  },
  domain_binding: {
    schema_id, schema_ref, validator_id,
    validator_source_ref, contract_ref
  }
}
```

All `*_ref` values preserve the complete path, digest, media type, and byte
size. Historical domain identity is checked against a closed packaged mapping;
an unknown schema/validator identity returns
`research_input_validator_unsupported`, while known identity with altered bytes
is treated as refused evidence rather than silently accepted as a new version.

## Focused verification

| Command | Result |
| --- | --- |
| `rtk proxy .venv/bin/pytest -q tests/test_scout06_research_inputs.py` | 8 passed in 60.68s |
| `rtk ruff check src/gigai/scout_research_inputs.py tests/test_scout06_research_inputs.py` | passed; no diagnostics |
| `rtk python -m py_compile src/gigai/scout_research_inputs.py tests/test_scout06_research_inputs.py` | passed |

The synthetic disposable journal tests cover successful resolution and exact
revalidation, retention of an older completed Run when a newer Run exists,
missing/foreign/wrong selectors, forged committed receipt bytes, cancelled
Runs, changed sealed inputs, and unknown historical validators. Fixtures use
the real candidate initializer, offline approval, journal writer, v2 Plan/Run/
checkpoint/submit services, and fixed packaged bridge with synthetic evidence.

No Luna test helper was imported. No providers, network, private user roots,
full suite, wheel, activation, default promotion, or repository commit were
used.

## Integration boundary

The caller must provide a committed snapshot containing the Run families plus
the exact selected graph, output contract, domain schema, and validator source
artifacts referenced by the sealed Plan. This module intentionally does not
expand or obtain that snapshot and does not alter `external_recording.py`,
`scout_inputs.py`, schemas, packet code, discovery code, or source inventory;
root owns the later input-union wiring.

## Correction — Luna ownership and bounded history hardening

Date: 2026-09-10. The earlier report incorrectly attributed this resolver lane
to Terra; ownership is Luna. Before caller wiring, the resolver was corrected
to authenticate the complete immutable history: Plan/Run/checkpoint/receipt
project, Gig, operation, and nested Run/Plan identities; matching Run and Plan
versions; `research-role` graph selector/selected-graph identity; and the exact
closed v2 output contract (`schema_version`, `kind`, `gig_id`, `fields`, and
`domains`). Schema-valid records with mismatched nested IDs are refused.

Receipt checks are now resolved across every authenticated checkpoint in the
Run, so an output in checkpoint A and its completion check in checkpoint B are
accepted while preserving checkpoint A as the normalized output identity. Each
check must use the exact four-field sidecar shape
`{evidence_kind, run_id, output_sha256, result}`, bind the research Markdown
digest and check kind, pass, and exactly cover the committed completion
check-contract fields. Partial, cancelled, malformed, foreign, and forged
records remain refused without selecting latest or consulting an active pointer.

The normalized `scout_research` envelope no longer embeds `selected_inputs`.
The original inputs remain fully preserved by the exact `run_plan_ref` and
domain packet reference, avoiding recursive ancestry duplication when reuse is
later wired. Public wrappers translate unexpected canonicalization/structure
exceptions to a redacted `ResearchInputError` without echoing payload data.

Final focused correction run:

| Command | Result |
| --- | --- |
| `rtk ruff check src/gigai/scout_research_inputs.py tests/test_scout06_research_inputs.py` | passed; no diagnostics |
| `rtk proxy .venv/bin/pytest -q tests/test_scout06_research_inputs.py` | 9 passed in 64.67s |

The added positive fixture records output and completion check in distinct
checkpoints and submits the exact cross-checkpoint tuple. No schemas, CLI,
`external_recording.py`, `scout_inputs.py`, packet/source/discovery inventory,
provider, private data, full suite, wheel, or repository commit was changed or
used; snapshot hydration and new input-union registration remain root-owned.
