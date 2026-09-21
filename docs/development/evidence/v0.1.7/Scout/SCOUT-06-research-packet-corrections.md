# SCOUT-06 candidate research-packet corrections

**Status:** bounded candidate correction, 2026-09-10.  This updates the pure,
unregistered role-research renderer only; it does not inventory, approve,
execute, persist, or expose the candidate tool.

## Corrected findings

- **R1 — rendered prose is data.** Every review-identified rendered text slot
  is normalized to a visible inline form (`↵` for a newline and `⇥` for a tab)
  before HTML and Markdown escaping.  A supplied string cannot create a
  heading, list, quote, or fence; useful multi-line content is retained rather
  than silently deleted.  The renderer now also prints the closed packet
  binding, claim/source relationships, declared checks, and all other sealed
  domain fields needed for the regenerated document to represent its sidecar.
- **R2 — verification is supplied evidence, not a GigAI attestation.** A
  verified source requires capture and verification artifacts with different
  identifiers *and* different SHA-256 values.  The sidecar records
  `verification.evidence_status: "supplied"`, retains its declared
  method/evidence/actor, and the document says that the actor is recorded in
  the sidecar.  `agent` remains an accepted declared reviewer kind; no code
  claims that it proves a human identity, that GigAI performed verification, or
  that the supplied assertion establishes source truth.
- **R3 — full sealed-data revalidation.** `validate_rendered_packet` now
  requires the in-memory artifact-byte map, validates every referenced capture
  and verification artifact again, rebuilds once from the closed sidecar, and
  requires both exact Markdown and exact canonical sidecar equality.  This is
  intentionally one-way (`validate -> build`); the builder never validates,
  so there is no recursive build/validate loop.  Salary, status, claim, check,
  origin, selected-input, malformed-sidecar, and document mutations refuse.
- **R4 — dates match the schema.** Runtime dates require lexical
  `YYYY-MM-DD` before calendar parsing; focused schema validation uses
  `jsonschema.FormatChecker`, so compact and ISO-week dates refuse too.
- **R5 — bounded compensation coherence.** When base and total are both
  declared in this one compensation context, total minimum and maximum cannot
  be below base.  Every known-compensation claim must point, through its
  declared source relationship, to a `salary_survey`, `employer`, or
  `job_posting` source.  That is a relationship/type check only: it does not
  parse prose, infer salary truth, or imply that an employer/job-posting text
  is factually sufficient evidence.

## Candidate contract details

`research.schema.json` remains a standalone candidate domain schema.  Its
final SHA-256 is:

```
4f1600ebb1a8205b1396691e473c782f440cbe4f9fccf040857f356e89223071
```

The schema makes the generated verification label mandatory and conditionally
requires the correct capture/verification shape for reported, captured, and
independently-verified sources.  No core schema registry, inventory, checksum,
CLI, external-recording contract, journal, or lifecycle file was changed.

## Focused verification

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout06_research_packet.py` | `35 passed in 0.12s` |
| `uv run ruff check src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.py tests/test_scout06_research_packet.py` | passed |
| `.venv/bin/python -m py_compile src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.py` | passed |

The focused tests cover no-resume role-only research, known and unknown
compensation, captured/reported/supplied-independent evidence, actual distinct
verification bytes, all twelve reviewed text slots with forged heading/list/
quote/fence payloads, source/claim and compensation association errors, strict
calendar dates, stale documents, full sidecar regeneration, and immutable
iteration.

## Remaining integration gates

This remains an inert candidate file outside `scout_source_files`, template
inventories, capability manifests, registered schemas, CLI, Run-output
contracts, and journal persistence.  A later authorized lane must decide and
implement the exact persisted Run-output/sidecar authority, inventory and
approve the source, bind external output under the writer lock, and prove
tailoring/interview consumers select a preserved research revision.  None of
these tests are live research, provider, private-data, or default-promotion
evidence.
