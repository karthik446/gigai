# GigAI

GigAI is an open-source, local-first tool for turning repeatable work into
Gigs that can be run, reviewed, verified, and improved with user-controlled
runtime agents.

## The problem

AI tools are useful until the model, prompt, skill, or provider changes. A
provider can quietly change or nerf the current model, and a workflow that
worked last week can produce weaker results today. It is also difficult to
answer basic questions such as:

- What did each model do?
- What evidence did it use?
- Who reviewed the result?
- What changed between Runs?
- Can the workflow be repeated on another machine?

GigAI makes the workflow itself explicit instead of treating one model response
as the whole system.

## What is a Gig?

A Gig is a repeatable set of Goals with stable instructions, changing inputs,
and verifiable, reviewable results.

For example, `tailor-resume-for-a-job` keeps the resume workflow stable while
the job posting, target role, and user preferences change between Runs.

Each Gig starts with a built-in review-verify-fix loop. You can use that loop,
customize it, or define a different loop for your own Gig. GigAI does not treat
a model response as approval, execution authority, or proof of correctness.

## What GigAI enables

### Set up GigAI once

Run:

```bash
gigai setup
```

GigAI opens a local, token-protected browser setup flow. It walks through five
small decisions and shows the configuration before applying it.

1. **Workspace** — choose where GigAI keeps its private machine state. GigAI
   derives the private workpad folder underneath that location. The target
   repository is not moved and no project files are uploaded.
2. **Access** — choose whether this installation may use local CLI models, API
   providers, or both. This is a machine boundary, not a Gig definition.
3. **Models** — review the models GigAI can actually see, including installed
   Codex and Claude CLIs. A readiness check must succeed before a model can be
   enabled. API providers use environment-variable references; GigAI stores the
   variable name, never the secret value. Claude’s bounded process can use an
   explicitly exported `CLAUDE_CODE_OAUTH_TOKEN`. Create one with
   `claude setup-token`; see [Claude authentication](https://code.claude.com/docs/en/authentication#generate-a-long-lived-token).
4. **Roles** — assign machine defaults for reviewer, verifier, researcher, and
   Gig creation. These are starting defaults only. A Gig can define or override
   its own workflow roles later.
5. **Ready** — review the selected workspace, access boundary, enabled models,
   and role assignments. Setup changes are applied only after explicit
   confirmation and can be rerun without silently replacing an existing home.

Setup also detects a local editor for opening private workpads later. It does
not define a Gig, approve a proposal, run work, or modify a target repository.

After setup, diagnose the local installation with:

```bash
gigai doctor
```

`doctor` checks configuration, paths, editor resolution, installed adapters,
and local storage health without making provider calls.

### Create a Gig through an adaptive interview

```bash
gigai create tailor-resume-for-a-job
```

Gig creation opens a local browser session. The user describes the desired
work, adds optional local context, and answers only the follow-up questions
needed to define the Gig. Approval creates a proposal; it does not silently
run work or modify the target.

### Run repeatable work

A Gig separates its stable definition from Run-specific inputs. The same Gig
can therefore be used with a different job posting, topic, company, repository,
or dataset without rewriting the workflow.

### Review and verify results

GigAI is designed to make model work inspectable:

- model and role selection are explicit;
- provider calls use bounded process or API boundaries;
- automatic fallback and hidden retries are not assumed;
- findings, feedback, adjudication, and addressed results remain inspectable;
- previous Gig versions and Run history remain available for comparison.

### Keep work local and portable

GigAI keeps private workpads, proposals, journals, and Gig state under the
operator-selected local home. A portable Gig carries its definition, version
identity, references, and capability requirements—not copied secrets or opaque
installed tool bytes.

On another machine, the required capabilities can be resolved and installed
locally, then verified against the Gig's pinned references. This makes a Gig
reinstallable rather than tied to one machine's incidental state.

## Under construction

GigAI's foundations are usable, but the complete agent workbench is still being
built. The main pieces under construction are:

- **Gig creation lifecycle** — a dedicated browser flow for discovering a Gig,
  researching its direction, displaying the proposal, collecting feedback,
  revising it, and reaching explicit approval.
- **Gig improvement lifecycle** — a separate browser flow for proposing
  evidence-backed changes to an existing Gig from completed Runs, feedback,
  and review results.
- **Multi-model review and verification** — assigning models to planner,
  researcher, reviewer, verifier, and adjudicator roles during Gig creation and
  execution, with disagreement treated as useful evidence rather than hidden
  consensus.
- **Observability** — making it easy to see what each model did, which evidence
  it used, what changed between Runs, and why a result passed, failed, or was
  blocked.
- **Evaluation layers** — keeping unit tests, integration tests, installed
  end-to-end checks, and behavioral evaluations distinct while reporting how a
  Gig actually performs.
- **Extensible roles and capabilities** — replacing scattered role names with
  centrally validated role definitions that Gigs can extend without changing
  the machine-wide defaults.
- **Broader portable execution** — reinstalling the required capabilities and
  running a Gig consistently across machines without copying secrets or opaque
  local state.
- **Release and human acceptance hardening** — repeated upgrade, reinstall,
  UAT, and dogfooding cycles before making alpha-level claims.

## Example Gigs

These are representative workflows a user can create and tailor to their own
needs.

### `tailor-resume-for-a-job`

Given a resume and a job posting, the Gig can build a responsibility and
qualification matrix, identify what matters for the role, and produce a
tailored resume, cover letter, or both. The original resume remains unchanged.

The same idea can be personalized:

```bash
gigai create tailor-joe-resume
```

Joe can describe his experience, preferred tone, target industries, and the
outputs he wants. The Gig definition remains reusable while each job becomes a
new Run input.

### `explain-a-topic`

The Gig asks what the user wants to understand, how much context they already
have, and what kind of explanation would help. A history lesson, technical
briefing, or study guide can each become a different Run without changing the
underlying teaching workflow. 

### `analyze-a-public-company`

The Gig can be defined to gather public information, organize a fundamental
analysis, identify missing evidence, and send the result through the
review-verify-fix loop before presenting it.

### `implement-backend`

A repository-focused Gig can define the implementation boundaries, inspect the
selected code, ask for missing requirements, propose a change, and require
review and verification before any target effect is authorized.

## Where GigAI is going

The core direction is a durable unit of work loop rather than a single model call:

- multiple models can contribute different roles during Gig creation and Runs;
- reviewer and verifier roles can challenge one another with explicit evidence;
- operators can inspect what Codex, Claude, or an API model contributed;
- completed Runs can provide evidence for carefully gated Gig improvements;
- portable Gig definitions can be reinstalled and verified across machines.

GigAI is open source and still evolving. The useful distinction is always kept
visible: a capability that is implemented and verified, a capability being
tested locally, and a future capability are not presented as the same thing.

## Install

```bash
uv tool install gigai
gigai --version
gigai setup
```

For the detailed command reference, see the
[command sheet](docs/reference/command-sheet.md). The
[external changelog](CHANGELOG.md) records operator-facing capabilities, while
the repository's deeper development notes remain in the `docs/` directory.
