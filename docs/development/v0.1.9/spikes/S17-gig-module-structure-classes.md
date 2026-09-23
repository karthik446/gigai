# S17 — Gig module structure: classes instead of loose functions

**Requested:** 2026-09-23.
**Status:** Research recorded 2026-09-23. Documentation only; no module
refactored, no class added, no test changed.
**READ vs EXECUTED:** everything is READ (source and function/line counts
inspected directly) or EXECUTED as a side-effect-free `ast`-based counting
script. No production source or test file was edited. No git add/commit
performed.

## Problem

`src/gigai/scout/` groups files by name prefix (`documents*`,
`interview*`, `proposal*`, `report*`, `research*`, `tailor*`,
`acquisition*`, …) but each `*_records.py` file independently re-implements
the same shape: private `_fail`/`_refuse` helpers, private `_ref`/`_id`
builders, a `validate → build record → publish to journal → read back`
function pair, and (separately) each `*_cli.py` file re-implements Click
group/option/JSON-emit wiring. Two research modules
(`research.py`/`research_v3.py`) duplicate a private-helper block byte for
byte. This makes it harder for a future gig (trader, shopper) to know what to
reuse versus rewrite, and is the concrete duplication S16's registration
seams would otherwise leave unaddressed inside Scout itself.

## Intended system-behavior change (proposed, not decided)

No behavior change — the goal is identical behavior with a smaller,
class-based surface: a `Records` repository class (validate/build/publish/
read), a `Service`/`Execution` class (business logic against models), and a
CLI command-group class, per capability family — plus a small set of core
base classes/protocols so trader/shopper reuse the shape rather than
re-copying it.

## Tasks

1. Inventory every `.py` file directly under `src/gigai/scout/` (and its
   `find_jobs/` subpackage) and group into families.
2. For each family: files, public function count, shared shapes.
3. Show concrete file:line duplication, including one byte-identical case.
4. Propose a class-based structure; sketch one family before/after.
5. Say where classes don't help.
6. Cover interaction with S16, test impact, and a behavior-preserving
   migration path.

## Acceptance criteria

- Every module under `src/gigai/scout/` (top-level `.py` files plus
  `find_jobs/`, `ui/`, `data/`) is assigned to a family or explicitly listed
  as unassigned, with a reason.
- Every claim cites `file:line`.
- One family gets a before/after pseudocode sketch.

## Investigate: module inventory

### Full listing of `src/gigai/scout/` (top-level Python files)

Counted directly with `ls src/gigai/scout/*.py` (34 files) plus the
`find_jobs/`, `ui/`, `data/` subdirectories.

| File | Lines | Public functions* |
| --- | --- | --- |
| `__init__.py` | 0 | 0 |
| `acquisition_cli.py` | 104 | 4 |
| `acquisition_records.py` | 576 | 7 (5 module-level + 2 nested `operation`) |
| `answer_cli.py` | 88 | 2 |
| `bundled_tools.py` | 122 | 1 |
| `checks.py` | 553 | 1 |
| `discovery_job.py` | 191 | 3 |
| `discovery.py` | 128 | 1 |
| `document_records.py` | 272 | 6 (4 module-level + 2 nested `operation`) |
| `documents_cli.py` | 125 | 3 |
| `documents.py` | 240 | 7 |
| `inputs.py` | 370 | 4 |
| `interview_cli.py` | 175 | 6 |
| `interview_records.py` | 757 | 9 (5 module-level + 4 nested) |
| `interview.py` | 37 | 0 (facade re-exports only) |
| `materialization.py` | 1026 | 5 |
| `posting_inputs.py` | 646 | 4 (3 module-level + 1 nested `operation`) |
| `projection.py` | 579 | 8 |
| `proposal_cli.py` | 47 | 1 |
| `proposal_execution.py` | 1404 | 9 |
| `proposal_records.py` | 528 | 7 (5 module-level + 2 nested `operation`) |
| `proposals.py` | 1149 | 15 |
| `report_cli.py` | 63 | 3 |
| `report_readers.py` | 459 | 8 |
| `report.py` | 343 | 3 |
| `research_inputs.py` | 749 | 5 |
| `research_v3.py` | 341 | 1 |
| `research.py` | 220 | 1 |
| `tailor_cli.py` | 97 | 5 |
| `tailor_execution.py` | 82 | 1 |
| `tailor_selection.py` | 353 | 7 |
| `tailoring.py` | 457 | 7 |
| `template.py` | 260 | 7 |
| `tool_adapter.py` | 70 | 3 |
| `tools.py` | 505 | 3 |

