# SCOUT-06 research reuse renderer implementation

Date: 2026-09-10

## Scope and version strategy

This bounded implementation adds a literal v3 research domain resource while
leaving the historical v2 resource and bridge unchanged. The external
recording protocol remains v2 in both cases.

| domain | packaged resource | fixed bridge | candidate/compiler |
|---|---|---|---|
| `research-packet:2` / `scout-role-research:2` | physical `.071` research.py/schema, retained historical `.073` identity | `gigai.scout_research:validate_research_domain` | existing candidates remain valid |
| `research-packet:3` / `scout-role-research:3` | literal `.076` research.py/schema | `gigai.scout_research_v3:validate_research_domain` | candidate `1.3`, compiler `scout-candidate-compiler:4` |

The v3 renderer accepts the exact normalized `scout_research` envelope,
preserves all historical run/receipt/checkpoint/output references, and rejects
unknown or mismatched schema/validator pairs. The broker authenticates the
committed schema/source bytes and hydrates the selected historical markdown,
domain sidecar, and supporting bytes from the same writer snapshot; the v3
bridge revalidates those bytes with a bounded one-hop rule and rejects nested
research ancestry. No latest lookup, user-path import, provider call, or
protocol/schema redesign was added.

## Verification

The real disposable public-path integration now covers v3 second-run render,
checkpoint/output and check, submit, exact submit replay, preserved selected
historical refs, unchanged rendered bytes, and no replacement research
identity. Missing receipt, cancelled source, changed committed bytes, and
foreign historical identity refuse before publication with the existing typed
guards and unchanged HEAD assertions. The focused unknown-validator test also
passes with explicit unsupported refusal.

Commands and results:

* `.venv/bin/pytest -q tests/test_scout06_research_input_integration.py tests/test_scout06_research_bridge.py tests/test_scout06_source_contract.py`: **29 passed**, 77.29s.
* `.venv/bin/pytest -q tests/test_scout06_research_inputs.py -k unknown_historical_validator`: **1 passed, 12 deselected**, 6.99s.
* `ruff check` on changed SCOUT-06 source, resource, and test files: passed.
* `python -m json.tool` on the packaged `.076` schema: passed.
* `tools/verify_installed_schemas.py`: previously verified **64** installed schemas; packaged research resources are additionally checked by fixed source/schema byte authentication.

## Boundary and deferred evidence

The focused public-path completion proof currently creates a v3 first Run and
reuses it in a v3 second Run. The v3 bridge and closed dispatch table contain
the v2 historical validator/resource path, and the legacy bridge/source
contract lane remains green, but this bounded run did **not** create and
complete a true v2 Run before reusing it in v3. Therefore legacy completed-v2
reuse is an explicit remaining acceptance gap, not claimed as proven here; it
requires a fixture that publishes the old v2 candidate/resource identity
without regenerating it as v3. Existing v2 replay compatibility is covered by
the focused bridge/source-contract tests, not by the new end-to-end v2-to-v3
completion path. Earlier hydration and broad suites were not rerun; provider,
network, private-user, activation, migration, commit, and whole-Scout
acceptance remain outside this implementation.
