# SCOUT-03 native lane implementation

## Delivered Lane A boundary

`native_records.py` publishes native structured records only through the
settled C1 `run_with_journal_writer` lock and authenticated snapshot.  Each
operation appends the accepted, unchanged `private-record-revision:1` outer
shape, a native `jsl_blob` sidecar at
`records/<record-id>/blobs/<revision-id>.json`, and the strict C1 operation
receipt; it does not create SQLite authority or write journal files directly.
Projection failures are reported after the durable commit as
`projection_pending` with `rebuild_index`, while the receipt itself remains a
strict committed receipt.

The single `native-record-content:1` schema has closed branches for profile
preferences, experience Q&A, imported references, supplied sources, and
selected conversation.  `imported_reference` is retained as the native
compatibility wrapper for an exact, already-journaled G45 reference or
run-input artifact: it adds no content store, does not relabel imported bytes,
and remains distinct from `supplied_source`'s typed source-status record.  It
keeps hard constraints separate from soft
priorities; keeps sponsorship need, employer sponsorship, and eligibility as
separate uncertain facts; requires four-state question semantics; requires
explicit supplied conversation text/summary and opaque declared-session
metadata; and leaves all actual content behind an explicit exact-content read.
Supplied artifact/provenance references are checked against the pinned C1
snapshot's committed bytes, digest, size, and media type before publication.
Known employer sponsorship additionally needs a non-empty exact committed
source/record ref and a posting/employer context; known eligibility needs its
jurisdiction/context but may remain legitimately user-reported without an
external proof.  Conflicting facts require committed conflict evidence.

Saved defaults use the pinned saved-default scope.  Task overrides are new
logical records with an immutable base relationship and opaque local
`task_context_...` label; generated labels are normalized in the operation
payload so retry returns the original allocated label and record rather than
creating another override.  Updates and archive append through parent CAS,
preserve old sidecars, and never rewrite scope or history.  Current archive
revisions deliberately write a new immutable `jsl_blob` copy of the selected
payload so every archived tail keeps the accepted uniform content shape and
exact-content read behavior; old revision bytes remain readable.  This is a
bounded at-rest duplication tradeoff, not a tombstone family or migration;
dedup/tombstone design is deferred.

## CLI and registration seam

`native_records_cli.py` exposes `native_record_group` with create, override,
update, archive, list, read, and context commands.  Coordinator registration
mounts it without replacing the accepted G45 wrapper surface, at
`gigai record native <subcommand>`; shared schema inventory registration is
coordinator-owned.  Both generated and explicit task-context override paths
normalize and compare the exact base before mutation, returning typed errors
for absent, malformed, or inconsistent input.

The following are explicitly not selected in this lane: C3 must define how to
resolve multiple saved defaults of one kind, and its update UX may later derive
the immutable scope from the selected current revision rather than requiring
the content file to carry it.  Neither is silently treated as accepted
selection/default behavior here.

## Focused evidence

`tests/test_scout03_native_records.py` covers generated record and task-context
replay, immutable sidecar exact reads, metadata/context redaction, parent CAS,
historical read, archive visibility, strict uncertain fact handling, committed
supplied-source reference/media authentication, and a fresh-process-style
`CliRunner` story using both the isolated group and mounted `record native`
commands.  The archive test confirms that the older selected revision is still
exactly readable after the archive copy is committed.

Completed locally:

```text
rtk .venv/bin/pytest -q tests/test_scout03_native_records.py -k 'not mounted'  # 8 passed, 1 deselected
rtk .venv/bin/pytest -q tests/test_scout03_native_records.py::test_mounted_native_override_cli_normalizes_generated_and_explicit_scope  # 1 passed
rtk ruff check src/gigai/native_records.py src/gigai/native_records_cli.py tests/test_scout03_native_records.py
rtk .venv/bin/python -m py_compile src/gigai/native_records.py src/gigai/native_records_cli.py
rtk .venv/bin/python -m json.tool src/gigai/schemas/native-record-content.schema.json
rtk git diff --check -- [Lane A files]
```

No full suite was run.  The central schema-inventory/top-level CLI integration
and combined-suite execution remain coordinator work.
