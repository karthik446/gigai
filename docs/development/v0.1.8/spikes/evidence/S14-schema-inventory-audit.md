# S14: Schema inventory and consolidation audit

**Date:** 2026-09-22
**Scope:** Audit and reporting only, per [S14](../S14-schema-inventory-and-consolidation-audit.md).
No schema is deleted, merged or version-bumped. No schema is called
redundant or safe to retire. Items flagged below are for operator review
only.
**Source state:** read at `b01675d` plus this worktree's uncommitted
changes. `src/gigai/` itself is unmodified in this worktree. The tests
cited are under `tests/behaviors/`, which is untracked in this worktree
because of the S11 reorganization.

## Summary

- **There are 82 `*.schema.json` files.** The ticket's count is correct.
  There are 85 files in total, plus a `__pycache__/` directory. Every schema
  is listed in `SHA256SUMS` (the lists were diffed and match).
- **76 of the 82 are validated against their file by current code.**
  That is 51 single-version schemas, 24 live-variant files in multi-version
  families, and `graph-selection-record-v2`, which is validated but flagged
  (see F3).
- **There are 12 multi-version families** (25 files). **None of them is
  legacy-read-only on the evidence found.** In 11 families, a current
  writer still emits the older version. It chooses the version from what
  the input contains, or, for external recording, from a CLI flag whose
  **default is v1**. The twelfth, `graph-selection-record`, has two
  *selection kinds* rather than two generations. So the "versions" here work
  as live feature variants selected by payload shape, not as a supersession
  chain. That is why the count only grows: the schemas README's rule (an
  additive field means a new version, and readers accept only exact
  versions; README "All top-level objects reject unknown fields" paragraph)
  forces a new file for every optional feature, and nothing retires the
  base variant while its writer still produces it.
- **Seven schemas are flagged for operator review.** Six are never
  validated against their file. One is validated but has no in-repo
  writer. **On current evidence, none of the 82 lacks a reader or
  writer**:
  - `common` is reached only through `$ref`, which is expected.
  - Four have **hand-rolled code equivalents that diverge from the file**:
    - `handoff-frontmatter`: `journal.py` writes every handoff in this
      shape. An executed check shows its output **fails** the schema
      whenever artifacts are attached.
    - `role-reference`: `roles.py` decodes structured roles, but rejects the
      schema's shape.
    - `scout-answer-association`: `scout_proposal_records.py` builds and
      checks a shape missing two of the schema's required fields.
    - `scout-tailor-selection-v2`: its writer emits the version const, but
      the output is never validated against the file.
  - One is **mirrored only in memory**: `scout-proposal-discovery-job`
    matches the `DiscoveryJob` dataclass field for field, and only a test
    imports that module.
  - One is **validated, but no in-repo writer produces it**:
    `graph-selection-record-v2`, the `agent_explicit` kind.
  - `common` is reached only through `$ref`, which is expected.
- **One incidental finding needs a decision (see F4):**
  `package_privacy._PRIVATE_SCHEMAS` lists only the **v1**
  external-recording schemas. A follow-up run confirmed that the guard doesn't recognize real v2
  records once they are renamed (F6).
- **Follow-up findings (F6):**
  - **0 of 28** real journal handoffs from a test run conform to
    `handoff-frontmatter`, so the packaged schema is stale.
  - `gigai occurrence declare` can't accept a graph-set (v2) Gig, and
    refuses with a misleading "invalid" error.
  - `scout-tailor-selection-v2`'s writer output does conform, for the one
    shape checked.

## Method

1. **How schemas are loaded.** `validators._schema_registry`
   ([`validators.py:163-173`](../../../../../src/gigai/validators.py))
   loads **every** name in `SCHEMA_NAMES` + `_VERSIONED_SCHEMA_NAMES` into
   one `$ref` registry on every validation call. Being in the registry
   therefore shows nothing about use; every one of the 82 files is in it.
   Evidence of use comes only from call sites and cross-schema `$ref`s.
2. **Census.** A script searched `src/gigai/` (excluding `schemas/` and the
   registry tuples in `validators.py`) for each filename, and searched for
   each schema's `$id` in Python and in the other 81 schemas. It also
   counted filename hits in `tests/`. This is a grep-based census, so it
   can miss a name assembled at runtime.
3. **Zero-hit cross-check.** Each schema with no code hit was re-searched by
   its stem, its `$id` and its `schema_version`/`selector_version` const.
   That search is what found `scout-tailor-selection-v2`'s writer.
4. **Writer tracing for multi-version families.** For each family, the
   reader's version dispatch and the writer that chooses which version to
   emit were read. Citations are in the table and in F2.

