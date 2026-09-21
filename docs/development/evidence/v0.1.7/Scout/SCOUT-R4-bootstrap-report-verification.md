# SCOUT R4 bootstrap and report verification

Date: 2026-09-11 (America/Denver)

This is a bounded evidence handoff for the remaining acceptance-review
bootstrap/replacement and exact-report-row checks. It does not reopen the
accepted R0-R4 source/lifecycle controls, claim acquisition-progress
persistence, or claim release/UAT/provider acceptance.

## Bootstrap and replacement authority

`tests/test_scout_manifest_publication.py` uses the existing Scout candidate
materializer and capability-review service against isolated, journal-backed
workpads. The initial prepared capability manifest is read through
`read_committed_artifact(..., allow_replaced_manifests=True)` and the test
records its exact committed bytes, path, publisher commit, one-publisher count,
and `scout_source_materialized` transition. A normal `review_local_tool` call
then creates a new reviewed manifest identity; its exact bytes and one publisher
are authenticated through the `capability_review_decided` transition.

The storage predicate is also exercised with the normal `JournalWriter` against
synthetic replacement bytes: a same-path valid replacement with
`capability_review_decided` is accepted as the explicit mutable capability
transition, while the same replacement with `scout_source_materialized` is
refused. An arbitrary nested software inventory replacement is refused despite
the mutable-manifest option because its path is outside the enumerated
capability-manifest family. Separate one-publisher path fixtures are refused for
manifest-ID/path mismatch, wrong Gig owner, and invalid schema. The direct
replacement fixtures prove the narrow journal predicate; they are not a claim
that a forged transition is a valid capability-review service result.

## Exact report-row assertions

The existing full synthetic journey now asserts the production reader/projection
rows, not only HTML headings:

- final selection rows equal the actual returned document kind, record ID,
  revision ID, and content digest; its opportunity/snapshot pair is exact;
- the opportunity reader has one matching opportunity/snapshot row;
- both immutable proposal revisions (initial and reassessed) appear with their
  actual record/revision IDs and the exact opportunity/snapshot;
- proposal and Tailor local Run rows contain each returned Run ID, succeeded
  status, exact `runs/<run_id>/run-details.json` path, and committed result path;
- every proposal/Tailor result path has exactly one reported evidence row with
  the same Run ID and path;
- the report is rebuilt from a fresh resolved workpad and the current report is
  non-stale. HTML checks remain supplemental readability checks.

The journey retains explicit application saved/applied/rejected correction and
replay behavior with the selected Scout document references; no Tailor or
application identity is fabricated by the assertions.

## Hash-algorithm correction

The acceptance review's 40-hex “SHA-256” table values were checked against the
current dirty-worktree bytes. They are Git blob object IDs produced by
`git hash-object` (40 hexadecimal SHA-1 values), not SHA-256 digests. Independent
SHA-256 values from `shasum -a 256` are recorded below for clarity; the
independent acceptance verdict was not edited.

```text
for p in src/gigai/journal.py src/gigai/run.py src/gigai/model_execution.py; do
  printf '%s ' "$p"; git hash-object -- "$p"
done
src/gigai/journal.py 2d8142dbefc9bc30688212e4c3744fca6f1fb440
src/gigai/run.py 65a0717ac551703f658031ed5ccc9c924ad99d40
src/gigai/model_execution.py 773b20835d561614f05fa654a585bb95710e6c01

for p in src/gigai/journal.py src/gigai/run.py src/gigai/model_execution.py; do
  printf '%s ' "$p"; shasum -a 256 "$p" | awk '{print $1}'
done
src/gigai/journal.py 4b2ed2d14ef68be66bda36bf0132ab5ec240660048e00b027e65fe613521d653
src/gigai/run.py 8a7f953a47d3d53275ead073916a66404904dfa9ac17ad5dc4a6e631b8fefde2
src/gigai/model_execution.py a209f9b040ff9c36f175a38f45adbca82b14c2e0af39f5819c7144d6880c0abc
```

## Verification

Commands used this wave; all fixtures were synthetic and offline.

```text
rtk .venv/bin/pytest -q tests/test_scout_manifest_publication.py --durations=0
-> 8 passed in 13.52s

rtk .venv/bin/pytest -q tests/test_scout_r4_journey.py::test_r4_full_tailor_report_application_journey --durations=0
-> 1 passed in 157.02s (0:02:37), including exact result-evidence digest assertions

rtk ruff check tests/test_scout_manifest_publication.py tests/test_scout_r4_journey.py
-> passed

git diff --check -- tests/test_scout_manifest_publication.py tests/test_scout_r4_journey.py
  docs/development/evidence/v0.1.7/Scout/SCOUT-R4-bootstrap-report-verification.md
-> passed
```

## Historical and current status

The earlier user-actions bootstrap failure occurred during concurrent journal
narrowing and was followed by the correction that explicitly permits one
publisher for initial materialization. It is preserved as historical evidence,
not treated as a current reproduction: the isolated bootstrap test now passes,
and the fresh full journey passes after exact-row assertions. The acceptance
review's separate P1 durable acquisition-progress gap remains outside this
owned test/evidence task; no production change was made to conceal or defer it.

## IPC note

The required Orca heartbeat was attempted after the focused checks but could not
connect because Orca was not running (`Could not connect to the running Orca app.
Restart Orca and try again. Orca is not running.`). The final worker completion
attempt will preserve this exact environment result without a retry loop.
