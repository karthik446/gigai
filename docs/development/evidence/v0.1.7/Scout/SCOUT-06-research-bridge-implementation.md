# SCOUT-06 candidate research bridge implementation

Date: 2026-09-10. This bounded implementation is candidate-only: it does not
register a schema, alter an external Plan, execute a provider, access a user
workpad, or claim journal acceptance.

## Delivered candidate contract

The previously synthetic selected-input projection has been replaced in the
candidate renderer with exact sealed input envelopes. The new sidecar is:

* schema ID: `urn:gigai:scout:research-packet:2`;
* payload version: `scout-research-sidecar:2`;
* fixed validator ID: `scout-role-research:2`;
* fixed validator source: `gigai.scout_research:validate_research_domain`;
* schema resource:
  `data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.schema.json`.

Its schema SHA-256 is
`1b23fbae6e85576b37876c2bad6cd77b191e75bc490a791b8b5f90b5db26d0c3`.
Root/Luna must use those literal constants when they add the separate,
versioned Plan input and generic domain registration.

The required input is exactly one closed Plan input:

```json
{
  "family": "role_request",
  "role_title": "Forward Deployed Engineer",
  "role_context": "B2B implementation work"
}
```

`role_title` is non-empty and at most 300 characters; `role_context` is null
or non-empty and at most 4096 characters. The candidate keeps that complete
object in `selected_inputs`; it does not manufacture a `record_id`, relabel a
posting, or assert that the caller is a confirmed user. The caller's actor and
origin remain in the Plan invocation authenticated by the later external
recording integration. `research.role_title` must match the sealed request
title exactly.

Strict supported optional inputs retain their complete normalized G45/native
envelopes: direct `g45_reference` and `g45_run_input`, G45 Scout wrappers,
and currently admitted native `profile_preferences`/`experience_qa` wrappers.
There is no role projection for a posting, preference, experience, source, or
conversation. The v2 packet also binds project, Gig, Gig version, graph
selector/ID/version, and Run ID in `origin`.

## Fixed bridge

`src/gigai/scout_research.py` exposes this frozen caller:

```python
validate_research_domain(
    *, value, markdown: bytes, supporting,
    run_id, project_id, gig_id, gig_version,
    graph_id, graph_selector, graph_version, selected_inputs,
) -> None
```

It imports only the literal packaged candidate renderer module, loads only the
literal packaged schema resource, and never imports a Gig/user path or the
external recorder. It verifies the full v2 schema, exact trusted origin and
canonical selected-input list, then rebuilds the packet deterministically and
requires byte-for-byte Markdown and canonical sidecar agreement. It also
requires supporting-byte IDs to be exactly the source capture/verification IDs
referenced by the packet—no missing or unused bytes.

All bridge failures are content-free `ScoutResearchError` values. The bridge
validates supplied evidence structure and bytes, not the truth of external
research, verification, or an actor report.

## Focused verification

```text
.venv/bin/pytest -q tests/test_scout06_research_packet.py tests/test_scout06_research_bridge.py
56 passed in 0.16s

uv run ruff check src/gigai/scout_research.py \
  src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.py \
  tests/test_scout06_research_packet.py tests/test_scout06_research_bridge.py
All checks passed!

.venv/bin/python -m py_compile src/gigai/scout_research.py \
  src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.py
passed
```

The preserved/adapted 48 pure-packet cases cover deterministic role-only
research, unknown compensation, safe Markdown, capture/verification evidence,
relationship integrity, malformed nested inputs, and canonical revalidation.
The eight bridge cases add exact role-only acceptance, role-context/title and
origin mismatch refusal, unsupported selected-input refusal, required
supporting-byte coverage, and preservation/mismatch detection for an optional
full G45 envelope plus an optional full native envelope.

## Remaining integration gates

Root owns the external input variant, schema registration/hash inventories,
Plan/start revalidation, generic fixed-validator dispatch, source inventory,
and all journaled checkpoint/submit behavior. This source remains outside the
Scout inventory and has not been provider-executed, security-reviewed,
approved in a Graph Set, or activated. No native CRUD or core external schema
was changed to create synthetic role evidence.