The original pass (F1–F5) came from reading source plus the census script.
The follow-up (F6) and parts of F3 and F4 were executed: one existing test
file was run into a scratch basetemp, and checks were run over its output.
**Every executed check can be reproduced** from the preserved scripts,
exact commands and sanitized results in the
[S14 reproduction kit](S14-repro/README.md).

## Findings

### F1: Most schemas have a direct, cited validator

76 of the 82 have at least one `validate_serialized_contract` (or
equivalent) call site in current code: 51 single-version, 24 live variants,
and `graph-selection-record-v2`. See the table.

A missing call site doesn't mean a missing reader or writer. Code can build
or check the same concept by hand, without ever loading the file. F3 traces
this for each of the other six.

### F2: The multi-version families are live variants, not legacy

| Family | Files | What selects the version | Current writer emits older version? |
| --- | --- | --- | --- |
| `external-recording-{plan,run,checkpoint,receipt,invocation}` | 10 | CLI `--protocol-version`, **default `"1"`** ([`external_cli.py:156-158`](../../../../../src/gigai/external_cli.py)); `plan_v2`/`start_v2`/`checkpoint_v2`/`submit_v2`/`cancel_v2` pass `protocol_version=2` | **Yes. v1 is the default public path.** |
| `model-invocation` v1/v2/v3 | 3 | `_invocation_record` emits `3.0` if source descriptors exist, `2.0` if a local identity exists, and `1.0` otherwise ([`model_execution.py:487-490`](../../../../../src/gigai/model_execution.py)) | Yes |
| `run-plan` v1/v2 | 2 | `plan_version: 2 if descriptor is not None else 1` ([`run_plan.py:2096`](../../../../../src/gigai/run_plan.py)) | Yes |
| `run-manifest` v1/v2 | 2 | v2 only when a graph descriptor was selected ([`run.py:319`](../../../../../src/gigai/run.py), [`run.py:2426-2430`](../../../../../src/gigai/run.py)) | Yes |
| `gig-proposal` v1/v2 | 2 | v2 iff the proposal carries `graph_set` ([`run.py:1764-1768`](../../../../../src/gigai/run.py)); `revise_offline` and `_build_proposal_artifacts` in [`lifecycle.py`](../../../../../src/gigai/lifecycle.py) still build `goal_graph`-shaped proposals | Yes (read around the builders only) |
| `active-gig-version` v1/v2 | 2 | v2 iff the pointer carries `graph_set` ([`run.py:1678-1682`](../../../../../src/gigai/run.py), `lifecycle.py:2199`) | Follows the proposal's shape |
| `scout-document-selection` v1/v2 | 2 | `FinalDocumentSelection.to_json` writes `:1` ([`scout_documents.py:141`](../../../../../src/gigai/scout_documents.py)); upgraded to `:2` when a `source_run` exists ([`scout_document_records.py:233`](../../../../../src/gigai/scout_document_records.py)) | Yes |
| `graph-selection-record` v1/v2 | 2 | By `selection_kind`: `agent_explicit` goes to v2 and everything else to v1 ([`graph_set.py:285-286`](../../../../../src/gigai/graph_set.py)) | Not a generation pair. v2 has no in-repo writer (see F3). |

This contradicts the premise that these families are accumulated
duplicates. In each family, the older file validates records that current
code still writes. Removing one would break a live path unless the writer
changes first.

Some readers accept only one version:

- `target_effect.py:474` and `occurrence.py:494` accept only v1
  `active-gig-version`.
- `scout_tools.py:226,399` accept only v2.
- `portability.py:135,150` requires v1, with v2 checked separately at
  `portability.py:124`.

**Follow-up (F6):** the single-version readers were traced afterwards.

- **Deliberate refusals:** `portability.py` refuses v2 with an explicit
  `unsupported_schema_version`. `scout_tools.py` requires graph-set
  authority.
- **Unreachable:** `target_effect.py` isn't imported by any `src/` module.
- **Reachable, with a misleading error:** `occurrence.py`'s
  `_active_pointer`, through `gigai occurrence declare`, refuses a v2
  pointer as "active Gig version is invalid". The v2 pointer can never pass
  the v1 schema; that was an executed schema comparison, and the CLI refusal
  itself wasn't run. This is tracked as
  [OCCURRENCE-01](../../../followups/OCCURRENCE-01-unsupported-version-error.md).

### F3: Seven schemas without a file-validated reader and writer (for operator review)

For each of these, the search covered the filename, `$id` and version
consts, and then the schema's distinctive field names. The last step is what
found the hand-rolled equivalents below.

