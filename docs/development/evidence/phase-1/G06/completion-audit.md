# G06 Completion Audit

- Goal: [G06 — Journal Locking and Recovery](../../../goals/phase-1/G06-journal-locking-recovery.md)
- Date: 2026-08-03
- Local result: Pass
- Verification host: macOS arm64
- Package version: 0.0.0

## Outcome

GigAI now has a generic private-journal writer for a caller-supplied G05 workpad. It accepts a caller-supplied project ID, Gig ID, handoff ID, semantic transition, and body; it never provisions a workpad, allocates project/Gig identity, selects an active Gig, or exposes a public lifecycle command.

The writer takes the repository-local `.git/gigai-writer.lock`, re-probes the selected workpad filesystem before mutation, reads the committed `HEAD` trailers for normal O(1) allocation, atomically publishes a canonical text handoff, and commits the handoff with durable identity trailers. The unborn G05 repository is the one valid sequence-zero predecessor.

## Acceptance reconciliation

1. **Unborn first transition — Pass.** The first caller-supplied transition writes `000000000001-creation-started.txt`, commits `.gitignore` plus that handoff, and creates `HEAD` without changing project or Gig identity.
2. **Concurrent serialization — Pass.** Eight spawned processes use the production interface, produce unique committed sequences 1 through 8, and leave eight linear journal commits.
3. **Bounded allocation — Pass.** Normal `record_transition` reads only `HEAD` trailers and the computed next path. An AST guard rejects `glob`, `iterdir`, and `rglob` in that normal writer path; directory scanning is confined to explicit reconciliation.
4. **Handoff and commit durability — Pass.** The writer fsyncs its temporary handoff, atomically replaces it, fsyncs the handoff directory, stages the exact handoff, and commits identity trailers before returning success.
5. **Fail-closed diagnostics — Pass.** Lock timeout includes last known owner metadata; unsupported locking, failed mount probes, mount identity change, remote configuration, invalid ownership markers, and trailer divergence raise typed errors without a fallback or repair.
6. **POSIX replacement discipline — Pass.** The production writer fsyncs both the handoff file and parent directory. The configured workpad mount is re-probed before each write, using the same atomic-replace and interprocess-lock checks established by G03.
7. **Crash recovery — Pass.** First and later transition tests inject crashes before replacement, after replacement, before commit, and after commit. Explicit reconciliation removes a temporary artifact or commits exactly one valid orphan; a subsequent write never duplicates a sequence.
8. **Eight-process proof — Pass.** The production race test runs on the configured disposable workpad filesystem and verifies strict contiguous handoff ordering.
9. **Identity/workpad ownership boundary — Pass.** An AST ownership test rejects project/Gig allocation and workpad-provisioning symbols in the journal module. All ownership IDs cross the journal boundary as validated caller input.
10. **No invented external success — Pass.** Recovery derives only from durable handoff bytes and local Git state. It has no provider, network, tool-execution, or process-exit success path.

## Verification

The focused G06 suite covers first commit, trailer inspection, G05 resolution after a journal commit, uncommitted orphan refusal, lock owner timeout, remote/mount/divergence refusal, first and later crash matrices, explicit recovery, and the eight-process race. The complete locked source suite passed locally after the final changes.

`uv build` produced a wheel and source distribution. In a fresh CPython 3.11 environment, the installed G06 verifier proved an unborn installed workpad can create its first durable handoff and local commit with the required trailers. The G05 and G11 installed verifiers also passed against that wheel.

All eight schema checksums pass. No packaged schema or canonical-vector bytes changed; the canonical-vector digest remains:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

## Completion decision

G06 is locally complete. Hosted CI on the exact goal commit remains the publication confirmation gate. G08 may not begin until G07 has its own committed completion evidence.
