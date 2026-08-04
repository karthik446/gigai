# GigAI

GigAI is a contract-first exploration of a local, user-controlled runtime for
turning goals into reviewable, finite execution graphs.

> **Status: pre-alpha contracts and executable research.** The installed
> distribution implements local `setup`, offline `doctor`, and idempotent
> target `init`, plus read/open operations for already-registered workpads,
> alongside help and package-metadata version output. It does not yet expose a
> Gig creation or activation command, run a scheduler, or provide a production runtime.
> The package also exposes versioned pre-release serialized contracts and
> canonical identity primitives. Their immutable release regime begins at
> GigAI's deliberately declared first public release.
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

- Eight versioned JSON Schema Draft 2020-12 contracts.
- One production implementation of restricted canonical JSON, owned-text
  bytes, exact imported-byte digests, prefixed UUIDv4 IDs, and explicit version
  selection.
- One installed `gigai` command exposing truthful package help and version,
  idempotent local setup, structured offline diagnostics, and target
  binding—with no later command stubs.
- A strict versioned `config.toml` containing structured editor argv,
  credential references rather than values, one deterministic offline
  endpoint and target, one default profile, and the authoritative workpad
  mount.
- An immutable content-addressed standard pack and fixture-backed offline
  adapter. Setup and doctor prove atomic replacement and real two-process
  exclusion on the configured mount without network or token use.
- A strict path-free `.gigai/project.toml` binding for Git targets, one
  idempotent `/.gigai/` local exclude entry, and a versioned private
  `registry.sqlite` that owns canonical target and workpad locators. The exact
  v1 registry migrates transactionally to v2 after retaining a private,
  validated v1 backup. Explicit non-Git targets are registry-only and receive
  no implicit target tree.
- An internal caller-ID-only workpad primitive that atomically publishes an
  empty, unborn, local-only Git repository under the configured mount. It sets
  repository-local identity and ownership markers, configures no remote, and
  creates no Gig proposal, semantic file, commit, or active selection.
- Installed `workpad path` and `open` commands for existing registered
  workpads. Their no-ID forms fail with `no_active_gig` until a later lifecycle
  goal creates and activates a Gig; no public provisioning shortcut exists.
- A reusable black-box scenario harness for isolated homes, target and workpad
  manifests, Git state, subprocess recording, and fail-closed effect checks.
- Exact-byte golden vectors that the production implementation must preserve.
- Executable evidence for schema instances, graph semantics, canonicalization,
  concurrent journal sequencing, and bounded Phase 0 feasibility questions.
- A source suite containing 206 tests: 59 G01 production tests, 19 G02 CLI and
  harness tests, 19 G03 setup/diagnostic tests, 35 G04 binding tests, 14
  contract tests, 17 Phase 0 tests, and 43 G05 migration, workpad, and installed
  scenario tests.
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
206 passed
~~~

## Configure, diagnose, bind, and inspect existing workpads

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

After setup, bind the current Git repository without changing tracked content:

~~~bash
cd /path/to/repository
gigai init
gigai init --json
~~~

Git init writes only the ignored `.gigai/project.toml` binding and the
user-local registry, while adding one `/.gigai/` entry to
`.git/info/exclude`. Existing dirty status and file bytes are preserved. An
explicit non-Git directory is supported without creating `.gigai` inside it:

~~~bash
gigai init --target /path/to/non-git-directory
~~~

`init` does not create a Gig, workpad, journal, or remote. G05 provides the
internal substrate that a later lifecycle goal will call with an already
allocated Gig ID; there is deliberately no public provision or activate
command yet.

For a workpad that is already registered by product code, the current read/open
surface is:

~~~bash
gigai workpad path <gig-id>
gigai open <gig-id>
gigai open <gig-id> --with-target
gigai open --target
~~~

Running `gigai workpad path` or `gigai open` without an ID currently returns
the typed `no_active_gig` failure. G08 will own the first lifecycle that may
allocate an ID, call the private provisioner, and activate that exact Gig.

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
.wheel-venv/bin/python tools/verify_installed_g04.py
.wheel-venv/bin/python tools/verify_installed_g05.py
~~~

Expected result:

~~~text
verified 8 installed GigAI schemas
verified installed GigAI canonical identity API
verified installed GigAI CLI: help, version, setup, doctor, init, workpad path, and open only
verified installed GigAI G03 setup, idempotency, pack, and offline doctor
verified installed GigAI G04 Git and non-Git target binding
verified installed GigAI G05 private unborn workpad and read/open surface
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
| src/gigai/cli.py | Installed help, version, setup, doctor, init, workpad path, and open surface | yes |
| src/gigai/canonical.py | Sole canonical byte, digest, ID, and version implementation | yes |
| src/gigai/config.py | Strict versioned typed machine configuration | yes |
| src/gigai/setup.py | Idempotent setup orchestration and mount preflight | yes |
| src/gigai/diagnostics.py | Structured offline installation and mount checks | yes |
| src/gigai/project_binding.py | Strict path-free Git target binding contract | yes |
| src/gigai/registry.py | Strict v2 private project/workpad registry and v1 migration | yes |
| src/gigai/target_binding.py | Git/non-Git identity, reconciliation, and init effects | yes |
| src/gigai/workpad.py | Caller-ID-only private Git substrate, resolution, active authority, and editor boundary | yes |
| src/gigai/adapters/ | Deterministic fixture-backed offline adapter | yes |
| src/gigai/data/ | Immutable built-in standard-pack resources | yes |
| src/gigai/schemas/ | Single canonical source for versioned serialized contracts | yes |
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

The next product work follows the canonical [Phase 1 G00-G11 development goal
graph](docs/development/goals/phase-1/README.md), derived from the V14 plan.
Product modules enter src/gigai/ only through an explicit implementation goal
and acceptance tests. The installed entry point exposes only behavior that
exists; planned command names are not shipped as placeholders for appearances.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md) before changing contracts or research
evidence. Report vulnerabilities according to [SECURITY.md](SECURITY.md).
Participation is governed by [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Licensed under the [Apache License 2.0](LICENSE).