| Schema | What code actually does | How established |
| --- | --- | --- |
| `handoff-frontmatter` | **It is written by code, and the output can fail the schema.** `journal._render_handoff` ([`journal.py:737-779`](../../../../../src/gigai/journal.py)) writes every handoff's front matter with this schema's field set, then merges in caller front matter. When artifacts are committed, the journal fills in `artifact_refs` itself ([`journal.py:407-417`](../../../../../src/gigai/journal.py)), and the schema's `additionalProperties: false` rejects that key. Readers (`_read_handoff`, recovery at `journal.py:505-526`) check selected fields by hand. | **executed** twice. (1) A synthetic `_render_handoff` call passes with default metadata only and fails with `artifact_refs`. (2) Across a real test run, **0 of 28** journal handoffs conform (F6). The code paths were read. |
| `role-reference` | **It is read and written by code, in a different shape.** `roles.py` builds, serializes (`RoleReference.as_dict`) and parses role references, and is called from [`validators.py:399,584`](../../../../../src/gigai/validators.py), `model_execution.py:199`, `scout_proposals.py:331`, `scout_proposal_execution.py:157` and `adapters/port.py:51`. Its structured decoder `_structured` ([`roles.py:106-120`](../../../../../src/gigai/roles.py)) requires the key set to be exactly `{namespace, id, version}`. The schema also requires `schema_version`, so a schema-valid object is rejected by the code, and the code's shape fails the schema. The schema's `id` pattern also excludes `_`, which `roles.py` doesn't check. Persisted contracts store `role` as a plain string (`goal-graph.schema.json:139`, `model-invocation*.schema.json`, `review-bundle.schema.json:70`). | read |
| `scout-answer-association` | **It is written and hand-checked by code, in a divergent shape.** `scout_proposal_records.py:331` builds `{record_id, revision_id, question_ids, purpose, content_sha256}`, and `scout_proposal_records.py:167` requires exactly that key set. The schema additionally requires `schema_version` (`scout-answer-association:1`) and `association_id`. | read |
| `scout-tailor-selection-v2` | **It has a writer, but the output is never validated against the file.** [`scout_tailor_selection.py:229`](../../../../../src/gigai/scout_tailor_selection.py) emits `selector_version: "scout-tailor-selection:2"`. Its `to_json` output **does conform** for the test fixture's shape, which has a proposal and an answer (F6). Only the write site doesn't validate. There is no v1 file, and it isn't described in the README. | read; conformance **executed** for one shape |
| `scout-proposal-discovery-job` | **It is mirrored only in memory.** The `DiscoveryJob` dataclass ([`scout_discovery_job.py:21-41`](../../../../../src/gigai/scout_discovery_job.py)) has exactly the schema's eight properties, and the module describes itself as a read-only view. No `src/` module imports `scout_discovery_job`; only `tests/behaviors/acquisition/test_public_import_state.py` does. The dataclass is never serialized (the module's `to_json` belongs to the separate import-progress type). | read |
| `graph-selection-record-v2` | **It is validated, but no in-repo writer produces it.** It is validated at [`graph_set.py:323`](../../../../../src/gigai/graph_set.py), but the only producers of `agent_explicit` are tests. It may be meant for externally supplied records; that wasn't established. | read |
| `common` | It is reached only through `$ref`, from 68 schemas. This is expected and not a concern. | census |

**None of these is a retirement candidate.** The earlier version of this
document named `scout-answer-association` and `scout-proposal-discovery-job`
as having no evident reader or writer. That was wrong, and the claim is
withdrawn: both have code equivalents. The pattern that matters is
different. **Code and packaged schema describe the same concept in shapes
that disagree** (`handoff-frontmatter`, `role-reference`,
`scout-answer-association`), and nothing reconciles them, because the file
is never loaded on those paths. The operator should decide, per schema,
whether the file or the code is the authoritative contract.

### F4: The privacy guard recognizes only v1 external-recording payloads (needs a decision)

[`package_privacy.py:39-51`](../../../../../src/gigai/package_privacy.py)
lists `_PRIVATE_SCHEMAS`, used at `package_privacy.py:72-74`, to recognize
private material that has been "renamed into an otherwise legitimate
portable source tree". It lists the five **v1** external-recording schemas
and none of the v2 ones.

v1 external-recording schemas require `schema_version` from
`common.schema.json`, whose `schema_version` is `const: "1.0"`. The v2
schemas require `const: "2.0"`. So a v2 record can't satisfy any listed
schema, and it isn't recognized by this schema-shape check. The path-based
checks above it (`runs/`, `run-plans/`, …) still catch records left in
place.

**This was first established by reading. The follow-up in F6 then ran real
v2 records through the function:** a renamed v2 record returns `False`. Whether a renamed v2 record could actually reach the export path
wasn't traced. It is recorded as an open question that needs a decision,
not as a confirmed leak.

