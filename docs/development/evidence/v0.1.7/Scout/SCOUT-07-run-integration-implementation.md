# SCOUT-07 real discovery Run integration

## Delivered bounded slice

The candidate Scout source is now definition `1.2` and compiler
`scout-candidate-compiler:3`. Its historical `.073` research source remains
inventoried and unchanged; `.074` discovery source and schema are added as
separate source members. `find-jobs` now has one composite `discovery` output
and retains the `find-jobs-completion` check, while the other graphs retain
their previous output contracts.

The graph output-contract validator and materializer admit only the two fixed
domain bindings (research and discovery), with exact schema/source suffixes,
media types, and packaged bytes. `external_recording` dispatches discovery
through the literal `gigai.scout_discovery` bridge; profile preference bytes
are recovered from the authenticated sealed native input and passed to the
validator. Domain binding is authenticated against committed schema/source
references before the fixed validator runs; no user Gig import, callback,
network lookup, provider, or arbitrary source execution was introduced.

The discovery flow test creates a disposable Git target, initializes and
offline-approves the shipped candidate, creates a real native
`profile_preferences` record, then performs Plan v2, start, composite
no-match discovery output plus completion check, submit, and exact submit
replay. A foreign native revision is rejected before publication. Supporting
capture/review bytes and the selected profile blob are authenticated by the
fixed `.074` renderer, so employer sponsorship remains unknown rather than
being inferred.

## Focused verification

Final owned integration command:

```text
.venv/bin/pytest -q tests/test_scout06_source_contract.py tests/test_scout07_discovery_bridge.py tests/test_scout07_discovery_packet.py tests/test_scout07_discovery_run_flow.py
57 passed in 18.49s
```

The existing v2 research regression was also run after the generic dispatch
change: `.venv/bin/pytest -q tests/test_scout06_research_run_flow.py` -> `5
passed in 15.33s`. Scoped lint passed:

```text
ruff check src/gigai/external_recording.py src/gigai/graph_set.py src/gigai/scout_template.py src/gigai/scout_materialization.py tests/test_scout06_source_contract.py tests/test_scout07_discovery_run_flow.py
All checks passed!
```

Python bytecode compilation passed for the four changed Python modules and the
new flow test. No full suite, wheel rebuild, provider, network, private user
state, activation, commit, or release operation was performed.

## Documentation correction and remaining gates

The earlier discovery packet note used the malformed resource path
`cap_00000000-0000-8000-000000000074`; both command examples now explicitly
use the actual UUID path
`cap_00000000-0000-4000-8000-000000000074`.

This is an offline journal/packaged-validator candidate proof, not live web
discovery and not release acceptance. Public caller/CLI wiring, source
inventory registration in any external central registry, approval/activation,
and additional match/partial fixture coverage remain separate gates. The
test intentionally uses a no-match packet with unknown sponsorship; no claim
is made about provider operation, market completeness, or eligibility beyond
the supplied synthetic native facts.
