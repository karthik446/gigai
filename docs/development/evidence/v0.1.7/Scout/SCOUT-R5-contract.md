# SCOUT-R5 interview and portability contract

Date: 2026-09-12 (America/Denver)

This is the Luna implementation contract for the bounded R5 slice. It does not
reopen accepted R0-R4 controls and is not release, provider, installed-wheel,
or live-runtime acceptance.

## Interview service

`gigai.scout_interview_records` is the journal-backed service. The supported
operations are:

```text
prepare_interview(selection, content, operation_key)
read_interview_preparation(record_id, revision_id=None)
revise_interview(record_id, parent_revision, content, operation_key)
save_interview_feedback(record_id, parent_revision, feedback, operation_key)
list_interview_preparations()
```

Each preparation and feedback operation publishes an immutable revision under
`records/scout-interviews/<record_id>/revisions/<revision_id>.json`, a readable
Markdown sidecar under `docs/interviews/<record_id>/<revision_id>/preparation.md`,
and a retry receipt under `records/operations/interview-<sha256(operation-key)>.json`.
`state.sqlite` is not authority. Reusing an operation key returns the original
record/revision; a changed payload is refused. Revision writes require the
current parent revision, preserving fresh-session lineage.

Selections require an explicit `role_only` or `opportunity` mode, role title,
stage/format, and explicit `reuse_prior_research`. Opportunity mode requires a
posting record/revision plus an exact snapshot identity. Selected research,
experience, feedback, and prior-preparation references are resolved against a
committed journal snapshot; source digests are checked when supplied. A role
only preparation cannot carry a posting reference.

Stories require source references. Hypothetical examples require the literal
`label: hypothetical`; feedback remains feedback and never becomes evidence of
a skill. Application state, employer decisions, invented metrics, and invented
skills are rejected. No model, provider, network, external recording, or new
agent loop is called.

The copied wrapper exposes the same flow as:

```text
python gig.py interview prepare --selection-file selection.json --content-file content.json --operation-key prep-001
python gig.py interview read --record-id record_... [--revision-id revision_...]
python gig.py interview feedback --record-id record_... --parent-revision revision_... --feedback-file feedback.json --operation-key feedback-001
python gig.py interview revise --record-id record_... --parent-revision revision_... --content-file content.json --operation-key revise-001
python gig.py interview list
```

The standalone adapter is `python -m gigai.scout_interview_cli`; the R6 main
CLI registration should expose it as `scout-interview` and register the
corresponding interview schema family without a second persistence path.

## Definition versus private transfer

`gigai.private_transfer` uses two distinct ZIP formats:

* `scout_definition_export` contains only inventoried/editable definition roots
  (`README.md`, `CHANGELOG.md`, `gig.py`, `goalgraphs/`, `ui/`, and `tools/`).
  It excludes records, documents, references, runs, reports, state.sqlite,
  credentials, tokens, configuration secrets, symlinks, and unlisted files.
* `scout_private_transfer` is an explicit operator-confirmed archive of one
  selected Gig's private history. It preserves journaled manifests, records,
  references, documents, runs, goalgraphs, and software working copies while
  omitting `state.sqlite`, generated reports, scratch, `.git/config`, hooks,
  credentials, and provider configuration. The manifest names project/Gig
  scope and says activation is `none`.

Both manifests list exact SHA-256 bytes and sizes. Imports validate every
member, refuse absolute/traversal paths, symlinks and hard-link markers, reject
manifest/member mismatches and refuse an existing destination before any
publication. Restore validates optional expected project/Gig identities. No
imported approval, active pointer, capability, provider target, or consent is
created; a second home must bind locally before new execution.

`compare_scout_template` and `decide_scout_template_update` provide the
explicit digest comparison and adopt/defer decision before a package update;
they never overwrite a customized working copy. A later approved materializer
must journal an adopted source snapshot and preserve prior records and graph
history.

Standalone commands are:

```text
python -m gigai.private_transfer_cli export-definition --workpad WORKPAD --archive scout-definition.zip
python -m gigai.private_transfer_cli import-definition --archive scout-definition.zip --destination SECOND_HOME
python -m gigai.private_transfer_cli backup-private --workpad WORKPAD --archive scout-private.zip --confirm
python -m gigai.private_transfer_cli restore-private --archive scout-private.zip --destination SECOND_HOME --confirm
```

R6 registration requests: add `scout-interview` and `scout-transfer` command
groups to the main CLI; register `scout-interview-preparation.schema.json`,
`scout-private-transfer-manifest.schema.json`, and
`scout-definition-export-manifest.schema.json` in the central schema registry,
installed inventory, `schemas/SHA256SUMS`, and verification tool. The service
must remain the sole journal writer.

## Bounded limits

This wave uses synthetic/offline fixtures only. It does not copy real user
data, call a provider/model/network, activate or publish a Gig, or claim a
second-home installed execution proof. The current checkout's dirty schema
inventory is an integration prerequisite owned by R6; focused R5 tests can
only run after that existing malformed schema is repaired.
