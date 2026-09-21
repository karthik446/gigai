# BUG-001 — `gigai gigs` is an opaque global registry dump

**Status:** Open — found in local v0.1.7 human UAT

**Affected command:** `gigai gigs` (including `uv run gigai gigs`)

## Bug summary

`gigai gigs` shows a global dump of opaque internal IDs instead of the
human-readable Gigs for the project the operator is working in. It gives no
useful name, state, or project context and makes old records look actionable.

## UAT observation

After running machine setup and entering a project workflow, the operator ran:

```shell
uv run gigai gigs
```

The command printed rows such as:

```text
gig_8e979d12-... project_139443ca-...
gig_52a79c8d-... project_1bd44240-...
```

These are raw `gig_id` and `project_id` values from the shared GigAI-home
workpad registry. They include historical records from other projects. They do
not tell the operator what a Gig is, which project it belongs to in familiar
terms, whether it is current, or what state it is in.

This is especially misleading during UAT because the command name implies
"show the Gigs I can use here," while it actually exposes internal global
storage records. The rows are not G42 built-in catalog entries and are not the
project-local `.gigai` package.

## Current behavior and cause

`gigs` reads every workpad row from the global registry directly. It does not
resolve a current project binding, which is why it can continue to print rows
even when another command rejects an incompatible shared-home configuration.

The existing output has no human-readable Gig title, project label, target,
lifecycle state, version, or indication that its scope is global. Last
activity is not a required output in this bug and is deliberately excluded.

The current `ProjectRegistry.workpad_records()` validates every row while
building one result tuple. A malformed ID or locator raises
`RegistryCorruptError` and aborts the entire call. Therefore the previous
claim that the command already tolerated bad historical rows was inaccurate.

## Required resolution

### Scope and option contract

1. Add `--target PATH`, using the same target-resolution contract as other
   project-scoped commands.
2. Without `--target`, resolve the project binding for the current working
   directory. With `--target`, resolve that explicit target binding. In both
   cases, ordinary `gigai gigs` lists only the resolved project's Gigs.
3. An unbound current directory or an unbound explicit target is an actionable
   refusal; it must never silently fall back to a global registry dump.
4. A global list is optional product scope. If retained, require explicit
   `--all`; reject combining it with `--target`; label its human output
   `All projects`; and do not make it the default.

### Display-data contract

5. Derive title, lifecycle status, and version from the resolved registered
   workpad's authoritative committed journal/manifests (including
   `gig-proposal.json` and `active-gig-version.json`), not from an unverified
   filesystem scan or the disposable projection alone.
6. Read that metadata only after validating the registry row and resolving
   its locator under the configured workpad root with the existing symlink and
   traversal protections. A workpad's journal identity must agree with the
   registry `project_id` and `gig_id` before its metadata is displayed.
7. Define stable fallbacks: a valid current proposal with no active-version
   manifest displays `Version: proposed`; a valid approved journal with a
   missing or malformed version pointer displays `Version: N/A`;
   unavailable or invalid semantic metadata displays `Status: N/A`
   and a concise diagnostic. Do not invent a title, status, or version from
   the path.
8. Render a compact human table with title, status, and version. For global
   scope, also show a safe repository/project label derived from the validated
   project binding (for example, repository basename plus project label), not
   an absolute target path. Do not display raw Gig/project IDs in ordinary
   human output.
9. Retain stable IDs only in the explicit machine interface, `--json`.

#### Exact display algebra

An entry is **authority-readable** only when all of these read-only checks
pass: the registry row is valid; its workpad resolves safely; the workpad is a
valid private journal; every committed handoff has the expected `gig_id`; and
the workpad journal's local `gigai.project-id` ownership marker equals the
registry row's `project_id`. The current projection reader receives a
`project_id` argument but does not make that last comparison; the listing
reader must.

For an authority-readable entry, use the committed HEAD versions of the
manifests and apply this algebra:

