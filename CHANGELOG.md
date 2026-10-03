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

### 0.1.10.7

Scout can now be used day to day from your own AI agent: ask "what's new?", answer the open
questions in chat, and let Scout tailor and score in the background. GigAI no longer stores your
name or contact details. Read "After you upgrade" first.

#### After you upgrade

- **Every stored assessment reads "Assessed with older settings".** The assessment prompt changed
  (see Security), so every assessment made by an earlier version is flagged. The verdicts still
  show. Nothing is assessed again by itself: "Assess these" on the Jobs page (and "Assess all" on
  a past run) plans them again, one model call each, and only when you click.
- **The next ranking pass ranks every posting again, once,** and a search run started by a script
  assesses its unchanged postings again, once. After that, costs are as before.
- **A one-time cleanup removes stored contact details** from your resumes and from Resume display,
  and tells you what it removed (counts only). Older copies can remain in GigAI's local history on
  your computer; see Security.
- **Your story bank is moved once** to answers that every profile shares. Nothing is lost: when two
  profiles answered the same question differently, the newest is the answer and the other is kept
  in its history. `gigai scout answers migrate` prints the counts.

#### Added

- **`gigai scout new`: what is new since your last check.** One command, for you or your agent,
  across all your active profiles, read from the stored postings (no job board is asked). It asks
  before it assesses, with the count and an estimate from your own past calls; `--yes` assesses,
  `--no-assess` shows ranks only. With nothing new it lists the 10 postings that still need your
  attention. The grid shows each posting, its score, what it still needs and its open questions.
  "What matches" comes from your own resume and answers, so it is a separate call (`--yours`) and
  is never shown next to posting text. The first use looks back 7 days.
- **An agent skill and a permissions snippet.** `gigai agent-skill` prints the instructions that
  teach your agent the daily loop (as a Claude Code skill, or as a section for an `AGENTS.md`).
  `gigai agent-permissions` prints a recommended Claude Code permissions snippet. GigAI prints it
  and never applies it: it does not touch your agent's settings.
- **Answers and stories, shared by every profile.** An answer is a short fact a posting asked for.
  A story is an experience worth telling: a title, where and when, your own words, a short
  narrative and the questions it answers. Your agent asks "Want me to make this a story?" when a
  reply has substance. An assessment reuses your answers and gets the few stories that fit the job.
  Both are written through `gigai scout answers`, `gigai scout story` and the matching API, with a
  stale-write check. Scout's own page (Settings > Answers and stories) is read-only: it lists them,
  shows which jobs used each, and deletes one that is wrong.
- **A background pipeline for the jobs you engage with.** After you answer a job's question (or
  press "Process now"), Scout tailors a resume for that job, assesses the tailored resume, computes
  a Scout ATS score and sets a Scout label, in the background. It is on by default and works only
  on jobs you engaged with. One trigger starts at most 10 jobs and the rest wait for your approval;
  the pipeline makes at most 40 model calls a day; background ranking makes at most 100 a day, with
  a warning past 60. All of these are settings (Settings > Background pipeline). The job page shows
  each step with its model, tokens and time, and "73 → 91 after tailoring".
- **A tailored resume you edited is never replaced by the background.** If you tailored a job's
  resume yourself or changed a line, the pipeline keeps it and scores your text. To refresh it,
  tailor that job again yourself.
- **Scout label.** "Recommended" or "needs attention", with the reasons. It is Scout's own
  suggestion from your settings, resume and answers, not a prediction of what an employer will
  decide.
- **Scout ATS score.** A score from 0 to 100 for a tailored resume PDF against its posting: how
  cleanly the PDF reads back, how many of the posting's skills it names, and basic format rules,
  with the missing skills listed. It is GigAI's own local check, with no model call, and not any
  real ATS's score. It is shown, never used to block anything. Skills in the PDF are now separated
  by a dot so they read back as separate words.
