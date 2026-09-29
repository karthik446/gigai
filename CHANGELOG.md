# GigAI Changelog

This is the external, capability-focused history of GigAI. It describes what
an operator can do, not how the implementation works. The internal technical
history is kept in the maintainers' local notes, outside this repo.

Goal labels are milestone references, not package-version numbers. Goal order,
phase order, and release order are deliberately different; release notes must
not be inferred from a Goal number.

## Unreleased capability milestones

Backfill from the accepted Goal completion audits is intentionally tracked in
the internal changelog first. Entries added here must describe only a verified,
operator-visible capability and must link to the relevant release or evidence.

### Added

<!--
External entry shape:

#### GNN — Capability name

- What an operator can now do.
- Important user-visible boundary or limitation.

Do not include commit IDs, schema field names, test counts, or implementation
mechanics here. Those belong in the internal changelog.
-->

## Released versions

### 0.1.9

#### Added

- **Ranking on your own model.** Find-jobs ranks the postings that pass your
  filters with the model target you already use, as a step of the run (up to
  a per-run limit on model calls; postings past it keep date order). Results
  stream into the grid as batches finish, with live progress ("Ranked 350 of
  1,458"), and the run's 500-posting import keeps the best-ranked postings
  instead of the newest. The order is "likely fits first, likely no-matches
  last"; postings with a hard blocker (no sponsorship, citizenship,
  clearance) move down and are never hidden. If ranking cannot run, the
  search falls back to date order and still assesses.
- **Rank / Re-rank** a run from its page. A re-rank is its own record, so you
  can see, resume or cancel it, and it does not appear in your run list.
- **Work mode and location filters.** Choose Remote, Hybrid + an area,
  Onsite + an area, or Any. The mode comes from the job board's own field
  when there is one, else from the location text (labelled as derived);
  postings whose mode cannot be told are kept and labelled.
- **Assess button** on run postings that were not assessed, on the job page.
- **Assess all new.** A finished run has an **Assess all new** button that
  assesses every new posting the run did not assess, in the background, four
  at a time; you can cancel it and click again to resume without redoing
  finished ones, and the counts and cards update as results land. The run
  dialog's "Full assessments" now offers **All new postings**, the starting
  choice for the Codex, Claude and Ollama targets (OpenRouter starts on a
  number). A time estimate is shown only when a per-call time has been
  measured. Codex and Claude CLI runs use your own CLI login and its usage limits
  (Scout passes them no API key); a hosted
  target sends each posting's assessment to that provider.
- **Tailored resume "Show changes".** The tailored resume shows each rewritten
  line with the original line(s) it came from struck through above it, the
  new words highlighted, and a summary of lines rewritten and copied and
  "New words (not in the cited lines)". A **Clean copy** toggle shows the
  resume as formatted text; the downloaded `.md` is unchanged.
- **Questions first** on the job page: your open questions sit at the top,
  with the requirements table collapsed below.
- **Per-skill requirement rows.** A requirement that joins unrelated skills
  in one bullet is split into one row and one question per skill; related
  stacks ("Java + Spring Boot") and alternatives ("Python or Kotlin") stay
  one row.
- **Pages of results.** The jobs grid loads a run's postings a page at a
  time, about 50 cards per page.
- **Work mode, pay and H-1B chips** on job cards and the job page; a posting
  that does not state sponsorship says "Sponsorship not stated".
- **Every job has one state**, and **Applications** shows the jobs you have
  applied to and beyond. **Assessments** lists the assessments you ran on
  demand. The app has a top bar with a page for each, and breadcrumbs.
- **Company index and Update sources.** `gigai scout sources update` (or
  **Update sources** in Settings) stores every watchlist company's postings
  locally, so a search reads the store in seconds instead of fetching boards.
  Only companies Exa newly finds during a search are fetched then, at most 20
  per search. Acquire rotates through the shipped company catalog, least
  recently fetched first.
