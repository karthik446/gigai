# Gigs to Create During UAT

- Status: UAT planning companion to G24; not an implementation contract
- Purpose: give human UAT a concrete set of repeatable Gigs to create, run,
  revise, and improve
- Evidence boundary: all session data stays under the local G24 UAT evidence
  directory and is never committed

## What this document is testing

A Gig is a repeatable set of Goals with a small number of changing run inputs.
The creation conversation should define the Gig once. Later runs should reuse
that approved definition while collecting only the context that changes for
that run.

The model is responsible for deciding which follow-up questions are useful. It
does not decide authority, approval, storage, or safety boundaries. GigAI
provides the bounded question structure and renders the questions in the local
interview. The operator decides whether the resulting Gig definition is worth
approving.

The same generic interview should support different Gigs. A Gig may ask for
different question types, options, references, and run inputs, but the UI
should not need a custom hardcoded screen for each Gig.

## The layers the operator should see

Every UAT session should distinguish these layers visibly:

```text
Gig definition
  -> reusable Goals, constraints, expected outputs, and question policy

Gig version
  -> the approved definition currently in force

Run input
  -> the small context that changes for this execution

Adaptive questions
  -> model-selected questions needed for this Gig and this Run

Run evidence and outputs
  -> research, decisions, artifacts, unresolved questions, and review results
```

When updating or improving a Gig, the original definition and original
references must remain visible beside the proposed changes. When running a
Gig, the current-run input must be visibly separate from the original material
used to define the Gig.

## Candidate 1 — Tailor resume for a job

Suggested Gig name: `tailor-resume-for-job`

### Stable Gig definition

The Gig should define a repeatable process for producing truthful,
job-specific application material. Its Goals may include:

1. Read and normalize the job posting.
2. Extract responsibilities, requirements, and signals about the role.
3. Compare the posting with the approved resume material.
4. Identify truthful strengths, gaps, and evidence to emphasize.
5. Produce the requested application material.
6. Review the result against the job posting and the Gig's constraints.

Initial references may include:

- the resume;
- an existing cover letter, if one exists; and
- optional supporting material such as a project list or work history.

Stable constraints should include factual accuracy, no invented experience,
preservation of the original resume, and explicit treatment of unresolved
qualification gaps.

### Run-time input

The normal changing input is a job URL. A run may also accept a pasted job
description when the URL is inaccessible.

### Questions the model may decide to ask

These are examples, not a mandatory questionnaire:

- What should the final output include?
  - tailored resume;
  - cover letter; or
  - both.
- Should the tone be conservative, direct, or highly tailored?
- Should missing qualifications be called out explicitly?
- Are particular experiences, projects, or skills important to emphasize?
- Should the original resume remain completely unchanged as a source artifact?
- What should happen when the job posting is inaccessible?

### UAT observations

Record whether the operator understands:

- which material defines the Gig and which job URL belongs only to the Run;
- why each follow-up question was asked;
- what the Gig will produce before approving it; and
- that approval creates or seals a Gig definition but does not silently submit
  an application or modify the original resume.

## Candidate 2 — Repository review and verify

Suggested Gig name: `review-and-verify-repository-change`

### Stable Gig definition

This Gig should repeatedly review a repository change against an explicit
review contract. Its Goals may include:

1. Inspect the selected change and relevant repository context.
2. Check behavior, tests, documentation, and boundary conditions.
3. Separate findings from questions and observations.
4. Produce evidence-backed findings with citations.
5. Recheck addressed findings and make a terminal review decision.

### Run-time input

Possible changing inputs are:

- a branch or commit range;
- a pull request URL or identifier; and
- an optional review focus supplied by the operator.

### Questions the model may decide to ask

- Is the review focused on correctness, security, usability, or all three?
- Should pre-existing issues be reported separately?
- What severity threshold should produce a blocking finding?
- Should generated files and dependency changes be included?
- Is a final approval/rejection decision required, or only findings?

### UAT observations

Check that the same review contract is reused across different changes while
the commit range and review focus remain Run inputs. Confirm that a model
opinion is not treated as an approval or as target-mutation authority.

## Candidate 3 — Release-readiness review

Suggested Gig name: `prepare-release-readiness-review`

### Stable Gig definition

This Gig should inspect a repository's release state and produce a bounded
readiness report. Its Goals may include:

1. Identify the intended package version and release scope.
2. Inspect version metadata, lockfiles, changelog, and release workflow.
3. Check release artifacts and required verification evidence.
4. Identify missing or contradictory release gates.
5. Produce a release checklist and a clear readiness decision.

### Run-time input

- candidate version;
- release branch or commit;
- optional release target such as TestPyPI or PyPI; and
- operator-specific release constraints.

### Questions the model may decide to ask