### F5: The naming scheme hides purpose

The schema filenames themselves are mostly descriptive. The *history* lives
only in the README's amendment narrative (G15 … SCOUT-R5). Three schemas in
F3 aren't described there at all.

The version suffix is also inconsistent:

- `-v1` appears on some first versions (`scout-document-revision-v1`,
  `scout-document-selection-v1`) but not on most others.
- `scout-tailor-selection-v2` has no v1 file.

Because the suffix means "feature variant" (F2) rather than "generation", a
name like `run-plan-v2` suggests supersession that the code doesn't
implement. A separate proposal could adopt one convention, for example a
per-family README table giving what selects each file and who writes it.
That would be a documentation change only, and it isn't authorized here.

### F6: Follow-up investigation of the open questions (2026-09-22)

This is docs-only follow-up work. Two things were executed, and both are
test-derived, not live runs. Both can be reproduced with the
[S14 reproduction kit](S14-repro/README.md), whose
[`results.txt`](S14-repro/results.txt) re-ran the preserved scripts and
matched every number below.

- One existing test file,
  `tests/behaviors/scout_discovery/test_scout07_discovery_run_flow.py`,
  was run into a scratch `--basetemp`. It reported `3 passed`.
- Scratch scripts were run over the workpads it produced.

No product code was changed.

**Single-version readers (open question 5):**

| Reader | Accepts | Can it receive the other version? | How |
| --- | --- | --- | --- |
| `portability.py:124-134` | v1 for export | It receives v2 but refuses it **deliberately**, with explicit `unsupported_schema_version` ("graph-selected active versions are inspection-only"). | read |
| `scout_tools.py:226,399` | v2 only | Scout tool bindings require graph-set authority. The refusal (`tool_authority_unavailable`) looks deliberate, but the code doesn't document that. | read |
| `target_effect.py:472-475` | v1 proposal and v1 pointer | **Not reachable from the product:** no `src/` module imports `target_effect`, only `test_g19_target_effect.py`. `cli.py:2462` reports `target_effect: "unsupported"`. | read |
| `occurrence.py:484-498` (`_active_pointer`, used by `declare_occurrence` at `occurrence.py:105`) | v1 pointer only | **Yes, and it's reachable through the `gigai occurrence declare` CLI (`cli.py:3221`).** A v2 pointer can never pass the v1 schema: v2 requires `graph_set`, and v1 has `additionalProperties: false` without it. So on a graph-set Gig, declaring an occurrence would refuse with "active Gig version is invalid". That misstates the cause, which is an unsupported version. | read; schema comparison executed. The CLI refusal wasn't run. |

**`handoff-frontmatter` against real journals:** all 28 handoff files the
test run produced were parsed with `journal._read_handoff` and validated
against the schema. **0 of 28 conform.**

| Cause | Count |
| --- | --- |
| `artifact_refs` is not an allowed property | 28 |
| `transition` value not in the schema's enum (`gig_graph_set_proposed`, `workpad_layout_migrated`, `scout_source_materialized`, `template_instance_bound`, `private_record_revised`, …) | 3 per transition kind |
| `actor.kind: "agent"` is not allowed. External recording writes it (`external_recording.py:754`). | 7 |
| Other unlisted keys: `operation`, `operation_key`, `layout_marker`, `layout_version` | 3 each |

This is a sample from one test file's workpads, not the whole transition
vocabulary. It is enough to show that the packaged schema doesn't describe
what the journal writes today. Because nothing validates handoffs against
the file (F3), the mismatch has no runtime effect. The effect is that the
packaged schema is not a trustworthy description of the journal format.

**`scout-tailor-selection-v2` conformance:** the `_selection()` fixture
from `test_scout_r2_tailor.py` was built, and `TailorSelection.to_json()`
was validated against the file. **It is valid.** That covers one shape
only; variants such as `proposal=None` or no answers weren't checked.

**Privacy guard (F4):** the run produced real v2 records for the checkpoint,
run and receipt families only. **Plan and invocation v2 weren't sampled**,
and PRIVACY-01's acceptance requires all five. Each one was
checked against its v2 schema and then passed to
`private_package_provenance`:

| Record | Valid against its v2 schema | Guard at its real path | Guard as `docs/x.json` |
| --- | --- | --- | --- |
| external-recording checkpoint v2 | true | **true** (path rule) | **false** |
| external-recording run v2 | true | **true** (path rule) | **false** |
| external-recording receipt v2 | true | **true** (path rule) | **false** |

The guard runs in `package.inspect_package` (`package.py:216`) on every
package file. So a v2 external-recording record copied into a package under
a non-private path isn't refused by this guard. A v1 record in the same
position would be expected to be refused, because the five v1 schemas are
listed. That expectation comes from reading; no v1 record was run. **This
still doesn't show an export leak:** it doesn't establish that a real
workflow copies such a record into a package. It stays a concrete
follow-up.

