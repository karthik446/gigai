# S14 reproduction kit

These scripts make the executed findings in the
[S14 audit](../S14-schema-inventory-audit.md) (F3, F4 and F6) reproducible.
They are cleaned copies of the scratch checks used during the audit.
[`results.txt`](results.txt) is their output from a fresh run of these
exact files on 2026-09-22 at `b01675d`, with `src/gigai/` unmodified. It
matches the originally reported numbers.

The scripts are read-only checks. They modify no product code. The only
thing that writes files is the pytest run, and it writes to a `--basetemp`
you choose.

## Commands

Run everything from the repository root, with `<BT>` set to an empty
scratch directory outside the repo:

```sh
R=docs/development/v0.1.8/spikes/evidence/S14-repro

# Census: filename/$id references per schema (Method step 2; F1 counts)
uv run python $R/census.py /tmp/census.json

# Produce real workpads, journals and v2 external-recording records
uv run pytest tests/behaviors/scout_discovery/test_scout07_discovery_run_flow.py \
  -q -p no:cacheprovider --basetemp=<BT>

uv run python $R/handoff_scan.py <BT>         # F6: real handoffs vs handoff-frontmatter
uv run python $R/handoff_synthetic.py         # F3: synthetic _render_handoff check
uv run python $R/privacy_guard.py <BT>        # F4/F6: guard on real v2 records, in place and renamed
uv run python $R/tailor_selection.py          # F6: one TailorSelection shape vs schema
uv run python $R/active_pointer_compare.py    # F6: v2 pointer can't satisfy the v1 schema
```

## Sanitization

- The outputs contain counts, schema and transition names, validator
  messages and booleans only.
- They contain no handoff bodies, record contents, host paths, Gig, project
  or run IDs, terminal handles or capability values.
- `results.txt` shows `<BT>` and `<scratch>` in place of the real
  directories.
- The committed files were scanned for local paths and capability or
  terminal-handle patterns before being added.

## Limits (the same as in the audit)

- **Handoff scan:** one test file's workpads (28 handoffs). That's a
  sample, not the journal's full transition vocabulary.
- **Privacy guard:** samples only the families the test produces, which
  are checkpoint, run and receipt. **Plan and invocation v2 were not
  sampled.** PRIVACY-01's acceptance criteria require all five.
- **Tailor selection:** covers one fixture shape only.
- **Census:** grep-based. A name assembled at runtime can be missed.
  `with_src_call_site=76` counts schemas that have any call-site
  reference; the audit's classification comes from reading those sites.
- **Brittle internals:** the scripts use internal helpers
  (`journal._render_handoff`, `journal._read_handoff`, a test module's
  `_selection`), so they can break if those internals change.
