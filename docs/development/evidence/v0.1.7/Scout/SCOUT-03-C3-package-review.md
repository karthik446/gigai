# SCOUT-03 C3 — Independent package/export privacy guard review

**Reviewer role:** Fresh independent read-only review. No source, test, schema,
or `.gigai` edits were made; no commits, providers, or private Gig state were
touched. One disposable in-process probe of `private_package_provenance` was run
(clearly marked below); no pytest file or suite was executed. The coordinator
owns rerunning the 15 reported focused cases.

**Inputs read:** `SCOUT-03-C3-package-guard.md`; `SCOUT-03-C3-integration-notes.md`
(§"Clean package/export guard"); `SCOUT-00-contract-amendments.md` §7 (supporting
code, customization, portability) and §8 matrix (A11); `SCOUT-03-caller-audit.md`
(frozen v2 user-owned layout / "single ownership boundary" list and contract
obligations). **Source read:** `src/gigai/package_privacy.py`,
`src/gigai/package.py` (full `inspect_package`, `export_package`,
`install_package`, `_initialize_and_prepare`, `_validate_adoption_tree`,
`_copy_package`, `_reject_symlink_components`, `_write_atomic`),
`src/gigai/catalog.py` (`package_bytes`, `materialize_catalog_package`, `CATALOG`),
`src/gigai/scout_template.py`, `src/gigai/validators.py`
(`validate_serialized_contract`, `SCHEMA_NAMES`), `src/gigai/canonical.py`
(`parse_json_bytes`, exception hierarchy), `tests/test_scout03_package_privacy.py`,
`src/gigai/schemas/gig-package.schema.json` and the 11 private schemas.

---

## 1. Bounded acceptance (verified from source; one item probe-confirmed)

The delivered boundary does what the C3 evidence claims, within the honestly
stated narrow-provenance scope, with the single exception in §2.

### 1.1 Recognized private provenance cannot cross the package edge

`inspect_package` (`package.py:216-220`) calls `private_package_provenance` on
every inventoried non-manifest file **after** all pre-existing checks (symlink,
dir, file-type, `hooks|install|scripts`, executable bit, size cap) and **before**
appending to `expected_files` / computing `content_digest`. A positive result
raises `PackageError(code="private_provenance_refused")`. Because this runs
inside the same inventory loop that later enforces `files != expected_files`
(`content_inventory_mismatch`) and `content_digest` equality, a **manifest whose
hashes and inventory are entirely correct is still refused** — the guard fires
before the hash comparison is even reached. Source-confirmed.

Guard recognizers (`package_privacy.py:53-73`):

- `_PRIVATE_EXACT_PATHS` = `{indexes/context.json, state.sqlite}` — exact match.
- `_PRIVATE_ROOTS` prefix match: `references/`, `run-inputs/`, `records/`,
  `runs/`, `run-plans/`, `review-inputs/`, `handoffs/`, `scratch/`,
  `reports/scout/`.
- `_PRIVATE_DOCUMENT_PATH` regex: canonical
  `docs/record_<uuidv4>/revision_<uuidv4>/…` native-document paths only
  (UUIDv4 variant/version nibbles enforced).
- Typed contract match: parses bytes with `parse_json_bytes`; on success tests
  11 strict schemas (`reference-record`, `run-input-record`,
  `private-record-revision`, `native-record-content`, `scout-operation-receipt`,
  the five `external-recording-*` families, `report`). All 11 are present in
  `validators.SCHEMA_NAMES` (checked) and all are `additionalProperties:false`
  with long `required` lists (checked), so a renamed valid payload of any of
  these families refuses even when placed under `docs/`, `definition/`, `ui/`,
  `support/`, `assets/` — filename wording and a field named `text` are
  irrelevant. This matches `tests/test_scout03_package_privacy.py`
  `test_renamed_typed_private_provenance_refuses_before_export`.

### 1.2 Reaches inspect / export / install / adoption

- **inspect:** direct (`cli.py:1965`).
- **export:** `export_package` → `inspect_package(source)` first
  (`package.py:253`); `_copy_package` re-inspects source and staged bytes
  (`package.py:1036,1059`); post-publish `inspect_package(destination)`
  (`package.py:281`).
