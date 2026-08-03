# GigAI

GigAI is a contract-first exploration of a local, user-controlled runtime for
turning goals into reviewable, finite execution graphs.

> **Status: pre-alpha contracts and executable research.** The installed
> distribution implements local `setup` and offline `doctor` alongside help
> and package-metadata version output. It does not yet bind targets, create Gig
> workpads, run a scheduler, or provide a production runtime. The package also
> exposes frozen serialized contracts and canonical identity primitives.
> Commands beyond the implemented surface remain planned interfaces, not
> product behavior.

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
- One installed `gigai` command exposing truthful package help and version,
  idempotent local setup, and structured offline diagnostics—with no later
  command stubs.
- A strict versioned `config.toml` containing structured editor argv,
  credential references rather than values, one deterministic offline
  endpoint and target, one default profile, and the authoritative workpad
  mount.
- An immutable content-addressed standard pack and fixture-backed offline
  adapter. Setup and doctor prove atomic replacement and real two-process
  exclusion on the configured mount without network or token use.
- A reusable black-box scenario harness for isolated homes, target and workpad
  manifests, Git state, subprocess recording, and fail-closed effect checks.
- Exact-byte golden vectors that the production implementation must preserve.
- Executable evidence for schema instances, graph semantics, canonicalization,
  concurrent journal sequencing, and bounded Phase 0 feasibility questions.
- A source suite containing 128 tests: 59 G01 production tests, 19 G02 CLI and
  harness tests, 19 G03 setup/diagnostic tests, 14 contract tests, and 17 Phase
  0 tests.
- A wheel-level verifier that proves the exact eight schema resources and their
  SHA-256 identities survived packaging.

The [V14 implementation plan](docs/architecture/v14-implementation-plan.md)
defines the intended product. The [command sheet](docs/reference/command-sheet.md)
contains both the implemented surface and planned design; this README and
installed help state what works today.

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
128 passed
~~~

## Configure and diagnose this installation

GigAI v1 currently supports macOS and Linux. Interactive setup reviews the
selected machine-state directory, authoritative workpad mount, and structured
editor command before applying anything:

~~~bash
gigai setup
gigai doctor
gigai doctor --json
~~~

For automation, `gigai setup --non-interactive` accepts explicit `--home`,
`--workpad-root`, `--editor`, and repeated `--editor-arg` values. Credential
configuration accepts references such as
`--credential-ref provider=environment:PROVIDER_API_TOKEN`; it never accepts
or copies the referenced value. Run `gigai setup --help` for the complete
current contract.

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
uv pip install --python .wheel-venv/bin/python \
  dist/gigai-0.0.0-py3-none-any.whl
.wheel-venv/bin/python tools/verify_installed_schemas.py
.wheel-venv/bin/python tools/verify_installed_canonical.py
.wheel-venv/bin/python tools/verify_installed_cli.py
.wheel-venv/bin/python tools/verify_installed_g03.py
~~~

Expected result:

~~~text
verified 8 installed GigAI schemas
verified installed GigAI canonical identity API
verified installed GigAI CLI: help, version, setup, and doctor only
verified installed GigAI G03 setup, idempotency, pack, and offline doctor
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
| src/gigai/cli.py | Installed help, version, setup, and doctor surface | yes |
| src/gigai/canonical.py | Sole canonical byte, digest, ID, and version implementation | yes |
| src/gigai/config.py | Strict versioned typed machine configuration | yes |
| src/gigai/setup.py | Idempotent setup orchestration and mount preflight | yes |
| src/gigai/diagnostics.py | Structured offline installation and mount checks | yes |
| src/gigai/adapters/ | Deterministic fixture-backed offline adapter | yes |
| src/gigai/data/ | Immutable built-in standard-pack resources | yes |
| src/gigai/schemas/ | Single canonical source for frozen serialized contracts | yes |
| tests/scenarios/ | Installed-process isolation and observation harness | no |
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
and acceptance tests. The installed entry point exposes only behavior that
exists; planned command names are not shipped as placeholders for appearances.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) before changing contracts or research
evidence. Report vulnerabilities according to [SECURITY.md](SECURITY.md).
Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Licensed under the [Apache License 2.0](LICENSE).
