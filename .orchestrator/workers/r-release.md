# R — 0.1.8.1 release-pipeline hotfix + README corrections

## READ

- `/Users/kar/orca/workspaces/gigai/v0.1.8-uat.md` — U2, U4, U6, U7, U10, U13, U14, U15 (setup
  model-target naming, output limits, Scout install blocker, README mismatch, resume file-type
  limit, UI-not-in-wheel, active-gig selection, resume record wrapper).
- `/Users/kar/orca/workspaces/gigai/v0.1.9-first-things.txt` items 0 and 3 (release-pipeline race,
  release-notes-from-CHANGELOG intent).
- `.github/workflows/release.yml` (full file, pre-edit) — job graph and every `needs:` edge.
- `tools/release_check.py` (full file) — `assert_release_tag`, `verify_lockfile_project`, and the
  rest of the version-handling code, to confirm it is version-string-agnostic (no 3-part
  assumption) before deciding whether to touch it.
- `tests/behaviors/installed_release/test_release_check.py` (full file, pre-edit).
- `pyproject.toml`, `src/gigai/catalog.py`, `uv.lock` (own-package record) for current version.
- `src/gigai/private_records.py:90-113` (resume file-type/size limit), `src/gigai/project_binding.py`
  (`active_gig_id` location), `src/gigai/default_init.py:100-107` (`default_inventory` excludes
  Scout), `src/gigai/scout/template.py` (`scout_candidate_inventory`), `src/gigai/scout/
  proposal_execution.py:105-127` (model-target-name resolution, confirms U2's root cause is still
  live), `src/gigai/setup.py` (`--target-output-limit` flag), `src/gigai/adapters/factory.py` and
  test fixtures under `tests/behaviors/` for the `codex` endpoint / `codex_cli` adapter naming
  convention used in the setup workaround.
- Ran `gigai --help`, `gigai reference add --help`, `gigai record create --help`, `gigai setup
  --help`, `gigai catalog --help`, `gigai approve --help` against the installed build to verify
  every README claim before editing (not from memory).

## EXECUTED

- `.github/workflows/release.yml`:
  - `publish-pypi` now needs only `[preflight, publish-testpypi]` — no longer gated by
    `verify-testpypi`.
  - `github-release` now needs `[preflight, build, publish-pypi]` (was `verify-pypi`) — it fires
    immediately after the real PyPI publish, not after a clean-install check.
  - `github-release` gained a sparse checkout of `CHANGELOG.md` at the release tag, plus a step
    that extracts the `### <version>` section with a Python regex into `release-notes.md`; `gh
    release create` uses `--notes-file release-notes.md` when that section exists, and falls back
    to `--generate-notes` when it's empty/missing.
  - `verify-testpypi` and `verify-pypi` moved after `github-release`/`post-release-compatibility`
    in the file, renamed "(post-publish)", given `continue-on-error: true` (non-blocking), and each
    wraps `uv tool install` in a `until ... do ... sleep 15 ... done` loop bounded by `SECONDS`
    against a 600s (10 min) deadline, using `uv tool install --refresh` so a stale index cache
    can't mask the wait.
  - `verify-testpypi` still needs `[preflight, publish-testpypi]` (unchanged trigger point);
    `verify-pypi` now needs `[preflight, github-release]` instead of `publish-pypi` directly, so it
    runs after the release is already public rather than racing it.
  - `post-release-compatibility` unchanged (`needs: github-release`).
- `pyproject.toml`: version `0.1.8` → `0.1.8.1`.
- `uv.lock`: refreshed via `uv lock` (own-package record only changed: `0.1.8` → `0.1.8.1`).
- `src/gigai/catalog.py`: `CATALOG_REVISION = "v0.1.8"` → `"v0.1.8.1"` — confirmed by git-log this
  constant has tracked the package's release version exactly on every prior release (v0.1.7 →
  v0.1.8), and no test fixture pins the literal string, so bumping it is both consistent with
  precedent and safe.
- `tools/release_check.py`: **not changed**. `assert_release_tag` and `verify_lockfile_project` do
  a plain string comparison against `[project].version`/tag — there is no 3-part-version parsing or
  digit-count assumption anywhere in the file, so `0.1.8.1` / `v0.1.8.1` already work.
- `tests/behaviors/installed_release/test_release_check.py`: added
  `test_four_part_hotfix_version_is_a_valid_release_tag` to lock in that a 4-part version and its
  `vX.Y.Z.W` tag pass, and that a mismatched 3-part tag still fails, as executable proof rather
  than a manual-only claim.
