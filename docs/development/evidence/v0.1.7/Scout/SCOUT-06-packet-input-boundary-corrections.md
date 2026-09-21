# SCOUT-06 candidate research packet input-boundary corrections

Date: 2026-09-10. This bounded correction hardens only the pure candidate
research renderer's malformed-JSON boundary; packet/sidecar shape, valid
rendered bytes, schema, source inventory, and persistence wiring are unchanged.

## Change

`research.py` now verifies primitive types before every adjacent set operation,
enum membership check, and regular-expression use reachable from supplied
research JSON.  This covers nested claim/source/domain/compensation ID lists,
output-role elements, verification actor/method, source kind/status,
compensation status/pay period, selected-input kind, and artifact-byte keys.
The source-locator parser catches only `ValueError` from `urlsplit` and turns
it into the existing typed `ResearchPacketError`; no broad exception handling
was added.

Malformed values refuse with `research_packet_invalid` and static,
content-free diagnostics rather than leaking a raw `TypeError` or URL parser
message.  The renderer remains pure and in-memory: it performs no disk reads,
network access, provider execution, journal/state mutation, CLI work, or new
schema/persistence action.

## Verification

| Command | Result |
| --- | --- |
| `.venv/bin/pytest -q tests/test_scout06_research_packet.py --tb=short` | `48 passed in 0.15s` |
| `uv run ruff check src/gigai/data/scout/tools/cap_00000000-0000-4000-8000-000000000071/research.py tests/test_scout06_research_packet.py` | passed |
| `.venv/bin/python -m py_compile .../research.py` and scoped `git diff --check` | passed |

The 13-case parameterized regression covers the four reproduced failures plus
unhashable nested ID-list members, enum-membership values of the wrong type,
an integer artifact key, and malformed `https://[broken` input.  The original
35 packet tests remain part of the same run and confirm valid role-only
rendering, sidecar/document digests, source/capture/verification handling,
safe Markdown data rendering, strict revalidation, compensation handling, and
iteration immutability retain their prior behavior.

## Remaining scope

This candidate tool remains outside bundled source inventory and all durable
Run/output/source persistence paths.  The correction does not ship or activate
the tool, perform external research, attest source truth, or complete SCOUT-06.
