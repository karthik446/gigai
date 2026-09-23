# Record application progress

First read the [shared handoff rules](README.md). Require a selected opportunity
and explicit user request evidence. An application made outside Scout is valid;
do not fabricate a Tailor Run or finalized document to record it.

Supported facts are saved, applied, interview_scheduled, offer_received,
rejected and withdrawn. A correction explicitly supersedes an earlier event.
Retain occurred-at and recorded-at separately. Resolve relative dates only with
recorded user context and timezone; ask when ambiguous. Late events may predate
events recorded earlier. Preserve append-only history and deterministic ordering.

An invoking agent can supply a selected user instruction such as "mark it
applied", labeled `agent_reported_user_request`. This is not direct CLI consent
or cryptographic attestation. A suggestion, completed resume, passing check or
generated cover letter is not request evidence. With no request, ask a question
and leave the state unchanged.

Hand off an idempotent event with opportunity identity, action, date, notes,
request evidence and exact document revisions when available. Publish event and
submission receipt atomically through validated persistence. Never contact an
employer or submit an external application. Show the resulting history, not a
silently overwritten status field.
