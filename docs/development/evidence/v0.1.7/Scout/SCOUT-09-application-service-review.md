# SCOUT-09 application service review

Date: 2026-09-10  
Scope: independent bounded review of the direct `application record`, `history`, and `status` service/CLI subset. No production or test files were changed by this review; this document is the only owned artifact.

## Executive disposition

The direct service subset is substantially correct for its demonstrated slice: it requires confirmation, publishes event and receipt atomically through the journal writer, rejects changed same-operation retries, folds status by UTC instant and journal sequence, isolates opportunities, and emits bounded JSON errors. The four owned tests pass (`4 passed in 5.19s`).

This is not whole SCOUT-09 acceptance. The opportunity identifier is still only regex-checked, `selected_user_request` and generic `record-application` Run integration are deferred, and no provider, activation, SQLite projection, or outbound effect is present or claimed.

## Reproducible findings

### F1 — High: document redemption trusts a stored digest pointer, not the committed bytes

`_redeem_documents` validates the referenced revision and scope, then compares the request digest only with `revision.content.snapshot_ref.content_sha256` (`src/gigai/application_events.py:184-216`). It does not fetch `snapshot_ref.path` from the same committed journal snapshot and recompute its bytes digest/size. A disposable probe created a committed private reference/revision, recorded an application against its referenced digest, changed the working reference source bytes to `tampered`, and a second application with a new operation key still returned `recorded`. This shows the current path does not establish the B4 requirement that the exact committed document bytes redeem the reference; the probe's working-tree mutation is evidence of the missing byte-authentication step, not a provider or acceptance result.

Minimal correction: after validating the revision, require a safe relative `snapshot_ref.path`, load that exact artifact from `snapshot.artifacts`, and compare both `digest_imported_bytes(bytes)` and `size_bytes` to the snapshot reference before publication. Keep the existing revision/project/Gig/opportunity checks and refuse missing, redirected, malformed, or mismatched artifacts without writing a transition.

### F2 — Medium: correction guard rejects a non-cycle branch

The service rejects any new correction if any prior event already supersedes the same target (`application_events.py:375-389`). Two disposable records demonstrated this: the first correction of event A was `recorded`; a second correction of A returned `application_correction_cycle`. A second child of A is not itself a cycle under the frozen contract (“earlier same-opportunity event and cannot form a cycle”), although product policy may choose to disallow branching. If branching is intentionally forbidden, freeze that as an explicit contract rule and test the typed refusal; otherwise detect actual ancestry cycles rather than repeated direct references and define deterministic status behavior for branches.

### F3 — Medium: event validation does not bind direct evidence fields to the event/receipt

`_validate_event` recomputes `requested_event_sha256` and `payload_sha256`, but does not verify that `request_evidence.scope_digest` equals `requested_event_sha256`, that the evidence actor/command/kind are the required direct-command values, or that the receipt's top-level `operation_key` equals the event's operation key (`application_events.py:130-153`, `347-372`). The generated path is deterministic and normal publication emits matching values, so ordinary retries are safe; however, a committed event/receipt with self-consistent payload hashes could contain semantically inconsistent evidence metadata. Minimal correction: validate the direct evidence union semantically and compare receipt operation/scope/payload identity to the redeemed event before returning `already_recorded`.

## Checks and focused probes

- `.venv/bin/pytest -q tests/test_scout09_application_events.py --disable-warnings --maxfail=1`: **4 passed**.
- The tests cover digest exclusion of generated identity/recording time, strict schema rejection, disposable CLI publication/replay, no-confirm no-write behavior, and per-opportunity status isolation.
- Disposable probes additionally confirmed changed notes under one operation key return `application_operation_conflict` and leave the journal commit count unchanged (`3` before and after); first correction records and repeated-target correction currently returns `application_correction_cycle`; missing document revision through `--json` returns exit 1 with typed `application_document_ref_missing` and no publication.
- Code inspection confirms UTC-aware ordering followed by journal sequence, same-Gig/event path checks, event requested/payload hash recomputation, and atomic event-plus-receipt publication under the journal writer lock.
- No network, provider, private-user data, full suite, wheel/package, activation, or commit work was performed.

## Deferred gates and minimal next acceptance

Named deferred gates remain: committed opportunity authority redemption (regex is not authority), exact `selected_user_request` private record/revision/message-index and normalized-request binding, generic `record-application` Run integration, and any later SQLite/UI/reporting/provider execution. The next narrow correction pass should add committed document-byte redemption, decide/freeze correction branching semantics, and bind evidence/receipt identity; then add focused fixtures for each without broadening schema families or claiming whole-feature acceptance.

## Correction clarification and disposition (2026-09-10)

The F1 probe's edit to the working reference source was not committed-byte
corruption. It demonstrated that the old application path did not itself
redeem the source bytes, while the journal's historical authority remained
healthy. The implementation now reads the exact published `snapshot_ref.path`
at the writer-pinned HEAD and validates its publication digest and size, so a
later working-copy/source edit does not change the historical document used by
application recording; no mirror-equality requirement was added to historical
authority.

The correction pass also removed the erroneous repeated-target-as-cycle rule:
two children may target an earlier same-opportunity event, while duplicate or
self event identity is refused. Direct evidence metadata and replay receipt
identity are now semantically bound to the separately committed event. The
original review findings and deferred product gates remain part of this record;
the opportunity regex is still not authority, and no whole SCOUT-09 acceptance
claim is made.
