# SCOUT R7 core corrections — 2026-09-21

Status: focused source corrections completed; no release, installed-wheel,
provider/model, activation, publication, Docker, loopback, or full-matrix
claim is made. The existing dirty G43/runtime and inventory work was preserved.

## Corrections

- `scout_interview_records.py:461-463` now uses the canonical
  `digest_imported_bytes(operation_key.encode("utf-8"))` owner and removes the
  `sha256:` display prefix, preserving the exact
  `records/operations/interview-<hex>.json` receipt filename format.
- `lifecycle.py:2597-2604` owns the small `allocate_gig_id` boundary;
  `default_init.py:694-697` reuses it inside the existing registry transaction.
  Init does not manufacture approval, active selection, or extra lifecycle
  side effects.
- `occurrence.py:233-243` treats only the exact
  `run_details_reconciliation_required:` Run refusal as transient and leaves
  the occurrence `run_prepared`; unrelated `RunError` values still propagate.
  A focused regression proves a later terminal retry reuses the same Run.
- `scout_materialization.py:178-181` now passes literal `shell=False` to the
  fixed-argv `git -C ... ls-tree` boundary.
- G41/G42 CLI fixtures now provide the required synthetic username while
  preserving package privacy, adoption, and negative assertions.

## Focused evidence

Commands used from the source checkout (all disposable/synthetic):

```text
rtk .venv/bin/python -m py_compile src/gigai/scout_interview_records.py src/gigai/default_init.py src/gigai/lifecycle.py src/gigai/occurrence.py src/gigai/scout_materialization.py tests/test_canonical_ownership.py tests/test_g08_offline_create_lifecycle.py tests/test_g21_occurrence.py tests/test_g41_package_boundary.py tests/test_g42_catalog.py
=> passed

rtk .venv/bin/pytest -q tests/test_canonical_ownership.py tests/test_g08_offline_create_lifecycle.py tests/test_g21_occurrence.py tests/test_g41_package_boundary.py tests/test_g42_catalog.py
=> 45 passed in 47.66s

rtk .venv/bin/pytest -q tests/test_g21_occurrence.py::test_transient_run_details_reconciliation_preserves_prepared_run_until_retry tests/test_g21_occurrence.py::test_prepared_occurrence_reconciles_without_relaunch tests/test_g21_occurrence.py::test_interruption_after_run_preparation_is_terminal_failure
=> 3 passed in 8.23s

rtk .venv/bin/pytest -q tests/test_setup_configuration_diagnostics.py::test_product_subprocesses_are_literal_argv_with_shell_disabled tests/test_scout05_materialization.py tests/test_scout_r5_interview_transfer.py
=> 17 passed, 1 failed in 85.44s; the failure is the pre-existing unowned
   src/gigai/capability_review.py subprocess.run call lacking shell=False.
```

The same combined command verified all six materialization tests and all
eleven R5 interview/transfer tests. The repository-wide AST contract cannot be
green from this lane without editing the coordinator-owned
`capability_review.py`; no ownership boundary was crossed.

Ruff was attempted with `rtk uv run ruff check ...` but the shared uv cache
refused access at `/Users/kar/.cache/uv/sdists-v6/.git` (`Operation not
permitted`); `.venv/bin/ruff` is not installed. Compilation and focused tests
passed.

## Source identity after correction

```text
98ee1d4ee213da0c0c3031bb694d9eb8f0e90cfb4cb48a1202c51337f4526192  src/gigai/scout_interview_records.py
a5afc465e74d36800a3341d3dad2cf80b30f7f20550e16566ebb50e1ea335486  src/gigai/default_init.py
ca4dd38257aeb18496d75c13d7ab86958f263a14b81243c8d340acd3ab675a1f  src/gigai/lifecycle.py
07b6bdd01f6eb0806817b299edc77f723a0e70fd41de72ffd1c2f70432fb4fec  src/gigai/occurrence.py
6cf3800bd3f4f63b0142e40cedb57c70874059280cd769839926b0df908f81fa  src/gigai/scout_materialization.py
```

These are new source identities; prior candidate/wheel evidence must not be
called verified for this source state. No commit was created.
