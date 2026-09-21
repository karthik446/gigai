# Scout search-first execution and GigAI local applications

Recorded: 2026-09-20 (America/Denver).  
Status: product direction and detailed execution topic for v0.1.8; not an
implemented contract, approved schema, or expansion of the active v0.1.7 wave.

This is a refinement of the existing direction, not a replacement roadmap or
a platform pivot. The user wants Scout to be installable, customizable,
functional local software, with persistent state and local-model assistance.
Its primary daily value is finding fresh jobs and quickly making them actionable.
Resume generation is a requested follow-up, not the default output of discovery.

## Motivation and scope

The operator wants to act on suitable jobs soon after they appear, ideally
within the first hour or two. Treat that as a responsiveness objective, not an
established claim that early applications are the only way to get hired.
Optimize time from discovery to visibility and from visibility to an informed
decision. Do not delay basic usefulness while waiting for an LLM.

Scout's long development exposed both domain requirements and reusable platform
needs. Preserve those lessons without making every future Gig inherit all of
Scout's domain complexity. This topic covers Scout's search/assessment/resume
experience and the general GigAI boundaries that support it.

## Scout: save first, assess independently

```text
Acquire a public job
  -> save source snapshot and discovery metadata
  -> immediately show "new — assessment pending"
  -> extract the job's requirements matrix
  -> select a relevant saved resume revision (separate decision)
  -> assess against that revision and selected private preferences/experience
  -> save a brief proposal, evidence, suggested edits and questions
  -> update the dashboard's assessment and ranking

User answers questions / changes preferences or experience
  -> new assessment revision; previous inputs and results remain explainable

User explicitly requests tailoring
  -> suggested/authorized edits -> new resume revision -> explicit final choice

User explicitly records application
  -> application history; never inferred from a finalized resume
```

Acquisition must not wait for extraction, resume selection, or assessment.
A slow/unavailable local model leaves visible saved jobs with pending or failed
assessment status. It must not hide jobs or lose acquisition progress. Keep the
bounded job/deadline/resume behavior; separately design any recurring scheduler
instead of silently installing an always-running agent.

Retain distinct metadata for first discovered time, last observed time, fetched
snapshot time, and the employer/source's claimed posting time. Posting time may
be missing, ambiguous, or changed by reposting: preserve its raw value and source,
do not manufacture an exact age. Record whether the job came from a user,
agent/search run, or another supported acquisition path, with source and Run links.
Keep duplicates, exclusions and failures with reasons; "not shown at the top"
must not mean "silently discarded."

## Requirement matrix and actionable proposals

The matrix separates employer requirements, candidate evidence, uncertainty,
and hard user constraints. Private preferences include salary, sponsorship,
location and other user-selected conditions; they stay local during assessment.

| Requirement | Selected evidence | Assessment | Action |
| --- | --- | --- | --- |
| Python | Specific resume/experience example | Supported | Keep relevant example |
| GCP | Not mentioned in selected resume | Unknown, not absence of experience | Ask whether user has relevant experience |
| RAG | Related retrieval experience, but no confirmed RAG claim | Needs clarification | Ask what was built and what the user owned |
| Location | Posting conflicts with saved preference | Constraint mismatch | Flag separately from skill coverage |
| Sponsorship | Posting silent; user requires it | Unresolved constraint | Request confirmation; do not assume eligibility |

A useful one-page proposal includes why the role fits, salary/sponsorship
uncertainty, unmet requirements, suggested evidence-backed edits, focused
questions, and references to the exact posting, resume, preferences and experience
revisions used. Missing resume text is not proof of missing experience.

Distinguish:

- Evidence exists but the resume omits it: an editing opportunity.
- Experience might exist but is unrecorded: a question for the user.
- The user lacks a requirement: a genuine gap, not permission to invent a claim.
- Salary, location or sponsorship conflicts: constraints, not details to average
  away inside a favorable skill score.

Answers become separately sourced local facts, with provenance and correction
history. They can inform later assessments and requested tailoring. An agent can
load the saved proposal/matrix and exact resume, review suggested changes, and
make edits when authorized without reconstructing context from old chat.

## Resume library and selection

Maintain generated and imported resumes as a user-owned local library: stable
identity, tags, creation date, revision/parent identity, intended role or focus,
source experience, and originating Gig/Run where applicable. Human-readable
Markdown with front matter is the desired initial representation; exact folder
and schema mapping remain a design decision, not a new v0.1.7 storage contract.
Imported material must not be assigned an invented generation Run.

Resume selection is separate from evaluation. Support an explicit user selection,
an explicit latest-version policy, or a proposed best-suited revision with a reason.
Newest does not necessarily mean most relevant. Never silently switch the resume
under an existing assessment. Retain the selected revision and selection rationale;
edits create new revisions, not rewritten historical assessment inputs.

## Dashboard and ranking

Show new jobs for the day/hour or since the last visit, pending assessments,
shortlisted/excluded jobs and reasons, questions awaiting answers, documents and
final selections, and explicitly recorded applications. Link to job details,
source snapshots, the requirements matrix, selected resume and originating Runs.

Offer freshness and match-oriented views without pretending to know hiring
probabilities. Example summary: "Found 18 minutes ago; 7 requirements supported;
2 questions; sponsorship unknown." Clearly label age as discovery age when the
posting time is unknown. Explain each ranking through its underlying criteria.