*Public function count is a plain AST walk counting every
`FunctionDef`/`AsyncFunctionDef` not starting with `_`, **including nested
functions** (e.g. an inner `operation(writer)` closure defined inside a
public function, which the walk still finds since it doesn't start with
`_`). Counts above are annotated where nested functions inflate the number,
so the table is not silently wrong; the true "public API surface" is lower
than the raw count for files with `operation`/`key`/`publish` nested
closures (`document_records.py`, `proposal_records.py`, `acquisition_records.py`,
`posting_inputs.py`, `interview_records.py`).

Subpackages not counted above: `find_jobs/` (8 files: `__init__.py`,
`ats_board_clients.py`, `bindings.py`, `contracts.py` [87 public
defs/classes — the largest single file, already class-heavy via
`_Contract`-based dataclasses], `exa_client.py`, `market_acquisition.py`,
`present_api.py`, `watchlist.py`); `ui/` (a Vite/React frontend, not Python);
`data/` (bundled gig template: `gig.py`, `goalgraphs/*.md`, `tools/*`,
`CHANGELOG.md`, `README.md` — the materialized copy shipped into a new
private Gig, not library code to refactor here).

## Module families

| Family | Files | Shared shape |
| --- | --- | --- |
| **documents** | `documents.py`, `documents_cli.py`, `document_records.py` | domain logic (`documents.py`) → CLI wiring (`documents_cli.py`) → validate/record/read (`document_records.py`) |
| **interview** | `interview.py`, `interview_cli.py`, `interview_records.py` | thin facade (`interview.py`, a re-export-only module) → CLI (`interview_cli.py`) → validate/record/read (`interview_records.py`, the largest records file at 757 lines) |
| **proposals** | `proposals.py`, `proposal_cli.py`, `proposal_records.py`, `proposal_execution.py` | domain/validation (`proposals.py`, 1149 lines) + execution against models (`proposal_execution.py`, 1404 lines, the largest file in `scout/`) + records (`proposal_records.py`) + **misnamed** `proposal_cli.py` (see below) |
| **report** | `report.py`, `report_cli.py`, `report_readers.py` | render (`report.py`) → CLI (`report_cli.py`) → journal-snapshot readers (`report_readers.py`) |
| **research** | `research.py`, `research_v3.py`, `research_inputs.py` | two near-duplicate domain-validation modules (`research.py` v2, `research_v3.py` v3) + input resolution/hydration (`research_inputs.py`) |
| **tailoring** | `tailoring.py`, `tailor_cli.py`, `tailor_execution.py`, `tailor_selection.py` | domain/bundle codec (`tailoring.py`) + standalone debug CLI (`tailor_cli.py`, not wired into the main `cli.py` — see S16's inventory, which found no `.scout.tailor_cli` import from core) + execution (`tailor_execution.py`) + selection/hydration DTOs (`tailor_selection.py`) |
| **acquisition** | `acquisition_cli.py`, `acquisition_records.py`, `discovery_job.py`, `discovery.py` | CLI (`acquisition_cli.py`) → records (`acquisition_records.py`, public-posting import/resume) → job resolution (`discovery_job.py`) → domain validation (`discovery.py`) |
| **answer** | `answer_cli.py` | Single-file family; no paired records/domain module of its own (delegates into `posting_inputs.py`/`proposal_records.py` — not verified line-by-line in this spike, flagged as an open item) |
| **posting inputs** | `posting_inputs.py`, `inputs.py` | Both are input-resolution/hydration modules but for different domains (posting selection vs. generic external-input resolution) — grouped as a shared *pattern* family, not a shared *domain* family |
| **find_jobs** (subpackage) | `find_jobs/*.py` | Already the most class-like family: `contracts.py` defines dataclass-based DTOs (`_Contract` base, `PinnedResume`, `RequirementMatrixRow`, etc. — 87 public names), `bindings.py` has `_register_nodes`/`_child_worker_entry` (the S16 registration precedent), `present_api.py` is a stdlib HTTP server class hierarchy |
| **tools/adapters** | `tools.py`, `tool_adapter.py`, `bundled_tools.py` | Tool-request publication (`tools.py`), native-record CLI adapter shims (`tool_adapter.py`), and manifest bundling (`bundled_tools.py`) — related by "tool plumbing" but not the records pattern |
| **checks** | `checks.py` | Standalone: text/markdown structural validation helpers (headings, terms, requirements). No CLI or records pairing; a pure-function utility module. |
| **template/materialization** | `template.py`, `materialization.py` | Gig-template comparison/decision (`template.py`) and default-init candidate materialization (`materialization.py`, the second-largest file at 1026 lines) — paired by the default-init/update lifecycle, not the records pattern |
| **projection** | `projection.py` | Standalone: builds `ScoutProjection` from journal snapshot rows; consumed by `report.py` and `report_readers.py` but doesn't itself follow the records pattern (it's read-only) |

