# G24 — Human UAT and Dogfooding

- Status: Proposed for review; human-executed; not activated
- Type: User-acceptance and dogfooding goal; not a runtime implementation goal
- Depends on: G22 create/interview, G19 target-effect boundary, G20 improvement
  boundary, G21 recurring/comparison boundary, and G23 portability evidence
- Unblocks: G25 alpha release readiness and final repository cleanup

## Outcome

G24 is a guided human–AI acceptance loop for using GigAI on the operator's real
machine with real, representative work. The operator runs the commands and
makes the decisions. The review partner observes each result with the operator,
checks the actual artifacts and authority boundaries, records confusion or
surprise, and decides whether the session may continue.

G24 answers a product question that automated tests cannot answer:

> Can a real operator understand what GigAI is asking, what it records, what it
> is allowed to change, and what will happen next?

The first sessions must be deliberately slow. A session does not advance from
one lifecycle layer to the next until the current layer has been inspected and
accepted by the operator and review partner together.

## Contract gate

Before the first session, the operator and review partner must agree on:

1. the installed GigAI version and installation source;
2. the machine-local GigAI home and workpad locations;
3. the real target repository or work area being used;
4. the data and credentials that must never enter the session record;
5. the model target, adapter, network policy, and credential policy, if any;
6. the local-only UAT evidence directory; and
7. the stop rule for an unexpected write, unclear prompt, authority mismatch,
   data leak, or unexplained artifact.

The default first session uses the deterministic/offline path. A named external
model target may be used only when it is actually configured and supported by
the installed package. A model nickname such as Luna or Terra is not evidence
of adapter support; the local record must name the resolved target and adapter.

## Local evidence boundary

UAT evidence is stored outside the repository, for example:

```text
$HOME/.gigai/uat/g24/<session-id>/
```

The local record may contain sanitized command output, timestamps, package
version, resolved target/adapter names, artifact paths relative to the GigAI
home, schema names, SQLite table/column names, Git commit IDs, and operator
observations.

It must not contain prompts, source documents, reference contents, credentials,
tokens, raw model output, browser cookies, full home-directory paths, or copied
workpad databases. If an observation cannot be sanitized, record only its type
and a local pointer to the unsanitized material.

No UAT session data, local database, transcript, screenshot, or target-repository
content is committed or pushed by G24.

## Human session protocol

Each session follows this sequence. The review partner must ask the operator to
describe what they expected before showing the implementation-level result.

### 1. Installation and version checkpoint

Record:

```bash
which gigai
gigai --version
gigai --help
```

For a local checkout or rebuilt package, record the exact install/reinstall
command and verify the resulting executable points at the intended install.
Repeat the install with the same version to test idempotent reinstall behavior,
then repeat after a deliberate package-version change when one is available.

Accept only if the operator can answer which executable ran, which version ran,
and whether the command changed machine state.

### 2. Machine-state checkpoint

Run the setup/diagnostic path using the operator's chosen home and workpad
locations. Inspect the resulting configuration without exposing credential
values. Identify which files are authoritative configuration and which are
diagnostic or derived output.

The operator must be able to explain where `registry.sqlite` lives and what it
owns before proceeding.

### 3. Real target binding checkpoint

Run `gigai init` against the real target repository selected for UAT. Before and
after the command, inspect Git status and the target's local `.gigai` binding.
Confirm that the operator understands the exact target-side changes and that
no target content was silently edited.

Stop immediately for any unexpected tracked-file change, unexplained Git
commit, remote, credential access, or target write.

### 4. First `create` checkpoint

Run one real `gigai create` session for a bounded request. The operator answers
the single Gig-definition question in their own words. Local references are
optional and may be added only when the Gig needs local context. The initial
boundary defaults are private-workpad proposal output, local-only privacy, and
local reading only for references the operator explicitly adds. Follow-up
questions are shown only when the interview needs more context. Do not optimize
for a successful approval; record unclear wording, missing context, incorrect
assumptions, and questions that do not help the decision.

At the end of the interaction, inspect the proposal ID, Gig ID, session state,
selected references, effect/privacy/capability choices, and approval state.
Do not approve until the operator can explain what approval authorizes and what
it does not authorize.

### 5. SQLite checkpoint

Inspect only schema and sanitized rows. The review partner and operator answer:

- Which SQLite file was opened?
- Is it `registry.sqlite` or workpad `state.sqlite`?
- Which tables exist?
- Which rows are durable authority versus a projection or trace?
- Are prompt/reference contents stored, or only hashes/metadata?
- Can the database be deleted and rebuilt without changing authoritative Git
  history?

