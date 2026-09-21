# SCOUT-06 candidate role-research packet

**Status:** bounded candidate implementation, 2026-09-10. This is a pure,
unwired domain-output renderer; it is neither bundled source, approved tool
authority, a Run output, nor evidence that SCOUT-06 is complete.

## Delivered candidate boundary

Two deliberately unregistered candidate files live beside the current Scout
CRUD tool:

- `tools/cap_00000000-0000-4000-8000-000000000071/research.py`
- `tools/cap_00000000-0000-4000-8000-000000000071/research.schema.json`

`build_research_packet` accepts only caller-supplied role-research data,
explicit `graph_id`/version/`run_id`, selected record/revision inputs, and an
in-memory `artifact_id -> bytes` mapping. It performs no filesystem reads,
network access, provider invocation, subprocess work, journal/SQLite write,
capability change, activation, or source execution. Locators are accepted only
as safe `http`/`https` data and are rendered as escaped text; they are never
opened or fetched.

The result holds deterministic UTF-8 Markdown, a closed sidecar, and canonical
sidecar JSON bytes. The sidecar binds the exact Markdown SHA-256 and byte size,
`research-role` graph identity/version and Run, selected record/revision inputs,
claims and sources, uncertainty, focused questions, checks, and explicit
`tailoring_context`/`interview_context` roles. `validate_rendered_packet`
refuses any changed document bytes.

Sources are `reported`, `captured`, or `independently_verified`. Reported URLs
remain declared evidence only; captured and verified sources require supplied
bytes whose size/digest match the declared reference. Independent verification
also requires a different supplied evidence artifact plus a method and actor;
setting a source status or author flag cannot manufacture verification.

Compensation is either explicit `unknown` with null context/ranges and a stated
limitation, or `known` with geography, uppercase currency, ISO date, pay
period, base and/or total bounded range, sourced claim links, and limitations.
No import timestamp becomes a retrieval date and no salary is inferred.

## Complete reusable synthetic example

The focused fixture produces a role-only Forward Deployed Engineer packet with
one selected role revision—no resume. Its generated sidecar has this complete
reusable relationship shape (the test supplies exact artifact bytes for every
non-null reference):

```json
{
  "schema_version":"scout-research-sidecar:1",
  "output_kind":"role_research",
  "output_roles":["tailoring_context","interview_context"],
  "origin":{"graph_selector":"research-role","graph_id":"graph_00000000-0000-4000-8000-000000000061","graph_version":1,"run_id":"run_00000000-0000-4000-8000-000000000062"},
  "selected_inputs":[{"record_id":"record_00000000-0000-4000-8000-000000000063","revision_id":"revision_00000000-0000-4000-8000-000000000064","kind":"role"}],
  "research":{
    "role_title":"Forward Deployed Engineer",
    "compensation":{"status":"known","geography":"Denver, CO","currency":"USD","as_of_date":"2026-09-10","pay_period":"annual","base_range":{"minimum":150000,"maximum":190000},"total_range":{"minimum":175000,"maximum":230000},"source_limitations":["Synthetic example; location, level, and equity assumptions can change the range."],"claim_ids":["claim_compensation"]},
    "sources":[
      {"source_id":"source_role","status":"independently_verified","claim_ids":["claim_delivery"],"capture_ref":{"artifact_id":"role_capture","content_sha256":"sha256:<exact supplied bytes>","size_bytes":55},"verification":{"method":"independent_review","evidence_ref":{"artifact_id":"role_review","content_sha256":"sha256:<exact supplied bytes>","size_bytes":58},"actor":{"kind":"reviewer","id":"synthetic-reviewer"}}},
      {"source_id":"source_variation","status":"captured","claim_ids":["claim_variation"]},
      {"source_id":"source_compensation","status":"reported","claim_ids":["claim_compensation"]}
    ],
    "uncertainties":[{"uncertainty_id":"uncertainty_scope","topic":"Employer specificity","detail":"The role scope varies by employer and customer segment.","claim_ids":[]}],
    "questions":[{"question_id":"question_priority","prompt":"Which delivery versus product-feedback emphasis matters most?","reason":"It narrows later tailoring without requiring a resume.","claim_ids":[]}],
    "checks":[{"check_id":"check_source","kind":"source_integrity","result":"pass","detail":"Supplied capture and verification bytes match their declared refs.","claim_ids":["claim_delivery"]}]
  }
}
```

The actual output also includes role summary, responsibility, variation,
reusable-section, and full source metadata fields required by the standalone
schema. Digest placeholders above are explanatory only; the API rejects them
unless the supplied bytes produce the exact actual digest and size.

## Focused verification

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout06_research_packet.py` | `12 passed in 0.06s` |

The cases cover synthetic role-only FDE research; known and unknown salary;
reported/captured/independently-verified sources; malicious Markdown strings as
escaped data; missing or mismatched capture/verification bytes; stale document
digest; duplicate, foreign, and dangling claim relationships; malformed dates,
currency, ranges, and locators; and a later iteration producing new packet
bytes while original packet bytes remain unchanged.

## Deliberately open integration work

These candidate files are **not** added to `scout_source_files`, current
template inventory, capability manifests, schema registry, CLI, external
recording contracts, or native/journal persistence. The follow-up approval
lane must first decide the exact registered Run-output/sidecar artifact shape,
then inventory and approve the source, bind a real Run output under the writer
lock, and prove later tailoring/interview consumers select the exact preserved
research revision. This packet has no default eligibility, no claim of live
research, and no provider/network or private-user-data evidence.
