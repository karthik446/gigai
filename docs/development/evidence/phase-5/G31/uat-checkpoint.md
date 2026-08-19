# G31 human UAT checkpoint

- Status: Partial operator checkpoint; final G31 UAT remains open
- Raw artifacts: kept outside Git in the operator's local UAT workspace

## Observed and resolved

- Browser-first setup opened locally and allowed a user-selected GigAI home and
  derived workpad location.
- The setup flow exposed CLI/API access, detected Codex and Claude, allowed
  multiple enabled models, and assigned reviewer, verifier, researcher, and Gig
  creator defaults.
- Claude authentication was initially classified as `authentication_required`
  when the bounded process could not see the provider session. After the
  operator configured the explicit `CLAUDE_CODE_OAUTH_TOKEN` boundary, the
  target became configured and usable.
- The adaptive create flow opened locally and exposed the Gig definition rather
  than a fixed review-loop workflow.
- The operator identified and recorded the stale installed G22 replay sequence;
  it was corrected before this release evidence was assembled.

## Remaining human scenarios

The operator still needs to run and record the final release-candidate pass
from a merged v0.1.6 install:

1. fresh setup, rerun, folder selection, and upgrade from the published
   predecessor;
2. create a representative Gig, build/review/revise/reject/approve its proposal,
   and confirm no target mutation before approval;
3. inspect SQLite `interview_events`, the workpad journal, projection/index
   ownership, and active-version authority without destructive rebuilds;
4. interrupt and recover setup/create, exercise missing credentials and CLI
   authentication failures, and inspect malformed/stale-target refusals;
5. run one approved Gig, inspect the result, and exercise the improve path; and
6. record expected, surprising, blocked, and unsafe outcomes separately.

No secrets, raw transcripts, private references, or local database contents
belong in the repository evidence.