- **What your model calls cost.** Every Scout model call is recorded on your computer: kind, model,
  tokens, time, and cost when the provider reports one (no text). Averages show beside Re-assess
  and Assess all, Settings has a Model usage table, `gigai scout metrics` prints them, and they
  feed the estimates `gigai scout new` shows.
- **A page that explains the numbers.** "What Scout's numbers and labels mean" covers rank,
  verdict, Scout label and Scout ATS score, and a "?" beside each of them on the job page opens it.
- **Delete a profile.** On the Profiles page, and with `gigai scout profile delete`. Its past runs
  are hidden. The default profile can never be deleted, and neither can your only active one.
- **A Generate PDF form.** See Security.

#### Changed

- **The Jobs page lists postings, not a run.** It shows the stored postings that match your
  profiles, live, with a tag for each profile a posting matches. The profile switcher became a
  filter. Chips narrow the list: New since last check, 7 days, 30 days, and state (needs your
  answers, assessed, Scout label, removed). "Mark all seen" resets "new".
- **"Run find jobs" is gone.** Ranking runs in the background. **First assessments happen only
  when you approve them**: "Assess these" on the Jobs page, `gigai scout new` and
  `gigai scout jobs assess` all ask first, with the count and an estimate. Past runs stay readable
  as history (read-only), and what they assessed shows beside newer assessments.
- **`POST /api/run` is deprecated.** It still works in this release, for scripts, and will be
  removed later. Use `GET /api/postings` (`gigai scout jobs list`) to search and
  `POST /api/postings/assess` (`gigai scout jobs assess`) to assess.
- **The per-profile story bank is replaced, with no alias.** `gigai scout story-bank` and the
  `/api/story-bank` routes are removed. Use `gigai scout answers` and `gigai scout story`, and
  `/api/answers` and `/api/stories`. The sharing setting is gone: every profile reads the same
  answers and stories. The Story bank page with its forms is replaced by the read-only page above.
- **A hybrid profile now keeps on-site roles in its own area.** A hybrid profile in Houston keeps
  an on-site Houston role and still drops an on-site New York one. The filter and the assessment
  use the same rule.
- **PDF files are named after the job, not you**: `<company>-<role>-<date>.pdf`.
- **Resume display holds only the title and the layout.** The name and contact fields are gone
  (see Security).

#### Security

- **GigAI never stores your name, email, phone, address or links.** You type them in the new
  Generate PDF form, they go into that one PDF, and GigAI forgets them. A resume you add is stored
  with its name and contact lines removed, and you are told what was removed. The limits: the
  removal works on patterns and can miss a detail inside a sentence; and versions before this one
  did store those lines, so a one-time cleanup removes them from the current files, but older
  copies can remain in GigAI's local history on your computer. `gigai scout privacy` shows what
  the cleanup removed.
- **A PDF made by an agent or the command line has no name or contact details.** GigAI has none to
  give it. The command prints an "Open in Scout" link; you open it, fill the form in your own
  browser and download the finished PDF.
- **Anything GigAI gives your agent is sent to that agent's model provider.** Agents get no contact
  data from GigAI, but an agent with shell access can read local files. The docs say this in bold,
  with what it means.
- **Every reply to an agent is checked for contact details.** Text shaped like an email address, a
  phone number, a profile link or a street address in your own fields is replaced by a marker
  before a reply leaves the local API. Posting text is left as published. It is a backstop that
  works on shapes, and it can miss things.
- **Replies say what kind of data they hold.** Each API reply is labelled: your private text,
  public text written by strangers, or neither. `gigai scout new` never mixes the two in one
  reply.
- **Posting text is fenced in Scout's own prompts.** Every prompt that carries a posting marks it
  as text written by strangers: data to read, never instructions to follow. In a test on synthetic
  postings written to steer the model, one of five steered the old prompt and none steered the new
  one; ordinary verdicts did not change. It lowers the risk and does not remove it.
- **The local API checks the Host of every request**, not only writes, so a web page you visit
  cannot read your data through your browser.

#### Fixed