### Follow-up tickets (recorded 2026-09-22; not authorized)

- [CONTRACT-01](../../../followups/CONTRACT-01-handoff-frontmatter-alignment.md):
  decide which handoff fields and transitions are intended, then align the
  schema and the writer. Don't regenerate the schema from current output.
- [PRIVACY-01](../../../followups/PRIVACY-01-v2-external-recording-guard.md):
  the guard-level gap is confirmed; export reachability is unproven.
- [OCCURRENCE-01](../../../followups/OCCURRENCE-01-unsupported-version-error.md):
  add an explicit unsupported-version error. Graph-set support is a separate
  decision.
- [CONTRACT-02](../../../followups/CONTRACT-02-tailor-selection-write-validation.md):
  one fixture conforms; write-time validation is still missing.

The F6 evidence is Claude's reported result and hasn't yet been
independently reviewed by the operator.

## Full inventory

State key:

- **validated by current code:** at least one current call site.
- **validated; live variant:** in a multi-version family whose version is
  chosen by payload shape (F2).
- Flagged states are explained in F3.

Citations show up to three call sites as `file:line` under `src/gigai/`.

| Schema | Family | State | Evidence |
| --- | --- | --- | --- |
| `active-gig-version-v2.schema.json` | active-gig-version ×2 | validated; live variant | `run.py:1679`, `portability.py:124`, `listing.py:263` (+7 more). v2 iff pointer has `graph_set` (`run.py:1678-1682`, `lifecycle.py:2199`) |
| `active-gig-version.schema.json` | active-gig-version ×2 | validated; live variant | `run.py:1681`, `occurrence.py:494`, `portability.py:135` (+8 more). v2 iff pointer has `graph_set` (`run.py:1678-1682`, `lifecycle.py:2199`) |
| `addressed-artifact.schema.json` | addressed-artifact | validated by current code | `review.py:619` |
| `adjudication.schema.json` | adjudication | validated by current code | `run.py:2798`, `review.py:46`, `review.py:546` (+1 more) |
| `application-event.schema.json` | application-event | validated by current code | `application_events.py:133`, `application_events.py:587` |
| `capability-installation.schema.json` | capability-installation | validated by current code | `capabilities.py:373` |
| `capability-manifest.schema.json` | capability-manifest | validated by current code | `journal.py:1144`, `portability.py:158`, `capabilities.py:230` |
| `capability-review-decision.schema.json` | capability-review-decision | validated by current code | `capability_review.py:39` |
| `capability-successor-binding.schema.json` | capability-successor-binding | validated by current code | `capability_successor.py:45` |
| `common.schema.json` | common | `$ref`-only | no direct validation call; `$ref`'d by 68 of the other 81 schemas |
| `external-recording-checkpoint-v2.schema.json` | external-recording-checkpoint ×2 | validated; live variant | `scout_posting_inputs.py:109`, `scout_research_inputs.py:122`, `external_recording.py:276` (+4 more). same protocol switch as plan |
| `external-recording-checkpoint.schema.json` | external-recording-checkpoint ×2 | validated; live variant | `scout_posting_inputs.py:109`, `package_privacy.py:48`, `scout_research_inputs.py:122` (+7 more). same protocol switch as plan |
| `external-recording-invocation-v2.schema.json` | external-recording-invocation ×2 | validated; live variant | `external_recording.py:439`. `external_recording.py:439-441` by protocol |
| `external-recording-invocation.schema.json` | external-recording-invocation ×2 | validated; live variant | `package_privacy.py:45`, `external_recording.py:441`. `external_recording.py:439-441` by protocol |
| `external-recording-plan-v2.schema.json` | external-recording-plan ×2 | validated; live variant | `scout_posting_inputs.py:107`, `scout_research_inputs.py:120`, `external_recording.py:274` (+7 more). CLI `--protocol-version` **defaults to 1** (`external_cli.py:156-158`); v2 via `*_v2` entry points |
| `external-recording-plan.schema.json` | external-recording-plan ×2 | validated; live variant | `scout_posting_inputs.py:107`, `package_privacy.py:46`, `scout_research_inputs.py:120` (+10 more). CLI `--protocol-version` **defaults to 1** (`external_cli.py:156-158`); v2 via `*_v2` entry points |
| `external-recording-receipt-v2.schema.json` | external-recording-receipt ×2 | validated; live variant | `scout_posting_inputs.py:110`, `scout_research_inputs.py:123`, `scout_report_readers.py:71` (+4 more). same protocol switch as plan |
| `external-recording-receipt.schema.json` | external-recording-receipt ×2 | validated; live variant | `scout_posting_inputs.py:110`, `package_privacy.py:49`, `scout_research_inputs.py:123` (+7 more). same protocol switch as plan |
| `external-recording-run-v2.schema.json` | external-recording-run ×2 | validated; live variant | `scout_posting_inputs.py:108`, `scout_research_inputs.py:121`, `external_recording.py:275` (+5 more). same protocol switch as plan |
| `external-recording-run.schema.json` | external-recording-run ×2 | validated; live variant | `scout_posting_inputs.py:108`, `package_privacy.py:47`, `scout_research_inputs.py:121` (+8 more). same protocol switch as plan |
| `feedback.schema.json` | feedback | validated by current code | `review.py:45`, `review.py:542` |
| `finding.schema.json` | finding | validated by current code | `run.py:2792`, `review.py:44`, `review.py:379` (+1 more) |
| `gig-builder-session.schema.json` | gig-builder-session | validated by current code | `validators.py:204`, `lifecycle.py:750` |
| `gig-comparison.schema.json` | gig-comparison | validated by current code | `comparison.py:138` |
| `gig-discovery-manifest.schema.json` | gig-discovery-manifest | validated by current code | `discovery.py:316`, `lifecycle.py:1342` |
| `gig-graph-set.schema.json` | gig-graph-set | validated by current code | `graph_set.py:186` |
| `gig-occurrence.schema.json` | gig-occurrence | validated by current code | `occurrence.py:447`, `occurrence.py:579` |
| `gig-package.schema.json` | gig-package | validated by current code | `package.py:159` |
| `gig-proposal-v2.schema.json` | gig-proposal ×2 | validated; live variant | `run.py:1719`, `run.py:1765`, `capability_successor.py:425` (+8 more). v2 iff proposal has `graph_set` (`run.py:1764-1768`); v1-shaped proposals still built by `lifecycle.py` `revise_offline`/`_build_proposal_artifacts` |
| `gig-proposal.schema.json` | gig-proposal ×2 | validated; live variant | `run.py:1767`, `validators.py:746`, `portability.py:296` (+4 more). v2 iff proposal has `graph_set` (`run.py:1764-1768`); v1-shaped proposals still built by `lifecycle.py` `revise_offline`/`_build_proposal_artifacts` |
| `goal-graph.schema.json` | goal-graph | validated by current code | `run.py:1762`, `validators.py:752`, `scout_proposal_execution.py:640` (+1 more) |
| `graph-selection-record-v2.schema.json` | graph-selection-record ×2 | validated; **no in-repo writer** | dispatched by `graph_set.py:286` when `selection_kind == "agent_explicit"`, checked at `graph_set.py:323`; only tests construct `agent_explicit` (in-repo writers `run.py:2288`, `external_recording.py:911` emit `operator_explicit`) |
| `graph-selection-record.schema.json` | graph-selection-record ×2 | validated; live variant | `graph_set.py:286`. distinct selection kinds, not supersession (`graph_set.py:286`) |
| `handoff-frontmatter.schema.json` | handoff-frontmatter | **code writer; output diverges from file** | `journal.py:737-779` writes this shape; with `artifact_refs` (auto-added at `journal.py:407-417`) the output fails the schema (executed, F3). Tests pin its hash. |
| `improvement-manifest.schema.json` | improvement-manifest | validated by current code | `improvement.py:129` |
| `learning-record.schema.json` | learning-record | validated by current code | `learning.py:74` |
| `model-exchange.schema.json` | model-exchange | validated by current code | `validators.py:317` |
| `model-invocation-v2.schema.json` | model-invocation ×3 | validated; live variant | `validators.py:267`. writer emits 1.0/2.0/3.0 by feature (`model_execution.py:490`); reader dispatch `validators.py:264-270` |
| `model-invocation-v3.schema.json` | model-invocation ×3 | validated; live variant | `validators.py:265`. writer emits 1.0/2.0/3.0 by feature (`model_execution.py:490`); reader dispatch `validators.py:264-270` |
| `model-invocation.schema.json` | model-invocation ×3 | validated; live variant | `validators.py:269`, `provider_review.py:447`. writer emits 1.0/2.0/3.0 by feature (`model_execution.py:490`); reader dispatch `validators.py:264-270` |
| `native-record-content.schema.json` | native-record-content | validated by current code | `package_privacy.py:43`, `native_records.py:27`, `data/scout/tools/cap_00000000-0000-4000-8000-000000000074/discovery.py:200` |
| `private-record-revision.schema.json` | private-record-revision | validated by current code | `private_records.py:446`, `private_records.py:458`, `private_records.py:472` (+8 more) |
| `proposal-draft-manifest.schema.json` | proposal-draft-manifest | validated by current code | `validators.py:232` |
| `proposal-interview.schema.json` | proposal-interview | validated by current code | `lifecycle.py:341`, `lifecycle.py:495`, `lifecycle.py:569` (+3 more) |
| `provider-review-closeout-receipt.schema.json` | provider-review-closeout-receipt | validated by current code | `provider_review.py:92`, `provider_review.py:519` |
| `reference-record.schema.json` | reference-record | validated by current code | `private_records.py:317`, `private_records.py:322`, `private_records.py:334` (+4 more) |
| `report.schema.json` | report | validated by current code | `run.py:2799`, `review_loop.py:516`, `package_privacy.py:50` (+5 more) |
| `requirements-baseline-approval.schema.json` | requirements-baseline-approval | validated by current code | `run_plan.py:327`, `run_plan.py:431` |
| `review-bundle.schema.json` | review-bundle | validated by current code | `run.py:2791`, `review_loop.py:250`, `validators.py:392` (+4 more) |
| `review-contract.schema.json` | review-contract | validated by current code | `review.py:43`, `review.py:307`, `provider_review.py:622` (+1 more) |
| `review-input-record.schema.json` | review-input-record | validated by current code | `run_plan.py:604`, `run_plan.py:787`, `run_plan.py:790` |
| `review-loop.schema.json` | review-loop | validated by current code | `run.py:2800`, `review.py:591`, `provider_review.py:864` |
| `role-reference.schema.json` | role-reference | **code reader/writer; shape diverges from file** | `roles.py:106-120` requires exactly `{namespace, id, version}`; the schema also requires `schema_version` (F3). Tests validate the file: `test_g28_roles.py:81,90`. |
| `run-brief-frontmatter.schema.json` | run-brief-frontmatter | validated by current code | `run.py:2419` |
| `run-details.schema.json` | run-details | validated by current code | `run.py:797`, `run.py:2455`, `scout_proposal_execution.py:636` (+1 more) |
| `run-input-record.schema.json` | run-input-record | validated by current code | `private_records.py:354`, `private_records.py:359`, `private_records.py:371` (+4 more) |
| `run-manifest-v2.schema.json` | run-manifest ×2 | validated; live variant | `run.py:869`, `run.py:2427`, `comparison.py:161` (+1 more). v2 iff a graph descriptor was selected (`run.py:319`, `run.py:2427`) |
| `run-manifest.schema.json` | run-manifest ×2 | validated; live variant | `run.py:866`, `run.py:2429`, `comparison.py:160` (+2 more). v2 iff a graph descriptor was selected (`run.py:319`, `run.py:2427`) |
| `run-plan-v2.schema.json` | run-plan ×2 | validated; live variant | `run_plan.py:1387`. writer emits `plan_version: 2` iff descriptor present (`run_plan.py:2096`); reader dispatch `run_plan.py:1386-1390` |
| `run-plan.schema.json` | run-plan ×2 | validated; live variant | `run_plan.py:1389`. writer emits `plan_version: 2` iff descriptor present (`run_plan.py:2096`); reader dispatch `run_plan.py:1386-1390` |
| `runtime-comparison-attempt.schema.json` | runtime-comparison-attempt | validated by current code | `runtime_comparison.py:51` |
| `runtime-comparison-intent.schema.json` | runtime-comparison-intent | validated by current code | `runtime_comparison.py:52` |
| `runtime-comparison.schema.json` | runtime-comparison | validated by current code | `runtime_comparison.py:50` |
| `runtime-evaluation-pack.schema.json` | runtime-evaluation-pack | validated by current code | `runtime_comparison.py:49` |
| `scout-answer-association.schema.json` | scout-answer-association | **code writer/checker; shape diverges from file** | `scout_proposal_records.py:331` builds, and `:167` requires, a key set without `schema_version`/`association_id` (F3) |
| `scout-definition-export-manifest.schema.json` | scout-definition-export-manifest | validated by current code | `private_transfer.py:249` |
| `scout-document-revision-v1.schema.json` | scout-document-revision | validated by current code | `application_events.py:246` |
| `scout-document-selection-v1.schema.json` | scout-document-selection ×2 | validated; live variant | `scout_report_readers.py:231`, `scout_document_records.py:259`. `scout_documents.py:141` writes `:1`; `scout_document_records.py:233` writes `:2` when a `source_run` exists |
| `scout-document-selection-v2.schema.json` | scout-document-selection ×2 | validated; live variant | `scout_report_readers.py:231`, `scout_document_records.py:259`. `scout_documents.py:141` writes `:1`; `scout_document_records.py:233` writes `:2` when a `source_run` exists |
| `scout-interview-preparation.schema.json` | scout-interview-preparation | validated by current code | `scout_interview_records.py:44` |
| `scout-operation-receipt.schema.json` | scout-operation-receipt | validated by current code | `private_records.py:227`, `private_records.py:237`, `private_records.py:275` (+4 more) |
| `scout-private-transfer-manifest.schema.json` | scout-private-transfer-manifest | validated by current code | `private_transfer.py:251` |
| `scout-proposal-discovery-job.schema.json` | scout-proposal-discovery-job | **in-memory mirror only** | same eight fields as `DiscoveryJob` (`scout_discovery_job.py:21-41`); the module is imported only by a test; never serialized (F3) |
| `scout-proposal-revision.schema.json` | scout-proposal-revision | validated by current code | `scout_proposal_records.py:184` |
| `scout-public-import-input.schema.json` | scout-public-import-input | validated by current code | `scout_acquisition_records.py:388` |
| `scout-public-import-progress.schema.json` | scout-public-import-progress | validated by current code | `scout_acquisition_records.py:359` |
| `scout-tailor-selection-v2.schema.json` | scout-tailor-selection | **writer, never validated against file** | `scout_tailor_selection.py:229` emits `selector_version: "scout-tailor-selection:2"`; no code validates against this file; no v1 file exists; not described in schemas README |
| `target-effect.schema.json` | target-effect | validated by current code | `validators.py:426` |
| `template-instance-binding.schema.json` | template-instance-binding | validated by current code | `default_init.py:196` |
| `trace.schema.json` | trace | validated by current code | `run.py:2793`, `review_loop.py:270`, `review.py:47` (+2 more) |
| `verification-record.schema.json` | verification-record | validated by current code | `run.py:2795`, `review.py:730`, `provider_review.py:769` (+1 more) |
| `workpad-layout.schema.json` | workpad-layout | validated by current code | `private_records.py:146` |

