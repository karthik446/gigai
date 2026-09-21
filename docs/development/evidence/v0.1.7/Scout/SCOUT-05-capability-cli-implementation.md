# SCOUT-05 generic local capability-review CLI adapter

**Status:** bounded implementation evidence, 2026-09-10. This adds a public,
thin `gigai capability review` caller for the settled local review service; it
does not attach a capability to an active version, approve a successor,
execute source, install anything, call a provider, or claim an OS-account
Python sandbox.

## Public command and fixed boundary

`gigai capability review` requires all of `--gig`, `--base-version`,
`--base-proposal-id`, `--manifest-id`, `--capability-id`, `--operation-key`,
and one local `--input` JSON file. The input is a closed object containing
only `reviewer`, `outcome`, `rationale`, `evidence_refs`, and optional
`selected_option_id`; it cannot supply an operator actor, effects,
permissions, source paths, or callbacks.

`--confirm` is separately required before the adapter reads input or resolves
the workpad. The adapter derives the distinct operator record as
`{"kind":"operator","id":"local-user"}` and passes the reviewer record
from JSON unchanged to `review_local_tool`; this is a direct local command
record, not an authentication sandbox or evidence that an agent was an
operator. Current help and the only admitted service boundary state exactly:
`write_workpad`, filesystem `write_isolated`, network `none`, credentials
`none`.

The adapter resolves an already registered, explicit Gig through
`resolve_workpad(..., gig_id=..., allow_semantic_state=True)`. It does not
initialize, migrate, select, activate, or otherwise repair a workpad. Local
input is restricted to a nonempty regular UTF-8 JSON file under 64 KiB, with
the supplied file and all parents rejected when symlinked; invalid input and
service/workpad errors use redacted typed JSON diagnostics.

On success the JSON result contains the exact immutable `decision`,
`decision_ref`, `reviewed_manifest_ref`, `parent_manifest_ref`, and `replayed`
flag. It states that review neither approves, activates, installs, nor executes
the capability: a separately proposed successor and a direct approval remain
required, with no invented executable next command.

## Focused verification

All tests use disposable real registries and materialized bundled Scout
candidates. No user workpad, provider, network, approval beyond the fixture's
Graph Set base, repository commit, or full suite was used.

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_capability_cli.py` | `11 passed in 19.62s` |
| `uv run ruff check src/gigai/capability_cli.py tests/test_scout05_capability_cli.py` | passed |
| `.venv/bin/python -m py_compile src/gigai/capability_cli.py src/gigai/cli.py tests/test_scout05_capability_cli.py` | passed |

The focused cases prove passed/rejected decisions, exact same-key replay,
separate agent reviewer and `local-user` operator records, missing-confirm
refusal before input/workpad access, closed hostile JSON rejection, stale or
foreign base refusal, symlink-input refusal, changed source refusal without
executing its bytes, help/import authority-free behavior, and unchanged active
selection. The changed-tool diagnostic is deliberately service-owned
`capability_review_tool_inventory_changed`.

## Remaining gate

The adapter deliberately stops at immutable review/consent publication. The
existing pending-manifest attachment path still needs its separately tracked
successor proposal and approval guard: a reviewed manifest must be carried by
an authenticated successor and directly approved before copied Scout CRUD can
become eligible for supported execution.

## 2026-09-10 final input-boundary corrections

The CLI now parses reviewer bytes through `canonical.parse_json_bytes`, rather
than `json.loads`. Duplicate member names at either the outer decision or the
nested reviewer actor level therefore refuse before workpad resolution; parser
details are intentionally mapped to the same redacted
`capability_review_input_invalid` diagnostic. A bounded but excessively nested
JSON value also maps to that diagnostic if Python's parser or canonical walker
raises `RecursionError`; it cannot escape as a CLI traceback or publish a
decision.

The service actor check now uses required-subset membership (`{"kind", "id"}`
is contained in actor keys) before reading either key. A malformed reviewer
such as `{"kind":"agent","model_target":null}` returns the typed
`capability_review_actor_invalid` refusal rather than raising `KeyError`. The
direct `--confirm` operator path, operation-key replay, active-selection
boundary, and review-only semantics are unchanged.

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout05_capability_cli.py` | `15 passed in 26.98s` |
| `.venv/bin/pytest -q tests/test_scout05_capability_review.py -k actor_refuses_missing_required_members` | `3 passed, 24 deselected in 0.17s` |
| `uv run ruff check` over capability CLI/review modules and their focused tests | passed |
| `.venv/bin/python -m py_compile` over the same four files | passed |

The 15 CLI cases include duplicate `outcome`, duplicate nested reviewer `id`,
and a 2,048-level bounded JSON value, each asserting typed refusal and no
capability-review publication. A broad `cli.py` lint is deliberately not
claimed: root observed nine unrelated legacy private-record warnings there,
and this correction changed only the separately owned adapter/service/test
surfaces.
