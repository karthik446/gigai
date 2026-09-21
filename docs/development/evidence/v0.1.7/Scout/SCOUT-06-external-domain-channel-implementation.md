# SCOUT-06 external typed domain channel implementation

**Date:** 2026-09-10  
**Scope:** bounded generic external-recording transport only; no provider,
network, source execution, approval, activation, or user-private state.

## Delivered

The external recording service now has a deliberate v1/v2 reader dispatch and
two additive service entry points, `checkpoint_v2` and `submit_v2`. Existing
v1 `checkpoint`/`submit` behavior and the four-field generic sidecar remain
unchanged. Version 2 requires every output to carry a typed `domain_sidecar`
and a bounded `supporting_artifacts` list; submit authenticates the exact
committed tuple and revalidates it without re-publishing checkpoint artifacts.

The v2 resources are:

* `src/gigai/schemas/external-recording-invocation-v2.schema.json`
* `src/gigai/schemas/external-recording-checkpoint-v2.schema.json`
* `src/gigai/schemas/external-recording-receipt-v2.schema.json`

The transport sidecar is a closed `{schema_id, value}` envelope. A v2
checkpoint artifact is a strict one-of: a typed domain output carries the
domain/supporting fields, while a completion-check artifact deliberately keeps
the v1 `{kind, markdown, sidecar}` shape. This allows one checkpoint to carry
both a research output and its completion check; v2 receipts retain only
domain-output refs in `outputs` and continue to reference checks separately.
The sealed
v2 output contract's `domains[kind]` is also closed to
`{schema_id, schema_ref, validator_id, validator_source_ref}`; both resource
refs must be committed, exact-digest workpad artifacts naming the packaged
research schema/source before dispatch. Supporting
items carry a safe logical ID, media type, canonical base64 bytes, exact
`sha256:` digest, and decoded size. Checkpoint publication derives paths only
from the authenticated Run/checkpoint/ordinal and safe IDs, and publishes
Markdown, generic sidecar, domain sidecar, supporting bytes, and checkpoint
record in one existing journal transition. The persisted output and receipt
shapes carry exact refs for all of those files.

## Fixed validator boundary

The only recognized domain ID is
`urn:gigai:scout:research-packet:2`, dispatched through a fixed import of
`gigai.scout_research:validate_research_domain`; there is no caller callback,
editable-Gig import, subprocess, network lookup, or plugin registry. The
bridge now exists as a separate Terra-owned packaged module; this lane checks
its fixed resource identity and invokes it only after transport checks. Unknown
IDs return `external_domain_unsupported`; malformed IDs, base64, media,
duplicate logical IDs, sizes, or digests return `external_domain_invalid`
before any journal write, and bridge refusals are mapped to redacted typed
domain errors. Root was asked to provide the bridge with this exact context:

```text
validate_research_domain(
    *, value, markdown, supporting, run_id, project_id, gig_id,
    gig_version, graph_selector, graph_id, graph_version, selected_inputs
) -> None
```

The bridge must retain the complete candidate `scout-research-sidecar:2`,
preserve exact selected-input identity (without guessing a role or relabeling
G45 input), and map each captured/verified logical ID one-to-one to a supplied
supporting byte. The candidate source inventory/Plan-v2 caller integration
remains separate and is not claimed as shipped here.

## Root integration already performed

After the concrete schemas were written, root registered all three v2 resource
names in `validators.py`, central SHA256/golden data, and the installed
resource verifier (61 resources reported by root). No local test monkeypatches
or registry edits were used. The new schemas deliberately reference the
existing common/v1 identity definitions where their shape is unchanged.

## Focused verification

Exact commands and results:

```text
ruff check src/gigai/external_recording.py tests/test_scout06_external_domain_channel.py
All checks passed!

python -m py_compile src/gigai/external_recording.py
passed (no output)

.venv/bin/pytest -q tests/test_scout06_external_domain_channel.py
9 passed in 0.10s

.venv/bin/pytest -q tests/test_scout04_external_recording.py
15 passed in 72.21s (0:01:12)
```

The focused tests cover complete v2 invocation/checkpoint/receipt schema
shapes, strict downgrade refusal, canonical base64 and digest/size checks,
unknown-domain refusal, fixed-bridge malformed-domain refusal, safe derived
refs, unsupported protocol versions, and the unchanged v1 four-field sidecar.
A positive journaled research packet and later submit are intentionally not
claimed because the source-inventory/Plan-v2 caller successor is not yet
integrated. The v1 regression lane was run before the final v2-only bridge
constant/path checks; those changes are isolated to v2 dispatch and the
existing v1 suite remains unchanged by construction.

## Remaining gates

1. The reviewed composite research output contract must pin the exact domain
   schema artifact ref and validator identity; v2 recording refuses a plain
   v1 field-list contract rather than downgrading required evidence.
3. A later consumer needs a distinct exact immutable research-revision input
   variant; no current reader selects “latest” or copies output bytes.
4. Public `external_cli` wiring, source inventory successor approval, and any
   fresh-session research integration remain separate lanes. There is no claim
   of live research, provider execution, semantic source verification, or
   privacy beyond this local journal transport boundary.
