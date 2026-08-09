# G22 completion audit

- Status: Complete
- Goal: G22 Deliberative Create and User-Facing Proposal Interview
- Audit date: 2026-08-09
- Contract baseline: accepted S22-01 protocol, additive proposal-interview
  resource, 22 installed schemas, and completed G18 boundary evidence

## Verdict

G22 is complete. The shipped `gigai create` path launches a short-lived,
token-bound loopback interview, persists authoritative proposal artifacts only
after operator approval, and never creates a Run or mutates the target. The
default questioner is the deterministic offline target through G18's factory
and model port. Non-deterministic provider question generation is refused by
default and is not advertised as shipped provider-backed quality.

No provider endpoint, credential value, public listener, capability execution,
target write, target commit, or Run was used by the G22 evidence.

## Acceptance reconciliation

| Criterion | Evidence and result |
|---|---|
| 1. Dependency and contract gate | S22-01 and the proposal-interview amendment are accepted; the 22-resource contract inventory and canonical ownership tests pass. |
| 2. CLI and terminal result | Source black-box and fresh-wheel `gigai create` flows reach `approved` with session/proposal IDs and no `runs` directory. |
| 3. Loopback isolation | The server rejects non-loopback construction, requires its token, expires at its lifetime, and closes at terminal state. |
| 4. Exact references | Explicit stable IDs select bytes; digest mismatch, symlink, changed reference, and unselected-input fixtures fail closed. |
| 5–6. Protocol and clarification | All four answer types, allowed values, revision/sequence guards, clarification rounds, and terminal cap blocking are tested. |
| 7. Model boundary | Deterministic questions use the G18 factory/port; selected-only input and explicit network denial are tested. |
| 8. Persistence and authority | SQLite stores redacted ordered metadata only; divergent, stale, or truncated traces reject, while workpad recovery remains authoritative. |
| 9. Revisions | Changed answers and model questions create explicit revision parentage, persisted in the schema snapshot and trace. |
| 10. Boundary approval | Only `read_local`/`write_workpad`, valid privacy/capability choices, selected references, and an operator action can reach approval. |
| 11. Proposal handoff | Existing G08 validation/sealing creates the authoritative proposal; repeat approval is idempotent and creates no second journal commit. |
| 12. Interruption/security | Process kill, SQLite loss, malformed payload, wrong token, expiry, cross-session URL use, symlink, digest, and network-denial negatives pass. |
| 13. S22-01 corpus | Repository feature, resume tailoring, reference synchronization, and tabular finance cases all reach bounded approved outcomes with required question coverage. |
| 14. Installed evidence | Fresh wheel replay and `tools/verify_installed_schemas.py` pass; sanitized evidence and the terminal handoff are committed here. |

## Verification record

```text
rtk uv run pytest -q
444 passed, 50 subtests passed in 138.50s

/private/tmp/gigai-g22-installed/bin/python tools/verify_installed_g22.py
verified installed GigAI G22 create interview

/private/tmp/gigai-g22-installed/bin/python tools/verify_installed_schemas.py
verified 22 installed GigAI schemas
```

The complete suite includes the G22 protocol, lifecycle, HTTP, CLI,
question-quality, contract, canonical-ownership, and installed-scenario
regressions. The installed replay used disposable roots and an offline
deterministic response; no local path, browser cache, credential, or raw
reference content is retained in this evidence.

## Authority and non-effects

The workpad/journal and sealed proposal remain authoritative. SQLite is a
rebuildable trace. Browser state, model output, and an interrupted draft do
not imply approval. G22 does not own target mutation, patching, capability
installation, provider fallback, or Run creation.
