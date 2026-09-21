# Scout local runtime decision — 2026-09-11

## Operator decision and limits

The operator accepted identifying the local execution setup and moving on with
Scout, rather than making further OS isolation experiments a release blocker.
This supersedes the spike's mandatory sandbox/container/helper installation
requirement for personal-use v0.1.7. It does not claim the isolation experiments
proved an absolute no-egress or arbitrary-filesystem protection guarantee.

Trust the user's installed local runtime and host. The GigAI caller must verify
an explicitly selected numeric-loopback endpoint and installed local model
identity/digest, refuse known cloud/remote targets and redirects, avoid inherited
proxies, and never silently fall back to hosted inference. No auto model download,
daemon restart, installation, or global configuration changes. A trusted local
server could itself be compromised or misconfigured; endpoint/model checks do
not independently prove its entire process tree is offline.

Private records, matching context, proposals and answers stay on local paths.
Hosted agents must not receive those inputs or local private output through
GigAI. Public acquisition uses only explicitly public requests/content. PII
detection remains optional defense-in-depth, not disclosure authorization.
This decision does not authorize using real user data in development tests.

## Product boundary retained

Daily acquisition retains considered postings and acquisition exclusions.
Private local assessment adds user-relative fit/rejection reasons, salary and
sponsorship checks, evidence, proposed resume focus and focused questions.
Proposals are private, revisioned and presented in simple local HTML; state.sqlite
is a rebuildable projection of journal authority. Changes to preferences and
experience create new assessments without overwriting prior evidence.

Only an explicit user request starts Tailor; neither ranking nor a proposal
generates a resume automatically or records an application. Model output must
be complete and validated before being presented as an accepted proposal.
Truncated/reasoning-only/invalid output is a failed attempt, not a finished job.

## Resume implementation

Use the supported Ollama API for the first core adapter. The experimental direct
bundled runner is evidence, not a required runtime dependency or a substitute
for Ollama API integration. Its incomplete 650-token outputs do not establish
model incapability; budget and template handling need bounded real verification.

Parallel bounded authoring can proceed on (1) a GigAI-owned local transport
adapter and (2) Scout proposal construction/validation. These modules need
reviewed configuration/caller and journal/projection integration before release;
standalone helper tests do not complete those goals. Bring the needed local
runtime slice forward to support private proposals; retain RUNTIME-01 comparison
and installed release evidence as later gates. Preserve historical SCOUT-00 and
G43 approvals unchanged. Independent review remains required for new boundaries.
