# S18 — Scout interview-prep goal graphs, used by agents as a tool

**Requested:** 2026-09-23.
**Status:** Research recorded 2026-09-23. This is the operator's idea,
captured as a spike, not a decision. No graph, schema, tool, or endpoint is
added by this ticket.
**READ vs EXECUTED:** everything is READ (source, existing goal-graph
markdown, and the JEV research evidence doc, read fully). No production
source, schema, or test file was edited. No git add/commit performed.

## Problem

The operator's idea: *"extend Scout with a prep-for-interview workflow:
research the role, research the company's interview process and so on, and
figure out what they could ask, with probabilities like JEV. The agents
(Claude, Codex or whatever) can use gigai Scout to do the research and
prepare the person for the interview. That's one set of goal graphs."*

v0.1.8 already shipped a `prepare-interview` goal graph and interview-record
plumbing, but not company-interview-process research or typed question
prediction. This spike investigates what exists to build on, how an agent
would actually call Scout to run this workflow, how to map JEV's typed
decision primitives onto question prediction without overclaiming
calibration, where to source company interview-process information without
violating a site's terms, and what a minimal first slice would look like —
all under the architecture rule that this stays a Scout (gig) concern, never
a core one.

## Architecture rule (applies to every proposal below)

> GigAI is a platform; a Gig is a self-contained package built on the
> platform. Scout is a Gig (later: trader, shopper, not now). A gig depends
> on and imports gigai core; core never imports any gig. Core provides
> registration and discovery points; gigs plug in.

Any new graph, record kind, or schema this spike proposes lives inside
`src/gigai/scout/` (or a new `src/gigai/scout/interview_prep*.py` /
`data/goalgraphs/*.md`), never in core. Where a new capability needs a core
registration point that doesn't exist yet (e.g. a new record kind), that is
exactly S16's registration seam — this spike does not propose bypassing it
with a direct core edit.

## Tasks

1. Inventory the goal-graph family and existing pieces to reuse.
2. Investigate how an agent uses Scout as a tool (CLI, localhost API, MCP,
   Claude Code skill) with consent/privacy tradeoffs.
3. Read the JEV research fully and map its primitives onto question
   prediction, including the confidence/abstention contract and a
   calibration-measurement proposal.
4. Identify terms-respecting sources for company interview-process research.
5. Propose a minimal first slice and its acceptance evidence.

## Acceptance criteria

- Every existing-piece claim cites `file:line`.
- The JEV evidence doc is cited as read in full, and its confidence/
  abstention contract is carried into the proposal, not restated loosely.
- Scraping/ToS risk is flagged for named sites, per S09/S13's existing
  discipline.
- Ends with open questions, non-claims, and a dated change-log entry.

## 1. Investigate: the goal-graph family and existing pieces

### What already exists to build on

