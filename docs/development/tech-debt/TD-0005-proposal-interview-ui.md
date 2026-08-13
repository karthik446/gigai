# TD-0005 — Proposal interview UI

- Status: In progress
- Discovered during: G24 human UAT, 2026-08-13
- Affected surface: G22 local HTMX proposal interview
- Ownering lane: G24 UAT / later alpha-readiness cleanup

## Observation

The local proposal interview launches successfully, but the rendered operator
experience is not alpha-ready. The page is effectively unstyled: fields have
little spacing or hierarchy, labels run into controls, and each field has an
ambiguous `Save` button.

The screen exposes internal protocol concepts and opaque reference IDs without
operator explanations. It does not clearly show the request context, progress
through the interview, what remains unanswered, or what the next action will
do. Privacy, capability, and effect choices are presented as raw controls
rather than decisions with plain-language consequences.

## Proposed resolution

- Add a clear page hierarchy, readable spacing, responsive layout, and
  accessible labels and focus states.
- Replace opaque reference IDs with safe operator-facing names while retaining
  canonical IDs in the underlying record.
- Explain each effect, privacy, and capability choice in plain language.
- Show the Gig/request context, interview progress, pending questions, and the
  next permitted action.
- Replace ambiguous per-field `Save` controls with explicit actions and a
  visible completion/approval boundary.
- Preserve the loopback-only boundary, token/session checks, deterministic
  state transitions, and fail-closed behavior.
- Keep a usable plain/no-style fallback for constrained terminals or clients.

## Exit evidence

- A first-time operator can identify the request, current question, pending
  decisions, and next action without reading source code or schemas.
- A reference is understandable from its displayed label without exposing
  source contents or weakening digest identity.
- Every effect/privacy/capability choice has an understandable explanation.
- Keyboard navigation, focus visibility, and the no-style fallback are tested.
- UAT confirms that the improved page preserves the existing authority and
  approval boundaries and does not introduce new writes or network access.