- **Reading the configuration no longer launches about a hundred helper processes.** A first read
  from Update sources, setup and a few other paths launched 109 processes; it now launches 30, and
  a repeat read launches none.
- **A resume with one very long line no longer takes minutes to process.** A single line of
  100,000 characters took almost two minutes; it now takes a fraction of a second. What is removed
  is unchanged.

### 0.1.10.6

#### Fixed

- **`gigai status` and other commands no longer slow down as your history grows.** `gigai status`
  took 14 seconds with a long history (37 seconds with a longer one) because it re-read every saved
  change each time; it now takes a fraction of a second whatever the history length. `gigai scout
  profile list` and `gigai scout story-bank list` are also flat, and a read-only command checks your
  data folder once instead of several times. The first command after upgrading does the one-time
  work.

### 0.1.10.5

#### Added

- **Story bank.** Every answer you give to a question about a job goes into your profile's story
  bank, kept per profile (a profile can read another profile's bank only if you set that). A new
  Story bank page lists, searches, edits and deletes entries and shows which jobs used each. When a
  later job asks a similar question, the assessment reuses your answer instead of asking again, or
  shows "We already know: ... use it?" prefilled. Agents can read and write the bank too:
  `gigai scout story-bank add|list|show|edit|delete|share` and the matching API, with a stale-write
  check and a record of who wrote each entry. Answers are checked for personal contact details.
- **Agents can render a resume PDF.** `gigai scout resume pdf` and `POST /api/resume/pdf` turn
  resume markdown into a PDF with your saved header, and a tailored line can be replaced with your
  own text (`use: custom`), marked as edited, with the same personal-info check.
- **Work mode in assessments.** The assessment now knows whether you want remote, hybrid or on-site
  work, so a remote-only profile no longer gets "Matched" on an on-site role.
- **Older assessments are flagged.** An assessment made before your settings changed (or before this
  release) shows "Assessed with older settings: re-assess". "Assess all" counts them separately and
  re-assesses them only when you click. A story bank answer flags only the assessments whose own
  open question it answers.
- **Check times setting.** Settings > Background updates lets you change when the background check
  runs.

#### Changed

- **Background checks run 8 times a day in work hours, at a moderate pace.** On weekdays at 03:00 and
  every two hours from 07:00 to 19:00, on weekend days at 09:00 and 18:00 (your local time), busy
  boards every time and quiet boards about twice a day, a few requests a second, backing off when a
  site asks. One catch-up check runs if Scout was closed. The strip says when the next check is;
  Update sources still runs at full speed.
- **First runs after this upgrade re-assess postings once, which means more model calls once.**
  Runs now give the assessment your real settings (see Fixed), so earlier verdicts are not carried
  forward. Runs also say why a posting was not assessed, and keep much less in their saved history.

#### Fixed

- **Runs assessed without your settings.** A search run's assessment did not see your location,
  countries, sponsorship need or target titles, so it could call a New York hybrid role with "no
  H1B" a fit for a Houston profile that needs sponsorship. It now does.
- **Pages loaded in 4 to 7 seconds.** The profile, config, setup, applications and runs reads are
  now tens of milliseconds, and Mark applied and the first applications read no longer slow down as
  your history grows. The workpad check behind every page and command no longer walks the whole
  history either.
- **The first background check no longer looks frozen.** The one-time tagging and keyword-index
  work now runs after the first boards, with a heartbeat. Tag counts say what they count, and only
  titles a model will tag are called "waiting". Tagging has its own thread and no longer starves.
- **The "Where this data comes from" link and the README links no longer 404.**
- **Keyword search and tagging on Debian 12** and other systems with an older SQLite no longer
  return nothing.
- **A rare "complete with the wrong counts" in Assess all** is fixed.

### 0.1.10.4

#### Added

- **Releases are now one click after a green pre-check.** The release gate also builds and smoke
  tests the package, and Release publishes exactly that tested build. A `rollback` workflow can
  yank a bad version and point the docs `latest` alias back.
- **Posted window on the Jobs page.** Posted chips (7d, 10d, 30d, 60d, Any) filter the shown
  postings at once, with no new run. A button finds postings from a wider window in the local index
  and adds only the new ones; existing assessments and answers stay.
- **Each profile has its own location, work mode, countries and posted window.** The first profile
  keeps the setup settings; other profiles are prefilled from it and editable in the profile form
  and with `gigai scout profile list` and `gigai scout profile update`.
- **Update sources is incremental.** Boards checked recently are skipped, so a second update right
  after the first takes seconds. A Full refresh option checks every board. Switching profiles never
  starts an update.
- **Background refresh.** While Scout runs, boards are refreshed in the background: busy boards
  about hourly, quiet boards every six hours or so, spread out instead of in a burst. Turn it off
  with the new Background updates setting.
- **Title tags.** Postings are tagged with a level and a job function (director, engineering and so
  on), so "Director, Engineering", "Engineering Director" and "Dir. of Engineering" are found for a
  "Director of Engineering" profile. Tags come from rules; a model fills in the function for the
  titles your profiles can reach (backfilling the rest is off until you enable it). Titles and
  locations are all a model sees.
- **Keyword search.** An optional Keywords field on the search form filters candidates by the text
  of their descriptions (Greenhouse descriptions are now fetched once per board, then only for new
  postings). Postings without stored text are kept and counted.
- **Starter snapshot.** Update sources can start from a shared metadata snapshot (companies, titles,
  tags and change markers; no descriptions) so a first update is much faster. Offline or not
  published is quiet; turn it off in Background updates. `gigai scout snapshot export`, `import` and
  `status` manage it.

#### Fixed

- **Title matching** matches whole words and ignores filler words and punctuation, so
  "Director, Engineering" matches "Director of Engineering" and "ai" no longer matches "maintain".
- **Rank years.** The posting's main years-of-experience requirement is read correctly
  ("10+ years ... 2+ years managing" is 10; a range is its lower bound). Cached ranks are refreshed
  once.

#### Changed

- **Converting a PDF or DOCX resume now needs nothing beyond `uv`.** The
  Quickstart, the Resume page and the `scout resume add` error use
  `uvx --from 'markitdown[pdf,docx]' markitdown resume.pdf > resume.md` instead of
  `pdftotext` or `textutil`, which a stock Mac does not have. `pdftotext`,
  `pandoc` and `textutil` still work if you already have them.

### 0.1.10.3

#### Changed

- **The resume PDF no longer leaves large gaps.** It uses a tighter type scale
  (a lighter body weight) and even spacing between every block.

#### Added

- **Spacing and Auto fit in Resume display.** A Spacing setting (0.7x to 1.4x)
  and an Auto fit toggle sit in Resume display, with a small live preview. Auto
  fit adjusts spacing only, never font size or margins, to fill the pages it
  already uses. Spacing alone has limits: a resume of about 1.5 pages ends its
  last page around 70% full at best, and content up to about 1.1 pages is pulled
  onto one page.

### 0.1.10.2

#### Fixed

- **A new profile can have its own resume.** The new-profile form in Settings
  lets you paste a resume (the default), upload one, or choose an existing one,
  instead of silently reusing the selected profile's resume. The profiles list
  shows which resume each profile uses and flags two profiles that share one.

### 0.1.10.1

#### Fixed

- **The PDF skills section no longer repeats itself.** Your skills show as
  de-duplicated tag chips, and the text is real, selectable text.
- **Fewer awkward page breaks in the PDF.** An entry no longer leaves a single
  bullet alone at the top of the next page, and a resume that barely overflows
  one page is fitted onto it.
- **The tailored resume text prints a wrapped line once.** A hard-wrapped line
  of your resume is no longer repeated in the `.md` text, the API markdown or
  the stored result, and a resume saved by 0.1.10 renders once too. The list in
  **Show changes** shows a resume saved before this release as it was stored,
  so it may still repeat lines: tailor again to refresh it.
- **The reason label in Show changes starts with a lower-case letter**
  ("for a working practice ..."), while acronyms such as AWS keep their case.

### 0.1.10

#### Added

- **Your name and contact lines are removed before a model sees your resume.** Before any resume text
  goes to a model, Scout removes the name and contact lines (email, phone,
  address, links); the tailored resume it writes has no header. The **Resume
  display** fields (name, title, contact line) are never sent to a model or
  over the network; they are added only to your PDF, on this machine. It
  cannot catch personal details elsewhere in the text (a first line that holds
  both a title and your name, or contact details inside a sentence), so keep
  those out of the resume body.
- **Resume display is easier to find.** It is a step in the setup wizard, a
  row on the Review screen, a section under the profile card in Settings, and
  an **Edit** link next to **Download PDF**.
- **Keep original / Use rewrite anyway.** In the tailored resume panel, each
  line can be switched between your original and the rewrite, and the PDF
  follows your choice.
- **A calmer first run.** The first-run steps are one compact stepper, a
  disabled button says why once, and the empty state is shorter.
- **A "posting text changed" marker.** When a posting's text changed after
  it was assessed, the job shows "Posting text changed since this assessment:
  re-assess" next to the verdict, which stays visible. A posting whose
  requirement list looks cut off is labelled "Posting text looks incomplete"
  and is not assessed.
- **An API and CLI manual for agents.** Scout's server has a `GET /api` index,
  an OpenAPI 3.1 description at `/api/openapi.json` and `/llms.txt`;
  `GET /api/jobs?url=` returns one job with everything known about it; an
  `unknown_key` error names the allowed top-level keys. `gigai agent-context`
  prints the CLI manual (`--json` for the machine-readable form).
- **A public docs site** at <https://karthik446.github.io/gigai/> (quickstart,
  concepts, CLI and API reference, changelog), and a short README that links
  to it.