| Field | Source and precedence | Fallback |
|---|---|---|
| Title | `manifests/gig-proposal.json.name`, only when the manifest is an object whose `gig_id` and `project_id` equal the registry row and whose `name` is a non-empty string after trimming. | `Untitled Gig` |
| Status | `N/A` if the entry is not authority-readable, its proposal manifest is invalid, or an existing active-version pointer is invalid. Otherwise: `Drafting`, `Proposed`, `Rejected`, or `Superseded` when the valid HEAD proposal has that status; `Approved` only when its status is `approved` **and** the valid pointer names that proposal. With no proposal manifest, use `Initializing`. | `N/A` with a diagnostic code |
| Version | `v<active_version>` when the valid active-version pointer has matching `gig_id`, a positive integer `active_version`, and an `approved_proposal_id` equal to the approved proposal manifest at the pointer's committed `journal_commit`. A later proposed/drafting/rejected/superseded HEAD proposal does not hide that existing active version. | `Proposed` when a valid proposal exists but no pointer exists; `N/A` when a pointer exists but is invalid, or semantic metadata is unavailable; `—` for `Initializing` |

For this contract, an **approved journal** means an authority-readable entry
with a valid active-version pointer cross-referencing the proposal manifest at
its committed `journal_commit`, whose status is exactly `approved`. It does
not mean merely that an arbitrary handoff has an approval-looking transition
name.

The implementation must use a read-only authoritative-journal reader. It must
not call `read_index()` (which may rebuild `state.sqlite`), reconcile a
journal, acquire a writer lock, create `scratch`, write a manifest, update a
binding, or otherwise create or rewrite any workpad artifact while listing.

#### Safe project-label algorithm

The registry has no explicit human project label. Derive the display-only
label for both project scope and (if retained) `--all` after the project record
and target locator have passed registry validation:

1. Take only `Path(target_locator).name`; never render the full locator.
2. Normalize it to NFC, replace control characters (including terminal escape)
   with `U+FFFD`, collapse whitespace, and truncate to 48 Unicode code points.
   If the result is empty, use `Project`.
3. Append ` [git]` or ` [directory]` from the validated target kind.
4. Sort equal candidate labels by canonical `project_id`; append ` #2`, ` #3`,
   and so on only to duplicates. This disambiguator is ordinal, not an ID.

The resulting label is for human output and the required project-scope
`scope.label`. JSON also uses the stable `project_id` and never emits a target
locator unless a separately designed, explicitly path-bearing machine
interface is approved.

#### Retention and ordering rules

A registry row with invalid IDs or locator is omitted: it cannot safely become
an entry. A row with valid IDs and locator is retained as exactly one entry,
even if its workpad is unavailable, unsafe, unreadable, or has invalid
semantic metadata. Such a degraded entry has `title: "N/A Gig"`,
`status: "N/A"`, and `version: "N/A"`, plus the applicable
diagnostic. This includes `journal_project_id_mismatch`; it is retained only
as a degraded registry entry, never displayed as a normal Gig.

`proposal_metadata_invalid` and `active_version_metadata_invalid` also retain
the valid registry row. The display algebra above determines any field that is
still authority-readable; all fields affected by missing authority use the
degraded values. Neither kind of diagnostic permits reading a replacement
workpad from disk.

The registry-owned listing query orders raw rows by SQLite binary
`project_id`, then `gig_id`, then `workpad_locator`, and carries that ordinal
only for internal tie-breaking. In project scope, entries sort by their safe
title after NFC normalization and Unicode `casefold`, compared as UTF-8 bytes,
then canonical `gig_id`. In global scope, they sort first by the safe project
label (with duplicate ordinals already applied), then by that same entry key.
Diagnostics sort by code, then valid canonical `project_id`, then valid
canonical `gig_id`, then the internal registry-row ordinal. IDs and ordinals
used for sorting are never rendered in human output.

#### JSON and diagnostic contract

Successful `--json` output is exactly one object with this shape:

```json
{
  "schema_version": "1.0",
  "scope": {"kind": "project", "project_id": "project_...", "label": "example [git]"},
  "entries": [
    {"gig_id": "gig_...", "project_id": "project_...", "title": "...", "status": "Proposed", "version": "Proposed"}
  ],
  "diagnostics": [
    {"code": "registry_row_invalid", "severity": "warning", "message": "..."}
  ]
}
```

`scope.kind` is `project` for implicit and `--target` calls, and `all` for an
explicit `--all` call. `scope.project_id` and `scope.label` are required only
for project scope. Every global entry also includes `project_label` produced
by the algorithm above. `entries` and `diagnostics` are always present arrays.
Human output renders the same entries and diagnostics, but does not render raw
IDs.

