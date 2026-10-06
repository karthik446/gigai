---
title: Resume and PDF
description: Prepare a resume, see the resume picked for a posting, and download a PDF.
---

## Resume input

`gigai scout resume add` accepts `.txt`, `.md` and
`.markdown` files up to 1 MB (the setup wizard's upload takes the same three
types); a `.pdf` or `.docx` resume is rejected (`media_type_unsupported`).
Convert it to Markdown first:

```bash
uvx --from 'markitdown[pdf,docx]' markitdown resume.pdf > resume.md
uvx --from 'markitdown[pdf,docx]' markitdown resume.docx > resume.md
```

This runs through `uv`, which you already have, so there is nothing else to
install. If you already have `pdftotext` (poppler), `pandoc` or macOS `textutil`,
those work too. Check the converted `resume.md` before you import it.

**GigAI never stores your name, email, phone, address or links.** The import
removes your name line and contact lines and stores the resume without them
("We removed your contact lines; you'll add them when you make a PDF."), so no
model sees them either. It works on patterns and can't catch personal details
elsewhere in the text, so keep those out: see [Privacy and security](../privacy/)
for the limits, including what versions before 0.1.10.7 stored.

```bash
gigai scout resume add ./resume.md --json    # import + wrap your resume for find-jobs
```

## The picked resume and the PDF

Tailoring is switched off in 0.1.11. The resume for a job is **picked** from your master, word for
word: the assessment picks the lines, and GigAI checks that the lines it shows are lines you wrote
(see [How accurate is the picked resume](../accuracy-0-1-11/)). The job page shows it, with what
was picked and what was left out; `gigai scout resume pick --job-url <url>` prints it.
`gigai scout resume tailor` is still there and answers `tailoring_off` (no model call) unless the
background pipeline was explicitly enabled in the settings; `POST /api/tailored-resumes` is the
same switched-off step.

> Every line comes from your resume, answers or stories. Read it before you send it.

The panel also says where the file is. Each job's resume (markdown) is saved in your
resumes folder (`~/Documents/GigAI/resumes` unless you chose another in Settings), named
`<company>-<role>-<date>.md`. A file you change there stays yours; Scout replaces only what
it wrote itself.

<!-- The images on this page are the release screenshots (`make media`, a synthetic demo home on the
fixture model; see the note under "The master resume"). Paths are relative to this page, as on For agents. -->
<a href="../../media/job-resume-dark.png"><img class="light:sl-hidden" src="../../media/job-resume-dark.png" alt="The tailored resume on the job page, with where its file is in your resumes folder." loading="lazy" /></a>
<a href="../../media/job-resume-light.png"><img class="dark:sl-hidden" src="../../media/job-resume-light.png" alt="The tailored resume on the job page, with where its file is in your resumes folder." loading="lazy" /></a>

**Generate PDF** opens a small form: name, email, phone, location, LinkedIn and
one more link. Fill what you want printed and press Generate PDF. The values go
into that one PDF and are dropped: GigAI does not save them, so you type them
each time (your browser may offer to autofill them). The file is named
`<company>-<role>-<date>.pdf`, never after you.

A skill the posting asks for that your resume does not name, and that one of your
answers says you have, is added to Skills with that answer as its source. An answer
that says you do not have it adds nothing.

A tailored resume is kept to 2 pages. Scout measures the pages itself and, when the
resume runs over, leaves out whole roles, the oldest first, until it fits; a role that
ended more than 8 years ago also keeps only its first 3 bullets. Nothing else is cut
for length. The panel says what was left out ("Cut for length: ...") and **Restore**
puts all of it back in one step (`gigai scout resume length --job-url <url> --restore`,
or `PUT /api/tailored-resumes/length`). When the pages cannot be measured, or leaving
out older roles would not be enough, no role is cut and the line says so.

The background pipeline is off by default in 0.1.11, so nothing is tailored by itself. A home
whose settings explicitly enable it keeps the 0.1.10 behaviour: it never replaces a resume you
made or edited. A resume you edited is replaced only when you take the new suggested one
(`gigai scout resume pick --use-proposed`).

A line can also carry your own wording: an agent (or a script) sets it with `PUT
/api/tailored-resumes/lines` and `"use": "custom"`. The line is then shown as
**edited**: it cites no source, because the text is yours, and **Use original** (or
**Use rewrite**) brings back the line it replaced. A text that looks like a name line or
a contact detail is refused: GigAI stores none of those.