- `CHANGELOG.md`: added a `### 0.1.8.1` section under "Released versions" — assess gets real
  posting text + a real prompt, tolerant model-output parsing + per-posting failure isolation with
  recorded causes, raw Exa/ATS responses stored per run, and release-pipeline fixes as landed;
  country filter / job-board-over-Exa preference, visa-sponsorship filter, and UI search/filters
  marked `_(pending: packet not yet landed)_` per instruction — coordinator to finalize/reorder
  against what actually ships.
- `README.md` — every change below verified against the installed CLI's own `--help` or source
  before writing (see READ):
  - New callout on **Setup**: the `codex_cli`/`ollama_local` target-name-literal requirement (U2)
    plus a `--target-output-limit codex_cli=4096` example (U4), marked as a 0.1.8.x workaround.
  - **Scout: install and approve** rewritten: removed the false "binding auto-materializes Scout"
    claim (U7); documents the actual working path — the installed interpreter running
    `initialize_defaults(inventory=scout_candidate_inventory())` via `uv tool dir`, then `gigai
    approve <proposal-id> --gig <gig-id>` (U6).
  - **Add a resume reference**: added the `.txt`/`.md`/`.markdown`-only, ≤1MB callout with
    `pdftotext`/`textutil` conversion commands (U10); the example command now uses `.txt` instead
    of the previously-false `.pdf` example.
  - New **Setting the active Gig** section: `active_gig_id` in `<target>/.gigai/project.toml`,
    with no CLI setter yet (U14).
  - New **Wrapping an imported resume for find-jobs** section: the `gigai record create --kind
    imported_reference --content-family g45_reference ...` wrapper requirement (U15).
  - **Start the API**: added one line stating it works from the installed package with no source
    checkout (previously ambiguous, sitting right before the checkout-only UI section).
  - **The UI is not in the wheel**: left as-is — already accurate (U13, checkout-only UI).
  - All four new/rewritten workaround sections are marked "0.1.8.x workaround; v0.1.9 replaces this
    with `gigai scout setup/run`" per instruction, kept to short paragraphs + one command block
    each.
  - **Status** section version bumped `v0.1.8` → `v0.1.8.1`.

## VERIFIED

```
uvx --from pyyaml python -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml'))"
  YAML parses OK; printed full job graph — publish-pypi/github-release edges confirmed unblocked
  by verify-pypi/verify-testpypi, which now depend on github-release / run non-blocking.

uv run --locked python tools/release_check.py verify-tag --tag v0.1.8.1
  verified release tag v0.1.8.1 for gigai 0.1.8.1

uv run --locked python tools/release_check.py verify-lockfile
  verified uv.lock project entry for gigai 0.1.8.1

uv run --locked --extra test pytest tests/behaviors/installed_release/test_release_check.py -q
  9 passed (was 8; +1 new four-part-version test)

uv run --locked --extra test pytest tests/behaviors/runtime_run_authority/test_g42_catalog.py -q
  5 passed (spot-check: CATALOG_REVISION bump doesn't break catalog tests)

rm -rf dist && uv build --no-sources
  Successfully built dist/gigai-0.1.8.1.tar.gz and dist/gigai-0.1.8.1-py3-none-any.whl

uv run --locked python tools/release_check.py verify-artifacts / write-checksums / verify-checksums
  all pass for gigai 0.1.8.1

uvx twine check dist/*.whl dist/*.tar.gz
  Checking dist/gigai-0.1.8.1-py3-none-any.whl: PASSED
  Checking dist/gigai-0.1.8.1.tar.gz: PASSED
  (dist/SHA256SUMS is not a distribution and correctly fails a literal `dist/*` twine glob; the
  workflow's own smoke-artifacts/verify jobs already scope to `dist/*.whl dist/*.tar.gz`, matched
  here)

dist/ removed after verification; no artifacts left behind.
```

grep-confirmed no other file hardcodes the old `0.1.8` version string (excluding `0.1.8.1` matches
and unrelated historical references in CHANGELOG/docs/spike reports, which are intentionally
retained as history).

## NOT DONE / handed to other workers

- CHANGELOG's pending items (country filter, job-board-over-Exa preference, visa-sponsorship
  filter, UI search/filters) are marked `_(pending: ...)_` — left for the coordinator to confirm
  which packets actually landed and finalize wording/order.
- Did not touch any Scout behavior code (assess prompt, posting-text plumbing, sponsorship/country
  filtering, raw-response storage) — that's the C0/other packets' scope; this packet only documents
  and ships the pipeline + version + docs.
- Did not run the full test suite (`make test`) — out of scope/time; ran the targeted
  `installed_release` and one catalog spot-check instead, per the packet's ACCEPTANCE criteria.
- No git add/commit — left for the coordinator, per instruction.
