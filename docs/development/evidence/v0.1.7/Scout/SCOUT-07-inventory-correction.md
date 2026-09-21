# SCOUT-07 inventory correction

Date: 2026-09-10. Implemented only the F1 correction from
`SCOUT-07-run-integration-review.md`.

`scout_materialization.py` now validates an existing inventory against the
complete member path set at the inventory's authenticated journal head, then
checks every row's path, digest, and size against the exact committed bytes.
Historical compiler IDs, timestamps, and the prepared capability manifest remain
unchanged and included in the inventory; retries do not regenerate and compare
fresh compiled bytes. Any omitted, extra, duplicate, wrong-digest, wrong-size,
or malformed member row fails closed, while Git multi-publisher refusal remains
delegated to `read_committed_artifact`.

Focused verification:

```text
.venv/bin/pytest -q tests/test_scout07_inventory_members.py tests/test_scout05_materialization.py -p no:randomly
13 passed in 20.31s
```

The focused tests perform real disposable materialization and exact idempotent
reuse, derive expected rows from the materialized bytes, and exercise all five
member-corruption classes against the authenticated committed-member validator.
No external recording,
CLI, schema registry, provider, wheel, private-root, broad-suite, or commit
operation was performed; this is not whole-Scout acceptance.