- **install:** `install_package` → `inspect_package(source)` (`package.py:432`),
  `_copy_package` (source + staged re-inspect), post-copy
  `inspect_package(destination)` (`package.py:447`); an already-present
  destination is re-inspected (`package.py:438`).
- **init / adoption:** `_initialize_and_prepare` inspects **every** package root
  (`package.py:367`); `_validate_adoption_tree` inspects the sole root
  (`package.py:918`). A pre-existing on-disk package containing recognized
  private provenance therefore blocks a plain `init` as well as `--adopt-package`
  (correct security posture; noted as a usability consequence in §3).

### 1.3 Valid inert Scout / template / frontend / Python content stays portable

Probe (disposable, in-process, `private_package_provenance` only): every file
returned by `scout_template.scout_source_files()` — `README.md`, `CHANGELOG.md`,
`goalgraphs/README.md`, `goalgraphs/<selector>.md` ×5, `ui/template.html`,
`ui/style.css`, `definition/scout-source.json` — returns `False` (portable).
`docs/notes.md`, `support/x.py`, a file literally named `text` → `False`.
Matches `test_inert_template_docs_and_supporting_python_remain_portable` and
`test_actual_scout_source_package_round_trips_without_private_provenance`.

**Catalog materialization** (`catalog.materialize_catalog_package`): unchanged in
this slice, but it *does* still run the guard via `inspect_package(destination)`
(`catalog.py:229`, and `:218` for an existing dir). Built-in catalog content
(`catalog.py:_entry`: `definition/gig.md`, `definition/goal-graph.json`,
`definition/review-contract.json`, `definition/evaluation.json`,
`catalog-entry.json`, `provenance.json`) cannot match a private root, an exact
path, the native-doc regex, or any of the 11 strict schemas (each carries its
own distinct field set with `schema_version:"1.0"`). The C3 evidence's
description — "catalog materialization was read as a trusted built-in source
producer and was not changed" — is **accurate for the actual caller**: no code
change, and the guard is a verified no-op on legitimate built-in bytes. This is
not a case of a producer being *excluded* from the guard; it is subject to it
and passes.

### 1.4 Errors redact contents

`private_provenance_refused` message is the fixed string
`"package contains recognizable private provenance"` — no path, no bytes, no
payload excerpt (`package.py:217-220`). `private_package_provenance` returns a
bare `bool`; callers receive no parsed payload. Satisfied. (Pre-existing sibling
refusals in the same loop *do* echo the relative path, e.g. `symlink_refused`,
`file_type_refused`; that predates C3 and a package-internal filename is not
private content, so this is consistent, not a regression.)

### 1.5 Bounded reads / honest parse failures

- The guard only ever sees files `<= MAX_PACKAGE_FILE_BYTES` (4 MiB): the size
  check at `package.py:212-215` precedes the guard call. `data` is the same
  `path.read_bytes()` buffer already read by `inspect_package`; the guard adds
  no new unbounded read.
- Parse failure is fail-**open toward "not typed-private"**, which is the honest
  gate semantics: `parse_json_bytes` raises `CanonicalizationError` (a
  `ValueError` subclass — verified: `canonical.py:42`) for non-UTF-8,
  non-canonical, or duplicate-member JSON; `package_privacy.py:67-70` catches
  `(UnicodeDecodeError, ValueError)` and returns `False`. Crucially the
  path/exact/regex checks run *before* the parse, so a non-JSON blob under
  `references/` still refuses (probe: `references/x/source.txt` with
  `b"\xff\xfe not json"` → `True`). `validate_serialized_contract` is itself
  fully defensive (unknown schema → report; parse error → report; never
  raises), so the un-try'd `any(validate_serialized_contract(...))` at
  `package_privacy.py:71-73` cannot throw.

### 1.6 No weakening of path / symlink / file-type / manifest guarantees

`git diff` shows the guard added as a **pure addition** after the existing
checks; nothing removed or reordered. `_copy_package` was rewritten but is
**strictly safer**:

- Old: `destination.mkdir(exist_ok=False)` then copy in place; on unsafe
  material mid-copy, `shutil.rmtree(destination)` + raise (left a
  partially-created destination during the window).
