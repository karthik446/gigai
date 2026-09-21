# SCOUT-05 capability review — independent-review findings implementation

**Status:** bounded corrections complete, 2026-09-10. This lane implements the
independent review findings F1–F4 and the requested F6 reference precision in
the generic local capability review service. It owns only
`src/gigai/capability_review.py`,
`tests/test_scout05_capability_review.py`, and this evidence note; it does not
change schemas, CLI, lifecycle, default initialization, journal registration,
private user state, providers, or activation.

## Corrections delivered

* **F1 — one passed review per pending parent:** while holding the existing
  journal writer lock, the service authenticates committed passed decisions
  for the same project, Gig, capability, and exact pending parent reference.
  Exact operation-key lookup is completed across all committed decisions
  before the distinct-key duplicate-parent check, so filename ordering cannot
  hide a valid rejection replay behind a later passed decision. A different
  operation key now returns typed `capability_review_conflict` without another
  decision or reviewed manifest, while a valid different pending parent is
  independent. Exact same-key replay still returns the committed result after
  validating its reviewed-manifest reference and digest. A rejected decision
  does not reserve the parent: a later explicitly passed review under a new
  key is intentionally allowed and tested as re-review after rejection, and
  the original rejection still replays exactly.
* **F2 — non-tautological publication assertions:** rejected review and
  source-race tests snapshot the complete capability-manifest membership and
  bytes before the call and require exact equality afterward. The rejected
  case permits exactly its one rejected decision; the source-race case permits
  no decision and no new manifest.
* **F3 — ownership and pointer diagnostics:** foreign project/Gig inputs are
  pinned to the actual journal ownership refusal
  (`capability_review_authority_unavailable`). Separate malformed foreign
  current-pointer and committed current-version-advance fixtures are refused
  before publication, with the latter reaching the final pointer inequality
  branch as `capability_review_current_version_conflict`.
* **F4 — effect diagnostic:** non-none network requirements and non-empty
  credential requirements now use the existing accurate
  `capability_review_effect_refused` code rather than the unrelated
  `unsupported_kind` code.
* **F6 — optional canonical digest:** artifact references continue to require
  exact relative path, media type, imported-byte content digest, and size.
  The schema-valid optional `canonical_sha256` member is accepted only when it
  matches the canonical digest of the referenced JSON; mismatches and unknown
  members are refused. No parallel schema or authority format was added.

The prior final-inventory comparison remains as harmless defense in depth;
the source inventory routine is the effective byte-drift detector and remains
the source of the typed source-change refusal. The existing multi-publisher
journal refusal is unchanged.

## Focused evidence

The independent review’s earlier bounded `pytest --collect-only` run counted
13 tests and did not execute them; its historical implementation evidence
reported 13 passed in 24.45s after central registration, with the prior
registry/transition monkeypatch run retained only as historical context. The
current tests contain no registration monkeypatch and exercise the production
schema/transition registrations already integrated by root.

| Command | Result |
| --- | --- |
| `ruff check src/gigai/capability_review.py tests/test_scout05_capability_review.py` | **All checks passed** |
| `.venv/bin/python -m py_compile src/gigai/capability_review.py tests/test_scout05_capability_review.py` | **Passed** |
| `.venv/bin/pytest -q tests/test_scout05_capability_review.py` | **24 passed in 48.53s** |

The 24 tests include the original pass/reject/consent/effect/replay/source
and current-version cases plus corrected same-parent conflict, exact-key
lookup ordering for rejected-then-passed replay, a valid different pending
parent, exact manifest snapshots, journal-boundary foreign ownership cases,
malformed pointer, committed current-version race, and optional canonical
digest acceptance/refusal cases. Fixtures are disposable workpads built from
the actual bundled Scout source; the committed race uses a disposable fixture
repository commit only, not a commit to this worktree. No real `.gigai` state,
provider, network, approval, activation, or full suite was used.

## Remaining gate

This remains a generic service without a public CLI caller or successor
amendment/approval integration. A later owner must consume the decision and
new manifest by their authenticated IDs, create a successor proposal, and use
the normal explicit approval path before attaching capability authority to an
active Gig; review itself still never activates, selects, executes, or grants
provider access.
