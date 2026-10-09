---
title: Quickstart
description: From zero to a running Scout, in three steps.
---

One path from zero to a running Scout: find jobs, assess them, get the resume picked from your master.

## 1. Requirements

- macOS or Linux, and Python 3.11+.
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/) (one line: `curl -LsSf https://astral.sh/uv/install.sh | sh`).
- **One model CLI, installed and logged in.** Codex: run `codex login`
  (check it with `codex login status`). Or Claude Code: run `claude`, then
  `/login`. Scout does not start without one of the two installed. A local
  Ollama model or an OpenRouter key alone does not start it:
  [Install](../../install/#requirements) says what that takes today, and
  assessing through them is not verified.
- Exa search is optional and off; you do not need it.
- Internet access for **Update sources** (it reads public job boards).

## 2. Prepare your resume

Use a Markdown (`.md`) or plain-text (`.txt`) file. Scout removes your name and
contact lines (email, phone, address, links) when it stores your resume, so they
are never kept and never sent to the model provider you picked, but it can't
catch personal details elsewhere in the text, so **keep those out** (anything
else you would not paste into Codex or Claude). A PDF or DOCX resume must be
converted first:

```bash
uvx --from 'markitdown[pdf,docx]' markitdown resume.pdf > resume.md
uvx --from 'markitdown[pdf,docx]' markitdown resume.docx > resume.md
```

This runs through `uv`, which you already have, so there is nothing else to
install. If you already have `pdftotext` (poppler), `pandoc` or macOS `textutil`,
those work too. Check the converted `resume.md` before you import it.

See [Resume and PDF](../resume/) and [Privacy and security](../privacy/).

## 3. Install

```bash
uv tool install gigai
gigai --version
```

To update later:

```bash
uv tool upgrade gigai
```

To install a specific release from git instead, use a tag from the
[Releases page](https://github.com/karthik446/gigai/releases) as the `@<tag>`
below; version-specific notes are in the [Changelog](../../changelog/).

```bash
uv tool install "git+https://github.com/karthik446/gigai@<tag>"
```

## 4. Run

```bash
gigai scout run      # starts Scout and opens the browser
```

The first run on a new machine creates GigAI's settings with their defaults
(`~/.gigai/config.toml`) and says so; there is nothing else to set up first.
In the browser, the setup wizard asks you to pick the model ("Model for
Scout"), add your resume (paste it or upload the `.md`/`.txt` file), and set
your roles, location and work mode. Then:

1. **Update sources** (in Settings): fills the company store from the public
   job boards. Before the very first one Scout shows this notice once:
   **Run GigAI on your own computer and your own network, not a work laptop or office Wi-Fi.** Scout checks about 16,000 public job boards (Greenhouse, Lever, Ashby and six more hiring systems): about 18,000 requests on the first update, and it keeps checking 8 times a day. An employer can see that traffic.
   You can turn the background checks off under Settings > Background updates. The first update over the whole catalog runs in passes of up to
   20 minutes each (the default time budget) and continues where it stopped;
   later updates are a single short pass.
   While Scout stays open it then re-checks the boards about once an hour by
   itself ([Background updates](../sources/#background-updates)).
2. **Jobs**: the page lists the stored postings that match your profiles,
   with no run to start. The background ranks them. Use the chips (profile,
   New since last check, 7 days, 30 days, state) and the search box to narrow
   the list.
3. **Assess these**: pick postings (or use the filter) and Scout asks first,
   with the count and an estimate; it assesses only when you approve. See
   [Assess a batch](#assess-a-batch) below.
4. A posting's page shows the resume picked from your master for that posting,
   word for word (the assessment picks the lines; nothing is rewritten), as a
   preview of the printed pages with a spacing slider and the list of its
   points beside it ([Resume and PDF](../resume/#the-preview-and-the-spacing-slider)).
   Review every line. **Generate PDF** opens a small
   form for your name and contact details and saves the PDF; GigAI does not
   keep what you type there. Under Settings > Profiles, **Resume display**
   holds the title under your name and the PDF layout. A job's own spacing is
   set with the slider on its page.

Prefer to work from your own AI agent? `gigai scout new` is the daily entry
point: see [For agents](../agents/), or give your agent [Start here](../agents/start/)
and let it do the setup. [Your first 10 minutes](../first-10-minutes/) walks through a
first session.

## 5. The Jobs page

### Fit and rank on each job

Under each title the Jobs list shows two chips:

- **Fit**, for an assessed job: "Fit 92% · 19/22" is the fit and how many of the posting's
  requirements are met (19 of 22). Fit counts the must-have requirements twice. The chip is
  green only on a job that is a match (Matched, or Resume ready); on a job that needs your
  answers, has a gap or is not a match it is grey, whatever the number.
- **Rank**: "Rank 92", or "Not ranked yet". Rank is a first guess from the posting and your
  resume; it is not a verdict.

An assessed job's page shows the same two numbers in a box at the top right: the fit, "19 of
22 requirements" and "Rank 92". [What Scout's numbers and labels mean](../numbers/) says how
each is made.

A row can also carry **New** and **Closed**. After **Mark applied** on a job's page the job
reads **Applied · Oct 6** (it becomes Interview, Offer, Rejected or Withdrawn as you record
those), and it leaves the list: jobs you have already applied to (and the ones that moved on
from there) are left out of the Jobs list, its counts and "Assess top 50". The **Applied** chip
above the list shows how many there are ("Applied 7") and lists only them; opening or
assessing one by its address still works. `gigai scout jobs list` and `gigai scout new` leave
them out too and say how many, and `gigai scout jobs list --state applied` lists them.

### Search all jobs

The box **Search all jobs** on the Jobs page searches every posting Scout has stored, not only the ones
your profiles hold. Type titles (a comma between two roles), and optionally a company or a location.
Every typed word has to be in the title, seniority included: "senior engineer" lists only Senior or Sr.
titles. Company and location words are whole words ("ai" finds Example AI, not Maintain). Results are
newest first and not ranked; they keep the default profile's remote/US/last-30-days filters until you
choose **Show all** (any place, any date). The page of results comes first and the count after it, and
**Load more** shows the next page. A search stores nothing and makes no model call.

- **Save this search as a profile** turns the words into a profile that ranks and assesses.
- **Assess** on a result assesses it as the default profile: one model call, and Scout asks first.
- **Mark applied** works on a result too. Its job page opens even when no profile holds the posting; the
  description is not stored for such a posting, so Assess fetches the page.

From a terminal: `gigai scout jobs search "Senior Systems Engineer, Staff Systems Engineer" [--company W]
[--location W] [--all] [--limit N] [--offset N] [--json]` (the same as `GET /api/search`).
After upgrading to 0.1.11.7, run `gigai scout sources update` once so the search index exists (see
[Update sources](../sources/#the-search-index)).

### Assess a batch

Above the list, "177 not assessed" has a button beside it. One approval assesses at most 50
postings, the top 50 by rank, so the button reads **Assess top 50 of 177**; with 50 or fewer
left it reads **Assess all 12**. **Assess these** does the same for the rows you ticked, or
for the filter.

Either button asks first. No model is called until you approve. The dialog says:

- the title, for example "Assess the top 50 by rank of 177 postings?";
- how many per profile, and "50 at a time": how many are left after these 50;
- **Estimate**: "~50 model calls, ~950k tokens, ~29 min", from your own earlier calls (with
  none recorded yet it says so);
- **Model**: the model the calls go to;
- when some postings rank too low to be in the count, a checkbox: "Include the 105 low-ranked
  ones in the pool (still 50 per run): 12 of them would be in this run." Ticking it does not
  add calls: the run is still the top 50, now chosen from all of them, and the estimate
  follows. When ticking it would change nothing, there is no box, and a line says why;
- "Nothing has been assessed yet. Assessing starts only when you approve."

**Approve and assess** starts the batch and the dialog closes. The Jobs page then shows a
progress row: "Assessing: 12 of 50 assessed · about 29 min for 50", the profile, a bar and
**Cancel**. The list fills in as each posting gets its result. You can keep using Scout; the
assess buttons are off until this batch ends or you cancel it.

- **Cancel** reads "Cancelling: finishing the 2 in flight". The calls already running finish
  and their results are kept; no other call starts. Then the page says, for example,
  "Cancelled: 14 of 50 assessed. What finished is kept; 36 were not started."
- From a terminal, `gigai scout jobs assess --cancel` cancels the running batch the same
  way. `gigai scout stop` stops Scout and ends the model calls it started.
- When the batch ends: "Assessed 50 of 50. 127 more not assessed yet: 50 at a time, "Assess
  all" takes the next."
- A job's page says where that job is while a batch runs: "Assessing… this posting is in the
  running batch (12 of 50 assessed)." or "An assess batch is running (12 of 50 assessed).
  This posting is not waiting in it." A job that waits in the batch has no Assess button of
  its own until its result is in.

A row that also matches another of your profiles has a link for that one: **Or assess as
Platform track** (the profile's name). It asks the same way.

### Closed postings

When you open a job, assess it, make its PDF or mark it applied, Scout checks that the
posting is still on its board. A posting the board no longer lists is marked **Closed**, is
left out of the list and of batches (the **Removed** chip lists them), and its page says:
"This posting is closed. Its board no longer lists it." with a link, "Open the posting to
confirm".

When the company's own page for an open job does not answer, the job's page says "The
company page for this job is down; the job is still open on the board." with **Open on the
job board**.

`gigai scout stop` stops Scout; `gigai scout run --port 9000` picks another
port if 8765 is taken. More detail for scripts and agents is under
[For agents](../agents/). Every `gigai scout` command is in the
[Scout CLI reference](../reference/cli/).
