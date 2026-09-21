# SCOUT-06 hydration corrections

Date: 2026-09-10. Corrected the two blocking findings from the independent
`SCOUT-06-hydration-review.md` within `scout_research_inputs.py` only.

Hydration now retains every committed checkpoint and receipt record for the
selected Run at one pinned writer HEAD, instead of deriving history solely from
the selected output/check tuple. Each retained record is authenticated through
the existing immutable artifact reader, so omitted predecessors, extra receipt
history, and competing terminal records remain visible to the existing sequence,
parent, and unique-terminal checks. The checkpoint-path normalizer also avoids
turning an already complete checkpoint filename into `.json.json`.

`hydrate_research_input_snapshot` and
`resolve_research_input_from_journal` accept an already-held `JournalWriter`;
the supplied writer is scope-checked and reused without acquiring a nested flock.
The standalone wrapper still acquires one writer lock exactly once. This keeps
later caller-side revalidation on the same supplied writer/snapshot boundary.

Focused verification completed with:

```text
timeout 300 .venv/bin/pytest -q tests/test_scout06_research_inputs.py -p no:randomly
13 passed in 113.31s (0:01:53), exit 0

ruff check src/gigai/scout_research_inputs.py tests/test_scout06_research_inputs.py
All checks passed!, exit 0
```

The added tests cover an unreferenced forked checkpoint and an extra competing
terminal receipt through real journal publication, plus held-writer hydration.
No broad suite,
wheel, provider, private-root, central-schema, CLI, or commit work was done.