| Piece | file:line | What it gives this idea |
| --- | --- | --- |
| `prepare-interview` goal graph (markdown instructions) | `src/gigai/scout/data/goalgraphs/prepare-interview.md:1-21` | Already establishes role + stage/format, selects existing research/candidate evidence/prior feedback, produces reusable sections (role expectations, technical topics, evidence-backed stories, practice questions, questions for the interviewer, gaps), records practice feedback as feedback (not proof of skill), and explicitly forbids turning hypothetical exercises into claimed experience. This is the graph a company-interview-process research step and question-prediction step would plug into. |
| `research-role` goal graph | `src/gigai/scout/data/goalgraphs/research-role.md:1-30` | Already documents responsibilities, common deliverables, and separates broad-pattern research from one employer's specifics; already has the source/citation/digest discipline ("every source needs its supplied URL/locator... captured content needs an exact digest") this idea's company-research step should reuse verbatim, not reinvent. |
| `InterviewGraph` facade | `src/gigai/scout/interview.py:22-27` | Declares `required_inputs=("role","stage_or_format")`, `optional_inputs=("posting","research_revisions","candidate_evidence","prior_feedback")`, `effects=("write_workpad",)`. A "company interview process" and "predicted questions" input/output would extend this dataclass or add a sibling `InterviewPrepGraph`. |
| `interview_records.py` (prepare/revise/read/list/feedback) | `src/gigai/scout/interview_records.py:1-757` (public functions at lines listed in S17's inventory: `prepare_interview`, `revise_interview`, `save_interview_feedback`, `read_interview_preparation`, `list_interview_preparations`) | The record-write/read machinery for interview preparation already exists; question-prediction output would be a new field/record type published through the same journal pattern (see S17's Pattern 1), not a new subsystem. |
| `research.py` / `research_v3.py` / `research_inputs.py` | `src/gigai/scout/research.py:1-220`, `research_v3.py:1-341`, `research_inputs.py:1-749` | Domain-validated research packets already exist with a fixed schema and resource discipline (`FIXED_DOMAIN_RESOURCES`, per S16's inventory); a "research the company's interview process" step is structurally the same shape as today's role research, differing only in subject matter and source set. |
| `proposals.py` / `proposal_records.py` | `src/gigai/scout/proposals.py:108-848`, `proposal_records.py:43-528` | Existing evidence/provenance-linking pattern (source refs, identity checks) that a "predicted questions" record should reuse for citing which posting/research/company-source backs each predicted question. |
| `PinnedResume`, posting snapshots, `RequirementMatrixRow` | `src/gigai/scout/find_jobs/contracts.py:633,857` (both read directly in this spike) | The pinned resume and requirements-matrix contracts already exist and already carry the private-evidence discipline (pinned digest, resolved bytes) this idea's "prep the person" step needs for grounding predicted questions in the candidate's actual resume, without re-deriving a new private-data contract. |
| Localhost API (`find_jobs.present_api`) | `src/gigai/scout/find_jobs/present_api.py:288-317` (`do_GET`/`do_POST` route dispatch), `:264-269` (`_check_loopback`) | Existing precedent for exposing Scout functionality over HTTP: `/api/config` (GET), `/api/run` (POST), `/api/runs/{id}` and `/api/runs/{id}/results` (GET), all gated by a peer-address loopback check (`client_address[0] in {"127.0.0.1","::1"}`, else 403). A new interview-prep endpoint would follow this exact pattern, not a new server. |

### Candidate goal-graph shape (proposal, not a decision)

```
research-role (existing)
      │
      ▼
research-company-interview-process (NEW — same shape as research-role,
      │                              different subject: company's known
      │                              interview stages/format/culture from
      │                              terms-respecting sources)
      ▼
predict-questions (NEW — typed question list with per-question probability/
      │             confidence, grounded in role + company research +
      │             posting + pinned resume; JEV-shaped output, see §3)
      ▼
prep-plan (extends existing prepare-interview graph — folds predicted
      │     questions into the existing reusable sections: role
      │     expectations, evidence-backed stories, practice questions)
      ▼
mock-drill (optional, later — interactive practice using prep-plan output;
            feeds back into existing save_interview_feedback, which already
            records practice feedback as feedback, not skill proof)
```

`research-company-interview-process` and `predict-questions` are the two new
nodes; `prep-plan` is `prepare-interview` unchanged in mechanism but fed
richer inputs; `mock-drill` is explicitly marked optional/later since it's
the least specified piece and the operator's framing ("figure out what they
could ask, with probabilities") centers on prediction, not drilling.

## 2. Investigate: how an agent uses Scout as a tool

| Option | How it would work | Trade-offs |
| --- | --- | --- |
| **CLI** | Agent shells out to `gigai scout-interview prepare ...` (existing pattern, `interview_cli.py:47-95`) plus a new `predict-questions` subcommand | Lowest new-surface cost — reuses the exact mechanism every other Scout capability already exposes to an agent harness. Downside: an agent must parse CLI stdout/JSON, and consent/scope must be re-derived from CLI flags each call (no persistent session). |
| **Localhost API** (`find_jobs.present_api`, extended) | Add `/api/interview-prep/run` (POST) and `/api/interview-prep/{id}/results` (GET) following the existing loopback-gated route pattern at `present_api.py:288-317` | Already-proven pattern for a browser/agent client; loopback-only by construction (`_check_loopback`, `present_api.py:264-269`) so it doesn't need a new network-exposure decision. Downside: it's find-jobs's own server today (a single-purpose HTTP server per `find_jobs/present_api.py`), so extending it for interview-prep either grows that module's scope or requires a second small server — an open question, not resolved here. |
| **MCP server** | A new `gigai-scout` MCP server exposing `research_role`, `research_company_interview_process`, `predict_questions`, `prepare_interview` as MCP tools, callable by any MCP-aware agent (Claude Code, Codex, others) | Matches the operator's framing ("Claude, Codex or whatever" use Scout "as a tool") most directly — MCP is a genuinely provider-neutral tool-calling protocol, unlike a CLI wrapper each harness must be told how to invoke. Downside: a new dependency/process to build, test, and keep in sync with Scout's actual record shapes; needs its own consent-surfacing story (MCP tool calls don't have an inherent "show scope, get operator confirmation" step the way the loopback UI's `direct_local_ui_confirm` does per D5). |
| **Claude Code skill/plugin** | A packaged skill that wraps the CLI or MCP calls with instructions (similar to this repo's existing `.claude/skills/gigai-orchestrator/SKILL.md` pattern) | Lowest integration cost for Claude Code specifically; does nothing for Codex or another harness — the operator's "whatever" framing suggests this shouldn't be the *only* path. Best treated as a thin convenience layer on top of the CLI or MCP option, not a replacement for either. |

**Recommendation for further research (not a decision):** MCP server as the
target integration (matches "any agent" framing) with CLI as the
already-working fallback/interim; a Claude Code skill as a thin wrapper on
top of either once one exists. The localhost-API route is attractive for
consistency with `find_jobs` but needs its scope-vs-`find_jobs`-ownership
question resolved first.

### Consent and privacy boundaries

Per the v0.1.8 D5/D11 contract already established for `find_jobs`
(`docs/development/v0.1.8/phase-2/evidence/P2-FREEZE-04-amendment-02-find-jobs-functional.md:36`,
D5: loopback-only UI consent redeemed before allocation with an exact typed
envelope; D11: network/credential effects admitted only for a sealed,
registered graph): any interview-prep flow that (a) sends the resume or
other private candidate evidence to a hosted model, or (b) fetches a
third-party URL for company research, must surface that as an explicit,
scoped consent step before the effect runs — never silently. The pinned
resume and other private evidence (`PinnedResume`,
`find_jobs/contracts.py:633`) must never leave the local machine without
that explicit consent, matching the existing "public search/config values
only; private preferences/resume bytes never enter [a hosted request]"
discipline already recorded in the v0.1.8 roadmap's gate table
(`v0.1.8-find-jobs-functional-roadmap.md:144`).

## 3. Question prediction with typed probabilities (JEV research)

The JEV/TypeSafe research evidence
(`docs/development/v0.1.8/spikes/evidence/jev-typesafe-ai-structured-decisions-research.md`,
read in full for this spike) documents three primitives: `choice` (one
option plus a per-option probability distribution and a confidence
statistic), `score` (a probability-weighted position on a caller-defined
ordered rubric, plus distribution and confidence), and `noul` (a bare
yes/no probability with no separate confidence field) — see the evidence
doc's "What is documented versus inferred" table (lines 17-28) and the
concrete request/response example (lines 67-128).

### Mapping onto interview-question prediction

| JEV primitive | Interview-prep use | Example |
| --- | --- | --- |
| `choice` | Predicted question **category** (behavioral / system-design / coding / take-home / culture-fit / compensation) | `{"type": "choice", "choice": "system_design", "probabilities": {"system_design": 0.62, "behavioral": 0.21, "coding": 0.17}, "confidence": 0.7}` |
| `noul` | "Will this specific candidate question likely be asked at this company/stage?" as a yes/no probability per candidate question | `{"type": "noul", "noul": 0.74}` for "Will they ask about your gap year?" |
| `score` | "How central is this topic to the interview loop?" on a caller-defined ordered rubric (e.g. `unlikely` / `possible` / `likely` / `core-topic`) | `{"type": "score", "score": 2.3, "legend": {...}, "confidence": 0.68}` |

Per the evidence doc's explicit warning (lines 144-149, "Probability,
confidence, and calibration"): **do not call a one-off `0.74` a verified 74%
chance the question will be asked.** These are model-produced uncertainty
signals that must be checked against the actual task distribution, not
treated as calibrated ground truth by default. The evidence doc's routing
example (lines 155-157) is directly applicable: a predicted question's
probability may recommend *reviewing* that topic more, but it must never
itself claim "the interviewer will definitely ask this" or silently become
an inflated confidence claim about the candidate's fit.

### Adopting JEV's confidence/abstention contract (from the evidence doc, lines 159-176)

```text
if transport/error/timeout/truncation/schema_invalid:
    outcome = refused_or_failed; no prediction is published
elif evidence_missing (no posting / no company research / no role research):
    outcome = abstain; tell the operator what's missing before predicting
elif confidence/probability below the frozen task threshold:
    outcome = uncertain; surface as "possible but not confident" in the prep output
else:
    outcome = typed_prediction; included in prep-plan output as a scored/probable item
```

This directly reuses the evidence doc's own proposed contract (lines
165-174) rather than inventing a new one; the threshold itself is a policy
parameter to evaluate later, not a value to assume now.

### Frozen cases and a grader (per the evidence doc's "Frozen cases and
grader" section, lines 233-255)

Before any live prediction, freeze a small versioned pack: synthetic
role/company/posting inputs with known interview-loop shapes (from public,
consented sources — see §4), gold "was this topic actually asked"
outcomes where available (see calibration-measurement proposal below), and
known-bad cases (wrong category, invented company-specific claim not
supported by any cited source, malformed output). A deterministic grader
checks schema validity, category correctness, and — critically — whether
each predicted question's cited source actually supports it, exactly as the
evidence doc's grader design requires (lines 242, "evidence support").

### Measuring calibration against actual outcomes (new proposal for this idea)

Model probabilities are not calibrated by default (evidence doc line 142,
citing TypeSafe's own group-calibration definition). This spike proposes
Scout record **actual interview outcomes** after the fact — which
predicted questions were actually asked, which weren't — as a new, small
record kind (e.g. `interview_outcome_recorded`, following S17's
`RecordRepository` pattern) linked back to the `prep-plan`'s predicted
questions by ID. Over enough recorded outcomes, a calibration check (bucket
predicted probability, compare to observed asked-frequency, per the evidence
doc's own definition of calibration) becomes possible. This is a measurement
proposal, not a promise the codebase currently has a calibration report —
none exists today, and this spike does not build one.

### Local model, hosted model, or Jev itself

The evidence doc is explicit (lines 178-192, 262) that Jev has **no
documented local/offline deployment path** — it is a hosted TypeSafe
service. Using it directly would mean sending the constructed `state`
(role, company research, posting, and potentially resume-derived text) to a
third-party hosted API, which is a hosted-model network effect requiring the
same explicit consent as any other hosted call (D5/D11 above) — and a
**cost** implication (per-call pricing, not evaluated here). The evidence
doc's own recommended next step (line 264) is "a separately authorized,
offline-first synthetic comparison," not adoption. For this idea, three
options remain open: (a) a local model (Qwen/Ollama, per Spike 6/S10) doing
structured-output-constrained prediction without JEV's specific `choice`/
`score`/`noul` types but the same shape; (b) a hosted general model (GPT,
Claude) with the same structured-output shape; (c) Jev itself, hosted, with
its specific typed primitives. None is decided here; each needs the same
frozen-case/grader evaluation the evidence doc already specifies before any
adoption claim.

## 4. Sources for company interview-process research

Per S09's and S13's already-recorded scraping/ToS discipline
(`docs/development/v0.1.8/spikes/evidence/S09-local-search-retrieval-capability-sourcing-research.md:15,36`
and `S13-existing-job-discovery-solutions-research.md:29,69`), this spike
extends the same caution to interview-process-specific sources:

| Source | Assessment |
| --- | --- |
| The job posting text itself | Already a first-class Scout input (`find_jobs/contracts.py`'s posting DTOs); zero additional risk — it's the same data Scout already ingests. |
| Company engineering blogs / careers pages | Public, typically no login wall, standard web-fetch etiquette (respect `robots.txt`, as S09 already checks for ATS feeds at `S09-...md:36`) — lowest-risk new source category. |
| Operator's own notes (prior interview experience, referrals) | Explicitly operator-supplied private material — the safest source, and the one the operator's framing ("figure out what they could ask") most directly implies for a specific company they've researched before. Should be a first-class optional input, same trust tier as `candidate_evidence` in the existing `InterviewGraph` (`interview.py:25`). |
| Glassdoor, Blind, LeetCode Discuss | **Flagged as scraping/ToS risk, not recommended as an automated source.** These sites are widely used for exactly this purpose (crowd-sourced interview questions) but typically have anti-scraping terms and rate-limit/anti-bot defenses; S13 already flagged general job-board scraping legal exposure for similar big-board sites (`S13-...md:69`, "ToS/scraping-legality posture ... was not assessed"). This spike does not recommend automating access to these sites; if the operator has an account and reads content manually/interactively (not via an automated scraper), that becomes "operator's own notes" above, not a Scout-automated fetch. |
| General web search (Exa, per S09) | Same provider S09 already evaluated for job discovery; reusable for company-research web search generally, subject to S09's existing cost/coverage/privacy findings (not re-derived here). |

## 5. Minimal first slice (proposal)

**One graph, one agent path:** extend the existing `prepare-interview` graph
with a single new optional input, `company_research_revision` (pointing to
an existing or newly-created company-interview-process research packet,
reusing the `research-role`/`research.py` shape exactly, just a different
subject), and one new output field in the prep-plan: a `predicted_topics`
list using only the `choice` mapping from §3 (predicted question
*category*, e.g. behavioral/system-design/coding) — deliberately deferring
per-question `noul` predictions and the calibration-measurement record to a
later slice, since category-level prediction needs less evidence per
prediction and is easier to grade for "is this category actually
representative of the posting/company" than a specific verbatim question
guess.

**Agent path:** CLI only for the first slice (`gigai scout-interview
prepare ... --company-research <revision-id>`), since it's the
already-working mechanism (§2) and defers the MCP-server-vs-localhost-API
decision to a later slice once the CLI shape is proven.

**Acceptance evidence for the first slice (proposal):**

- A frozen synthetic case (fake company, fake posting, fake company-research
  packet) with a hand-labeled expected topic distribution.
- The CLI command produces a `predicted_topics` list whose schema validates
  and whose `choice` value is one of the caller-defined category labels
  (never a free-text guess).
- A recorded abstention case: when no company-research revision is
  supplied, the command explicitly abstains from predicting topics (per
  §3's contract) rather than guessing from role research alone.
- No private resume bytes appear in any request to a hosted model during
  this first slice (it should not need the resume at all for
  category-level prediction) — an explicit non-goal to verify, not assume.

## Open questions for the operator

1. Should the localhost API extension live inside `find_jobs/present_api.py`
   (growing its scope beyond find-jobs) or as a new, second small server —
   or should the first agent-integration path be CLI-only until an MCP
   server is separately authorized?
2. Is per-question `noul` prediction (a probability per *specific* predicted
   question) wanted at all, or is category-level `choice` prediction (the
   proposed first slice) sufficient value on its own?
3. Does the operator want Jev itself evaluated as a hosted option (with its
   cost/privacy implications), or should local/hosted-general-model
   structured output be tried first, deferring Jev evaluation entirely?
4. Is recording actual interview outcomes (for calibration measurement) something
   the operator is willing to do manually after real interviews, or is this
   too much overhead to be worth building the record kind for now?

## Non-claims

- No goal graph, schema, record kind, CLI command, HTTP route, or MCP server
  was added.
- No claim is made that JEV/Jev's probabilities, or any model's, are
  calibrated for this task — calibration must be measured, not assumed.
- No scraping of Glassdoor/Blind/LeetCode Discuss (or any site) was
  performed or is recommended by this spike.
- The "minimal first slice" is a proposal for review, not a committed scope
  or an estimate of effort.
- This spike does not evaluate cost, does not sign up for or call any
  hosted API (Jev or otherwise), and does not select a final agent-tool
  integration mechanism.

## Change log

- 2026-09-23: Spike recorded. Read the JEV/TypeSafe evidence doc in full;
  inventoried existing Scout pieces to reuse (`prepare-interview` and
  `research-role` goal graphs, `InterviewGraph` facade, interview/research
  records modules, pinned resume and requirements-matrix contracts, the
  loopback-gated `find_jobs` localhost API) with file:line citations;
  compared CLI, localhost API, MCP server, and Claude Code skill as
  agent-integration options with trade-offs and a non-binding
  recommendation; mapped JEV's `choice`/`score`/`noul` primitives onto
  question-category/question/topic-centrality prediction; adopted JEV's
  confidence/abstention contract verbatim; proposed a new interview-outcome
  record kind to measure calibration against actual results; flagged
  Glassdoor/Blind/LeetCode Discuss as scraping/ToS risk per S09/S13's
  existing discipline and recommended terms-respecting sources instead;
  proposed a minimal category-level-only first slice with acceptance
  evidence; recorded four open questions for the operator.