Non-fatal diagnostics use only these stable codes:

- `registry_row_invalid`
- `workpad_unavailable`
- `workpad_path_unsafe`
- `journal_unreadable`
- `journal_gig_id_mismatch`
- `journal_project_id_mismatch`
- `proposal_metadata_invalid`
- `active_version_metadata_invalid`

Each has `severity: "warning"` and a human-safe `message`; diagnostics never
contain a raw malformed locator. Fatal command failures exit nonzero and, with
`--json`, emit exactly:

```json
{"schema_version":"1.0","error":{"code":"gigs_project_unbound","message":"..."}}
```

The initial fatal-code set is `gigs_project_unbound`, `gigs_target_unbound`,
`gigs_scope_conflict`, and `registry_unavailable`. Human-mode failures use the
same code in the actionable Click error message.

### Corruption tolerance without authority bypass

10. Add a registry-owned, listing-specific read API rather than reading the
    SQLite table directly in the CLI or scanning workpad directories to
    reconstruct entries. It must validate each row with the same ID and
    absolute-locator rules as `workpad_records()`.
11. For a project-scoped list, one malformed row must not prevent valid rows
    for that project from being displayed. The bad row is omitted, never
    converted into an entry, and produces a typed warning/diagnostic. Invalid
    rows for another project do not block the selected project's listing.
12. For `--all`, return valid rows and report a deterministic count or typed
    diagnostics for omitted corrupt rows. Do not expose malformed locators or
    recover records from disk. The registry remains the source that decides
    whether a candidate row exists; tolerance only changes failure isolation.
13. Keep catalog discovery separate: `gigai catalog list` remains the command
    for available built-in catalog packages. `gigs` must not imply that
    historical registry rows are catalog choices.

## Acceptance evidence

- A bound project's implicit invocation and `gigai gigs --target PATH` return
  the same project-scoped entries; neither contains unrelated historical
  records.
- An unbound current directory or target returns a clear, actionable refusal
  rather than raw global IDs.
- Each displayed entry has a journal/manifests-derived title, status, and
  version, with the specified deterministic fallbacks for missing or invalid
  semantic metadata.
- A valid HEAD proposal with `status: "superseded"` displays `Superseded` and
  retains any independently valid active version.
- Project scope uses the same safe basename label algorithm as global scope;
  tests cover unsafe leaf characters, duplicate labels, and deterministic
  ordinal disambiguation.
- A workpad whose local journal `gigai.project-id` marker differs from the
  registry row is never displayed as a normal entry. Coverage proves it emits
  `journal_project_id_mismatch`; the existing `gig_id` check alone is not
  accepted as sufficient identity validation.
- A deliberately malformed registry row does not turn into a listing entry or
  make valid rows disappear. It produces the defined diagnostic; tests prove
  the CLI did not replace registry authority with a filesystem scan.
- A valid registry row with an unavailable/unsafe workpad or unreadable
  journal remains exactly one degraded `N/A Gig` entry with its
  diagnostic, while an invalid registry row remains omitted. Tests assert the
  specified entry and diagnostic ordering across repeated calls.
- `gigai gigs --all` (only if implemented) is explicit, rejects `--target`,
  is labeled global, and uses safe project/repository labels rather than full
  `/Users/<operator>/...` paths.
- `gigai gigs --json` retains stable IDs for scripts, but obeys the same
  project-scope/default and explicit-`--all` contract as human output, with
  the exact success, diagnostic, and failure shapes above.
- Update `tests/test_index_projection.py` so its current global
  `gigs --json` expectation uses explicit project scope (or explicit `--all`
  if that optional interface is retained), and add coverage for the new
  semantics.
- Tests preserve the distinction between `gigs` and unchanged `catalog list`.
- A read-only regression snapshots the workpad tree and verifies that listing,
  including an unreadable or stale projection, does not create or rewrite
  `state.sqlite`, `scratch`, manifests, journal history, or project bindings.

## Non-goals

- This bug does not change the G41 project-local package authority or G42
  catalog installation rules.
- This bug does not migrate, delete, or otherwise clean up historical
  workpad records.
- The shared-home configuration compatibility issue observed with an older
  installed CLI is separate from this output/command-contract defect.