## Open questions for the operator

1. Should the v1 external-recording protocol stay the CLI default? That is
   what keeps five v1 schemas live.
2. Should F4 be fixed, and where: by extending `_PRIVATE_SCHEMAS`, or by
   recognizing the family rather than one version?
3. For `handoff-frontmatter`, `role-reference` and
   `scout-answer-association`, is the packaged file or the hand-rolled code
   the authoritative contract? Today they disagree (F3).
4. Is `scout-tailor-selection-v2` meant to be validated at its write site?
   Is `scout-proposal-discovery-job` meant to become a persisted record, or
   only document the in-memory view?
5. ~~Can the single-version readers listed in F2 receive the other
   version?~~ Answered in F6. `occurrence declare` is reachable with a v2
   pointer and would refuse with a misleading error. The other readers are
   deliberate or unreachable.
7. Should `handoff-frontmatter` be regenerated from what the journal
   actually writes, or should the journal be constrained to the schema?
   Today no sampled real handoff conforms (F6).
6. Would a per-family "selector and writer" table in the schemas README be
   useful as a separate documentation follow-up?

## Relationship to S15

S15 asks how goals and tools should be *composed* going forward. This audit
shows the current sprawl mostly comes from the additive-variant rule applied
to live feature combinations, not from abandoned generations. A composition
model would reduce the count only if it also changed how optional features
are versioned. That is an input to S15, not a conclusion from it.