**Every file accounted for:** all 34 top-level `.py` files plus the 8
`find_jobs/` files above are assigned to a family. `__init__.py` is empty
(0 lines) and assigned no family (nothing to refactor).

## The duplicated pattern, concretely

### Pattern 1: "validate → build record → publish to journal → read back"

Confirmed at 6+ sites across 4 families, each independently defining its own
`_fail`/private validators and its own `operation(writer)` closure passed to
`run_with_journal_writer`:

| Family | Write site (`file:line`) | Read site (`file:line`) |
| --- | --- | --- |
| documents | `document_records.py:138-181` (`record_document_revision`, builds refs, calls `writer.record(JournalTransition(...))` at line 174, returns via `run_with_journal_writer` at line 181) | `document_records.py:184-198` (`_existing_revision`) and `:200` (`read_document_revision`), both via `read_committed_artifact` |
| documents (selection) | `document_records.py:217-249` (`record_final_selection`, `writer.record(JournalTransition(...))` at line 246) | `document_records.py:252` (`read_final_selection`) |
| proposals | `proposal_records.py:376-457` (`record_proposal_revision`, `writer.record(...)` at line 451) | `proposal_records.py:489` (`read_proposal_revision`) via `read_committed_artifact` at line 371 |
| proposals (assessment) | `proposal_records.py:460-486` (`save_assessment_revision`, `writer.record(...)` at line 480) | (read path shared with `record_proposal_revision`'s reader) |
| acquisition | `acquisition_records.py:467-536` (`import_public_rows`, `writer.record(JournalTransition(...))` at line 520) | `acquisition_records.py:382,409,554` (`read_committed_artifact` call sites) |
| interview | `interview_records.py:557` (`writer.record(JournalTransition(...))` inside `prepare_interview`'s `run_with_journal_writer` call at line 567) | `interview_records.py:589,719,736` (further `run_with_journal_writer` reads) |

Each of the four `*_records.py` modules also independently defines its own
`_fail(code, message)` (`document_records.py:50`, `proposal_records.py:65`,
`acquisition_records.py:103`, `interview_records.py` uses a differently-named
equivalent) and its own `_ref`/`_id`-shaped helper
(`document_records.py:128`, `proposal_records.py:75`,
`acquisition_records.py:291`, `interview_records.py:95`) — same shape, four
separate implementations, none shared.

### Pattern 2: byte-identical duplicated helper block

`research.py:39-141` and `research_v3.py:39-141` are **byte-identical**
(verified with `diff` on that line range — no output, meaning zero
differences) for the private helpers `ScoutResearchError` (class, line 39),
`_refuse` (47), `_canonical` (51), `_schema_validator` (59), `_renderer`
(74), `_trusted_origin` (87), `_supporting_ids` (112), `_validate_supporting`
(131). `research_v3.py` then adds two more private helpers
(`_historical_context` at line 142, `_validate_historical_packet_integrity`
at line 176) before its own `validate_research_domain` at line 213 (versus
`research.py`'s at line 142). This is not "similar" — it is the same source
text copied into a second file when v3 was created.

A third, partial instance of the same private-helper cluster exists in
`tailoring.py:60-99`: `_refuse` (60), `_canonical` (64), `_schema_validator`
(72), `_renderer` (87) reappear with identical names and (not diffed here,
but same signatures) very likely similar bodies, before `tailoring.py`
diverges into its own `encode_tailoring_bundle`/`_validate_request_shape`
logic at line 99 onward.

### Pattern 3: CLI wiring duplication (lighter, but real)

Every `*_cli.py` file redefines its own `_emit(value, as_json)` /
`_resolve(home, target, gig)` pair for JSON-vs-text output and workpad
resolution: e.g. `acquisition_cli.py:25,32` and `report_cli.py:19,23` are
separately-implemented versions of the same two helper shapes (`_emit`,
`_resolve`), one per CLI module, rather than one shared base.

### A naming trap: `proposal_cli.py` is not a CLI module

`proposal_cli.py:19` defines exactly one function,
`run_saved_proposal(*, resolved, config, run_id, goal_id, model_target,
posting_selector, private_selectors, local_allowed, configured_digest,
budget=None)` — a service function, with **no `@click.group`, no `@click`
decorators at all**. Despite its filename, it is not part of the CLI family;
it belongs with `proposal_execution.py`. Flagged explicitly since a
family-by-filename assignment would misclassify it.

## Proposed class-based structure

Two proposed layers, matching S16's core/gig boundary:

### Core base classes/protocols (belong in core — trader/shopper reuse them)

```python
# core: gigai/gig_records.py (new, proposed)
class RecordRepository(Protocol):
    """One capability's validate → build → publish → read-back shape."""
    def validate(self, payload: Mapping[str, object]) -> dict[str, object]: ...
    def build_record(self, *, validated: Mapping[str, object], ...) -> RecordPayload: ...
    def publish(self, *, resolved: ResolvedWorkpad, record: RecordPayload,
                operation_key: str) -> RecordResult:
        """Default implementation: wraps run_with_journal_writer + JournalTransition,
        calling self.validate/self.build_record; a subclass overrides only the
        domain-specific validate/build_record, not the journal plumbing."""
    def read(self, *, resolved: ResolvedWorkpad, record_id: str, revision_id: str) -> RecordPayload: ...

class GigCliGroup(Protocol):
    """Shared _emit/_resolve helpers a gig's CLI classes subclass instead of
    re-implementing per module."""
    def emit(self, value: object, as_json: bool) -> None: ...
    def resolve(self, home: Path | None, target: Path | None, gig: str) -> ResolvedWorkpad: ...
```

### Scout subclasses (stay in Scout — domain-specific)

```python
# scout/document_records.py (proposed shape, pseudocode, not implementation)
class DocumentRecords(RecordRepository):
    def validate(self, payload): ...       # today's inline checks in record_document_revision:151-160
    def build_record(self, *, validated, ...): ...  # today's _paths/_record_payload (document_records.py:123,132)
    # publish() and read() come from the core base class unchanged
```

### Before/after sketch for ONE family: `document_records.py`

**Before** (today, pseudocode of the actual shape at
`document_records.py:138-198`):

```python
def record_document_revision(*, resolved, revision, invocation, operation_key, uuid_factory=uuid.uuid4):
    if not isinstance(revision, DocumentRevision) or not isinstance(resolved, ResolvedWorkpad):
        _fail(...)
    if not isinstance(invocation, Mapping) or not operation_key or ...:
        _fail(...)
    for lineage in revision.source_lineage:
        ...  # scope check
    content_path, record_path = _paths(revision)
    record_data = _record_payload(revision, content_path)

    def operation(writer):
        existing = _existing_revision(writer.root, ...)
        if existing is not None:
            ...  # conflict or idempotent return
        refs = [_ref(content_path, ...), _ref(record_path, ...)]
        entry = writer.record(JournalTransition(...))
        return DocumentRecordResult(revision, True, entry)

    return run_with_journal_writer(workpad=resolved.path, ..., operation=operation)

def read_document_revision(*, resolved, record_id, revision_id, document_kind):
    ...  # separate function, own read_committed_artifact call
```

**After** (proposed, pseudocode):

```python
class DocumentRecords(RecordRepository):
    """Owns document-revision validate/build/publish/read. Subclasses the
    core RecordRepository base, which owns run_with_journal_writer wiring."""

    def validate(self, *, resolved, revision, invocation, operation_key):
        if not isinstance(revision, DocumentRevision) or not isinstance(resolved, ResolvedWorkpad):
            self._fail(...)         # _fail becomes a base-class method, not per-module private fn
        if not isinstance(invocation, Mapping) or not operation_key or ...:
            self._fail(...)
        for lineage in revision.source_lineage:
            ...
        return revision  # validated

    def build_record(self, *, validated_revision):
        content_path, record_path = self._paths(validated_revision)
        record_data = self._record_payload(validated_revision, content_path)
        return RecordPayload(refs=[...], transition_kind="private_record_revised", ...)

    def check_conflict(self, *, writer, record_id, revision_id, candidate):
        existing = self._existing_revision(writer.root, ...)
        if existing is not None and existing.content != candidate.content:
            self._fail("document_record_conflict", ...)
        return existing

    # publish() inherited unchanged from RecordRepository: calls validate(),
    # build_record(), check_conflict(), then run_with_journal_writer(...)

    def read(self, *, resolved, record_id, revision_id):
        return self._existing_revision(resolved.path, resolved.project_id, resolved.gig_id, record_id, revision_id)
```

The behavior is identical; what moves is where `_fail`/journal-plumbing live
(base class, written once) versus where domain validation/record-building
live (subclass, one per capability). `record_final_selection`/
`read_final_selection` (same file, lines 217-255) would become a second
`RecordRepository` subclass (`DocumentSelectionRecords`) in the same module,
rather than two more loose functions sharing the file's private helpers by
accident.

## Where classes DON'T help

- **`checks.py`** (553 lines, pure text/markdown validation functions like
  `_line_for_offset`, `_normalise_headings`, `_normalise_requirements`) — no
  shared state, no journal/CLI pairing, called as pure transforms. Wrapping
  these in a class would add ceremony with no reuse benefit; they should stay
  functions.
- **`projection.py`** (read-only journal-snapshot → `ScoutProjection`
  builder) — a pure read/reduce pipeline (`_rows`, `_record_rows`,
  `_application_rows`) with no publish side, no per-instance state to carry
  between calls. A `Protocol` for `ProjectionReader` (already present as a
  `Protocol` at `projection.py:48`) is the right level of structure; the
  individual `_rows`/`_record_rows` helpers don't need to become methods.
- **`find_jobs/contracts.py`'s DTOs** — already dataclasses via `_Contract`;
  no change needed, they're the pattern S17 recommends elsewhere.
- **One-shot CLI commands with a single function** (`proposal_cli.py`,
  `tailor_execution.py`) — a single public function with no shared state
  doesn't need a class wrapper just to match the family's shape; the
  filename-vs-content mismatch in `proposal_cli.py` is a naming problem, not
  evidence it needs a `Service` class of its own.

## Interaction with S16

S16 proposes core registration seams keyed on a `GigManifest`
(`record_kinds`, `schema_names`, `node_bindings`, etc.). The `RecordRepository`
base class proposed here is exactly the object a gig would register per
record kind: `GigManifest.record_kinds` could hold
`{"document_revision": DocumentRecords(), "final_selection":
DocumentSelectionRecords(), ...}` instead of core needing to import
`gigai.scout.document_records.record_document_revision` by name (S16's
`records/journal kinds` category, e.g. `run.py:1485`). The core base classes
proposed here **are** S16's registration seams, not a separate design.

## Test impact

The `tests/behaviors/` packages that exercise these modules directly (not
enumerated file-by-file in this spike — flagged as an open item to inventory
before implementation) would need their call sites updated from
`record_document_revision(...)` to `DocumentRecords().publish(...)`-shaped
calls, but the underlying journal entries, schema validation, and error
codes should stay byte-identical if the migration preserves behavior. A
migration path:

1. Introduce the class wrapping the existing module-level functions
   unchanged (the class's `publish()` calls the *existing*
   `record_document_revision` internally) — zero behavior risk, pure
   addition.
2. Move tests to call the class API; keep the old function as a thin
   deprecated wrapper calling the class, so any caller not yet migrated
   still works.
3. Once all callers (including `run.py`'s direct imports, per S16) use the
   class API, delete the old free functions.

This keeps every step independently testable and reversible, and never
requires a single big-bang rewrite.

## Non-claims

- No class was written; the sketch above is pseudocode only.
- Public-function counts include nested closures (annotated per file above)
  and are not a precise "API surface" metric without further filtering.
- The `answer` family's actual downstream dependencies were not traced
  line-by-line; flagged as an open item.
- This spike does not claim the proposed base classes are the only possible
  design — they are one proposal for operator review.

## Change log

- 2026-09-23: Spike recorded. Inventoried all 34 top-level `src/gigai/scout/`
  files plus the 8-file `find_jobs/` subpackage into 13 families (with every
  file assigned or explicitly marked as standalone with a reason). Found and
  verified (via `diff`, zero output) a byte-identical duplicated
  private-helper block between `research.py:39-141` and
  `research_v3.py:39-141`, a third partial instance in `tailoring.py:60-99`,
  and 6 concrete write/read site pairs of the "validate → build record →
  publish → read back" pattern across the documents/proposals/acquisition/
  interview families. Flagged `proposal_cli.py` as a misnamed non-CLI
  service module. Proposed a core `RecordRepository`/`GigCliGroup` base-class
  pair, sketched a before/after for `document_records.py`, listed where
  classes don't help (`checks.py`, `projection.py`, `find_jobs/contracts.py`,
  single-function CLI modules), and tied the base classes to S16's
  registration seam.
