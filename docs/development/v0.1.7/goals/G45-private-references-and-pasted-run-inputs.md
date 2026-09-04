# G45 — Private References and Pasted Run Inputs

**Version:** v0.1.7
**Status:** Proposed — contract defined; not activated
**Depends on:** G40, G41, G42, G43, and G43.2 complete and ratified
**First consumer:** G46.1 Job Search Lifecycle v1 `tailor-application`
**Later consumers:** the broader G45 reference, research, and repository
workflows

## Outcome

G45 provides the first private, explicit reference path needed by a real Gig.
It lets an operator snapshot local text material as a stable private reference,
snapshot pasted text as a Run-specific input, and pass only those sealed
artifacts to a G43 Run Plan.

The initial path is intentionally small:

```text
operator-selected local text file
  -> private, immutable reference snapshot

operator-pasted job description
  -> private, immutable Run-input snapshot
  -> G43 Run Plan with exact artifact identities
  -> existing Run authority and direct confirmation
```

G45 is evidence and provenance infrastructure. It does not approve a Gig,
select an active version, classify a task, choose a model, grant a capability,
allocate a Run, or turn a local file into portable package material.

This is the first G45 delivery contract, not a claim that the broader G45
research/reference objective is complete. Repository classification, reference
sync, URL/network sources, and research reporting remain later explicitly
reviewed G45 increments. G45.2 separately owns bounded Exa-backed agent
research, immutable citation/lead snapshots, and lead-to-input binding; this
G45 path remains local only.

## First delivery boundary

The first G45 delivery exists to support G46.1. It accepts only:

- stable private references of kind `resume`, `project_evidence`,
  `role_history`, or `cover_letter`; and
- a Run input of kind `job_description` supplied as pasted text or an explicit
  local text file.

Accepted media types are UTF-8 `text/plain` and `text/markdown`. The importer
rejects binary files, PDFs, DOCX files, archives, directories, symlinks, device
files, oversized input, invalid UTF-8, and a path whose parent traversal or
symlink resolution is unsafe. G45 does not invoke OCR or document conversion.

The exact import limits are 1,048,576 bytes for one stable reference and
262,144 bytes for one Run input, whether supplied by `--file` or `--stdin`.
Labels are 1–120 Unicode scalar values after trimming and may not contain a
line break or control character. An omitted local-file label is its safe
basename; an omitted pasted-input label is `pasted-job-description`. The
importer reads at most limit + 1 bytes before refusal, so an oversized stream
is not fully retained in memory or written to the workpad.

The first delivery does **not** fetch a URL, crawl a repository, observe a
clipboard, inspect the surrounding agent conversation, synchronize a reference,
or make any provider or network call. Those are later G45 work and require their
own reviewed policy and consent details.

## Authority and privacy boundary

The registry remains the project/workpad binding authority. G41 continues to
own the portable `.gigai/packages/` boundary; G45 material is never added to a
portable package, catalog entry, Git-tracked package file, or package export.
The authoritative workpad stores these snapshots privately with owner-only
permissions.

The source file remains the operator's material. G45 creates an exact private
snapshot; it never edits, moves, deletes, renames, or overwrites the source.
The record retains a content digest and safe source class (`local_file` or
`operator_paste`), but not an absolute source path. Human output uses a supplied
label or safe basename only. JSON and diagnostics never emit source text,
credentials, home-directory prefixes, or the surrounding conversation.

A reference or Run input is not permission to use a provider. It may be read
only by a Run after G43 has bound its exact artifact reference into a sealed
plan and the existing G40 direct `--confirm` Run consent has succeeded. G45
does not infer consent from import, plan creation, or an agent envelope.

## Durable records

G45 adds two private, content-addressed record families under the authoritative
workpad:

```text
references/ref_<uuidv4>/reference.json
references/ref_<uuidv4>/source.txt
run-inputs/input_<uuidv4>/input.json
run-inputs/input_<uuidv4>/source.txt
```

`reference.json` is `reference-record.schema.json`, schema ID
`urn:gigai:schema:private-reference:1`, schema version `1.0`. It rejects
unknown fields and requires `schema_version`, `reference_id`, `project_id`,
`state`, `kind`, `privacy_class`, `label`, `origin`, `media_type`, `size_bytes`,
`content_sha256`, `snapshot`, `created_at`, and `created_by`. `reference_id`
matches `ref_<uuidv4>`; `state` is exactly `sealed`; `kind` is `resume`,
`project_evidence`, `role_history`, or `cover_letter`; `privacy_class` is
exactly `private_sensitive`; `origin` is `local_file`; `media_type` is
`text/plain` or `text/markdown`; `size_bytes` is 1 through 1,048,576; and
`snapshot` is an existing artifact reference whose path is exactly
`references/<reference_id>/source.txt` and whose digest, size, and media type
equal the enclosing fields. `label` follows the limit above and `created_by`
uses the existing actor definition.

