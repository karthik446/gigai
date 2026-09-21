# SCOUT R5 final corrections handoff

Date: 2026-09-20 (America/Denver)  
Lane: Luna implementation, interview records and private transfer only  
Status: bounded synthetic correction evidence; not release, installed-wheel,
live-provider, publication, or R7 acceptance.

## Scope and boundary

This handoff addresses review findings 4 and 5 from
`SCOUT-R5-R6-corrections-review-20260920.md`. Inputs were disposable
synthetic journals/directories and saved synthetic posting/research/feedback
records. No user `.gigai`, private data, network, provider/model, activation,
publication, commit, reset, cleanup, or broad suite was used.

The reviewed baseline was not repeated: the prior acceptance report records
19 focused R5/R6 tests passed and 81 installed schemas verified. R6-owned
files and the four R6 P1 findings remain outside this lane.

## Finding 4 — exact feedback versus preparation distinction

Disposition: corrected in `src/gigai/scout_interview_records.py`.

The installed interview schema is additive but closed (`additionalProperties:
false`), so this lane did not invent a new revision subtype field. The
`_is_actual_feedback_revision` predicate admits a selected `feedback` source
only when the committed revision has a parent revision for the same record,
has a nonempty feedback list, and strictly extends the parent list while
preserving its prefix. Therefore an initial preparation with `feedback: []`
is refused, and an ordinary later preparation edit that merely copies
cumulative feedback is refused. The authenticated source still requires the
exact committed artifact path, digest, size, project, and Gig identity.

The new positive/negative test covers one real `save_interview_feedback`
revision selected as prior feedback, initial preparation relabeling refusal,
and ordinary post-feedback preparation-copy refusal. The same owned interview
fixture now also accepts actual saved research and an actual saved discovery
posting snapshot through their journal readers; discovery selector identity is
authenticated with `opportunity_id` and `snapshot_id`, while the persisted
closed source-ref drops the non-schema selector field after validation.

## Finding 5 — anchored transfer publication and symlink swap

Disposition: corrected in `src/gigai/private_transfer.py`.

`_extract_new` now refuses platforms without `O_DIRECTORY`, `O_NOFOLLOW`, and
the required directory-FD operations. It opens the destination parent chain
from `/` with directory FDs and no-follow flags, reserves the destination
with `mkdirat`-equivalent `os.mkdir(..., dir_fd=...)`, opens every nested
directory through its already-open parent FD, and creates each member with
`O_CREAT|O_EXCL|O_NOFOLLOW` through the final parent FD. Checked parents are
not reopened by pathname. Existing destination/member refusal and bounded
archive reads remain intact.

Failure cleanup is FD-anchored and inode-checked. It removes only files and
directories created by this invocation when their device/inode still matches;
if an adversary swaps a path, cleanup skips that entry rather than deleting a
replacement or unrelated path. The new deterministic probe swaps a nested
`tools` directory for a symlink between reservation and open, verifies a
typed refusal, verifies no payload appears in the outside directory, and
verifies the outside sentinel and swapped original directory remain.

## Focused verification

Commands and results:

```text
rtk proxy .venv/bin/pytest -q \
  tests/test_scout_r5_interview_transfer.py \
  tests/test_scout_r5_transfer_corrections.py
17 passed, 1 warning in 68.83s (0:01:08)
The warning is ZipFile's expected duplicate-name warning from the existing
duplicate-member negative fixture.

rtk proxy .venv/bin/pytest -q \
  tests/test_scout_r5_interview_transfer.py::test_feedback_source_requires_real_feedback_delta_not_preparation_or_copy \
  tests/test_scout_r5_transfer_corrections.py::test_restore_rejects_nested_symlink_swap_without_escape_or_unrelated_cleanup
2 passed in 7.30s

rtk proxy .venv/bin/pytest -q \
  tests/test_scout_r5_interview_transfer.py::test_interview_accepts_actual_saved_discovery_posting_snapshot
1 passed in 14.11s

rtk proxy .venv/bin/pytest -q \
  tests/test_scout_r5_interview_transfer.py::test_interview_accepts_actual_saved_research_revision_snapshot \
  tests/test_scout_r5_interview_transfer.py::test_feedback_source_requires_real_feedback_delta_not_preparation_or_copy
2 passed in 20.37s

rtk proxy ruff check \
  src/gigai/scout_interview_records.py src/gigai/private_transfer.py \
  tests/test_scout_r5_interview_transfer.py tests/test_scout_r5_transfer_corrections.py
All checks passed!

rtk .venv/bin/python -m py_compile \
  src/gigai/scout_interview_records.py src/gigai/private_transfer.py \
  tests/test_scout_r5_interview_transfer.py tests/test_scout_r5_transfer_corrections.py
passed (no output)
```

## Remaining limits

The symlink probe is deterministic and exercises the relevant swap boundary;
it is not a probabilistic concurrent race or proof for every filesystem. The
secure-primitives refusal branch was implemented but not exercised on an
unsupported platform. Evidence remains focused source/test evidence, not
installed-wheel, provider readiness, model identity, live execution,
publication, or full-release acceptance. The public interview `prepare
--run` path still processes caller-supplied content; these tests do not claim
model generation or a new model loop. Fresh approval/execution after private
transfer and all R6 resume/authority/identity findings remain outside this
handoff.

Orca follow-up checks were unavailable with `runtime_unavailable`; no repeated
IPC retry or restart was attempted. No commit, reset, or unrelated file edit
was performed.
