# SCOUT-R6 correction handoff — 2026-09-20

Status: bounded synthetic correction implementation; not installed/live R7
acceptance, publication approval, or provider/model evidence.

## Implemented in this lane

- Runtime comparison now resolves the approved authority through the existing
  journal/tag readers (`_resolve_authority` and
  `resolve_selected_graph_authority`) before allocating comparison Runs. A
  comparison refuses legacy/global packs and requires a selected Graph Set
  evaluation contract whose journaled `pack_ref` is byte-, size-, owner-,
  graph-, and Goal-bound to the selected Gig.
- Each attempt uses the authenticated Goal Graph and Goal contract bytes, emits
  the standard sealed Run manifest, records configured readiness separately
  from observed invocation identity, and retains per-case journal checkpoints.
  Retryable invocation failures can be retried once with `--retry-failed`; the
  original structured errors remain in the case result.
- Grading now rejects duplicate criteria, empty evidence for supported or
  unsupported claims, wrong criterion evidence, wrong evidence kind, duplicate
  evidence IDs, and unsupported evidence IDs. Criterion evidence IDs and kinds
  are frozen in the pack schema and synthetic pack.
- Comparison reads validate the outer schema and re-authenticate Graph Set,
  Goal Graph, selected graph, pack, Run manifest, and per-case result refs from
  committed journal bytes. `comparison status` exposes authenticated case
  checkpoints without rerunning a provider.
- Main CLI registers `scout-interview` and `scout-transfer` and exposes
  comparison graph selection, explicit retry, and status. The three R5 schema
  resources are registered in validators, `SHA256SUMS`, the installed verifier,
  and schema README.

## Focused evidence

Commands run in the shared dirty worktree:

```text
PYTHONPATH=src .venv/bin/pytest -q tests/test_runtime_comparison.py
6 passed

PYTHONPATH=src .venv/bin/python tools/verify_installed_schemas.py
verified 81 installed GigAI schemas

ruff check src/gigai/runtime_comparison.py src/gigai/cli.py tests/test_runtime_comparison.py tools/verify_installed_schemas.py
All checks passed

git diff --check
passed
```

## Positive disposable authority fixture

`tests/test_runtime_comparison.py::_fixture` now creates and approves a real
v2 two-graph Graph Set through `propose_graph_set_offline` and
`approve_offline`, selects `career` explicitly, and binds a synthetic pack to
the selected graph's actual graph/Goal IDs and the created Gig ID. The
evaluation contract declares the pack reference; the fixture journals those
exact bytes into the pending proposal before approval, so the approved
commit—not a fabricated active pointer or mutable workpad file—is the source
used by `_load_authenticated_pack`. Both attempts therefore run through the
authenticated authority path with injected invocation transports.

The fixture also proves durable retry/status and reader boundaries: a
retryable injected transport preserves its original structured error in the
committed case result while the retry succeeds; `comparison status` reloads
all case checkpoints; and a journaled forged nested result digest is rejected
by `show_comparison`. The legacy v1/global-pack path remains intentionally
rejected, preserving the reviewed `HANDCRAFTED_PACK_GRAPH_ACCEPTED` and
global-pack provenance gates.

No live provider/model, network, private user data, activation, publication,
commit, reset, dependency upgrade, daemon restart, or whole-suite run was used.
Orca IPC was unavailable throughout (`runtime_unavailable`), so no lifecycle
message could be delivered from this worker; the coordinator should reconcile
the working-tree diff and send the required completion notification.
