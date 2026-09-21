# SCOUT-03 C3 native input integration

## Delivered boundary

This slice lets the external recording lane seal direct G45 references and
run-inputs, legacy unscoped Scout revisions which wrap those G45 families, and
two native `jsl_blob` record kinds: `profile_preferences` and
`experience_qa`.  A native selection is always explicit: the caller supplies a
canonical record and revision plus either `{mode: saved_default,
task_context_id: null}` or `{mode: run_override, task_context_id:
task_context_<UUIDv4>}`.  The resolver reads only the writer's committed journal
snapshot, authenticates the exact native revision, blob, and (for overrides)
the pinned base revision, then seals the full committed scope including `base`.

The scope refinement was approved during this delivery because B1 required
native task scope but the prior external envelope did not carry it.  The raw
selection is deliberately narrow while the sealed external Plan is closed and
contains the native kind, exact blob reference and digest, plus the complete
committed scope; the unscoped legacy Scout-over-G45 shape remains compatible.
Missing, malformed, foreign, redirected, uncommitted, or scope-mismatched
native references refuse before Plan publication.  A Plan may combine saved
defaults with overrides from one task context only; multiple override contexts
refuse.

Start, checkpoint, and submit re-resolve every sealed input from the locked
snapshot.  There is no latest/default lookup, no copied blob, no implicit
provider disclosure, and no acceptance of native `jsl_blob` kinds outside the
two admitted records.  A newly recorded answer or archive does not mutate an
old sealed Plan or waiting Run: a caller must create an explicit successor Plan
with a pinned predecessor and its own selected revisions.

Managed readers now recognize an external Plan or Run sibling and fail with an
explicit family refusal before managed allocation or provider execution.
External execution remains `unobserved` and actor evidence remains `declared`;
this integration neither authorizes C3 tools nor claims a provider executed.

## Focused evidence

The owned focused tests cover the following cases:

- `test_external_plan_pins_native_revisions_and_keeps_old_history`: G45 posting
  plus native profile/experience selection; a newer user-reported revision is
  recorded before old-Plan start and cannot replace its sealed identity;
  explicit successor and archive-safe historical checkpoint continuation.
- `test_native_scope_is_closed_and_override_contexts_cannot_mix`: required
  supplied native scope, mismatch refusal, one-context rule, and strict sealed
  schema rejection for omitted or unknown native scope members.
- `test_native_resolver_refuses_forged_foreign_tampered_and_uncommitted_refs`:
  forged scope, foreign ID, uncommitted redirected path, and corrupted locked
  blob rejection.
- `test_managed_readers_and_provider_route_refuse_external_native_inputs`:
  managed Plan/Run family refusal and native provider ingress refusal.
- `test_fresh_cli_question_answer_and_successor_external_plan`: a fresh CLI
  process reads question metadata, a second fresh native CLI process records a
  user-reported answer, and fresh external CLI Plan/Start creates a successor
  pinned to the old waiting checkpoint while preserving its history.

The pre-existing external-recording focused regression remains the authority
for R1 exact output tuples and R2 declared check `pass`/`fail`; this slice does
not alter those receipt gates.

Final focused commands (no combined or full suite was run):

- `uv run pytest -q tests/test_scout04_input_integration.py::test_native_scope_is_closed_and_override_contexts_cannot_mix tests/test_scout04_input_integration.py::test_native_resolver_refuses_forged_foreign_tampered_and_uncommitted_refs` — 2 passed in 15.75s.
- `uv run pytest -q tests/test_scout04_input_integration.py::test_external_plan_pins_native_revisions_and_keeps_old_history` — 1 passed in 23.38s after the final changed-source-at-start assertion for both preference and answer revisions; the preceding isolated managed-reader pair had already passed.
- `uv run pytest -q tests/test_scout03_c3_inputs.py` — 1 passed in 16.14s.
- `uv run pytest -q tests/test_scout04_external_recording.py::test_completion_requires_contract_bound_output_and_check tests/test_scout04_external_recording.py::test_all_admitted_g45_input_families_revalidate_at_start` — 2 passed in 29.89s.
- `uv run pytest -q tests/test_scout04_external_recording.py::test_waiting_input_can_continue_unchanged_or_require_a_pinned_successor tests/test_scout04_external_recording.py::test_replay_conflict_checkpoint_cas_terminal_and_agent_origin` — 2 passed in 17.15s.
- `uv run ruff format --check ...`, `uv run ruff check ...`, and `uv run python -m compileall -q ...` across the owned modules/tests and the two narrow reader guards — all passed; `jq empty` and `git diff --check` also passed.

## Schemas and remaining gates

Changed schema digests, after the final verification pass:

- `external-recording-invocation.schema.json`: `87074566f311913b4aea92a3700dd3833d83220c25c5f2650571f85074d27a8a`.
- `external-recording-plan.schema.json`: `2961b6eddfee7bf966bf49d9fbdd2fc144d5a76275cebb32ad49541b3396c2bd`.

Still outside this slice: C3 tool authorization, bundled/package initialization,
native `jsl_blob` source families beyond profile/preferences and experience Q&A,
central schema inventory/hash registration, and combined/full-suite acceptance.
Native persistence, the journal core, C1 storage, and provider routes were not
rewritten.