`input.json` is `run-input-record.schema.json`, schema ID
`urn:gigai:schema:run-input-record:1`, schema version `1.0`. It rejects unknown
fields and requires `schema_version`, `run_input_id`, `project_id`, `state`,
`kind`, `privacy_class`, `label`, `origin`, `media_type`, `size_bytes`,
`content_sha256`, `snapshot`, `created_at`, and `created_by`. `run_input_id`
matches `input_<uuidv4>`; `state` is exactly `sealed`; `kind` is exactly
`job_description`; `privacy_class`, media type, label, actor, and snapshot
rules match the reference schema; `origin` is `local_file` or `operator_paste`;
`size_bytes` is 1 through 262,144; and the snapshot path is exactly
`run-inputs/<run_input_id>/source.txt`.

The record state is immutable: a missing or unreadable snapshot is reported as
`reference_not_found` or `run_input_not_found`, not persisted by rewriting its
state. The error enums are the stable diagnostic codes listed below; no record
has a mutable recovery or approval state.

Snapshot text is hashed exactly as imported bytes. The record ID is a generated
UUIDv4 identity; content equality is decided by the imported-byte SHA-256,
kind, media type, project ID, and privacy class. Re-importing equivalent bytes
for the same kind returns the existing record. Changed bytes create a new
record; an earlier snapshot is never rewritten.

The record artifacts are private evidence, not new lifecycle authority. The
G43 Run Plan stores their existing artifact references and digests. At handoff,
the Run revalidates those exact bytes before allocation. A missing, altered,
unreadable, foreign-project, malformed, or unsafe record fails closed.

## CLI and diagnostic contract

G45 adds explicit, non-effectful import and inspection commands:

```text
gigai reference add --kind KIND --file PATH [--label LABEL] [--target PATH]
gigai reference list [--target PATH] [--json]
gigai reference show REF_ID [--target PATH] [--json]
gigai run-input add --kind job_description (--file PATH | --stdin)
  [--label LABEL] [--target PATH]
gigai run-input show INPUT_ID [--target PATH] [--json]
```

`--stdin` consumes only the explicitly piped or pasted bytes. It does not read
terminal history or an agent transcript. `reference add` and `run-input add`
write only private workpad snapshots; they do not call a provider, mutate the
target repository, create a proposal, or allocate a Run.

The initial stable diagnostics are `reference_kind_unsupported`,
`reference_source_unsafe`, `reference_media_type_unsupported`,
`reference_too_large`, `reference_invalid_utf8`, `reference_not_found`,
`reference_project_mismatch`, `reference_digest_mismatch`,
`run_input_kind_unsupported`, `run_input_not_found`, `run_input_project_mismatch`,
and `run_input_digest_mismatch`. Human output gives a concise next action; JSON
uses the common `{ ok, error }` shape and contains only safe labels, IDs, kinds,
and digests.

## Run-plan binding

The G46.1 input contract requires at least one `resume` reference and exactly
one `job_description` Run input. Optional private references must be named by
their exact IDs. `gigai run-plan create` receives those IDs explicitly; it does
not inspect a directory for likely résumés or job material.

The resulting G43 plan must include each selected record's private artifact
reference and imported-byte digest in `inputs` and `sealed_sources`. A changed
source file outside the workpad does not alter a previously sealed snapshot. A
new job description creates a new Run-input record and therefore a new Run Plan
identity. The same resume reference can be reused across Runs.

No G45 command has a mutable “current resume” or “current job” selector. A
user must name the reference and Run-input IDs for each plan, and `gigai run
--plan PLAN_ID --confirm` remains the only effectful handoff.

## Acceptance evidence

G45 is complete only when evidence proves:

- local resume/project text imports create private snapshots without modifying
  the original files;
- equivalent import is idempotent and changed bytes create a distinct record;
- pasted job text is a Run input rather than a stable reference;
- reference and input records remain unavailable outside their bound project;
- malformed IDs, traversal, symlinked parents, symlink files, binary files,
  invalid UTF-8, oversized input, and changed snapshots fail closed;
- no command scans ambient files, captures conversation/clipboard contents,
  performs network activity, invokes a provider, or writes a target repository;
- G43 plans pin exact reference/input bytes and reject an altered or missing
  record before Run allocation;
- human and JSON projections disclose no raw source text or absolute personal
  paths; and
- a sanitized fixture pack and a private human UAT prove the same path without
  committing a real résumé, job description, credential, prompt, or Run output.

## Out of scope

- URL fetching, browser use, network reads, job-board access, login, or
  credential acquisition;
- PDF, DOCX, image, OCR, spreadsheet, archive, or arbitrary-binary ingestion;
- repository classification/crawling, reference synchronization, or research
  collection beyond this first local-text path;
- catalog entries, portable package contents, clone/create-from, or Gig
  proposal/approval authority;
- model selection, profile selection, Run Plan sealing, review routing, Run
  allocation, background work, or reporting; and
- application submission, email, external writes, or target mutation.

## Stop conditions

G45 must stop and remain incomplete if private material can enter a portable
package or committed evidence, an import can read an undeclared path, a source
snapshot can be silently replaced, a plan can consume changed bytes, a provider
can receive material before direct Run consent, or any URL/network/provider
behavior appears in this first delivery.
