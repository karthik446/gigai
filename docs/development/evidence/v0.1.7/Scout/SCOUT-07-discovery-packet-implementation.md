# SCOUT-07 candidate discovery packet implementation

## Scope

This bounded candidate adds only the pure, unshipped discovery renderer at
`data/scout/tools/cap_00000000-0000-4000-8000-000000000074/`.  It performs no
filesystem reads, workpad/journal/SQLite mutation, provider or network call,
Graph Set approval, active-selection change, job selection, application, or
tailoring action.  The source is deliberately not in `scout_source_files` or a
capability inventory.

## Candidate contract

`build_discovery_packet` requires exactly one sealed, normalized
`scout_record` input whose `native_kind` is `profile_preferences`.  Its
record/revision/scope/blob reference is retained verbatim in the sidecar; the
caller must supply bytes under exactly that sealed blob-ref path.  The renderer
checks the digest and size, validates `native-record-content.schema.json`, and
requires its kind and scope to equal the selected reference before extracting
preferences.  Choice B retains strict normalized G45 and native-experience
optional envelopes in original selected-input order, without reading their
contents for discovery inference; unknown or malformed families refuse.

The sidecar has identity `urn:gigai:scout:discovery-packet:2` and version
`scout-discovery-sidecar:2`; its SHA-256 is
`12f77c45c859192835c332869ef2e991bc3601b7e8577ba53e05ecc281c78b33`.
It seals project/Gig/version, `find-jobs` graph/version, Run, selected native
preference input and the normalized preference facts.  Every source, posting
fact, capture, independent verification declaration, snapshot, duplicate,
shortlist reason, exclusion and question is closed and validated.  A reported
or captured source is not verified; independently verified status needs a
separate supplied evidence artifact with distinct bytes and a declared actor.
That declaration is evidence supplied by an external agent, not a GigAI claim
of truth, current availability, eligibility, market coverage, or hiring
outcome.

Opportunity identity uses employer plus a supplied source posting ID, falling
back only to an exact normalized safe HTTP(S) locator, never a title.  Snapshot
identity includes observed date/source/facts, so duplicate discovery retains
both snapshots and links later ones with `duplicate_of`.  `agent_reported_match`
requires every hard preference plus sponsorship need/employer sponsorship and
eligibility to be known and have a reason tied to the selected snapshot's
source.  Unknown or conflicting compensation/sponsorship/eligibility therefore
cannot be promoted to a hard match; a partial/no-match outcome, unresolved
question, or exclusion remains explicit.

All text is rendered inline with escaped controls, Markdown delimiters, and
link syntax.  `validate_rendered_packet` recomputes from complete selected
profile/supporting bytes and requires both exact Markdown and canonical sidecar
equality, so altered selected preferences, source bytes, identity fields, facts
or rendered text refuse.

## Focused evidence

On 2026-09-10:

```text
rtk .venv/bin/pytest -q tests/test_scout07_discovery_packet.py
11 passed in 0.15s

rtk .venv/bin/python -m py_compile \
  src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py \
  tests/test_scout07_discovery_packet.py

rtk uv run ruff check \
  src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py \
  tests/test_scout07_discovery_packet.py
All checks passed!
```

The synthetic tests cover a successful fully evidenced match, no-match and
partial unknown-sponsorship cases, a stale duplicate snapshot, exact native
preference revision change without old-byte mutation, unsafe locators, foreign
source IDs, malformed input shapes, unused preference bytes, capture-digest
spoofing, safe rendering of forged headings/lists/fences, and document/sidecar
tampering.  They use only supplied synthetic bytes and no resume or live job
lookup.

## Remaining integration gates

SCOUT-06 persistence and SCOUT-07 source-inventory/compiled-contract work are
not part of this candidate.  A later trusted bridge must authenticate the Plan
and Run origin, expose a versioned selected-input union for optional posting,
experience, or G45 inputs, bind this packet as a validated output, and journal
its exact bytes.  That bridge must not interpret this renderer as provider
execution, an approved Graph Set, a real job search, a selected opportunity, or
permission to apply/tailor.

## Correction: normalized optional input retention (Choice B)

The coordinator answered Choice B in `msg_0e80588dc67f`: this candidate now
requires exactly one native `profile_preferences` envelope while retaining
strict, already-normalized optional inputs in their original list order.  The
accepted optional forms are direct `g45_reference`, direct `g45_run_input`, a
`scout_record` wrapper over either G45 form, and native
`scout_record`/`experience_qa`.  Each closed envelope keeps every identity,
scope, content and artifact reference; unknown families, malformed nested
references, missing profiles, and duplicate/conflicting profile selections
refuse.  Optional contents are intentionally not supplied to or interpreted by
the renderer, so they cannot establish a discovery hard constraint,
eligibility, competence claim, or match explanation.

This is a candidate contract expansion, so the unshipped schema is now
`urn:gigai:scout:discovery-packet:2` with literal
`scout-discovery-sidecar:2`; its SHA-256 is
`12f77c45c859192835c332869ef2e991bc3601b7e8577ba53e05ecc281c78b33`.
The profile's supplied blob bytes remain the sole input bytes accepted by this
pure renderer and are validated against the native content schema as before.
The deterministic sidecar binding includes every retained optional envelope,
so revalidation refuses an altered optional reference even though it was never
used for fit inference.

Focused correction verification on 2026-09-10:

```text
rtk .venv/bin/pytest -q tests/test_scout07_discovery_packet.py
17 passed in 0.19s
```

The added coverage preserves all prior cases and adds ordered G45/native
experience/wrapper retention, malformed optional nested reference refusal,
unknown-family and duplicate-profile refusal, an unread experience envelope
that cannot satisfy unknown sponsorship, exact optional-reference revalidation,
and same-title/no-source-ID postings with distinct locators remaining distinct
rather than being deduplicated by title.  This does not add a registered schema,
Plan-input integration, journal persistence, provider behavior, release, or
candidate source-inventory inclusion.