- **Profiles.** Keep several resumes and preferences as profiles; runs,
  assessment, discovery and the CLI use the selected one.
  `gigai scout resume add` stores a resume; `gigai scout resume tailor` (and
  the job page) produce a tailored resume for a posting.
- **Setup and discovery.** A setup interview in the Scout UI, a Discover
  panel and `gigai scout discover` propose companies to watch. Interview prep
  (`gigai scout prep`) is hidden in this release until it has been tested.
- **One-command Scout.** `gigai scout install`, `gigai scout run`, `stop` and
  `status`, `gigai gig use`, and `gigai secrets add|list|rm` (stores API keys
  locally; the Exa key is read from there).
- **Claude as a model target.** Scout can rank and assess with Claude (the
  `claude` CLI on your PATH), offered as "Claude (claude CLI)" wherever a
  model target is chosen; Codex or Claude is the one model target Scout
  needs. Assessments run Claude Code's default model.
- **Tailored resumes are drafts.** Review each line; every line shows its
  sources. Two confirming live runs on the release candidate (155 and 151
  lines) found no fabricated facts once one judge-flagged plural ("Kubernetes
  platforms" for the source's "Kubernetes platform") was reviewed as a wording
  difference, not a new fact; each run had 1 minor precision flag (0.6% and
  0.66% of lines).

#### Changed

- **A clearer README.** A numbered Quickstart (requirements, a resume with your
  personal info removed, install, run), a Roadmap / TODO with known
  limitations, and privacy wording that no longer promises contact lines are
  stripped from ranking.
- **`gigai scout run` sets itself up.** On a new machine it writes GigAI's
  settings with the defaults `gigai setup` offers, then starts Scout; no
  separate `gigai setup` step. An existing config is never changed.
- **Full assessments wording.** The run dialog's cap is "Full assessments" with
  a line saying every matching posting is ranked; postings past the limit are
  labelled "Not fully assessed" (use Assess to assess one), and Settings says
  "Default full assessments". A saved "all" shows as "All new postings".
- **Tailoring status sits next to the Tailor button** with the model name and
  a running timer, and points to the result or the error when it finishes.
  The requirements table on the job page is always open.
- **The setup wizard saves your model choice as the default model target.** The one
  "Model for Scout" choice reads your resume and is also the model your runs use
  (change it later in Settings or in a run's dialog). An existing setup keeps
  every other saved value.
- **Exa is off for a new setup.** A new `find-jobs.json` (the starter file or
  the first setup save) starts with Exa off, so searches use the bundled
  company list and stored boards and a model target is the only requirement.
  Settings has a "Search sources" toggle, "Also search the open web with Exa
  (needs an Exa key)", after `gigai secrets add exa`; the setup wizard no
  longer mentions an Exa key while Exa is off. An existing config keeps the
  Exa setting it saved.
- Assessing a job by URL reads a company-careers link's text from the job
  board's own posting (Greenhouse `gh_jid` links), and a page whose text has
  no readable requirements is now reported as "couldn't read this posting's
  requirements" instead of being called a match.
- Tailored resumes resolve board postings the same way.
- Scout always lives in `<home>/scout`, whichever folder you run it from.
- The setup wizard stores your resume itself and finishes without the CLI; a
  new profile never rewrites the selected one; `resume add` accepts any file
  name.
- `gigai scout run` stops an older project's Scout server that holds the
  port, and restarts a server left running old code.
- Run pages open in a fraction of a second, and the grid renders from the
  first page.
- The local Scout API rejects cross-site write requests.
- What Scout sends to a model is now stated in the README: ranking sends
  one-line posting digests and a compact resume digest (digest-v3: titles,
  skills, domain and years, no header or contact details); assessment sends
  the posting and your resume; with a local Ollama target nothing leaves the
  machine.

#### Fixed

- **Lever postings keep their requirements.** When Lever's plain text is cut
  down, Scout now uses the full HTML description (about 2% of Lever postings).
  The first Update sources after upgrading re-reads every Lever company once
  from the local cache; postings whose text grew show as changed.
- An assessment of a long posting that finds fewer than 3 real requirement
  rows is no longer reported as Matched: it is not assessed, with "Posting text
  looks incomplete: open the posting".
  Known limit: an assessment saved before this fix stays as it was until you
  click Assess again.
- The tailored resume preview no longer shows doubled heading markers
  ("### ### GUILD EDUCATION").

Known limit: choosing "All new postings" in the run dialog is not written back
to `find-jobs.json`; set `"default_assess_cap": "all"` there to make it the
saved default.

#### Removed

- **Jev ranking** — its settings, budget, usage and the "Score with Jev"
  actions are gone, and nothing calls Jev any more. `gigai secrets add jev`
  now fails because `jev` is no longer a known service. A Jev key you
  stored earlier stays on disk, untouched and unused, and so does any old
  Jev score cache.

### 0.1.8.1

- Assess now sends the model the real posting text and a real assessment
  prompt (with the output schema and examples) instead of the title alone.
- Assess tolerates sloppy model output (normalizes odd field shapes) and
  isolates a bad answer to that one posting instead of failing the whole
  run; when a posting can't be assessed, the recorded cause explains why.
- Raw Exa and applicant-tracking-board responses are now stored per run for
  debugging and as test fixtures.
- Acquire applies an explicit country filter and prefers a job board's own
  posting over an Exa search result for the same job.
- Adds a visa-sponsorship filter to find-jobs.
- Adds search and filters to the Scout UI.
- Fixes the release pipeline: the GitHub Release now publishes right after
  PyPI, and PyPI/TestPyPI clean-install checks run as non-blocking
  post-publish checks with a bounded wait for the index instead of racing
  it.

### 0.1.8

- Adds Scout's `find-jobs` workflow, GigAI's first shipped Gig: acquire public
  postings from Exa search and the Greenhouse/Lever/Ashby applicant-tracking
  boards through an auto-managed watchlist; assess each posting with a
  requirements-by-resume matrix that surfaces suggestions and open questions,
  defaulting to a local model with explicit hosted-model targets available;
  and present results through a localhost API and Vite UI that asks for
  explicit consent before any network call or hosted-model use.
- Moves the Scout package to `gigai.scout` so it imports and packages as a
  self-contained Gig built on GigAI core. Core still imports Scout in places;
  removing those so core never imports a Gig is planned for v0.1.9.
- Reorganizes the test suite into behavior-grouped directories (S11) with a
  `make test` runner that separates source, behavior, and wheel-resource
  suites.
- Fixes release CI's setup verifier and workflow model-target wiring, and adds
  an interpreter safety guard to the wheel-resource test lane.

### 0.1.7

- Adds the bundled Scout authoring source, local record workflow, bounded public
  acquisition import, and rebuildable local report surface.
- Adds resumable interview and private-transfer preparation plus a local runtime
  comparison workflow with explicit synthetic/offline boundaries.

### 0.1.5

- Adds a browser-first local setup flow for GigAI's private workspace,
  workpads, model choices, and machine-wide role defaults.
- Adds adaptive Gig-definition interviews that turn an operator's intent and
  selected local context into a reviewable proposal before approval.
- Adds explicit proposal feedback, revision, approval, rejection, Run
  inspection, and recurring/comparison command flows around local Gig state.
- Keeps the workpad, proposal history, approved versions, and review evidence
  under the operator's selected local home.

### 0.1.4

- Adds the model-facilitated Gig builder for UAT: GigAI can guide an operator
  through a Gig definition, ask bounded adaptive follow-up questions, build a
  reviewable proposal, and require explicit approval before sealing it.
- This release is an alpha UAT candidate; configured live model families and
  real operator workflows remain subject to the G24/G26 UAT gate.

### 0.1.3

Release-specific capability notes will be reconciled from the G12 release
evidence and the verified capability inventory.

## Deferred and not advertised

This section records capability families that research or implementation
documents explicitly do not advertise as shipped. It prevents a feasibility
spike or roadmap item from becoming an external support claim by implication.