- Is this a patch, minor, alpha, or internal UAT release?
- Which publication environments are in scope?
- Should the review inspect only repository state or also installed artifacts?
- What evidence is required before calling the release ready?
- Should failures block publication or be recorded as follow-up debt?

### UAT observations

The Gig may recommend a release action, but the review must make clear whether
it has authority to perform that action. The operator should be able to see the
difference between a readiness finding, an approved proposal, and an actual
publication.

## Candidate 4 — Research and document synthesis

Suggested Gig name: `produce-research-brief`

### Stable Gig definition

This Gig should turn selected local references and an explicit research goal
into a cited brief. Its Goals may include:

1. Define the research question and intended audience.
2. Select and classify relevant references.
3. Identify disagreements, missing evidence, and uncertainty.
4. Produce a structured brief with citations.
5. Review whether every important claim is supported.

### Run-time input

- the current research question;
- additional references selected for this Run; and
- desired output format or audience.

### Questions the model may decide to ask

- Is the desired result exploratory, decision-oriented, or publication-ready?
- Which source types should be preferred?
- Should conflicting sources be presented side by side?
- How should unsupported claims be marked?
- What length and output format are useful?

### UAT observations

Confirm that selected references are explicit, that excluded references do not
enter the model context, and that the final brief distinguishes source-backed
claims from model synthesis and unresolved questions.

## Candidate 5 — Structured-data or finance review

Suggested Gig name: `review-structured-data-set`

### Stable Gig definition

This Gig should inspect a structured data set and produce a bounded analysis or
review. It must not imply trading, execution, or financial advice authority.
Its Goals may include:

1. Describe the data and its provenance.
2. Check schema, missingness, outliers, and consistency.
3. Answer the operator's stated analytical questions.
4. Identify limitations and alternative interpretations.
5. Produce a cited report and review checklist.

### Run-time input

- the current data file or table;
- the analysis question; and
- optional comparison period or segment.

### Questions the model may decide to ask

- What decision is the analysis intended to inform?
- Which columns or measures are in scope?
- Should anomalies be investigated or only reported?
- What comparison period is appropriate?
- Should the result remain descriptive and paper-only?

### UAT observations

Check that the Gig's analytical scope does not silently become execution
authority, and that data changes between Runs are visible as Run inputs rather
than changes to the Gig definition.

## Required lifecycle test for every candidate

For at least the first two candidates, run the same lifecycle:

1. Create the Gig from a short operator description.
2. Let the model ask adaptive follow-up questions.
3. Inspect the proposed Goals, inputs, outputs, constraints, and unresolved
   decisions.
4. Revise at least one answer and rebuild the proposal.
5. Reject one proposal and verify that no approved Gig is created.
6. Create and approve the intended version.
7. Run the Gig with one set of changing inputs.
8. Run the same Gig again with a different set of changing inputs.
9. Improve the Gig and compare the original and proposed definitions.
10. Reopen the session after interruption or reinstall and inspect the same
    authority boundaries.

The operator should record what they expected at each step before seeing the
result. Confusing wording, unnecessary questions, hidden assumptions, mixed
Run context, or unclear approval consequences are UAT findings even when the
underlying tests pass.

## Question-shape expectations

The model may select the questions, but each question rendered by GigAI should
make its shape understandable:

- free text when the operator must explain intent or constraints;
- single choice when the alternatives are known and mutually exclusive;
- multi-select when several outputs or references may be selected;
- confirmation when an explicit decision is required;
- reference selection when exact local files matter; and
- a clear dependency or reason when a question appears only after an earlier
  answer.

The model should ask only what is needed to define or run the Gig. It should
not re-ask stable facts already approved in the Gig definition unless the
operator is explicitly updating them.

## UAT record for each Gig

For each candidate, record locally:

```text
gig_name:
creation_inputs:
approved_definition_summary:
stable_references:
changing_run_inputs:
model_target_and_adapter:
questions_asked:
questions_skipped:
operator_expected:
observed_behavior:
proposal_revision_result:
approval_or_rejection_result:
run_result:
improvement_result:
operator_verdict:
review_partner_verdict:
follow_up:
```

Do not copy resumes, job postings, repository contents, prompts, credentials,
raw model output, or private workpad databases into this repository. Store only
sanitized observations and local pointers under the G24 UAT evidence boundary.

## Recommended order

1. `tailor-resume-for-job` — tests stable personal references plus a changing
   job URL and adaptive output questions.
2. `review-and-verify-repository-change` — tests repeatable review Goals and
   changing commit-range inputs.
3. `prepare-release-readiness-review` — tests recommendation versus actual
   release authority.
4. `produce-research-brief` — tests reference selection, citations, and
   uncertainty.
5. `review-structured-data-set` — tests a structured-data boundary when the
   operator has a suitable paper-only example.

The first candidate is the primary G24 dogfood workflow. The remaining
candidate Gigs test whether the builder is genuinely generic rather than
quietly optimized for resume tailoring.
