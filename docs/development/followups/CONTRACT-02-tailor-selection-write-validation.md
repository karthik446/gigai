# CONTRACT-02: Validate tailor-selection output when it is written

**Status:** Recorded 2026-09-22 from the S14 follow-up. Not started, and no
implementation is authorized.
**Owner/system:** Scout tailoring (`scout_tailor_selection.py`) and the
packaged `scout-tailor-selection-v2.schema.json`.
**Priority:** Contract hygiene. No non-conforming output has been observed.

## Ticket

**Problem:** `TailorSelection.to_json` (`scout_tailor_selection.py:226-236`)
emits `selector_version: "scout-tailor-selection:2"`, but no code validates
its output against the packaged schema. One fixture shape (proposal and
answer present, from `test_scout_r2_tailor.py`'s `_selection()`) conforms.
Other shapes, such as `proposal=None` or no answers, weren't checked. The
schema is also not described in the schemas README.

**Intended behavior:** Output is validated against the packaged schema at
the point where it is written or used. Otherwise the decision not to
validate it is recorded explicitly.

**Tasks:**

1. Check conformance for the remaining shapes: no proposal, no answers,
   each allowed `requested_outputs` combination.
2. Decide where validation belongs: in `to_json`, or where the selection is
   sealed or recorded.
3. Add the schema to the schemas README.

**Acceptance:** Every constructible shape conforms, or the mismatches are
listed. Write-time validation exists, or its absence is justified in the
code or README.

**Evidence:** [S14 audit, F3 and F6](../v0.1.8/spikes/evidence/S14-schema-inventory-audit.md).
Conformance was executed for one shape only. **The operator hasn't
independently reviewed it yet.**