`gigai scout resume pdf` renders a PDF without the UI: `--job-url <url>`
for a job's stored resume (`--tailored --job-url <url>` is the older spelling of the same), or `--in resume.md` for resume markdown of your own
(`POST /api/resume/pdf` over the API). Both run on this computer only, with no model
call. A PDF made this way has no name and no contact details. The command prints an
"Open in Scout" link: open it, fill the Generate PDF form in your browser, and download
the finished PDF. [For agents](../agents/#change-a-resume-and-render-a-new-pdf) has a
worked example and the markdown format.

**Working on one job's resume with your agent.** GigAI does not reword a resume. Your agent
reads the job's brief (`gigai scout resume brief --job-url <url>`, and `--posting` for the
posting, a separate call), changes the wording with you, ends each changed line with the master
lines and answers it comes from, and hands the resume back
(`gigai scout resume store --in edited.md --job-url <url> --as agent`). GigAI checks it in code
against your master resume, your answers and your stories, and stores it or refuses it line by
line. This check is a guard on numbers, names, ownership, entries and sources. It does not prove
that a reworded line is true: read every changed line before you send the resume.
[For agents](../agents/#5-the-picked-resume-and-the-pdf) has the five steps.

## The master resume

A master resume is one document that holds everything: every role, bullet, project and
skill you have, with an id on every line and no name or contact details. A profile shows
a selection of it, and a resume tailored for one job is picked from all of it, so a line
you add once is there for every profile and every job.

**Make it.** Settings > **Master resume** (`#/master`), or `gigai scout resume master
init`. Scout merges the resumes your profiles hold: every line once, the newer wording
kept where two say the same thing. Where two resumes word one line with different
numbers it asks which is right (A, B, or both) and writes nothing until you answer. Each
profile keeps showing the resume it shows now, so nothing already assessed changes.

**Change it.** The Master page lists the master by role. Add a line under a role, edit
one, retire one, add a role. A line the master already has in other words is not added
at once: the page shows the line it looks like, and you edit that one or choose **Add it
anyway**. A retired line is never selected again; **History** lists what is retired and
puts it back. Each line shows how strong its evidence is: backed by a story or an answer,
states a number, or stated. Only your own facts belong here; an agent writes with
`gigai scout resume master add`, `edit` and `remove` (see [For agents](../agents/#6-your-master-resume)) or
through the API (`POST` and `PUT /api/master/lines`), under the same rules, and sends the
revision it read, so a change that crosses yours is refused instead of overwriting it.

<a href="../../media/master-lines-dark.png"><img class="light:sl-hidden" src="../../media/master-lines-dark.png" alt="The same page further down: every role and line of the master, each marked backed, stating a number, or stated." loading="lazy" /></a>
<a href="../../media/master-lines-light.png"><img class="dark:sl-hidden" src="../../media/master-lines-light.png" alt="The same page further down: every role and line of the master, each marked backed, stating a number, or stated." loading="lazy" /></a>

**The file.** Your master is also a file you can open: `master.md` in your resumes folder
(`~/Documents/GigAI/resumes` unless you chose another in Settings). Scout writes it again
after every change of the master. Edit it in your own editor, then import it: **Import the
file** on the Master page, or `gigai scout resume master sync`. A line you typed gets an
id, every other line keeps its own (leave the `<!-- id:... -->` comments as they are; a
deleted one comes back when the line's text is unchanged), and a line you removed is
retired and can be put back from **History**. Scout never reads the file by itself:
until you import it, the Master page, `gigai scout status` and `gigai scout resume master
show` say "master.md has changes not imported yet". It never replaces a file you changed
either: a change made in Scout or by your agent meanwhile is written beside it as
`master-2.md`. An import is refused, and your file left as it is, when the file does not
read as a master (the line is named), when it holds a name, an email, a phone number, a
link or an address (Scout stores no contact details), and when the master changed since
the file was written; **Import it anyway** (`--revision N`) then imports your file as it
is, and what was added since is retired.

<a href="../../media/master-dark.png"><img class="light:sl-hidden" src="../../media/master-dark.png" alt="The Master resume page: its revision, the file you can edit in your resumes folder, and each profile's selection of it." loading="lazy" /></a>
<a href="../../media/master-light.png"><img class="dark:sl-hidden" src="../../media/master-light.png" alt="The Master resume page: its revision, the file you can edit in your resumes folder, and each profile's selection of it." loading="lazy" /></a>

**Profiles.** A profile's selection stays as it is until you refresh it. When the master
gains lines, the profile says so ("3 new master lines: refresh?") under Settings >
Profiles and on the Master page; **Refresh** selects again from the whole master, by
code and with no model call, and makes the result the profile's resume. When you edit
or retire a line a profile shows, its resume follows by itself.

**One job.** With a master stored, a profile's resume for a job is picked from the whole
master, also for a profile that has no selection yet. The one exception is a profile whose
resume you replaced by hand after its selection was made: its jobs' resumes are made from
that resume. The header of the resume on the job page says which ("from your master
resume" or "from resume" and the profile), and so does each profile's line on the Master
page and in `gigai scout resume master selection status`. A resume tailored from the
master shows **Picked (n)** and **Left out (m)**
on the job page, every line with the reason it is shown or not. **Remove** takes a line
off this job's resume; **Add** puts one on. If an added line would make the resume 3
pages, Scout names the line that would be cut to keep 2 and asks: cut it, or keep both.
A line that was edited on a tailored resume offers **Save this wording to your master**.

<a href="../../media/job-picked-dark.png"><img class="light:sl-hidden" src="../../media/job-picked-dark.png" alt="Picked: the lines of your master resume this job's resume shows, each with why." loading="lazy" /></a>
<a href="../../media/job-picked-light.png"><img class="dark:sl-hidden" src="../../media/job-picked-light.png" alt="Picked: the lines of your master resume this job's resume shows, each with why." loading="lazy" /></a>

<a href="../../media/job-left-out-dark.png"><img class="light:sl-hidden" src="../../media/job-left-out-dark.png" alt="Left out: the other lines of your master resume, each with why, and Add to show one on this resume." loading="lazy" /></a>
<a href="../../media/job-left-out-light.png"><img class="dark:sl-hidden" src="../../media/job-left-out-light.png" alt="Left out: the other lines of your master resume, each with why, and Add to show one on this resume." loading="lazy" /></a>

The companies, postings and person in these images are made up, and no model wrote the resume
in them: the build uses a fixture in place of a model, whose "tailored" resume is a few lines
and shows none of the master's lines. The four lines under Picked were added with **Add**, which
is why each says "you added it to this resume"; a resume a model tailored gives the reason each
line was picked.

## Resume display

Under Settings > Profiles, **Resume display** holds the title printed under your
name and the PDF layout (spacing, auto fit). They are stored only on your
computer, never sent to a model, and added to the PDF locally. It no longer holds
a name or a contact line: those are typed in the Generate PDF form for one PDF.

The PDF renderer is Typst; see [Known limitations](../limitations/) for the
platforms it does not support.
