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

### 0.1.11.2

- **A posting with too few requirements is no longer a perfect match.** A match read from fewer than 4
  requirements says "thin posting: too few requirements to judge" in place of "Matched · fit 100%", on
  the Jobs list, the job page, in the terminal and in the API (each row says `thin_posting`). Thin
  postings are listed after the real matches: below every other assessed posting and every posting that
  is not assessed yet, in the Jobs list, `gigai scout jobs list` and `gigai scout new`; nothing is
  hidden. A match with no requirement about the job at all (none read, or only "No stated
  requirements") is not a match: it has its own state (`thin_posting`), is not counted or filtered as
  matched, and is listed last of all. Assessments already stored are covered; nothing is re-assessed.
- **Jobs are ordered by rank again, and weak fits are collapsed.** The Jobs list (and `gigai scout jobs
  list`) is ordered best fit first: assessed postings by fit, then the rest by rank, then the newest. A
  posting that is not assessed yet and ranked below 50 is no longer shown as a candidate: it is collapsed
  under a count ("12 weak fits, ranked low: show", or `--state ranked_low`). Nothing is filtered away: the
  count opens the list. A posting that is not ranked yet stays in the list, after the ranked ones, and
  says "not ranked yet". Sponsorship stays a label and never hides a posting.
- **Assess the top 50 by rank.** The Jobs page shows "N not assessed" with an "Assess all" button. It
  asks first, as before: the top 50 by rank, the estimate, the model and how many are left after these
  50. `gigai scout new`, "Assess these" and the API take the same top 50 by rank instead of the newest
  50; postings you select or name are still the ones assessed. While ranking is still running, the page
  and the question say how many postings are ranked so far.

- **Ranking is back, on its own switch.** The background pipeline stays off and nothing is tailored, but
  ranking runs again: the Scout server ranks when it starts (so an upgrade ranks what is not ranked yet
  without waiting for a sources update) and whenever new postings arrive, with no approval. It ranks
  postings posted in the last 7 days (a posting with no date uses the day it was first seen), up to 50
  a call, within the same daily call cap as before; a ranked posting is not ranked again. `gigai scout
  pipeline status` shows "Ranking: on|off"; `rank.enabled` in the background settings turns it off.
- **The job page shows the whole posting.** The description is no longer cut to one short paragraph; a
  preview that is still cut says so, and an assessment of a posting longer than 12,000 characters says
  that only the first 12,000 were assessed.
- **Onboarding:** work mode starts on Remote-only for a new profile (a stored choice is kept), the
  sponsorship answer no longer says "hard filter" (it is a label, nothing is filtered), and the Companies
  step is gone (4 steps; company lists stay in Settings). The assess dialog says "Assessing N postings…"
  while it runs. A job address that differs only by a trailing slash before the query now finds its
  posting, and a job assessed from an address says so instead of "Job not found".
- **Docs:** the Install page pins the current version, the pipeline-off and picked-resume behaviour is
  described as it is, and "Start fresh" explains how to reset and start again from a resume file.

### 0.1.11.1

CI: one cut-down lane for the PR and the release pre-check (about 5 minutes). The full suite, the browser
tests, the 290k-posting gate and the macOS smoke move to a non-blocking full run, nightly and after each
release. No product change.

### 0.1.11

GigAI is now an alpha (it was labelled pre-alpha). Tailoring is switched off: the resume for a job is
picked from your master, word for word. The assessment picks the lines, and GigAI checks that the lines
it shows are lines you wrote. The removal of tailoring and the pipeline rework are in 0.1.11.1. There is
no migration and no schema change in this version. Read "After you upgrade", "Known limits" and "Not
proven".

#### What changed for you

- **The assessment lists what the posting asks for, and holds the verdict until you have answered.**
  Each requirement is a row marked required or optional. A required row your resume does not show and
  you have not answered is "Needs your answers"; a required row you have confirmed you do not meet is
  "Has a gap". Rows that are only examples ("such as Python, Go or Rust") and nice-to-haves never hold
  the verdict. A job with a gap gets no resume of its own until you ask for a draft.
  <!--V9--> Sponsorship and work authorization are shown as a label on the job. They never hold a
  verdict and never decide whether a resume is made. <!--/V9-->
- **The resume for a job is picked, not written.** The assessment names the master lines that answer
  each requirement, the code fits them to two pages, and a recent role always shows at least one line.
  A line a requirement cites is not cut for length while an uncited line stays. The job page shows what
  was Picked and what was Left out, and says why.
- **Suggestions.** Where your resume could say more (a word the posting uses for something you already
  did, a line to move up), the job has a suggestion with the line it concerns. You dismiss it, or
  resolve it by editing the resume (`resume store --resolves`). An answer goes into a role only when it names that role. Nothing is
  applied without you.
- **An agent can do the wording with you.** `gigai scout resume brief --job-url URL` gives your agent a
  two-part brief: your master with its line ids, and the job's requirements and suggestions.
  `gigai scout resume store --in FILE --job-url URL` hands the edited resume back and runs a check: a
  line must be a line from your master, a line reworded from one with the source named, or a claim an
  answer of yours states. A line that states something none of them state is refused, and the refusal
  says which line and why. The check is a rule on wording, not a judge of truth: it can refuse a true
  line and pass a loose one. (`gigai scout resume tailor` still exists in this release and is switched off by default.)
- **Line notes.** A line or an entry in your master can carry a private note
  (`gigai scout resume master edit ITEM --note "..."`, `--clear-note`). A note steers the pick and
  appears in the brief. It is never written into a resume or a PDF. It is sent to your model, once and
  labelled "private note", in the assessment.
- **`gigai scout resume pick --job-url URL`** shows the stored job resume: who picked it, Picked and
  Left out, the gate, what is out of date, conflicts. It makes no model call. `--refresh` picks again
  from the stored assessment, `--draft` makes the draft for a job that has a gap, `--use-proposed`
  takes a new selection that waited. A resume you edited is never replaced by itself; a new selection
  waits as "proposed". `gigai scout suggestions` has `list`, `add`, `resolve` and `dismiss`.
  `resume store --fit` lets code cut a resume over two pages by the pick's rules; without it a long
  resume is refused with its page count.
- **One profile is the default** for `resume brief`, `resume pick`, `suggestions` and `resume store`;
  with two profiles and none marked default they ask which.
- **Never more than 50 at a time, newest first.** An offer to assess many postings acts on the newest
  50 (this began in 0.1.10.11). <!--N7--> `gigai scout new --yes`, `--reassess-stale`, `jobs assess`
  with a filter, "Assess all new", pipeline runs and approvals work on one page of 50 and say how
  many are left; `--page N` takes the next. Naming more than 50 jobs in one `jobs assess` is refused
  with the count. `--all` exists on the command line and asks once more with the real number.
  <!--/N7-->
- **The editor is optional.** Setup no longer needs one. With none set, `gigai open` says so and
  names `--editor` or `EDITOR`; everything else runs.
- **The job page** shows the suggested resume, Picked / Left out, the suggestions, the gate state, and
  Apply, which downloads the PDF of that resume.

#### After you upgrade

- **The background pipeline is OFF by default in 0.1.11.** A home whose settings explicitly enable it
  keeps working as it did on 0.1.10.
- **Do not go back to 0.1.10.x after you use this version** if you used these two things: a 0.1.10.x
  binary refuses a config that has no editor, and it drops line notes when it writes a master revision
  (see below). Nothing was changed in your stored files at upgrade: there is no migration. The downgrade
  was not run by hand: see "Not proven".
- **Every assessment made before 0.1.11 reads "older prompt".** They keep showing. Re-assessing is
  your click, and the offer is the newest 50.
- **Your notes are dropped if a 0.1.10.x binary writes a master revision.** Do not edit the master
  with an older GigAI once you use line notes.
- **Resumes tailored on 0.1.10 stay.** Nothing is rewritten at upgrade. One you edited, or one the
  old tailoring made, is never replaced automatically; a new selection waits as "proposed" and "Use
  it" replaces it. Lines the old tailoring reworded show the rewrite and its original.
- **Other lines cut for length have no Restore.** When the page cuts lines of an "Other" section to
  fit two pages, there is no Restore for them; the cuts in your other sections can be restored.
- **`gigai scout resume tailor` still exists and is switched off by default.** It is removed in
  0.1.11.1.

#### Known limits

- A posting that is only bullet fragments (no sentence and no requirement wording) is refused as having
  no readable posting; paste its text.

#### Not proven

- **Accuracy on real postings is the point of this release and is reported separately:**
  15 unseen real postings of one person, fully correct by an answer key built blind (fourth set):

  | Model | Fully correct | Unnecessary questions | Seconds per call (mean) |
  | --- | --- | --- | --- |
  | Claude Code, `claude-opus-5-5` (pinned, the default) | 14 of 15 | 0 | 37.0 |
  | Claude Code, `claude-sonnet-5-5` | 12 of 15 | 4 | 24.1 |
  | Codex CLI, default (`gpt-6-astra`; the adapter does not report the model) | 10 of 15 | 9 | 38.5 |

  No invented facts in any row. One call failed on each Claude row (a correct answer the "too few
  requirements" guard refused; fixed, and still counted as a failure here). One person's master; the ask
  side is thin on this set. An assessment made with a model other than the Claude reference says so.
  Read the standard, the method, the per-point columns and what the test cannot show on the
  [accuracy page](https://karthik446.github.io/gigai/scout/accuracy-0-1-11/) of the docs.
  A real install of the candidate on a real home, with the time of each step, is on the
  [real-run check page](https://karthik446.github.io/gigai/scout/real-run-0-1-11/).
- A real agent session through the brief and the hand-back, on Claude Code and on Codex, was not run
  when this was written. The Codex sandbox on this flow is untested.
- The hand-back check's refusals of true lines were measured on synthetic text only.
- Requirement lists from the two model CLIs for the same posting differ in places; the verdict and the
  gate agreed on the sets tried, the lists did not always.
- The downgrade path above was read from the code, not run.

### 0.1.10.11

Speed, and the fixes from the first days of daily use. On a store with a lot of finished work a save
no longer takes minutes (saving your preferences took about six minutes and now takes about a second
and a half), and the first Jobs load of a day no longer waits for every company to be matched again.
A posting says when it was posted, when its board last changed it, or when Scout first saw it, and a
Greenhouse posting's date is the day it went up. An old assessment can be re-assessed from its job page.
`gigai scout resume master init` reads resumes the way people write them, and a heading that is a link
keeps its name. A resume made from your master resume keeps the lines its job's assessment points to
and the project or role the posting's title is about. An offer to assess many postings acts on the
newest 50 at a time. For an agent, the assess preview says what would be sent, a failed assessment
says what happened, and the guide names the three approvals. `gigai doctor --repair-journal` finishes
a save that a crash cut off. The first steps of a new user were walked through from a fresh install,
and the docs and messages that sent them wrong are corrected. Scout's memory no longer grows with
every resume pick. Several saves still take more than a second: see "Still slower than one second".
Read "After you upgrade".

#### After you upgrade

- **One more preparation of the postings, once.** The first Jobs load, or the first `gigai scout new`,
  after you install this version prepares every company's postings again, in the background: about 7
  seconds with 290,000 postings from 10,350 companies on our test machine, longer on a slower one. The
  list you had is shown meanwhile, with progress. Nothing is lost and nothing needs to be done. The
  same preparation puts every Greenhouse posting under the day it was posted (next note). If you ran
  an earlier build of 0.1.10.11, the postings are prepared once more.
- **Greenhouse postings get their posting day; your next sources update finishes it.** Until now Scout
  kept the day a Greenhouse posting last CHANGED as its date. After the preparation above, a Greenhouse
  posting shows the day it was posted wherever the company's stored list has it, and "first seen"
  otherwise; the 7 days and 30 days filters go by that day at once. Your "posted within" window goes by
  it for a company after the next `gigai scout sources update` (or "Update sources") has checked that
  company: the update reads each Greenhouse company's list once more with the request it makes anyway
  (no extra request; a company whose list did not change is read again from what is stored). Until
  then a Greenhouse posting that is older than your window can still be listed. No posting is marked new
  or changed by this.
- **Ashby postings that had no text get it; your next sources update finishes it.** Some Ashby boards
  send a posting's description as HTML only, and Scout stored no text for such a posting (see "Fixed").
  One that is already in your list can be assessed at once. The next `gigai scout sources update` reads
  each Ashby company's list once more with the request it makes anyway (no extra request); a posting
  that gets its text this way is then found by a keyword search and counts once as changed in a
  search's "new or changed since the last search". No other Ashby posting is marked new or changed.
- **Resumes made from your master resume are made again, one job at a time.** The rule that picks the
  lines for a job has a new version (`sel-4`, see "Master resume: the lines picked for a job"). A
  profile's stored selection stays as it is until you refresh it. Every tailored resume the background
  pipeline made from your master is out of date under the new rule, so there are as many to make again
  as you have such resumes stored. None is made again at once: each is made again when the pipeline
  next works on its job (after an answer that job asked for, a change to its profile, or "Process
  now"), as one tailoring within the pipeline's limits (10 jobs per change, the daily cap on model
  calls). The pick itself is about 0.2 seconds a job, in the background. A resume you edited or
  attached yourself is never replaced.
- **Old assessments: the re-assess offer is the newest 50 at a time, and it says the size.** This
  version makes no assessment old. But if you came from 0.1.10.8 or earlier, 0.1.10.9 marked every
  assessment made before it "old assessment: older prompt", and `gigai scout new` offers to re-assess
  them. On a large store that is a big number: one store had 574 marked old, 422 of them ranked 50 or
  more, and re-assessing all 422 was quoted at about 422 model calls, 9.9 million tokens and 4.9 hours.
  The offer now acts on the newest 50 and never more, and says the real total, the 50 and what the 50
  cost: "422 have only an old assessment; re-assess the newest 50 of 422? ~50 calls, ~1170k tokens (372
  more after these 50)". For that store, 50 are about 1.2 million tokens and 35 minutes. Nothing is
  re-assessed unless you say yes. An old assessment stays readable, the same command again takes the
  next 50, and a job page re-assesses its own job in one call.
- **A bare `gigai scout run` no longer stops another home's Scout.** If you keep more than one GigAI
  home (`--home`), `gigai scout run` used to stop whatever Scout held the port. It now stops only a
  Scout of its own home; for another home's it ends with one line that names the port and that home
  and says to pass `--port`. A Scout whose command line names no home (one started by hand) is not
  stopped either: stop it yourself or pass `--port`.
- **Nothing else changes.** Which jobs are checked again after a change, and the limits on them, are
  what they were.

#### Faster

Measured on a test store of 290,000 postings from 10,350 companies, with 2 profiles and 100 jobs per
profile that the background pipeline had finished, on a quiet machine. "First call" is the first one
after Scout starts (a command such as `gigai scout resume add` is always a first call): the slowest you
would see. Scout keeps its store's history in git and starts a git process for every question it asks
of it; the count is in the table because, unlike seconds, it does not move with how busy the machine
is.

| What you do | 0.1.10.10, seconds | Now, first call | Now, median of 3 calls | Git processes, 0.1.10.10 | Now |
|---|---|---|---|---|---|
| Save your preferences | 372 | 1.6 | 1.0 | 45,234 | 110 |
| Rename a profile | 274 | 1.2 | 0.65 | 22,812 | 75 |
| Answer a question many jobs asked | 249 | 1.9 | 1.45 | 30,020 | 130 |
| Edit a master resume line both profiles show | 244 | 3.4 | 2.6 | 21,410 | 302 |
| `gigai scout resume add` | 116 | 2.5 | 1.7 | 9,890 | 166 |
| Refresh a profile's selection from the master | 100 | 7.0 | (one call) | 9,962 | 196 |
| Answer a question no job asked | 6.8 | 1.4 | 1.0 | 218 | 100 |
| Add a master resume line | 5.6 | 1.6 | 0.97 | 199 | 106 |
| Retire a master resume line | 5.8 | 0.96 | 0.96 | 199 | 106 |
| Create a profile | 3.3 | 1.1 | 0.60 | 129 | 78 |
| Assess from the job page (GigAI's own time; the model's is extra) | 1.8 | 0.31 | 0.11 | 185 | 27 |
| Open a job page | 1.9 | 0.45 | 0.07 | 172 | 34 |
| The Jobs list on a Scout that just started | 0.81 | 0.59 | 0.13 | 40 | 40 |

The 0.1.10.10 column is the same test on a busy machine: its seconds would be lower on a quiet one, its
counts would not.

- **Saving no longer takes minutes once the background pipeline has finished jobs.** Saving your
  preferences, renaming a profile, adding a resume, editing a master resume line your profiles show, or
  answering a question many jobs asked made Scout check every finished job for changes, and each job's
  check read the same profile, answers and master resume again. The check now reads what the jobs
  share once. Which jobs run again after a change, and the limits on them (10 jobs per change, the
  daily cap), are what they were.
- **Three more things are out of every save.** Saving an answer first read the stored record of every
  watched company (one answer opened about 11,000 files with 10,350 companies; it opens about 660):
  it now reads the answers and what they cite. Every save of an answer, a master resume line or a
  resume rebuilt an internal index of your records before it replied; on a store with a watchlist that
  rebuild had been failing every time, after about half a second of work, and was thrown away. The
  index is now brought up to date in the background when Scout starts, and no save waits for it. And
  each time the store checked that a folder is its own, it started 7 git processes; it starts 3, and
  one save no longer repeats the check for every step.
- **The first Jobs load of a day.** Every watched company used to be matched again the first time you
  opened Jobs, ran `gigai scout new` or clicked "Assess these" after midnight UTC (a wait of 7 to 11
  seconds with 10,350 companies, every day). Now only the postings that just became older than your
  "posted within" window are looked at, and no company is matched again for it: with the app running,
  that first load is as fast as any other (0.1 to 0.3 seconds). The window itself works as before: a
  posting leaves the list on the first load of the day after it becomes too old.
- **The job page, the pipeline status, and assessing or tailoring a job.** The job page, the lists of
  assessments and answers, the pipeline status, "Assess", "Assess these", "Tailor resume" and "Process
  now" checked your store again and again within one request. They now check it once: the time GigAI
  itself spends on one of them (the model's own time is extra) went from 0.7 to 2.2 seconds down to
  0.04 to 0.35 seconds, measured on a busy machine before and after. A batch (`gigai scout new`,
  "Assess these") checks once for each posting it assesses too. The job page reads only its own job's
  assessment and tailored resume, not every one you have stored, and the pipeline status, which the
  Background pipeline panel in Settings reads, no longer looks up the project once for every job it
  lists.
- **"Assess these" no longer waits for the Jobs list to refresh.** While the list is being matched
  again in the background, the question is answered from the list you see. A posting the refresh is
  still working on is assessed when the refresh is done, as before.

#### Still slower than one second

The bar is one second for anything you wait on. These are still over it, measured as under "Faster"
(the same store, a quiet machine); they are next in line, not done in this version:

- **Refreshing a profile's selection from the master: 7.0 seconds.** `gigai scout resume master
  selection refresh` (and `POST /api/master/selection`). It makes 3 writes to the store; where the
  rest of its time goes has not been measured yet.
- **Editing a master resume line that two profiles show: 3.4 seconds** (2.5 to 2.6 after the first
  call). One edit is 8 writes to the store: the line, then each profile's selection and resume written
  again.
- **`gigai scout resume add`: 2.5 seconds** (1.6 to 1.7 after the first). 3 writes to the store, in a
  command that starts fresh every time.
- **Answering a question many jobs asked: 1.9 seconds** (1.4 to 1.5 after the first). One write, and
  the jobs that asked are looked up for the pipeline.
- **Saving your preferences, answering a new question, adding a master resume line, renaming or
  creating a profile: 1.1 to 1.6 seconds on the first call after Scout starts,** 0.5 to 1.0 seconds
  after it.

What is left in each of these saves is the store's own write (its journal: one entry is a git commit
with the checks around it) and the store reading its own state again before and after it.

Also over a second, measured on a second test store (20,720 companies, about 290,000 postings, 2
profiles) with each command run twice:

- **`gigai scout sources update`** asks every company that is due: against 20,720 local test boards it
  took 76 seconds, then 39 seconds. The `gigai scout new` after it matches every company again (8 to 9
  seconds there), not only the ones that changed, because the update writes every company's "checked"
  time.
- **`gigai scout resume master selection refresh --all`**: 7 to 9 seconds for the two profiles (one
  refresh for each).
- **`gigai scout resume master init`: 1.3 to 2.5 seconds** (1.3 to 1.5 with `--dry-run`), and
  **`gigai scout resume tailor`: 1.2 to 2.1 seconds** of GigAI's own time. Where their time goes has
  not been measured.

#### Master resume: the lines picked for a job

- **A resume made from your master resume no longer gets worse when the master gets better.** Adding
  strong lines to your master could push other strong lines out of the resume for a job and leave
  older, general ones in: for length the oldest roles went first and then lines were cut by a score
  that favoured what your profile already showed, with a fixed minimum of lines for every recent
  role. Now the lines are picked for what the posting asks: every requirement of the posting that
  your master can support keeps a line, and it keeps the strongest one (a line backed by a story or
  an answer, then one that states a number). How recent a role is only breaks a tie: an older role's
  line stays when it is the only evidence for a requirement, while recent lines that support nothing
  the posting asks for are cut first. No role is printed without a line, and every recent role keeps
  at least its best one.
- **A job you assessed keeps the lines its assessment pointed to.** An assessment says which lines of
  your master back each requirement, and it reads them by meaning: the line it points to may share no
  word with the requirement. The pick for that job keeps one of those lines for every requirement
  the assessment found evidence for, the required ones first, before any line chosen by shared words.
  A job with no assessment is picked by words.
- **A job's resume keeps the project or role its title is about.** A posting's title says what the
  job is about (`... Agent Platform`). The pick did not read it: a project whose own heading says the
  same could be dropped whole, while older roles kept lines about nothing the posting asks for.
  A role or project the title names (its heading or its own title holds a word of the posting's title,
  or half of its lines do) now always keeps its best line; it goes only when nothing but required
  lines and pins fit, and then `conflicts` says so (`title_entry`). What is left of the page after
  every requirement has its line goes to lines about the posting before lines that are only stronger
  or more recent. No required line and no skill is cut for it.
- **Your Skills section is kept whole.** A job's resume showed only the skills the posting or a shown
  line named, and a group written as `Python/Go/TypeScript` counted as one name that no posting ever
  asked for. Skills are now matched inside a group, the ones the posting asks for come first, and the
  whole section is shown. A skill is cut for length only when no line is left to cut (a master that
  lists more than 40 skill names gives up the ones nothing asks for first), and every cut is listed.
- **When something required cannot fit, you are told.** If the strongest line for a requirement, or a
  line you pinned, cannot be shown within 2 pages, `gigai scout resume master selection show` and the
  stored tailored resume say which and why (`conflicts`). Nothing required is dropped silently.
- **A refresh does not replace a profile's selection with a worse one.** `gigai scout resume master
  selection refresh` compares the selection a profile holds with the new one, check by check, against
  the master as it is now. The new one is stored when it is no worse on any check; the one held is
  kept when it is still valid and the new one is worse on a check; when neither can stand, nothing is
  stored and the command says what is unresolved.
- **A job's resume says what it was made from.** The job page read "from resume **(profile)**" also
  for a resume picked from your master. It now reads "from your master resume (revision 3), picked for
  profile ..."; `gigai scout resume tailor` prints the same, and an older stored resume keeps saying
  what it was made from. `gigai scout resume master selection status` says for each profile whether a
  resume for a job is picked from your whole master resume or made from that profile's own resume (a
  profile whose resume you replaced by hand after its selection was made). The rule itself is what it
  was.
- The same lines in another order in your master give the same resume, and a line that repeats
  another line is shown once.

#### Added

- **Newest posted first.** Jobs has an "Order" chip, "Newest posted": on, the list is ordered by the day
  each posting went up, the newest first (a posting whose board gives no date: by the day Scout first
  saw it). Off, the order is what it was: the best fit first. The address keeps it, so a reload or a
  bookmark does too.
- **New commands and options,** each described below: `gigai doctor --repair-journal` and
  `gigai scout resume master init --from FILE --dry-run` (under "Fixed"), and
  `gigai agent-permissions --port PORT` (under "For agents").

#### Changed

- **50 at a time.** The offers and commands that assess many postings now act on the newest 50 and
  never more in one go: the "assess the new ones" question of `gigai scout new` (and `--yes`),
  its low-ranked question (`--include-low-rank`), the old-assessments question (`--reassess-stale`),
  "Assess these" on Jobs and `gigai scout jobs assess`, "Assess all new" on a run, and
  `gigai scout new --process` (at most 50 waiting steps a call). "Newest" is the day the posting went
  up on its board, or the day Scout first stored it when the board gives none. Each question says the
  real total, the 50 and what the 50 cost, for example "Assess the newest 50 of 120 postings? ~50
  calls (70 more after these 50)". After a batch that left some, the output says how many are left and
  prints the command for the next 50 ("70 more ...: 50 at a time. Next: ..."). For new postings run
  that command, not a bare `gigai scout new --yes` again: the yes moved the "new since" time, and the
  printed command carries the time the 70 were counted from (`--since`). For "Assess these" and
  `gigai scout jobs assess`, the same click or command again takes the next 50. 50 or fewer: nothing
  changes. One path is not covered: a find-jobs run started with the cap "all" still assesses up to
  500 postings in that run. The background pipeline keeps its own limits (10 jobs per change, the
  daily cap on model calls).

#### Fixed

Posting dates:

- **A posting's date is shown.** Jobs rows and the job page say "posted 10 days ago" (the exact day on
  hover): the day the posting went up on its board. When the board changed the posting on a later day,
  the job page says so beside it ("posted 2 months ago · updated 3 days ago"). When a board gives no
  posting day, the page says "first seen 3 days ago", which is the day Scout first stored the posting.
  `gigai scout jobs list` and `gigai scout new` say the same with the day ("posted 2026-09-24").
- **A Greenhouse posting's date is the day it was posted, not the day it last changed.** Scout stored a
  Greenhouse posting's last change as its date. A posting that had been up for two months and was edited
  three days ago counted as three days old: it passed the 7 days and 30 days filters and your "posted
  within" window. Scout now reads the day the board first published it, and the filters, the window and
  the new "Newest posted" order go by that day. The day of the last change is kept beside it, never in
  its place (see "After you upgrade"). Lever and Ashby postings already had the right day.

Assessments:

- **Re-assess works on an old assessment.** A job whose assessment was marked old (made with an older
  prompt, or before you changed a setting, an answer or your resume) could not be re-assessed from its
  job page when it had no open question: the button was off. It is now on, says why the assessment is
  old and that re-assessing is one model call. This also covers a job with a tailored resume, whose page
  did not say the assessment was old at all.
- **One "old assessment" label, one reason.** A Jobs row said "old assessment: older prompt" and, beside
  it, "Stale: older settings". It now says it once, and the job page gives the same reason in the same
  words.
- **After a re-assessment the job page shows the new assessment.** "Assessed ..." showed the day of the
  job's first assessment; it now shows the day of the one on the page. A Scout label or a tailored
  resume made before the new assessment says so ("from before the latest assessment"), with what to
  click to make it again. "Minor gaps" is said beside a match only, not under "Needs your answers".
- **An Ashby posting whose description is only HTML can be assessed.** Some Ashby boards send a
  posting's description as HTML only, with no plain-text copy. Scout read only the plain-text copy, so
  such a posting had no text at all: assessing it failed every time with `job_text_unavailable`
  (reason `no_text`), and a keyword search never found it. Scout now reads the HTML description, turned
  into text the same way as for the other boards, when there is no plain-text one (see "After you
  upgrade"). A posting that has a plain-text description is read exactly as before. This is not a fix
  for an assessment that failed with `posting_requirements_unreadable`: that posting had its text, and
  the answer now says which rule refused (see "For agents").

`gigai scout new` and `gigai scout sources update`:

- **`gigai scout new --no-assess` never waits for an answer.** In a terminal it stopped at "N have
  only an old assessment; re-assess? [y/N]" and waited, although `--no-assess` means "do not ask and do
  not assess". It now prints the offers with their counts and ends, in a terminal and without one.
- **A question you have not answered does not use up "new".** `gigai scout new` moved its "new since"
  time before it asked its first question. If you pressed Ctrl-C at the question, the next run said
  "nothing new since" the run you had left; and an agent's asking call (`gigai scout new --json`, or
  any call without a terminal) was followed by a `gigai scout new --yes --json` that said "Nothing
  new" and assessed nothing. A call that asks now moves nothing: ask again and you get the same
  postings. The time moves with the answer: a yes or a no at the prompt, `--yes`, or `--no-assess`.
- **"Nothing new" says what it counts.** `gigai scout new` could say "Nothing new since your last
  check" right after a sources update that stored hundreds of new postings. Both were right, and
  nothing said why: "new" counts the postings your profiles match that Scout first stored after your
  last check, and an update counts every new posting on every board, whatever its title. The message
  and the update's summary now say so. ("First stored" is the time Scout first read the posting, never
  a date of the board's; a posting that changes later is not new again.)
- **`gigai scout sources update` says how many companies it checked out of all of them.** It said
  "3859 boards" while `gigai scout sources status` said 10,349 stored companies. An update asks only
  the companies that are due; the ones checked within the last day are left alone. The line now reads
  "Checked 3,859 of 10,349 companies this run (...). The other 6,490 were checked within the last day
  and were not asked again."
- **"~1 call", not "~1 calls",** in the questions of `gigai scout new` and `gigai scout jobs assess`.

Your resume and the master resume:

- **`gigai scout resume master init` reads the resumes people have.** It refused a resume with a
  `---` rule between two roles ("text after an entry's bullets"), and `--from FILE` refused section
  headings such as `## WORK EXPERIENCE` or `## AGENTIC AI PROJECTS` and a project written as a bold
  title with a tagline. Both forms now read them: a rule is skipped; a heading that is not one of the
  six sections is read as the closest one (Technical Skills is Skills, Selected Projects and Open
  Source are Projects, Publications, Awards and Certifications are Other lines, anything unknown is
  Other); text with no place of its own in its section is kept, never refused. The command says which
  heading it read as which section and which lines it left out, by line number, with a count that adds
  up; `--from FILE --dry-run` shows it and writes nothing. A file that starts with the marker line of
  GigAI's own `master.md` (`gigai-master:1`) is still held to GigAI's own format, and contact lines are
  removed as before. A refusal names the line number and the rule; on the terminal it now also shows
  that line.
- **A heading that is a link keeps its name.** A project or employer written as a link
  (`### [Driftwatch](https://github.com/...)`) lost its whole line when you made the master from a
  file or imported `master.md`, so the project had no name and its bullets went to the entry above;
  `gigai scout resume add` stored `### [Driftwatch](`. Now the link goes and the words stay, in an
  entry's heading, a bold title line and a role line right under one: the entry is "Driftwatch", with
  its bullets. The command says which heading lost a link, by line number ("link removed from the
  heading Driftwatch"); `--dry-run` shows it. A heading that is only a link is refused by line number
  ("this heading is only a link: give the project a name") instead of being dropped: for
  `gigai scout resume add` that means the resume is not imported until the heading has a name. Nothing
  else changes: GigAI still stores no links, and a link in a bullet or a paragraph, an email or a
  phone number in a heading, and your name and contact lines are handled as before.
- **`gigai scout resume clean` keeps a title that is a link.** `**[Dispatch Optimizer](https://...)**
  *(Python, Kafka)*` came out as `**[Dispatch Optimizer]( *(Python, Kafka)*`, the check called that
  clean, and the broken line reached the master resume and the PDF. The link goes and the words stay,
  as `gigai scout resume add` does; the cleaned copy is now what `resume add` stores. A heading that is
  only a link is refused by line number, as `resume add` refuses it.

The store and the running app:

- **A save cut off by a crash no longer leaves a store with no way out.** When GigAI was killed, or the
  machine lost power, in the middle of a save, every later save was refused (and, when the crash came
  after the save's files were written, every read of your answers too), and all you saw was a Python
  traceback (in the Scout page: "an internal error occurred"). The store already knew how to finish
  such a save, but no command ran that. Now one does: `gigai doctor --repair-journal`. It finishes the
  interrupted save in every workpad of the home and says what it finished; the save that was cut off is
  completed, not lost, and the next save works. On a healthy home it reports "nothing to repair" and
  changes no file, and it can run beside a running Scout server. GigAI does not run it on its own, not
  when the server starts and not before a save. You learn about it where you are stopped: the refused
  save or read ends in one line that names the command with your `--home`, in the terminal and from
  the API. When the crash came after the files were written, plain `gigai doctor` also fails on
  `journal.index` and names the command. The command checks that each workpad's ownership markers name
  the folder it is in, and refuses one where they do not, saying which marker differs.
- **Scout's memory no longer grows by about 32 MB with every resume pick.** Every time Scout measured
  how many pages a resume takes (one pick from a master resume measures about 18 times), the layout
  engine kept memory that was never given back: a Scout left running through many tailorings grew by
  gigabytes. Its memory now stays level. Measured in one process: 50 picks grew it by 1,599 MB before
  and by 17 MB now, and the read-only pick report peaked at 2,099 MB before and at 146 MB now. The
  price: a pick takes about 1.15 times as long (0.15 s before, 0.17 s now).
- **`gigai scout run` never stops the Scout of another GigAI home** (see "After you upgrade").
- **`gigai scout status` no longer says "stopped" about a running Scout.** Where it could not
  identify the server's process (inside an agent's sandbox), it said "stopped" and forgot the running
  Scout. It now checks the process and the API apart and says what it found (see "For agents").

A new user's first steps:

- **`gigai scout run` on a machine with no model CLI says what to install.** It used to say "rerun
  `gigai setup`" to someone who never ran it; now: "no model CLI was found: install Codex or Claude
  Code, then run `gigai scout run` again", with the way in for an API key or a local Ollama model.
- **With no settings yet, `gigai scout status` and `gigai doctor` name the way in.** "configuration is
  missing at ...; run 'gigai setup'" is now "...; run 'gigai scout run' once (it needs Codex or Claude
  Code installed) or run 'gigai setup'".
- **The getting-started pages say what is true.** The quickstart no longer says a local Ollama model
  or an OpenRouter key "also work": Scout does not start without Codex or Claude Code installed. The
  install page runs `gigai doctor` after the first `gigai scout run`, not before it. The agent start
  page, the agent guide, llms.txt and the instructions `gigai agent-skill` prints give the exact
  command for a yes (the one the question carries).
- **`gigai models` prints a CLI's version as `v0.160.0`,** not `vcodex-cli 0.160.0`.

#### For agents

- **The assess preview says what would be sent.** `gigai scout jobs assess URL --json` without `--yes`
  (and `POST /api/postings/assess` without `approve`) still makes no model call. Its reply now carries
  `model_input_summary`: the profile (id and label), where the resume comes from (`profile_view` or
  `master_evidence`), whether your answers and stories go with it, the model target and where it runs,
  and whether a posting is fetched from its public board first. It holds ids, labels and counts, never
  a line of your resume or answers. In a terminal the same facts are printed above the y/n question.
- **A failed assessment says what happened and what to do.** For `model_target_unavailable`,
  `model_denied`, `model_unavailable`, `assess_timeout`, `model_output_invalid` and
  `assessment_not_stored`, the CLI's JSON, the API's error and the job page now also say whether a model
  call started (`model_call_started`), whether it may have used tokens (`may_have_used_tokens`), that
  no new assessment was stored (`fresh_assessment_stored`), and the next action (`next_action`).
- **"Could not read this posting's requirements" says which rule refused.**
  `posting_requirements_unreadable` is one code for two rules (the model said "Matched" on fewer than
  three requirements for a long posting; or the text has no requirement wording and the model found
  none). A batch's failure, the terminal line and the API's error now carry a `reason`
  (`matched_on_too_few_requirements` or `no_requirements_in_text`). The model answered in both cases
  and nothing is stored.
- **Batches are 50: the questions carry the numbers.** Each question of `gigai scout new --json` and of
  `gigai scout jobs assess --json` (and `GET /api/new`, `POST /api/postings/assess`) has the total,
  `batch` (what a yes acts on, at most 50) and `more_after`; the estimate is the batch's. After a yes
  that left some, `assessed` / `reassessed` carry `more_after` and `next` (the call for the next 50,
  `cli` and `api`). Run `next`: for new postings a bare `--yes` again says nothing new.
- **A `gigai scout new` reply that asks is a preview.** A reply with status `ask` moves nothing
  (`anchor.advances` is `false`): ask again and you get the same postings and the same question. The
  "new since" time moves with the answer: `--yes` (the command in `question.yes.cli` carries `--since`
  and covers exactly the postings you showed; a bare `gigai scout new --yes --json` right after the
  question works too) or `--no-assess` for a no. A reply that asks nothing (nothing new, every new
  posting assessed, or only the low-ranked question) moves it as before. `GET /api/new` never moves it.
- **A posting's dates each have their own name.** `gigai scout jobs list --json`, `gigai scout new
  --json` and the API rows carry `published_at` with `published_kind` (`posted` for Greenhouse, Lever
  and Ashby; `updated`, the weaker claim, for a kind of board Scout has no posting day for),
  `updated_at` for the board's last change, and `first_seen_at`.
  `GET /api/postings?sort=newest_posted` is the "Newest posted" order (`sort=fit` is the default).
- **`gigai scout status` has a state `unreachable`.** It checks the process and the API apart. A
  running Scout whose address cannot be reached from where the command ran (a sandbox) is reported as
  `process: running (pid N); API: not reachable from here` (state `unreachable`), and the JSON has a
  `process` and an `api` block. It says "running" only when the API answers. A script that switches on
  running / stopped / crashed sees a new value.
- **A store that needs the repair says so in a code.** A save or a read refused after an interrupted
  save carries `next_action` (the `gigai doctor --repair-journal --home ...` command) in `--json`, and
  the API answers with the code `journal_reconciliation_required` and `next_action` instead of
  `internal_error`.
- **`gigai agent-permissions --port PORT`** prints the permissions snippet for a Scout you started on
  another port than 8765.
- **The agent guide names three separate approvals** (your choice to assess, Scout's own `--yes`, and
  your agent runtime's own approval), says what one assessment sends, what to check after an
  interruption before retrying, and gives the address of a job's page. `gigai agent-skill` prints the
  updated instructions: install them again to get them.
- **A record write says `projection_pending: true`, and that needs no action.** The JSON of
  `gigai record native create` and `override`, and a record tool's result, used to say `false` after a
  write. A write no longer rebuilds the internal index of records before it replies (see "Faster"), so
  it now says `true`: the record is stored, and the index follows when Scout next starts. Nothing reads
  that index to answer you. `rebuild_action` is now always `null`: it used to name `rebuild_index`,
  which was never a command, and there is nothing to run.
- **Which resume a job's resume is made from.** `gigai scout resume master selection status --json` and
  `GET /api/master/selection` carry `tailoring_basis` (`master` or `profile_resume`) and
  `tailoring_basis_line` (the sentence) for each profile. A resume with a heading that is only a link
  is refused by `gigai scout resume add` and `gigai scout resume clean` with the code
  `resume_heading_only_link`. `gigai scout run` on a port another home's Scout holds ends with
  `scout_run_port_in_use`.

### 0.1.10.10

A fix for 0.1.10.9: `gigai scout new` stopped with an error on the first run after an update when
your store of postings is large. Nothing to do after you upgrade: install this version and run it
again. The docs screenshots are current again too.

- **Fixed: `gigai scout new` crashed on a large store.** On the first run after an update, with 500 or
  more companies to match again, `gigai scout new` stopped with `NameError: name 'time' is not defined`
  instead of an answer (it worked again once the local app had opened the Jobs page). It now prepares
  the postings itself and says how far it is ("preparing your postings: 40% ...") while you wait.
- **Screenshots.** The screenshots in the docs are from this version again (0.1.10.9 kept the older
  ones) and now show the Master resume page, Picked and Left out on a job's resume, where a job's
  resume file is in your resumes folder, and an agent adding a story to your master resume.

### 0.1.10.9

The Jobs page now loads on a large store of postings (about 290,000 postings from about 10,000
companies, where it never finished loading) and has real pages. Fixes from the first days of real
use: re-assessing a job keeps its posting, an answer says who wrote it, an assessment lists every
stated requirement, one missing tool from a list no longer holds a job back, weak fits have their
own group, postings are ordered by how well they fit, and a yes assesses only postings ranked 50 or
more. Tailoring puts a skill you confirmed in an answer on the resume, cuts for length only by
leaving out your oldest roles (with Restore), stores a resume you edited back for one job, and saves
your resumes in a folder you can see. New: a master resume, one document that holds everything you
have done, from which each profile and each job takes its lines. Read "After you upgrade".

#### After you upgrade

- **One more preparation, with progress.** The stored postings are prepared again once, in the
  background. The Jobs page shows "Preparing your postings (one time after an upgrade)... N%" and
  the rest of Scout stays usable meanwhile; `gigai scout new` prints the progress too. After that
  the Jobs page answers in a fraction of a second.
- **You cannot go back to 0.1.10.8 without one step.** Scout's local posting store
  (`pipeline.sqlite`, a cache plus the history of its background work) has a new layout. If you
  install 0.1.10.8 again, delete that file first; it is rebuilt.
- **Stored assessments read "Assessed with older settings" once.** The assessment rules changed (see
  "One missing tool from a list" and "Every stated requirement" under Fixed), so an assessment made
  before this release is offered for re-assessment like any other old one. Nothing is assessed again
  until you say yes.
- **The pipeline may tailor a job's resume again.** The tailoring instructions changed (see "A skill
  you confirmed" and "over 2 pages" under Fixed), so the next time the background pipeline processes
  a job it tailors that job's resume again, within the daily limit on model calls. A resume you
  tailored or edited yourself is kept, as before.
- **In the API and in `--json` output, `company` is now the company's name.** The board's id (what
  `company` held before) is in `company_slug`. `company_name` is still there and says the same name.
  A script that used `company` as an id should read `company_slug`.
- **Your tailored resumes are copied into a folder you can see.** The next time Scout starts (or
  when you run `gigai scout resume folder`) it copies the tailored resumes you already have, as
  markdown, into `~/Documents/GigAI/resumes`, which it makes when it first writes there. Nothing is
  moved or deleted, and the folder never gets your name or contact details. See "Your resumes have
  one visible folder" under Changed.
- **The master resume changes nothing until you make one.** The upgrade makes no master: profiles,
  assessments and tailored resumes work as before until you run `gigai scout resume master init` or
  make the master on the Master resume page in Settings (opening the page writes nothing). Look
  first: `gigai scout resume master init --dry-run` writes nothing and lists every line of your
  resumes that would be left out.
- **Once you have a master.** An assessment for a profile reads the lines of your whole master that
  fit the posting best, not only the profile's 2-page resume; making the master marks no stored
  assessment as old. The background pipeline tailors a job's resume again the next time it processes
  that job, within the daily limit on model calls (a resume you tailored or edited yourself is
  kept). You can no longer go back to an older version: it cannot read a profile that selects from
  the master, nor an assessment made since (`resume_basis`). And creating a profile answers at once,
  with the profile's first selection made a few seconds later (up to about 15 seconds on a very
  large home). Saving a change to the master takes several seconds on a very large home: about 4.5 s
  with 290,000 postings and 10,350 companies, about 12 s when two profiles show the changed line.

#### Fixed

- **The Jobs page loads on a real-sized home.** Scout now prepares the postings once and shares the
  result between every request instead of each request doing the whole job itself. It remembers
  the result between runs, so the command line and a restarted Scout start ready, and a company's
  update re-checks only that company. On a test home of 290,000 postings and 10,350 companies the
  server stays near 200 MB, a warm request takes about a tenth of a second (it took 5 to 40
  seconds), and `gigai scout new` takes under a second.
- **A skill you confirmed in an answer is put on the tailored resume.** When a posting asks for a
  skill, your resume does not name it, and one of your answers says you have it, the tailored resume
  now shows it in Skills, with the answer as its source (for example Helm, after you answer "yes" to
  the Helm question). Before, the answer was passed to the tailoring and the resume often came back
  unchanged, so the Scout ATS score still listed the skill as missing. An answer that says you do
  not have the skill adds nothing.
- **A tailored resume over 2 pages is cut to 2, and only by leaving out your oldest roles.** Scout
  measures the pages itself and leaves out whole roles, the oldest first, until the resume fits.
  Nothing else is cut for length: the tailoring no longer drops bullets from recent roles to save
  space. One older rule stays: a role that ended more than 8 years ago keeps its first 3 bullets.
  The job page shows what was left out ("Cut for length: ...", the roles and the older bullets)
  with a **Restore** button that puts all of it back in one step, and "Cut for length again" to
  undo that. `gigai scout resume tailor` prints the same line, `gigai scout resume length --job-url
  URL` shows it later (`--restore`, `--cut`), and the API has `result.length` and
  `PUT /api/tailored-resumes/length`. If the pages cannot be measured, nothing is cut and the line
  says so; if leaving out older roles would not get the resume to 2 pages, no role is cut and the
  line says it is over the limit.
- **The Skills section no longer repeats one bullet inside another.** A Skills line whose every
  skill another Skills line already lists is dropped (it happened when a resume's skills lines had
  no bullet markers and the tailoring listed them in a new order).
- **The background ranking no longer slows everything down.** It used to match the whole store of
  postings against your profiles again on every turn.
- **The Jobs page has real pages.** Pages of 50 postings (25 or 100 if you prefer) with Prev, Next
  and page numbers, and "Showing 51-100 of 591 postings", instead of a list that grows by "Show 50
  more". The page, its size and your filters are in the address (`#/jobs?page=3&state=needs_answers`),
  so Back from a job returns to the same page and a page can be bookmarked. Changing a filter or the
  search starts again at page 1, and the counts at the top stay the totals.
- **Going back from a job to the list keeps the list.** The Jobs page keeps what it loaded,
  refreshes it in place and asks for one thing at a time. A slow answer says what it is waiting for.
- **A browser that goes away is no longer an error in the log.** It is logged as "client closed the
  connection".
- **Re-assessing a job after an answer keeps the posting.** A posting from a Greenhouse board that a
  company shows on its own site (a `...?gh_jid=...` address) was read from the stored posting the
  first time, and from the company's web page after you answered a question: the page's menus were
  stored as the posting and the location was lost. Every assessment and tailoring of a job address
  now reads the stored posting first and never falls back to a web page when the board's own text
  is known. A page that is the only source is read without its menus, header and footer.
- **An answer says who wrote it and where it came from.** An answer your agent saved was recorded as
  yours. `gigai scout answer` and `gigai scout answers save` take `--as agent` (`--actor` still
  works) and a free-text `--source` ("from the user's repo, at the user's request"). An API write
  that names no writer is yours from the Scout page and the agent's from anywhere else. The Answers
  and stories page shows both. The agent instructions tell agents to pass them.
- **One missing tool from a list no longer holds a job back.** A posting that asked for "Docker,
  Helm, and Kubernetes" in one sentence waited at "Needs your answers" when your resume showed
  everything but Helm: one tool inside a list counted as much as "8+ years of experience". A tool
  named inside a list of three or more is now its own kind of requirement ("One of a list"). One of
  them unknown leaves the job Matched, says "1 minor gap: Helm", and still asks the question, which
  you can answer or leave. Two or more unknown tools of a list, or one unknown requirement that
  stands on its own line, wait for your answer as before. Requirements under "bonus" or "highly
  desirable" that you do not meet are named as minor gaps too.
- **Every stated requirement gets a row.** An assessment listed at most 12 requirements and dropped
  the rest without saying so, so many jobs read "N of 12". It now lists every requirement the
  posting states, must-haves first, including "highly desirable" lines and experience such as
  mentoring engineers or having worked in a named kind of company. Past 40 rows it keeps the first
  40 and says "+N not shown".
- **A job opened by its address on the company's own site has its rank, pay and location.**
  `GET /api/jobs?url=` for a posting that no find-jobs run had picked up (every posting `gigai scout
  new` assesses, including a Greenhouse posting shown on the company's site as `...?gh_jid=...`)
  answered with no rank, no pay, no work-mode fit and no H-1B figures. It now reads them from the
  stored posting, and answers for a stored posting that has not been assessed yet.
- **The API says a company's name in `company`.** `posting.company` was the board's id (for example
  `ospreylabs`) while the name sat beside it in `company_name`, and agents read the id as the name.
- **The job page shows the resume the pipeline just tailored.** After you answered a question on a
  job page, the timeline said "Tailor resume: Done" while the Tailored resume panel, "Tailor again"
  and the state "Resume tailored" appeared only after a reload. The page now reads the stored
  resume again when the tailor step finishes.
- **Past runs lists the selected profile's runs.** Past runs could show every profile's runs under
  the selected profile when the first read answered last.
- **Saving is faster on a home that watches many companies.** Every write of a resume, of the master
  resume or of a profile's selection read and checked every file of the watched-companies list first
  (one file per board: over 10,000 on a large home). A write now reads only what it uses. On a synthetic
  home of 290,000 postings and 10,350 boards, adding a line to the master went from about 6.9 s to
  about 4.5 s, an edit of a line that two profiles show from about 18.6 s to about 12.1 s, and `gigai
  scout resume add` from about 7.7 s to about 5.3 s. Saving an answer is not changed by this.

#### Changed

- **Weak fits no longer sit in "Needs your answers".** A posting that waits on your answers while
  few of its requirements are met and its rank is low (fit below 40% and rank below 50) is now a
  "Weak fit". It has its own chip on the Jobs page, off by default, and is listed only while that
  chip is on (`gigai scout jobs list --state weak_fit`). It asks no questions, is not counted in
  "Need your answers", is left out of `gigai scout new`, and a saved answer never queues it in the
  pipeline.
- **Postings are ordered by how well they fit.** Each assessed posting shows one fit number: the
  share of its requirements that are met, with the must-haves counted twice ("Matched · fit 85% ·
  9 of 11 requirements · rank 76"). Inside each group the best fit comes first, then the higher
  rank, then the newer posting. The Jobs page and `gigai scout new` use the same order.
- **A yes assesses only postings ranked 50 or more.** `gigai scout new --yes` and "Assess these"
  leave out the low-ranked postings and ask about them separately ("112 low-ranked ones are skipped
  (rank below 50); assess those too? ~112 calls"). `--include-low-rank`, or the box in the approval
  dialog, assesses them too. A posting that has no rank yet is still assessed.
- **The three numbers are settings.** Add a `fit` block to the project's `settings.json`:
  `{"fit": {"assess_min_rank": 50, "weak_fit_below_percent": 40, "weak_fit_below_rank": 50}}`.
  Each is 0 to 100, and 0 turns that rule off.
- **Your resumes have one visible folder.** Each job's tailored resume (markdown) is now also saved
  in `~/Documents/GigAI/resumes`, named `<company>-<role>-<date>.md`, instead of only under a hidden
  path with a hash for a name. `gigai scout resume pdf` without `--out` writes its PDF there too
  (it used to write into whatever directory the command was run from) and prints the path. Change
  the folder in Settings, with `gigai scout resume folder --set PATH` or `PUT /api/resumes-folder`;
  `gigai scout status` and the job page show it. The folder never holds your name or contact
  details: it gets the markdown and the PDFs made without a header, and a PDF you make with the
  Generate PDF form is saved only where you save it. A file you change in the folder stays yours:
  Scout replaces only files that are exactly what it last wrote, and gives a newer one a new name
  (`...-2.md`). Tailored resumes you already have are copied in the next time Scout starts. A GigAI
  home other than `~/.gigai` keeps its folder inside itself (`<home>/resumes`).
- **An edited resume can be stored back for one job.** `gigai scout resume tailor --in FILE --job-url
  URL` (or `PUT /api/tailored-resumes`) stores your edited markdown as that job's tailored resume,
  marked edited with who wrote it (`--as agent` for an agent, and `--source` for where the edit came
  from). Before, the only choices were a PDF that was stored nowhere and never scored, or replacing
  the profile's resume for every job. Lines you did not change keep their sources. A line you
  changed or added is checked: no name or contact detail, and every number and skill it states must
  be in your resume or one of your answers; a refusal lists each problem by line number, and the fix
  is to save the answer first. The Scout ATS score and the Scout label are then made again from the
  edited resume (one model call, for the assessment against it). Background tailoring never
  replaces an edited resume; asking for a new tailoring does.

#### Added

- **A master resume.** One document that holds every role, bullet, project and skill you have, with
  an id on every line and no contact details. Each profile shows a selection of its lines, each
  job's tailored resume is picked from the whole of it, and an assessment reads the lines that fit
  the posting best. Lines are picked by code, with no model call; the one tailoring call and the one
  assessment call are the ones Scout made before. It is opt-in: nothing changes until you make one
  (see "After you upgrade").
  - **The store, and a file you can edit.** `gigai scout resume master show` lists every line with
    its id and `gigai scout resume master history` lists the revisions: each change is a new
    revision and nothing is rewritten. The master is also a file, `master.md` in your resumes folder
    (`~/Documents/GigAI/resumes` unless you chose another), written again after every change,
    whoever made it. Edit it in your own editor, then import it with **Import the file** on the
    Master page or `gigai scout resume master sync`: a line you typed gets an id, every other line
    keeps its id, and a line you removed is retired and can be restored. GigAI never reads the file
    by itself; until you import it, Scout says "master.md has changes not imported yet" and never
    replaces it (a change made in Scout meanwhile is written beside it as `master-2.md`). An import
    is refused, with your file left as it is, when the file does not read as a master, when it holds
    a name, email, phone number, link or address (named by line number), or when the master changed
    since the file was written, unless you import it anyway (`--revision N`, or **Import it
    anyway**): what was added since is then retired.
  - **What a profile shows.** When the master is made, each profile's first selection is its own
    resume, which is not rewritten, so nothing you assessed or ranked goes stale. A selection is
    sticky: when you edit or retire a line a profile shows, its resume follows; lines you add to the
    master are only offered (`gigai scout resume master selection status`: "3 new master lines:
    refresh?"). `gigai scout resume master selection refresh` selects again from the whole master,
    against the postings the profile's titles match in your local index, and makes the result the
    profile's resume. A selection is fitted to 2 pages by measuring it with the PDF template: recent
    roles always appear, and for length the oldest roles are shortened, then dropped, first. `gigai
    scout resume master selection show` lists it as Picked / Left out, each line with its reason,
    for a profile or for one job (`--job-url`, or `--job-text FILE`). A new profile gets its own
    first selection the same way. A profile whose resume you replace by hand keeps that resume: the
    master leaves it alone until you refresh its selection.
  - **Making it from your resumes.** `gigai scout resume master init` merges the resumes your
    profiles hold: the lines of all of them, the same line kept once, and a line worded twice folded
    into the newer wording. When two resumes state one line with different numbers, Scout asks which
    is right and writes nothing until you answer (`--answer ID=a`, `=b`, or `=both` to keep the two
    lines). Nothing is left out silently: `init` and `init --dry-run` count every line of your
    resumes as kept, folded into a line the master holds, or left out, and name each left-out line
    by its line number in the stored resume and the reason, never by its text ("Of 20 lines of
    resume text ...: 18 kept, 0 folded into a line the master holds, 2 left out";
    `migration.source_lines` in `--json`). Your name, a headline and contact lines are never kept.
    `gigai scout resume master init --from FILE` stores a resume markdown file as the master
    instead.
  - **A job's tailored resume is picked from the whole master.** Code first picks that job's
    candidate lines from the whole master (about twice what fits on 2 pages), starting from the
    lines the profile shows. The one tailoring call then orders and words the lines inside that set,
    as before, and code cuts the result to 2 pages: the oldest roles first, every recent role still
    present, and a line that is the only one naming something the posting requires kept. What was
    cut is listed and one Restore puts it back; the "Cut for length" line says "older bullets" only
    when every bullet it names belongs to a role that ended more than 8 years ago. The Skills line
    is put together by code (the posting's required skills that your master lists, then its
    nice-to-haves, then what the offered lines name; 28 skills at most), so the master's whole
    Skills list is never put on one resume. When the model call fails or no model is available,
    tailoring on demand still gives you a resume: the code's own 2-page selection, marked as picked
    by code. A pasted resume, a profile whose resume you replaced by hand and a home without a
    master are tailored exactly as before.
  - **An assessment reads it, and says when your resume changed for it.** Assessing a job for a
    profile (the job page's Assess, `gigai scout assess`, "Assess all new", `gigai scout new --yes`)
    shows the model the lines of your whole master that fit that posting best, picked by code,
    within the same limit on the resume's size and in the same single call. A requirement your
    master covers is no longer a question because one profile's 2 pages left the line out: in our
    test on invented postings (11 cases, each assessed twice both ways) the open questions went from
    63 to 6, no case got a worse verdict and no evidence was invented; the prompt is about 14%
    larger. The assessment of a tailored resume still reads the 2 pages that will be sent, a
    find-jobs run still reads the profile's resume, and a pasted resume or a profile whose resume
    you replaced by hand is assessed as before. A stored assessment can now read "old assessment:
    resume changed" ("Resume changed" on a job's card), only when the change concerns it: a line it
    quoted as evidence was edited or retired, or a new line names something it left as an open
    question ("A new line of your resume may answer: Have you used Helm?"). Nothing is assessed
    again on its own. For agents: `basis_stale_reason: "resume_changed"` with `basis_stale_resume`,
    and `resume_basis` on an assessment that read the master.
  - **Your agent keeps it up to date from a chat.** `gigai scout resume master add`, `edit` and
    `remove` change one line, role or skill by its id. A story or an answer becomes a line of the
    master with `--from-story ID` or `--from-answer ID`: the line is linked to it, and from then on
    every profile and every job can select it. An agent passes `--as agent` and says where the
    evidence came from with `--source`. An edit or a removal names the revision it read (`--revision
    N`) and is refused when the master changed since. Text that looks like contact data is refused
    and stored nowhere, and a line the master already has in other words is not added unless you
    pass `--force`. A removed line is retired, not deleted: `gigai scout resume master show
    --retired` still lists it, and `gigai scout resume master add --restore ID` puts it back under
    the same id. The agent instructions (`gigai agent-skill`) have a step for this: after saving a
    story or an answer with substance, the agent asks "Want this on your resume?". An agent reads
    the master with `gigai scout resume master show --json`, never from the file.
  - **The Master page, and Picked / Left out on a job.** Settings has a new page, **Master resume**
    (`#/master`). With no master yet it offers to make one: it shows what the merge would do, counts
    every line of your resumes and lists each left-out line by resume, line number and reason, asks
    about each line your resumes word with different numbers (keep A, keep B, or both), and writes
    nothing until you answer. After that it lists the master by role: add a line or a role, edit
    one, retire one (History puts it back); each line shows how strong its evidence is (backed by a
    story or an answer, states a number, or stated) and which profiles show it. A change that
    crosses one your agent made is refused, and the page shows the master as it is now. Each profile
    says where its selection stands ("3 new master lines: refresh?") with a **Refresh** button. On a
    job page, a resume tailored from the master has **Picked (n)** and **Left out (m)**: every line
    with the reason it is shown or not, **Remove** to take a line off this job's resume and **Add**
    to put one on; when an added line would make the resume 3 pages, Scout names the line that would
    be cut to keep 2 and asks. A line your agent edited on a tailored resume offers **Save this
    wording to your master**. The API has `GET /api/master`, `GET /api/master/history`, `POST` and
    `PUT /api/master/lines` and `/api/master/entries`, `GET` and `POST /api/master/migration`, `GET`
    and `POST /api/master/selection`, `POST /api/master/sync` and `PUT
    /api/tailored-resumes/selection`; a tailored resume carries what was picked and left out as
    `selection` (`gigai scout resume tailor --json`, `GET /api/tailored-resumes`).

### 0.1.10.8

Fixes from the first days of real use of 0.1.10.7, a security fix for everyone who uses Scout
with the Codex CLI, and the pages an AI agent needs to set GigAI up by itself. Read "After you
upgrade" and Security first.

#### After you upgrade

- **The stored postings are rebuilt once, on the next read.** The rules for which profile a
  posting belongs to and for when a posting counts as changed were fixed (see Fixed), so the first
  `gigai scout new` or Jobs page after the upgrade works them out again. No model call is made.
  That first read can take longer than usual.
- **Postings with only an old assessment are a separate question now.** `gigai scout new` offers
  them with their own count and estimate. Nothing is assessed again until you say yes to that
  question (`--reassess-stale`).
- **Scout's Codex calls check Codex's tool list before every call.** A Codex upgrade that adds a
  tool is handled: Scout reads the list again each time and turns the new tool off. If a Codex
  release changes the commands Scout reads that list with, Scout's Codex calls stop with a clear
  error instead of running with tools on, until GigAI is updated. Checked with codex-cli 0.159.3.
- **A Codex call sends fewer input tokens**, because Codex no longer adds its tool definitions to
  it. Your averages in `gigai scout metrics` will move.
- **If you pasted the 0.1.10.7 permissions snippet into Claude Code, replace it** with what
  `gigai agent-permissions` prints now (see Changed).

#### Security

- **Every model call now runs with all tools off. For Codex this closes a real gap.** In 0.1.10.7
  and earlier, a Scout call through the Codex CLI turned off Codex's shell tool and memories, and
  nothing else. Codex's web search is on by default, so it stayed on, together with any MCP
  servers, app connectors and plugins in your Codex settings, sub-agents and the image tool. Job
  posting text is written by strangers. A posting could in principle have steered the model into
  a web request or a tool call in the same call that holds your resume. Scout now turns off web
  search, every configured MCP server, apps, plugins, hooks, browser and computer use,
  sub-agents and the other tool features on every Codex call, whatever your Codex settings say.
  Your Codex login, model provider and default model are unchanged. The 0.1.10.7 notes said
  Scout's model calls had no tools; for the Codex CLI that was not true.
- **It fails closed.** If Scout cannot read Codex's lists of features, MCP servers or models, or a
  server stays on, no model call is made. If Codex ever reports a tool call, Scout discards that
  answer.
- **Claude Code, the hosted APIs and Ollama needed no change.** Claude Code calls already ran
  with no tools, no MCP servers and no user settings; the hosted API and Ollama calls send no
  tool definitions. This is now checked by tests.
- **A resume is checked for contact details before an agent reads it.** See the two new commands
  below. The agent skill tells your agent to run the check first and to stop when it finds
  something. If you paste a resume with contact details straight into a chat, that is outside
  GigAI's control.

#### Added

- **`gigai scout resume check FILE`: is this resume free of contact details?** It runs on your
  computer, with no model and no network, and prints the kind and the line number of each
  finding (name, email, phone, address, link, work authorization), never the text itself. It
  exits with code 2 when it finds something. It works on patterns and can miss a detail inside a
  sentence.
- **`gigai scout resume clean FILE --out COPY`: a copy without those lines.** Your file is not
  changed. The copy passes the check.
- **A "Start here" page for your agent.** One docs page that you give to Claude Code or Codex
  before anything is installed. It walks the agent through installing, the resume check, a few
  questions for your profile, Update sources and the first `gigai scout new`, and it tells the
  agent to ask before anything that uses the network or a model. The README has a prompt to
  paste, and the docs site has a plain-text `llms.txt` with the same steps.
- **How to use Scout from each agent.** The For agents page now says, for Claude Code, Codex and
  other agents, what to install, where it goes and what to type.
- **"Your first 10 minutes"**: the same path for a person, from install to a PDF.
- **A Token usage page.** Measured tokens and time per step (rank, assess, tag, tailor,
  re-assess) on Codex and on Claude Code, and what a first catch-up and a normal day used on a
  real install. Tokens, not prices. The first run is the expensive one.
- **A network notice before the first Update sources.** Scout checks about 10,000 public job
  boards and keeps checking 8 times a day, so run it on your own computer and your own network.
  The UI shows the notice once, the README and the docs carry it, and an agent tells you before
  it starts the first update. Background checks can be turned off in Settings.
- **Progress while a batch is assessed.** `gigai scout new --yes` prints lines such as
  "assessed 120 of 333 · ~18 min left" while it works. They go to the error stream, so `--json`
  output is unchanged.
- **`gigai scout profile list` warns about a title that matches too much.** A generic title such
  as "Staff Engineer" on its own matches every posting with those words. The command says how
  many, so you can add a more specific title.

#### Changed

- **The order of the grid.** Postings with a current assessment come first, then ones whose only
  assessment is old, then ones not assessed. Inside each group: Scout's "recommended" label,
  then the verdict (matched, needs your answers, other, not a match), then the rank. `gigai
  scout new`, `gigai scout jobs list` and the Jobs page use the same order.
- **The score column is words, not a bare percent**: the verdict, "9 of 11 requirements" and the
  rank. An old assessment says so and why ("old assessment: older prompt").
- **`--yes` answers the "new postings" question only.** The old assessments are the second
  question, and `--reassess-stale` is its yes. The two can be combined.
- **"To assess" means new postings that no matching profile has assessed.** It no longer moves
  while the background ranking runs, and after a full yes it counts only the ones that failed.
  Postings with only an old assessment are counted apart.
- **A generic title no longer pulls in other functions.** When a profile has both a generic
  title ("Staff Engineer") and a specific one, a posting that only the generic title matches is
  kept only if its known function fits the profile. "Staff Security Engineer" no longer lands in
  a software profile. A profile with only generic titles is unchanged, and gets the warning
  above.
- **Company names read as the company writes them** ("Garner Health", not "Garnerhealth"), in
  `gigai scout new`, the Jobs page, the job page, tailored resumes and PDF file names.
- **A posting stays with the profile that assessed it.** A later rank for another profile no
  longer moves it to that profile's "not assessed" row. A tailored job keeps its verdict and
  says "resume tailored" beside it.
- **Update sources fetches descriptions for every active profile's titles**, not only the
  selected profile's. A posting whose description is still missing has it fetched when you
  assess it, with one request.
- **The agent artefacts.** `gigai agent-skill --out` creates the folders it needs. The skill's
  description names "what's new on Scout", so Claude Code picks it up for that question. The
  permissions snippet's curl rules name Scout's port (`curl http://127.0.0.1:8765/*`), the form
  Claude Code's rule syntax matches.

#### Fixed

- A posting was marked "changed since it was assessed" when only its description had been
  missing at the time. It is read as current now, without a new model call.
- `gigai scout new --yes` skipped postings whose description was not stored yet. It fetches the
  description first, and says why when it cannot (removed, refused by the board, no text,
  network error).
- A city that shares a state's name ("New York, NY") was read as no city at all, so a
  remote-only profile kept those on-site roles. It is read as the city.
- A model call could be recorded as successful while no assessment was stored. Such a call is
  now recorded as failed, with the reason, and the batch lists it. A failed write no longer
  stops the whole batch.
- Assessments imported from old runs that recorded no prompt version are labelled as made with
  an older prompt, instead of showing nothing.
- The yes and no commands that `gigai scout new --profile` suggests keep the `--profile`.

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
