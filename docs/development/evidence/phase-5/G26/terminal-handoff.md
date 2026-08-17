# G26 Terminal Handoff

- Status: Implementation handoff; waiting for human G24 acceptance
- Current consumer: G24 human UAT
- Downstream consumer: G27 adaptive Gig discovery, after G26 closeout

## What is ready

G26's model-facilitated builder path is implemented and machine-verified:

- setup/readiness distinguishes detected, configured, verified, usable,
  unavailable, and unsupported model targets;
- the deterministic installed target can drive typed questions and bounded
  proposal research;
- build start is explicit;
- drafts remain subordinate to `gig-proposal`;
- review supports revise, reject, and approve;
- provider calls are bounded by target, reference, network, cancellation, and
  budget policy; and
- recovery, mutation, installed-replay, and schema checks pass.

## What must happen next

Run G24 as a human UAT session on the real operator machine. The operator
must drive the commands and decisions; the review partner must inspect the
resulting CLI output, browser flow, SQLite rows, workpad artifacts, Git
handoffs, and reinstall behavior. Use G24's local-only evidence boundary and
keep raw prompts, references, model output, credentials, cookies, databases,
and target content out of Git.

The specific closeout gate is the G24 acceptance checklist, not another broad
automated test run. G26 should remain pending while the human session is
unperformed or while any storage, authority, usability, or data-boundary
finding remains unresolved.

After G24 evidence is accepted, update this handoff and
`completion-audit.md` to `Accepted`, record which target families are usable
versus detected-only/deferred, and then activate G27 runtime work against its
accepted discovery-manifest contract.