Match percentage and fixable percentage are desired concepts to investigate,
not frozen formulas. Define the denominator, required/preferred weighting,
constraint treatment, missing evidence and uncertainty before displaying them.
If used, describe them as rubric-based coverage/editability scores, never a
probability of hiring. Do not count an unanswered question as an assured fix.
Changing a rubric must not silently change historical score meaning.

The dashboard must work with inference disabled: browse history, inspect evidence,
change preferences, record application events and view status using ordinary
local software. Models are used only for operations requiring interpretation or
generation, not every read or mutation.

## GigAI: portable applications, not disposable prompt bundles

GigAI owns reusable execution, eligible model selection, validated persistence,
history/provenance, output validation and review/verification facilities. Each
Gig owns its domain code, goal graphs, records, documents, views and customizable
assets. Data must remain available beyond a chat session or one agent provider.

Portability means more than producing a ZIP: install/restore elsewhere, inspect
history, bind local configuration and a suitable model, and continue through
supported commands. Preserve historical bytes and provenance; separate them
from new-machine execution authorization. Do not copy credentials or silently
inherit permission to execute. Distinguish definition export from explicit
private-data transfer. Do not treat the ongoing v0.1.7 transfer work as already
accepted second-home functionality.

SQLite supports local queryable views. Keep the current authoritative-record/
rebuildable-projection distinction until an explicit design changes it. A UI
must call the same validated operations as agent/CLI tools, not introduce a
second independently writable source of truth.

FastAPI plus a simple React frontend is an option for a future local dashboard,
informed by the user's experience in another project. It is not a framework
decision, deployed-service requirement or v0.1.7 addition. Compare it with the
existing generated HTML and other local interfaces in the accessibility spike.
If chosen, specify loopback binding, local request protection, server lifetime,
installation and model-off operation. Frontend replacement remains possible
without replacing the Gig's data or domain operations.

Before building a second full Gig, audit concrete reusable boundaries: snapshot
and provenance storage; revisioned records; execution/output validation; local
read/report and transfer facilities. Identify actual Gig-agnostic APIs/tests
versus Scout-specific implementations. A generic name is not evidence of reuse.
Use a small second-domain fixture as a cheap leading indicator, then measure
whether a subsequent Gig needs substantially less platform work.

## Local inference, search and structured decisions

Local Qwen is a candidate for extraction, resume selection, assessment, questions
and drafting, subject to measured quality. It avoids metered provider inference
charges for those operations, not hardware/electricity costs or latency.
Searching/fetching is a separate capability with its own availability, access
terms, quotas and possible charges; local inference alone does not supply it.
Separate public retrieval queries from private assessment inputs, including
preferences that could leak through overly personalized search queries. No
silent hosted fallback for private material.

Use the [Jev structured-decision research](../spikes/evidence/jev-typesafe-ai-structured-decisions-research.md)
as a conceptual reference, not an adoption decision. Typed output is a useful
boundary; valid JSON, confidence, evidence support and correctness are different
properties. Compare Luna/Codex and Qwen/local Ollama as complete execution setups
on the same frozen synthetic cases and output contracts. Preserve per-criterion
evidence, unknowns/abstention, raw failures and retries; pin model/runtime/settings
and measure warm/cold latency, quality and total turnaround. Do not equate a
model-generated confidence number with calibrated fit or hiring probability.

## Proposed execution sequence and evidence

1. Audit the existing Scout acquisition, proposal, document and report paths;
   identify missing behavior before proposing replacement infrastructure.
2. Freeze minimal job lifecycle/requirements/resume-selection/proposal shapes
   and lineage. Specify unknown timestamps, duplicates and changed inputs.
3. Prove immediate durable visibility with deliberately slow/failed assessment,
   restart recovery and bounded acquisition; no duplicate proposal publication.
4. Prove resume library selection, matrix evidence, focused answers and revised
   assessment, with no automatic Tailor or application event.
5. Prototype the local dashboard and portable model-off history experience;
   compare UI options without binding domain services to one frontend.
6. Evaluate structured local/CLI setups and measure reusable platform boundaries
   using a small non-Scout fixture before committing to another full Gig.

Measure discovery-to-visible latency separately from assessment latency, missing/
duplicate job counts, pending backlog age, source and requirement correctness,
question usefulness, evidence-backed edit acceptance, ranking usefulness and
resume-selection quality. Set thresholds from a representative workload; no
unmeasured speed or accuracy target is declared achieved here.

Open decisions: acquisition sources and polling cadence; scheduler ownership;
resume tagging/selection policy; matrix and scoring rubrics; UI choice and local
server behavior; hardware-specific quality/latency budgets. These need bounded
design/evaluation, not expansion of the active correction workers' tasks.

## Relationship to existing work

- [v0.1.8 backlog](../README.md): this direction adds a detailed topic, not a release gate.
- [S07 execution modes](../spikes/S07-execution-modes-and-cost-aware-orchestration.md)
  retains first full-spike priority; eligibility, cost and event-driven execution
  apply across Gigs.
- [Accessibility, memory and local-model spikes](../spikes/README.md) retain their
  scope and numbering. This topic gives them a concrete Scout use case.
- [v0.1.7 release execution graph](../../evidence/v0.1.7/Scout/SCOUT-release-execution-graph.md)
  and [current corrections](../../evidence/v0.1.7/Scout/SCOUT-R5-R6-correction-wave-20260920.md)
  remain unchanged. This document does not start any implementation, scheduler,
  model evaluation, migration or private-data export.
