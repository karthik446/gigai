# G00 Completion Audit

- Goal: [G00 — Standalone Contract Baseline](../../../goals/phase-1/G00-standalone-contract-baseline.md)
- Date: 2026-08-03
- Result: Pass
- Verification host: macOS 26.5.2, arm64
- Package version: 0.0.0
- uv version: 0.5.25

## Outcome

The standalone contract baseline satisfies G00. It is an installable
schema-resource package with frozen contract evidence, an honest pre-alpha
front door, a locked test environment, public governance files, canonical
Phase 1 development goals, and no placeholder CLI or runtime claim.

## Acceptance reconciliation

### 1. Root development workflow passes

Pass. The stricter locked form of the documented workflow completed from the
repository root for every matrix interpreter:

```text
uv sync --locked --extra test --python <python-version>
uv run --locked --python <python-version> pytest
```

Each environment resolved the committed `uv.lock`, installed the `test` extra,
and installed GigAI from the source tree.

### 2. The source suite discovers exactly 31 tests

Pass. Every matrix run collected exactly 31 tests from the two configured
research suites and completed with 31 passing tests.

### 3. Python 3.11, 3.12, and 3.13 pass

Pass.

| Interpreter | pytest | Collected | Result |
|---|---:|---:|---|
| CPython 3.11.9 | 9.1.1 | 31 | 31 passed |
| CPython 3.12.8 | 9.1.1 | 31 | 31 passed |
| CPython 3.13.1 | 9.1.1 | 31 | 31 passed |

The local matrix proves interpreter compatibility. The repository CI repeats
the locked matrix on macOS and Ubuntu after publication; hosted CI is a
post-push confirmation because no hosted run can precede the root commit.

### 4. The wheel contains only intended package resources

Pass. `uv build` produced both the source distribution and
`gigai-0.0.0-py3-none-any.whl`. The wheel contained 17 entries:

- two package marker modules;
- the schema README and SHA-256 manifest;
- exactly eight schema JSON files; and
- six standard distribution metadata and license files.

It contained no `research/`, test, development-goal, tool, cache, or build
source.

### 5. An isolated wheel installation discovers exactly eight schemas

Pass. The wheel was installed without dependencies into a fresh CPython 3.11.9
environment. `tools/verify_installed_schemas.py` exited zero and reported:

```text
verified 8 installed GigAI schemas
```

### 6. Frozen contract hashes are unchanged

Pass. `shasum -a 256 -c SHA256SUMS`, executed from
`src/gigai/schemas/`, reported `OK` for all eight schema files.

The canonical-vector digest is:

```text
14461cff88552b9ec1a86b02f47619208d8a50c952a73e43e09407d2b074587f
```

This matches the approved Phase 0 baseline.

### 7. The publication surface is sanitized

Pass. The final publication checks established:

- no private-key, OpenAI-style, GitHub-style, Slack-style, AWS-access-key, or
  credential-bearing database URL pattern;
- no personal home path, development-tree path, unrelated source-repository
  name, or prior workstation username;
- one intentional email, `local@gigai.invalid`, which is non-routable contract
  data;
- no virtual environment, cache, bytecode, build, distribution, egg-info,
  editor, or operating-system artifact;
- 33 publishable Markdown files after adding this audit and its terminal
  handoff, with zero missing local links; and
- `.codex/` excluded from publication by `.gitignore` and omitted from the
  publishable count.

### 8. Public maturity claims are accurate

Pass. The root README identifies GigAI as contract-first pre-alpha software,
states that the installed package currently contains schema resources only,
and explicitly states that no working CLI, scheduler, or execution runtime is
shipped.

## Bootstrap check

Pass. The canonical Phase 1 graph contains exactly G00 through G10. Each goal
has an outcome, in-scope and out-of-scope boundaries, acceptance criteria,
verification evidence, and a stop boundary. The dependency declarations match
the V14 plan, including the G03, G07, and G08 joins, and all local links resolve.

## Additional structural checks

- `uv lock --check`: pass; 24 packages resolved from the committed lockfile.
- `pyproject.toml`: parsed successfully with Python `tomllib`.
- `.github/workflows/ci.yml`: parsed successfully as YAML.
- Runtime dependencies remain empty, and no console script is registered.
- Click remains test-only until G02 introduces the minimal real CLI and moves
  Click into runtime dependencies in that same change.

## Completion decision

G00 is complete. No acceptance criterion is waived, no frozen contract byte is
changed, and no product behavior is claimed beyond the packaged schema
resources. The root commit containing this audit is the G00 completion commit.

Hosted CI on that exact commit remains the publication confirmation gate. G01
and G02 must not begin until the pushed root commit passes that workflow.