No schema deletion, merge, version bump or code change follows from this
document.

## Revision notes (same-day correction after operator review)

1. **Count corrected from 75 to 76.** The inventory's own rows were 51
   single-version + 24 live variants + `graph-selection-record-v2`, which is
   validated but was flagged separately. The summary had dropped that last
   one.
2. **"No schema-file reference" was wrongly treated as "no reader or
   writer."** `role-reference` and `handoff-frontmatter` were called
   tests-only, and `scout-answer-association` and
   `scout-proposal-discovery-job` were called unreferenced. Tracing by field
   names showed code equivalents for all four:
   - `roles.py` for role references.
   - `journal.py` for handoff front matter. An executed check shows that
     output can fail the schema.
   - `scout_proposal_records.py` for answer associations.
   - The `DiscoveryJob` dataclass for discovery jobs.

   F3 and the affected table rows were rewritten, and the "no evident
   reader or writer" claim is withdrawn.

**Second review round (same day):**

- F2's stale sentence saying the single-version readers weren't traced is
  replaced with its F6 follow-up.
- The Method section no longer claims "no tests were run".
- The executed checks are now reproducible from
  [S14-repro/](S14-repro/README.md): the preserved scripts, exact commands
  and sanitized `results.txt`, which re-ran and matched.
- The privacy sample's limit (plan and invocation v2 unsampled) is stated
  explicitly.
