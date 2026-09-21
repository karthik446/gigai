# SCOUT-R7 live corrections implementation — 2026-09-21

Status: **owned corrections implemented and bounded verification complete; no
release, publication, provider-quality, or UAT acceptance claimed.** The
historical comparison workpad remained unchanged; all provider/model inference
and network calls were avoided.

## Scope and ownership

Implemented only the prepared overlays in the owned runtime-comparison/setup
surface:

- `src/gigai/runtime_comparison.py`: authenticate raw source bytes against the
  pinned approval head, parse/validate those bytes, then bind their canonical
  digest to the published pack, authority graph/goal, case IDs, and consent;
  retain historical packs without `output_contract`; compose the public
  output contract into both setup prompts and persist that exact prompt in the
  selected reference/input artifact.
- `src/gigai/cli.py`: add explicit
  `NAME=ollama_local:http://127.0.0.1:PORT` setup syntax and
  `NAME=ENDPOINT:MODEL@sha256:<64 lowercase hex>` model syntax; require the
  digest for local targets while retaining existing remote endpoint parsing and
  HTTPS checks.
- `src/gigai/schemas/runtime-evaluation-pack.schema.json` and
  `src/gigai/data/runtime-comparison-pack-v1.json`: add the versioned public
  criterion-descriptor contract without expected status, gold evidence, or
  answer leakage; update the pack output-contract version to
  `r7-output-contract:1`.
- `src/gigai/schemas/SHA256SUMS` and
  `tools/verify_installed_schemas.py`: update the schema resource identity.
- `tests/test_runtime_comparison.py`: production-path tests for canonical
  reload, authority negatives, captured prompts, public setup roundtrip, and
  parser negatives.

`capability_review.py` and `test_scout05_capability_review.py` were not touched.
No historical workpad, approved source bytes, global config, v0.1.8 material,
commit, tag, push, or publication was changed.

## Verification

All commands were run from the checkout unless noted:

```text
rtk .venv/bin/python -m py_compile src/gigai/runtime_comparison.py src/gigai/cli.py tests/test_runtime_comparison.py
  passed

rtk .venv/bin/pytest -q tests/test_runtime_comparison.py
  12 passed in 38.35s

rtk .venv/bin/python -m json.tool src/gigai/schemas/runtime-evaluation-pack.schema.json
rtk .venv/bin/python -m json.tool src/gigai/data/runtime-comparison-pack-v1.json
  both passed

rtk .venv/bin/python tools/verify_installed_schemas.py
  verified 82 installed GigAI schemas

rtk ruff check src/gigai/runtime_comparison.py src/gigai/cli.py tests/test_runtime_comparison.py tools/verify_installed_schemas.py
  passed

rtk git diff --check -- <owned paths>
  passed
```

The focused tests invoke actual `run_comparison`, `show_comparison`,
`load_evaluation_pack`, production CLI parser functions, and the public Click
`setup` command. The comparison fixture uses pretty-printed journal source
bytes while retaining canonical pack identity; tests also reload canonical,
pretty, and whitespace variants. Captured prompts contain the public shape and
criterion descriptors, but no `expected` or `criterion_evidence` fields.

## Disposable installed-wheel proof

Fresh disposable directory:
`/private/tmp/gigai-r7-live-corrections-0F5F5u/`.

- Built with `rtk uv build --out-dir .../dist`.
- Installed the wheel with dependencies into `.../venv`; uv installed 17
  packages including GigAI 0.1.7, Click, httpx, and jsonschema.
- Isolated import command used `python -I` from outside the checkout and
  resolved `gigai` to
  `.../venv/lib/python3.11/site-packages/gigai/__init__.py`.
- Wheel SHA-256:
  `fbca831bc46c6992ee813d9c37ce90802a83073440777897a2c7b84ffc8e2fe9`.
- Sdist SHA-256:
  `bd15ce027cbcf357d2a84627910ac22e5f26827bf5fb7eb586c53d2ce38c4c7a`.
- The installed bundled pack reports `r7-output-contract:1`.

The existing synthetic comparison was copied to the disposable
`read-workpads` tree, with its registry locator adjusted only in the disposable
copied registry. Installed public commands then succeeded against that copy:

```text
gigai comparison show comparison_1455227f-fc9d-4893-87d4-cc86cb368422 \
  --gig gig_a5ed1db7-715c-4329-86f4-987477806008 ... --json
gigai comparison status comparison_1455227f-fc9d-4893-87d4-cc86cb368422 \
  --gig gig_a5ed1db7-715c-4329-86f4-987477806008 ... --json
```

`show` returned the canonical pack identity
`sha256:fe708376b8f2b4b1f8045f295574e8a7b02e1d7138769e77492e1b5a6c4ecc29`;
`status` returned the same comparison with two attempts. SHA-256 manifests of
all files under the copied workpad were identical before and after both reads.
An initial physically read-only mount was refused by the existing authority
mount guard (`configured workpad mount is read-only`); the final proof used a
disposable writable mount with immutable content comparison before/after, and
the original workpad was never opened for writes.

## Unproven gates and failures

- No provider/model invocation, readiness probe, download, daemon start, live
  replay, private data, or new comparison was run.
- No full source suite was rerun. The previously recorded release matrix had
  its unrelated capability-review refusal side effect; that parallel owner
  remains responsible for it.
- No cross-platform, Debian/Docker, exact-tag CI, publication, release, or
  user-UAT gate is proven here.
- The approved Gig-owned pack contract/source must still be updated through its
  normal authority path for a new public comparison. This implementation does
  not rewrite the historical approved bytes or retroactively turn the old live
  provider outputs into acceptance evidence.

## Source hashes after implementation

```text
src/gigai/runtime_comparison.py                         91873a67b07123eef1323db376d75f084d4623715b411c3e039f375098ba624c
src/gigai/cli.py                                        63c6989708c1a75ed4f2546e66025626b3e7cba6d341ba1241c4a29637c77f8b
src/gigai/schemas/runtime-evaluation-pack.schema.json   c43f43f8c1536c37d8bfcc6124709621a2e0c069da445c595cb0f12e7febe87c
src/gigai/data/runtime-comparison-pack-v1.json          3624398d5aeaff93f640c60aef88aa1ece26a0d8319e47f916dfce6429a304bd
```