- New: `inspect_package(source)`; stage into a fresh
  `tempfile.mkdtemp(dir=destination.parent)` sibling; copy with the same
  symlink/executable rejection; `inspect_package(staged)`; compare
  `staged.content_digest == source.content_digest` (TOCTOU / mid-copy mutation
  → `package_copy_refused`); re-check `destination.exists()` →
  `package_conflict`; `os.replace(staged, destination)` (atomic same-dir
  rename); `finally: shutil.rmtree(stage_parent, ignore_errors=True)` (stage
  cleanup on every path).
- Non-overwrite is still enforced, now via the explicit `destination.exists()`
  re-check immediately before `os.replace` rather than `mkdir(exist_ok=False)`.
- Idempotency preserved upstream: `export_package` / `install_package` short-
  circuit on an existing destination with a matching `content_digest`
  (`package.py:266-278`, `:437-443`) and never call `_copy_package` in that
  case.
- Destination-nonoverwrite on **refusal**: because `inspect_package(source)` (in
  `export_package` at `:253` and again inside `_copy_package` at `:1036`) raises
  before any staging or `os.replace`, a refused export/install leaves the
  destination absent — matches
  `test_renamed_typed_private_provenance_refuses_before_export` /
  `test_canonical_private_paths_refuse_untyped_private_bytes` /
  `test_private_package_refuses_before_install_destination`
  (`assert not destination.exists()`).
- Symlinked-parent guarantee intact: `_reject_symlink_components` still runs in
  `inspect_package` and `export_package`; the new stage dir is created by
  `mkdtemp` under the already-validated `destination.parent`, introducing no new
  symlink surface. (Coordinator's rerun of
  `test_g41_package_boundary.py::test_package_export_refuses_symlinked_parent`
  covers this.)

### 1.7 Not misrepresented as universal private-prose detection

`package_privacy.py` module docstring and `SCOUT-03-C3-package-guard.md`
§"Detection limit" both state plainly that transformed/renamed private prose or
HTML without a canonical path or valid persisted contract is **outside** the
automatic claim and must not be sold as clean-template safety. The guard
deliberately does **not** blanket-ban `docs/`, `references`-worded text
elsewhere, or Python. This is the correct, accepted design (amendment §7:
template export is inventory-only but a blanket `docs/`/Python ban is explicitly
not wanted). No invented requirement here.

---

## 2. Finding (actionable, low–moderate severity)

### F1 — `manifests/` authoritative workpad files are not recognized private provenance

**Claim.** `private_package_provenance` returns `False` for
`manifests/proposal-interview.json`, `manifests/gig-proposal.json`,
`manifests/gig-builder-session.json`, `manifests/active-gig-version.json`,
`manifests/proposal-draft-manifest.json`, `manifests/improvement-manifest.json`,
and `manifests/gig-discovery-manifest.json`. A package that inventories any of
them **with correct hashes and a correct `content_digest` passes
`inspect_package`, `export_package`, `install_package`, and adoption.**

**Probe (disposable, in-process):**
```
manifests/proposal-interview.json  -> private=False
manifests/gig-proposal.json        -> private=False
manifests/active-gig-version.json  -> private=False
reports/scout/x.json               -> private=True   (contrast)
records/r/x.json                   -> private=True   (contrast)
```

**Why this is a gap, not accepted scoping:**

