# SCOUT-06 completed-research input integration

Date: 2026-09-10. This implementation adds the v2 caller integration that
selects a completed research Run through the closed `scout_research` selector.
It does not add a CLI, provider, source asset, materialization, private-user,
or whole research-iteration authority.

## Implementation

`scout_inputs.resolve_external_input` admits `family: scout_research` only when
the caller explicitly enables the v2 family and supplies the existing
writer-held lock. It delegates to
`resolve_research_input_from_journal(..., writer=writer)`, preserving the
normalized exact Run/Plan/receipt/checkpoint/output/domain-binding provenance;
the normalized input carries no recursive `selected_inputs` ancestry.
`revalidate_external_input` reconstructs only the four-field closed selector
from a sealed normalized value and resolves it again through that same writer,
then requires exact normalized equality.

Plan v2 resolves and seals the research input inside its existing publication
lock. Start, checkpoint, and submit v2 revalidate every sealed input through
the same lock before reading or publishing semantic records. v1 remains
unaware and rejects the additive family through its strict invocation schema;
no latest Run is searched or selected. Existing role/G45/native input families
retain their behavior.

`external-recording-plan-v2.schema.json` now has closed strict definitions for
the four-field `scout_research_selector` and the normalized
`scout_research_input`, including exact refs, selected graph, research output,
supporting refs, and domain binding. The v2 plan schema hash was updated in
`src/gigai/schemas/SHA256SUMS` and `tools/verify_installed_schemas.py`.

## Verification

Real disposable integration fixture `_complete` performs role-research Plan,
Run, checkpoint, research output/check, and submit journal publication. The
new lane then creates a v2 Plan containing the completed Run selector,
replays the same operation key byte-for-byte, and starts a v2 Run bound to the
sealed selection. It also proves v1 refusal and missing historical receipt
refusal without a new publication.

```text
rtk .venv/bin/pytest -q tests/test_scout06_research_input_integration.py --disable-warnings --maxfail=1
2 passed in 33.78s

rtk .venv/bin/pytest -q tests/test_scout06_protocol_replay.py tests/test_scout06_role_request_input.py --disable-warnings --maxfail=1
26 passed in 21.30s

rtk ruff check src/gigai/scout_inputs.py src/gigai/external_recording.py tests/test_scout06_research_input_integration.py
All checks passed

.venv/bin/python tools/verify_installed_schemas.py
verified 64 installed GigAI schemas
```

No full suite, wheel/package, network, provider, activation, commit, or
private-user data work was performed. A second research checkpoint/submit
continues to use the existing v2 Run path and no source asset changes were
needed; broader domain semantics and caller product behavior remain outside
this bounded integration.
