# SCOUT-07 — Inventory correction verification

Date: 2026-09-10. This independent bounded review owns only the focused
inventory tests and this evidence note; no production source was changed.
Review covered the frozen workpad/source-inventory contract, the prior
SCOUT-07 Run integration review and correction note, and
`scout_materialization.py`.

## Verdict

**The corrected exact-member validation is accepted for this bounded lane.**
The prior six tests included five in-memory calls to
`_validate_existing_inventory` with altered payloads but unchanged committed
inventory bytes; those were useful predicate coverage, not proof that the
public materialization reuse branch rejects legitimately journaled corruption.
The new tests close that evidence gap without weakening multipublisher refusal
or claiming whole-Scout acceptance.

## Verification added

`tests/test_scout07_inventory_members.py` now performs real disposable public
materialization in two ways:

1. It materializes a valid candidate, then calls
   `materialize_scout_candidate` directly on the existing workpad and asserts
   exact no-new-publication reuse. The historical inventory/proposal bytes and
   every materialized member byte remain unchanged, including the prepared
   capability manifest and generated compiled graph IDs/timestamps embedded in
   those historical bytes.
2. For each of omitted, extra, duplicate, wrong-digest, and wrong-size member
   rows, it monkeypatches only the disposable first materialization’s row
   renderer, thereby journaling one malformed inventory with one publisher.
   After restoring the renderer, the public reuse call refuses and the Git
   HEAD remains unchanged. These fixtures do not edit a working copy or create
   a second publisher, so the failures exercise member validation rather than
   an unrelated conflict path.

The focused command passed:

```text
rtk .venv/bin/pytest -q tests/test_scout07_inventory_members.py -p no:randomly
12 passed in 55.32s
```

## Source review result

The correction’s `_validate_existing_inventory` authenticates the inventory
publication, captures its returned commit, reads every listed member at that
pinned commit, and enumerates the committed subtree at the same commit before
requiring exact set equality (`scout_materialization.py:113-157`). This rejects
omitted, extra, duplicate, wrong-digest, wrong-size, malformed, missing, and
uncommitted rows. The reuse path then rechecks top-level identity and reads the
prepared manifest as committed authority (`:535-569`); proposal reuse returns
the historical proposal bytes in the positive test. `read_committed_artifact`
continues to reject multiple committed publishers, and no correction changed
that guard.

No additional production defect was established in this bounded review. The
materialization operation’s broader concurrency/public-caller behavior was not
stress-tested here; this result is not a claim of whole-Scout, installed-wheel,
provider, private-root, activation, or release acceptance.

