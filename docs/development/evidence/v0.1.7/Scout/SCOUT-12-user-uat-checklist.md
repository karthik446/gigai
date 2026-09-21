# v0.1.7 Scout — user review and UAT checklist

Operator update: human UAT follows publication for this personal-use release.
Implementation, automated checks and installed-artifact verification still
precede publication, which requires explicit authorization. Install the
published package normally for UAT; failures become tracked patch fixes.

Status: planned acceptance checklist, **not executed**. The automated candidate
handoff is recorded in `SCOUT-R7-candidate-verification-20260920.md`; human UAT
still follows explicit publication. This document does not mark any
implementation, approval, evaluation, publication or UAT complete.

Use the exact published wheel and its recorded digest in an isolated local
workspace first. Begin with synthetic information; using personal data and
choosing which agents may receive it remain explicit user decisions. Record
the actual installed version, commands, model/backend identity, result paths,
failures and timing alongside each completed scenario.

## Code review before UAT

- [ ] Review the release diff, including existing G43/runtime changes separately
  from Scout; component review verdicts are not whole-release approval.
- [ ] Inspect packaged default source, local tool behavior, storage ownership,
  template-update behavior and privacy boundaries.
- [ ] Confirm release tests and installed-artifact evidence apply to the exact
  candidate being tested, with remaining limitations stated plainly.

## User workflows

- [ ] Initialize with a username. Included default Gigs become owned instances;
  repeating initialization is recoverable and does not duplicate or overwrite
  customizations. Required Gig approvals remain visible and separate.
- [ ] Ask an agent to research a Forward Deployed Engineer role without a
  resume or posting. Open readable responsibilities, role variations, sources,
  compensation context or explicit unknowns, and useful follow-up questions.
- [ ] Extend that research in a later Run. Both revisions remain inspectable;
  the newer research does not rewrite the earlier Run's inputs or evidence.
- [ ] Supply a resume and preferences, including a sponsorship requirement.
  Find matching, excluded, stale and unresolved opportunities. Unknown employer
  sponsorship must not appear as confirmed eligibility. Rediscovery retains
  old snapshots and links duplicates without conflating equal titles.
- [ ] Open the day's saved local proposals: fit reasons, salary/sponsorship
  uncertainty, focus and questions link to exact inputs. Inspect discarded and
  duplicate postings and their reasons. Answer a question, change a preference
  and reassess; old proposals remain explainable. Finding/ranking jobs creates
  no resume and records no application. A bounded discovery invocation exits
  with saved progress rather than leaving an unbounded background process.
- [ ] Explicitly request Tailor for a supplied or discovered posting, optionally
  selecting its proposal and saved answers. Produce only the requested resume, cover letter, or
  both. Inspect the requirement/evidence matrix, gaps and advisory checks;
  answer an experience question and create a new draft without invented claims.
- [ ] Finalize a document. Confirm this does **not** mark the job as applied.
  Explicitly record an application, retry it, then correct a date or note.
  History stays append-only and the retry creates no duplicate application.
- [ ] Prepare for an interview using selected research and candidate evidence.
  Start with role-only preparation, then opportunity-specific preparation;
  unrelated private records must not be silently included.
- [ ] Open the simple local tracker. Follow job, document, source and Run links.
  Use local CRUD, rebuild the query/report view, and confirm saved truth is
  unchanged. Customize UI source; regeneration must not overwrite it.
- [ ] Restart the agent/session and resume from saved Gig context. Transfer the
  Gig through the supported private path and confirm referenced history is
  readable without paths pointing back into the old workspace.
- [ ] Run the same synthetic case through local Ollama and Luna/Codex using
  GigAI's comparison workflow. Inspect separate attempts, quality checks,
  durations, failures and full setup identities. No silent hosted fallback or
  model-quality conclusion from an unsuccessful request.

## Final decision

- [ ] User records code-review outcome and UAT results, including any accepted
  limitations or release-blocking issues.
- [ ] Candidate documentation and version match the tested artifact.
- [ ] Tagging, merging and publishing receive a separate explicit instruction.

The coordinator must provide working commands and readable artifact links
with the completed candidate handoff; this checklist is not a substitute for
that handoff or an instruction to test unfinished features now.

## Candidate handoff boundary

The exact candidate wheel and digest, installed verification environment,
full-matrix commands, failure groups, and post-publication UAT commands belong
to the R7 candidate-verification report. Keep this checklist focused on the
operator's later review: do not substitute a source checkout, a dependency
incomplete environment, or a synthetic injected transport for the published
package and real user decision.