1. `SCOUT-03-caller-audit.md` (frozen "Contract obligations carried into the
   audit") names the v2 user-owned layout as `records/`, `references/`,
   `run-inputs/`, `docs/`, `runs/`, **`manifests/`**, `indexes/`,
   `reports/scout/`, `state.sqlite`, … "must be validated as a **single
   ownership boundary**." The guard picks up 8 of these roots plus
   `run-plans/`, `review-inputs/`, `handoffs/`, `scratch/`, but silently omits
   `manifests/`.
2. `manifests/proposal-interview.json` is the **G22 proposal-interview trace
   snapshot** — its schema `required` set includes `request`, `questions`,
   `answers`, `events` (the user's proposal Q&A). The same SCOUT-03 audit
   repeatedly treats the interview trace as private-sensitive (writer-lock
   coordination, "fail visibly rather than discard", "not … ambient
   conversation harvesting"). `gig-proposal.json` / `gig-builder-session.json`
   likewise carry free-text proposal intent.
3. The C3 evidence **explicitly discusses and defends** its exclusions of
   arbitrary `docs/`, `references`-worded prose, and Python — but says nothing
   about `manifests/`. So this reads as an oversight in enumerating the frozen
   root list, not a deliberate, stated boundary choice like the others.
4. `gig-package.schema.json` puts **no allowlist on `files[].path`** (pattern
   `^[A-Za-z0-9._/-]+$`), so `package_privacy.py` is the only control between a
   private `manifests/` file and a published/exported package. The integration
   notes' own threat statement applies verbatim: "a manifest can faithfully
   inventory private content."

**Severity rationale — low-to-moderate, not high:** a `manifests/` file only
reaches a package if a caller deliberately copies workpad-authoritative bytes
into a `.gigai/packages/<id>/` tree; packages and the private workpad are
separate Git repos, so this is not a default data flow. But it is exactly the
deliberate-mis-inventory case the guard exists to stop, and the leaked content
(interview Q&A / proposal intent) is squarely private-sensitive.

**Narrow remedy (one of):**
- Add `"manifests/"` to `_PRIVATE_ROOTS` in `package_privacy.py:18-28`
  (consistent with how `reports/scout/`, another workpad-authoritative
  projection directory, is already handled), **or**
- if some `manifests/` files are intended to be portable Gig-definition
  material, add the private-sensitive schemas
  (`proposal-interview.schema.json`, `gig-proposal.schema.json`,
  `gig-builder-session.schema.json`, `proposal-draft-manifest.schema.json` —
  all present in `SCHEMA_NAMES`, verified) to `_PRIVATE_SCHEMAS` so a renamed
  copy is also caught regardless of directory.

Add one parametrized negative case mirroring
`test_renamed_typed_private_provenance_refuses_before_export` with a
`manifests/proposal-interview.json` payload.

---

## 3. Observations (no action required)

- **O1 — cost of the typed path.** For each JSON-parseable inventoried file the
  guard runs `parse_json_bytes` once (`package_privacy.py:68`) then up to 11
  `validate_serialized_contract` calls, **each of which re-parses the same
  bytes**. Bounded (≤4 MiB/file, small file count per package tree) and not a
  security issue, but a package full of ~4 MiB JSON members would see ~12×
  parse work per member. If ever a concern, short-circuit the `any(...)` (it
  already does) and/or parse once and pass the instance.
- **O2 — pre-existing package now blocks plain `init`.** Because
  `_initialize_and_prepare` inspects every root (`package.py:367`), a
  `.gigai/packages/<id>/` tree that already contains recognized private
  provenance will fail `gigai init` (not only `--adopt-package`). This is the
  correct fail-closed posture; flagging only so it is not mistaken for a
  regression during acceptance.
- **O3 — `_PRIVATE_ROOTS` is a prefix match on the POSIX relative path**
  (`str.startswith`), so `records-notes/` is *not* matched (only `records/…`).
  That is the intended narrowness ("not a ban on similarly named material below
  an arbitrary documentation path") and is correct.
- **O4 — redundant pre-parse.** `package_privacy.py:67-70` parses the bytes to
  decide whether to attempt schema matching, but `validate_serialized_contract`
  re-parses and degrades gracefully on parse failure anyway. The pre-parse is a
  harmless fast-path guard against 11 wasted schema loads on non-JSON; keep or
  drop, no correctness impact.

---

## 4. Scope notes

- Native-records and external-recording optional notes were **not** reopened.
- No universal private-prose detection requirement was invented; the guard's
  stated narrow-provenance boundary is accepted as-is.
- No new blanket `docs/` or Python prohibition was proposed; F1's remedy is
  confined to the `manifests/` root already named in the frozen ownership map.
- Source-only conclusions vs. the one disposable probe are labeled inline
  (§1.3, §1.5, §2). No pytest file or suite was run; the coordinator reruns the
  15 reported focused cases.

## 5. Disposition

**Accept with one finding.** The guard is correctly integrated across
inspect/export/install/adoption, fires before hash/inventory comparison, redacts
its error, keeps reads bounded, fails parse-honestly, weakens none of the
existing symlink/file-type/executable/manifest/TOCTOU guarantees, and does not
overclaim. **F1** (`manifests/` root omitted from the private-provenance
recognizers) should be fixed with the narrow remedy above before this slice is
treated as a complete workpad-ownership package edge.
