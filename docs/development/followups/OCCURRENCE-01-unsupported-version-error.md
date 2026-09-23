# OCCURRENCE-01: Explicit unsupported-version error for graph-set Gigs

**Status:** Recorded 2026-09-22 from the S14 follow-up. Not started, and no
implementation is authorized.
**Owner/system:** GigAI occurrences (`occurrence.py`).
**Priority:** Error clarity. This ticket does **not** add graph-set support.

## Ticket

**Problem:** `_active_pointer` (`occurrence.py:484-498`), used by
`declare_occurrence` and reachable through `gigai occurrence declare`,
validates the active pointer against the v1 schema only. A graph-set Gig's
pointer is v2: it requires `graph_set`, and v1 forbids unknown keys. So it
can never pass, and the refusal says "active Gig version is invalid". That
misstates the cause. The pointer isn't invalid; its version is unsupported
here.

**Intended behavior:** When the pointer is a valid v2 pointer, refuse with an
explicit unsupported-version error. `portability.py:124-134` is the existing
precedent: it refuses v2 with `unsupported_schema_version` and a message
saying graph-selected versions aren't supported there. Truly malformed
pointers keep the existing "invalid" error.

**Out of scope:** Adding graph-set support to occurrences. That is a separate
product decision and needs its own ticket if wanted.

**Tasks:**

1. Confirm the behavior by running `gigai occurrence declare` against a
   graph-set Gig. Today the finding comes from reading the code and
   comparing the schemas; the CLI hasn't been run.
2. Propose the error code and message, matching the portability precedent.

**Acceptance:** A test shows that a valid v2 pointer produces the explicit
unsupported-version error and a malformed pointer still produces
"invalid".

**Evidence:** [S14 audit, F6](../v0.1.8/spikes/evidence/S14-schema-inventory-audit.md).
The finding is from reading, plus an executed schema comparison. **The
operator hasn't independently reviewed it yet.**