#### Changed

- **Tailoring keeps your lines as written.** It copies your resume lines
  verbatim by default. A rewrite needs a stated reason and must keep every
  number, named technology, scope word and ownership verb of the lines it
  comes from; otherwise your original line is kept. Resumes aim for at most two
  pages; roles older than about eight years keep at most three bullets, and
  bullets are dropped whole, never shortened.
- **The personal-info warning** now says your name and contact lines are
  removed before a model sees the resume, and what it cannot catch.
- **Downgrade note.** Tailored resumes written by 0.1.10 do not appear in the
  list of an older version after a downgrade. They are not corrupted and
  return after upgrading again.
- **The PDF layout is tidier.** Spacing is more even, lines wrapped in your
  resume text are joined, and skills and summary read as paragraphs.
- **`gigai scout run` frees the port from an older Scout.** If an older Scout
  server (including 0.1.9.x) is verifiably holding the port, it is stopped
  and `gigai scout run --json` reports it as `stopped_server`. A process that
  is not Scout is left alone.

#### Fixed

- **`gigai scout resume add` works as the first command** on a fresh
  machine, without running `gigai setup` first.

### 0.1.9.1

#### Added

- **Download PDF for tailored resumes.** The tailored resume panel downloads a
  PDF. A new **Resume display** section on the profile page holds your name, a
  title per profile and a contact line; they are stored only on this machine
  and added to the PDF locally. GigAI now depends on Typst to draw the PDF;
  Alpine/musl Linux is not supported.
- **A personal-info warning wherever a resume is added** (the setup wizard,
  Assessments paste, Settings > Profiles and `gigai scout resume add`), and a
  local heads-up when the text contains an email address, phone number, link
  or street address.

#### Changed

- **Codex and Claude CLI calls run locked down.** Codex runs with its shell
  tool and memories disabled; Claude runs with its settings, MCP servers and
  tools turned off. Your installed Codex or Claude must support these flags,
  or the call fails with a message to upgrade it.
- **Download .md is replaced by Download PDF** in the tailored resume panel.

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
  ("### ### EXAMPLE CORP").

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