In the current implementation, G22's `interview_events` table is stored in the
same workpad `state.sqlite` file as the rebuildable `projection` table. Inspect
both tables and the ordered events. Confirm that the trace contains event
identity, state, payload hash, and timestamp rather than silently becoming a
second proposal authority. Then perform or observe a projection rebuild and
compare the table set and event rows before and after. If rebuilding the
projection replaces or erases `interview_events`, record that as a blocking
data-ownership finding; do not assume the two SQLite responsibilities are
compatible merely because they share a filename.

### 6. Workpad and journal checkpoint

Inspect the workpad tree and Git history. Review the proposal JSON, handoff
front matter, journal transition, goal graph, and any active-version pointer.
Map each visible artifact to its owner:

```text
registry.sqlite       -> machine target/workpad locator authority
proposal JSON         -> proposal contract
handoff Git commit     -> lifecycle transition authority
active version pointer -> approved Gig version authority
state.sqlite           -> workpad SQLite container
projection table       -> rebuildable read projection
interview_events       -> ordered interview trace metadata
```

The review partner must verify this map against the actual session, not merely
repeat the labels from this document.

### 7. Review, revise, approve, and reject checkpoints

Run at least one revision and one rejection path. For an approval path, inspect
the sealed version and confirm that approval does not start a Run or mutate the
target. For a rejection path, confirm the terminal decision and preserved
history.

If the operator cannot predict the result of a command from the CLI wording,
stop and record a usability finding before continuing.

### 8. Reopen and reinstall checkpoint

Reinstall or upgrade the package without deleting GigAI home state. Reopen the
existing target/workpad and verify that the same authoritative proposal and
journal history remain available. Compare the operator's expected state with
the observed state before and after reinstall.

### 9. Representative-workflow checkpoint

Repeat the guided loop with several real workflows, selected by the operator:

- review-and-verify feedback loop;
- repository or pull-request review;
- resume/job-material tailoring;
- research or document synthesis; and
- a structured-data or finance review, if appropriate.

Each workflow must use real operator intent and representative references, but
the local record stores only sanitized observations and identifiers.

## Review record for each checkpoint

The local UAT record uses this shape:

```text
session_id:
package_version:
install_source:
machine_and_model_policy:
workflow:
checkpoint:
operator_expected:
observed_command_result:
artifacts_inspected:
authority_mapping:
operator_verdict: pass | confusing | surprising | unsafe | blocked
review_partner_verdict:
finding:
next_action:
```

The operator verdict and review-partner verdict remain separate. Agreement is
useful evidence, not a substitute for recording disagreement.

## Acceptance criteria

G24 is complete only when:

1. At least one real-machine session completes installation, setup, target
   binding, `create`, artifact inspection, and an explicit operator decision.
2. The session includes one revision and one rejection, with their durable
   artifacts inspected.
3. The operator and review partner jointly trace one session across CLI,
   SQLite, workpad files, Git handoffs, active-version state, and rebuildable
   projections.
4. The authority map is verified against actual rows/files/commits and records
   at least one thing that was not intuitive from the CLI alone.
5. The SQLite checkpoint proves whether projection rebuild preserves the G22
   `interview_events` table and rows; any loss or unexplained replacement blocks
   closeout.
6. Reinstall or upgrade is performed without deleting the GigAI home, and the
   prior authoritative history is reopened successfully.
7. At least three representative workflows are attempted, including the
   review-and-verify dogfood case.
8. Every session records operator expectations, observed results, separate
   human–AI verdicts, and next actions in a local-only record.
9. Any unsafe, unexplained, or authority-confusing behavior is either resolved
   and re-tested or explicitly blocks G24 closeout.
10. No raw user data, credentials, model output, local database, or target
   repository content is committed or pushed.
11. A local UAT summary identifies the minimum changes or clarifications G25
    must address before alpha-readiness review.

## Stop boundary

Stop the session and preserve the local evidence when:

- the operator cannot tell what a command will do;
- SQLite appears to be treated as authority without a documented reason;
- a projection disagrees with committed Git history;
- a prompt, reference, credential, or model output crosses the evidence
  boundary unexpectedly;
- a real target changes without an explicit, understood authorization;
- reinstall changes or loses approved history; or
- the review partner and operator disagree about whether the result is safe.

G24 does not fix these findings silently during UAT. It records them, narrows
the next action, and sends unresolved product or contract changes to the proper
follow-up goal or G25 release-readiness review.
