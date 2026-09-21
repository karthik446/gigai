# SCOUT-06 research Run hardening

Date: 2026-09-10. Scope is the disposable, synthetic role-only research Run
lane. The tests exercise the actual candidate initializer, offline approval,
journal writer, public CLI/service entry points, and packaged research bridge;
they do not call a provider, network, private user workpad, or live execution
path. No repository commit, wheel build, or full-suite run was performed.

## Focused evidence

| Command | Result |
| --- | --- |
| `rtk proxy .venv/bin/pytest -q tests/test_scout06_research_run_flow.py` | 5 passed in 15.97s |
| `rtk proxy .venv/bin/pytest -q tests/test_scout06_research_run_adversarial.py` | 11 passed, 1 strict xfailed in 69.90s |
| `rtk proxy .venv/bin/pytest -q tests/test_scout06_research_run_flow.py tests/test_scout06_research_run_adversarial.py` | 16 passed, 1 strict xfailed in 86.68s |
| `rtk ruff check tests/test_scout06_research_run_flow.py tests/test_scout06_research_run_adversarial.py` | passed; no diagnostics |

The positive integration lane proves v2 Plan and Start, public CLI forwarding,
typed mixed research/checkpoint publication, submit, exact submit replay, and
the corrected `research-role-completion` check kind. The adversarial lane adds:

- exact checkpoint replay with no new journal/artifact bytes;
- changed intent under a reused operation key and stale checkpoint parent CAS,
  both refused without mutation;
- malformed, missing, extra, and corrupt domain/supporting evidence, each
  refused atomically before journal publication;
- submit rejection for forged digest and forged output-kind tuple references;
- v1 checkpoint and submit downgrade refusal against a v2 Run; and
- completed-Run history stability after exact terminal receipt replay.

## Remaining race gate

The committed domain source/binding race is recorded as one strict `xfail`, not
as passing proof. The test runs the real domain validator first, then uses only
a test seam on `_journaled` to mutate the committed packaged `research.py`
working-tree bytes immediately before publication. It expects the writer to
refuse and leave the artifact set unchanged; the current implementation instead
has no post-validation committed-domain re-read seam, so the expected refusal
does not occur.

The lock/caller evidence is precise: `run_with_journal_writer` acquires the
single POSIX `gigai-writer.lock` and invokes `_progress` under that lock;
`_progress` snapshots and validates the domain before calling `_journaled`, and
`_journaled` calls `JournalWriter.record` without re-authenticating the sealed
domain schema/source binding. The journal writer protects concurrent writers,
but it does not by itself detect a source mutation that occurs after the domain
validation and before artifact publication. Luna/source ownership must add the
post-validation CAS/re-read or an equivalent narrow race seam before this gate
can be converted to a passing regression. No source edit was made here because
the assigned ownership permits tests/evidence only and requires asking before
source changes.

This lane remains synthetic offline evidence, not provider dogfood, activation,
default promotion, release acceptance, or proof of later historical research
input reuse.
