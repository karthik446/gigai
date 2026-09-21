# SCOUT-06 public research Run implementation

**Date:** 2026-09-10  
**Scope:** bounded generic external Plan/Run v2 dispatch and public command
forwarding for the role-only research request. This is an offline integration
lane; it does not approve, activate, select, or execute a provider.

## Delivered

`external_recording.py` now exposes `plan_v2`, `start_v2`, and `cancel_v2`,
with version-aware readers and matching protocol checks for v2 checkpoint and
submit. A v2 Plan admits exactly the explicitly validated
`{family: role_request, role_title, role_context}` input through
`scout_inputs` and seals it unchanged; v1 callers continue to reject this
family. A strict v2 Run envelope was added because a v2 Run must contain a v2
invocation and cannot validate against the unchanged v1 Run schema:

* `src/gigai/schemas/external-recording-run-v2.schema.json`

The public `gigai external plan|start|checkpoint|submit|cancel` commands now
accept `--protocol-version 1|2` (default `1`) and select only the matching
service entry point. No automatic downgrade is possible. Plan/Run/inspect and
replay readers dispatch from the committed `schema_version`; a v1 operation
against a v2 Run (or vice versa) gives a typed protocol mismatch before
publication. Existing v1 contracts and four-field sidecars remain unchanged.

## Verification

The command help was checked with:

```text
.venv/bin/python -c 'from gigai.cli import cli; from click.testing import CliRunner; print(CliRunner().invoke(cli,["external","plan","--help"]).output)'
Usage: cli external plan [OPTIONS]
  --protocol-version [1|2]  [default: 1]

python -m py_compile src/gigai/external_recording.py src/gigai/external_cli.py
passed (no output)

ruff check src/gigai/external_recording.py src/gigai/external_cli.py tests/test_scout06_research_run_flow.py
All checks passed!

.venv/bin/pytest -q tests/test_scout06_external_domain_channel.py
9 passed in 0.10s

.venv/bin/pytest -q tests/test_scout06_research_run_flow.py
1 passed, 4 failed in 3.39s; all four integration failures stop in the
candidate lifecycle before entering external recording (the same
`graph_contract_unsupported` diagnostic below).

.venv/bin/pytest -q tests/test_scout04_external_recording.py
15 passed in 66.08s
```

The new disposable role-only integration fixture is intentionally wired to
the actual candidate initializer, lifecycle approval, journal writer, and
packaged Terra research bridge. It currently reaches a pre-service lifecycle
gate in this checkout: `DefaultInitError: graph-set proposal is invalid:
graphs/0/output_contract:graph_contract_unsupported`. The candidate's strict
v2 composite research contract therefore cannot yet be approved by the
existing graph-set validator, so no positive Plan/Run/checkpoint/submit claim
is made here; the fixture should run once the owning lifecycle integration
accepts the already registered Plan2 contract.

## Registration and remaining gates

The new run-v2 schema requires central registration in `validators.py`, the
schema SHA inventory, installed-resource goldens, and any registry resource
count. Existing invocation/checkpoint/receipt-v2 and Plan-v2 resources must
remain registered. I attempted the required orchestration registration ASK
with the concrete filename and `$id`, but the worker's Orca endpoint reported
`Orca is not running`; no local monkeypatch or registry edit was used.

The remaining bounded gate is one real disposable role-only path:

1. approve the candidate's v2 composite research Graph Set through the normal
   lifecycle owner;
2. run public v2 Plan and Start, then checkpoint a Terra-rendered mixed
   research output and completion check;
3. submit and replay the exact receipt, asserting no duplicate publication,
   v1 downgrade refusal, changed-input/CAS refusal, and preservation of prior
   committed bytes.

This lane does not claim real online discovery, provider execution, later
research-revision input reuse, full SCOUT-06 acceptance, or release readiness.
