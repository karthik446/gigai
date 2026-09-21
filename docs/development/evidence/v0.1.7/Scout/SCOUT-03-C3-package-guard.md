# SCOUT-03 C3 package/export privacy guard

## Delivered boundary

`package.inspect_package` now refuses an otherwise complete, hash-correct
portable manifest when an inventoried member has recognizable Scout private
provenance.  The guard uses the accepted workpad map's private roots
(`references/`, `run-inputs/`, `records/`, `runs/`, `run-plans/`,
`review-inputs/`, `handoffs/`, `scratch/`, and `reports/scout/`), exact
private projections (`indexes/context.json`, `state.sqlite`), and canonical
native-document paths under `docs/<record-id>/<revision-id>/`.  It deliberately
does not reject arbitrary `docs/`, arbitrary `references` text elsewhere, or
Python source.

The guard also reads JSON only to test strict existing persisted identities:
G45 reference/run-input records, Scout private revisions and native content,
Scout operation receipts, all external-recording envelope families, and typed
generated reports.  Thus a valid private record or receipt renamed into a
legitimate source directory still refuses without relying on filename wording
or a field named `text`.  Error code `private_provenance_refused` contains no
source bytes or payload excerpt.

`export_package` and `install_package` both obtain the guarded inspection
before any destination publication.  Their shared copier now stages in a
sibling temporary directory, re-inspects the staged bytes, verifies the source
digest did not change, and atomically publishes only a passing package; a
failed copy removes the stage.  Existing init/adoption validation continues to
call `inspect_package`; catalog materialization was read as a trusted built-in
source producer and was not changed in this package-only slice.

## Focused evidence

- `uv run pytest -q tests/test_scout03_package_privacy.py tests/test_g41_package_boundary.py::test_package_export_is_validated_idempotent_and_authority_free tests/test_g41_package_boundary.py::test_package_export_refuses_symlinked_parent` — 15 passed in 1.88s.
- `uv run ruff check src/gigai/package.py src/gigai/package_privacy.py tests/test_scout03_package_privacy.py` — passed.
- `uv run python -m compileall -q src/gigai/package.py src/gigai/package_privacy.py` and `git diff --check` — passed.

The new disposable tests prove an actual `scout_source_files()` catalog package
inspect/export round trip; inert template documentation and non-executable
supporting Python remain admissible.  They reject valid-manifest G45 records,
Scout revisions, native content, native and external receipts renamed beneath
ordinary source trees; reject canonical imported-source, context, report and
native-document paths; preserve a nonexistent export/install destination on
refusal; and rerun the existing normal export and symlink-parent cases.

## Detection limit and remaining gates

This is a provenance boundary, not a universal private-text classifier.
Unrecognized transformed or renamed private prose/HTML without a canonical
path or valid persisted contract remains outside this automatic detection
claim, so it must not be represented as clean-template safety.  Deferred work
includes explicit private-transfer/backup format, C3 tool authorization and
initialization, central package/schema verification, and combined/full-suite
acceptance; no provider execution, source execution, public export, or real
private Gig state was used.
