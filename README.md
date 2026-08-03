# GigAI

GigAI is a contract-first exploration of a local, user-controlled runtime for
turning goals into reviewable, finite execution graphs.

> **Status: pre-alpha contracts and executable research.** This repository does
> not yet contain a GigAI CLI, scheduler, journal, or production runtime. The
> installable Python distribution currently exposes the frozen serialized
> contracts plus canonical identity primitives for JSON, owned text, imported
> bytes, entity IDs, and explicit version selection. Commands described in the
> design documents are planned interfaces, not implemented product behavior.

## Why this exists

Agent systems often blur three different decisions: what should happen, who
approved it, and what is authorized to execute. GigAI keeps them separate:

~~~text
create or improve -> non-executable proposal
operator approval -> immutable Gig version
run               -> authority for one execution
~~~

A Gig is a finite, user-owned Goal Graph. Its private workpad is authoritative;
indexes are rebuildable; provider calls are explicit exposure; and imported user
content is hashed as exact bytes rather than silently normalized.

## What is implemented

- Eight frozen JSON Schema Draft 2020-12 contracts.
- One production implementation of restricted canonical JSON, owned-text
  bytes, exact imported-byte digests, prefixed UUIDv4 IDs, and explicit version
  selection.
- Exact-byte golden vectors that the production implementation must preserve.
- Executable evidence for schema instances, graph semantics, canonicalization,
  concurrent journal sequencing, and bounded Phase 0 feasibility questions.
- A source suite containing 90 tests: 59 production G01 tests, 14 contract
  tests, and 17 Phase 0 tests.
- A wheel-level verifier that proves the exact eight schema resources and their
  SHA-256 identities survived packaging.

The [V14 implementation plan](docs/architecture/v14-implementation-plan.md)
defines the intended product. The [command sheet](docs/reference/command-sheet.md)
is a design contract, not evidence of a working command.

## Verify the source evidence

Python 3.11 or newer is required. Compatibility is continuously tested rather
than capped without evidence; see
[ADR 0001](docs/adr/0001-python-version-range.md).

~~~bash
uv sync --extra test
uv run pytest
~~~

Expected result:

~~~text
90 passed
~~~

## Canonical identity API

The shipped [gigai.canonical](src/gigai/canonical.py) module is the only product
implementation of canonical rendering and SHA-256 identity. Its API keeps
owned and imported bytes visibly separate:

~~~python
from gigai.canonical import (
    canonical_json_bytes,
    canonical_json_digest,
    canonicalize_owned_text,
    digest_imported_bytes,
)
~~~

`canonicalize_owned_text()` applies GigAI's UTF-8/LF/final-newline contract.
`digest_imported_bytes()` accepts bytes only and hashes them exactly as read;
it never performs implicit decoding, encoding, or normalization. The same
module owns canonical front matter, prefixed UUIDv4 IDs, and explicit active or
requested Gig-version resolution.

## Verify the built artifact

The wheel verifier is deliberately separate from the source test suite. Tests
are not shipped in the wheel.

~~~bash
uv build
uv venv --python 3.11 .wheel-venv
uv pip install --python .wheel-venv/bin/python --no-deps \
  dist/gigai-0.0.0-py3-none-any.whl
.wheel-venv/bin/python tools/verify_installed_schemas.py
.wheel-venv/bin/python tools/verify_installed_canonical.py
~~~

Expected result:

~~~text
verified 8 installed GigAI schemas
verified installed GigAI canonical identity API
~~~

The lockfile is committed. CI and release verification use uv with
`--locked`. A standards-based fallback remains available:

~~~bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[test]"
python -m pytest
~~~

## Repository map

| Path | Purpose | Shipped |
|---|---|---|
| src/gigai/canonical.py | Sole canonical byte, digest, ID, and version implementation | yes |
| src/gigai/schemas/ | Single canonical source for frozen serialized contracts | yes |
| research/contract_spike/ | Executable contract and concurrency proof | no |
| research/phase0_spike/ | Bounded feasibility evidence | no |
| research/experiments/ | Supporting experiments and sanitized fixtures | no |
| docs/architecture/ | Approved product and authority design | no |
| docs/reference/ | Planned operator-facing command contract | no |
| docs/research/ | Research records and maturity boundaries | no |
| tools/ | Maintained repository and artifact verification | no |

The [schema README](src/gigai/schemas/README.md) documents canonical bytes,
identifiers, version selection, journal ordering, and validation beyond JSON
Schema.

## Non-negotiable boundaries

- Proposals do not execute and cannot approve themselves.
- Approval creates an immutable version and starts no run.
- A run invocation grants authority for exactly one run.
- Workpads remain local and are never published by GigAI.
- SQLite is rebuildable; committed workpad records are authoritative.
- GigAI has no hosted history, background sync, or telemetry service.
- Provider and tool calls are explicit user-authorized exposure.

## Roadmap

The next product work follows the canonical [Phase 1 G00-G10 development goal
graph](docs/development/goals/phase-1/README.md), derived from the V14 plan.
Product modules enter src/gigai/ only through an explicit implementation goal
and acceptance tests. A real CLI will be introduced when its behavior exists;
this repository will not ship a placeholder command for appearances.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) before changing contracts or research
evidence. Report vulnerabilities according to [SECURITY.md](SECURITY.md).
Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Licensed under the [Apache License 2.0](LICENSE).
