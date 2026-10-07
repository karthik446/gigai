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

A pick holds your best lines for the job, **at most 20 bullets**, and it does not count pages:
you fit the page yourself with the spacing slider beside the preview on the job's page, which
says how many pages the resume prints on ("2 pages", or "3 pages" when it runs over). The
lines that back a must-have requirement of the posting are kept first, then the lines you
pinned; the lines past the 20 are under **Left out**, where you can add one back. A pick is
never "cut for length", and a resume that prints on 3 pages is not held back for it. To make
a resume shorter, remove a point on the job's page ("Shorten automatically" and
`resume pick --shorten` are retired: they change nothing and say so). A resume picked before
0.1.11.5 stays as it is until you pick it again
(`gigai scout resume pick --job-url <url> --refresh`).

No employer is dropped. A role none of whose lines is shown (an older role, usually) is still
listed, on one line, under **Earlier experience** at the end of the Experience section, newest
first: `Senior Full Stack Developer, Example Agency | Feb 2016 - Jan 2017`. The title, the
employer and the dates are your master's own. With three such roles or fewer the PDF prints their
lines right after the last role, with no "Earlier experience" title; with four or more it keeps
the title.

The PDF sets four blocks to leave room for your points, and changes none of their words: the
Summary is a plain paragraph; a degree is one line (school, degree, years at the right); the
Skills are plain comma-separated lines, four at most, with what the posting asks for first and no
skill twice. A Skills list too long for four lines is first set a little smaller; if it is still
too long, the skills at the end of the list are not printed (your master keeps them all).

A degree with a long name stays on one line when it can: the degree's name is set a little
smaller (the school and the years are not). When that is not enough, the years go to a second
line. A project's line of technologies is set on the project's title line when both fit. And no
page holds only Education, only the Skills or only the roles listed by their heading: the end of
the block before them moves to that page with them, at every spacing.

The panel also says where the file is. Each job's resume (markdown) is saved in the job's own folder of your jobs folder
(`~/Documents/GigAI/jobs` unless you chose another with `gigai scout jobs-folder --set PATH`):
`<company>/<role>/resume.md`, for example `thrive-market/staff-software-engineer-fullstack/resume.md`.
Two roles at one company are two folders, and no name holds a date. The folder never holds your
name or contact details, and never a PDF. A `resume.md` you change there stays yours; Scout
replaces only what it wrote itself and writes a newer resume beside yours as `resume-2.md`.
Files that an earlier version put in your resumes folder (`<company>-<role>-<date>.md`) are
left where they are. To copy them into the new layout, once:

```bash
gigai scout jobs-folder migrate --dry-run
gigai scout jobs-folder migrate
```

The dry run lists every copy and writes nothing. The company and role come from the stored job,
not from the file's name. The old folder is left exactly as it was (it is a legacy place for job
resumes now); `master.md` and the PDFs stay there. A file you edited is copied as yours and is
never replaced. A file with no stored job, or one that holds contact details, is listed and not
copied. A second run copies nothing. `gigai scout status` says so in one line while old files
still wait.

<!-- The images on this page are the release screenshots (`make media`, a synthetic demo home on the
fixture model; see the note under "The master resume"). Paths are relative to this page, as on For agents. -->
<a href="../../media/job-resume-dark.png"><img class="light:sl-hidden" src="../../media/job-resume-dark.png" alt="The tailored resume on the job page, with where its file is in your resumes folder." loading="lazy" /></a>
<a href="../../media/job-resume-light.png"><img class="dark:sl-hidden" src="../../media/job-resume-light.png" alt="The tailored resume on the job page, with where its file is in your resumes folder." loading="lazy" /></a>

**Generate PDF** opens a small form: name, email, phone, location, your GitHub id, your
LinkedIn id, a website and one more link. Fill what you want printed and press Generate PDF. The values go
into that one PDF and are dropped: GigAI does not save them, so you type them
each time (your browser may offer to autofill them). The file is named
`<company>-<role>-<date>.pdf`, never after you.

### Your header file

To skip the typing, keep your details in one JSON file that you own. Scout fills the
Generate PDF form from it each time the form opens: the form says "Filled from
`~/Documents/GigAI/header.json`", and you can still edit any field before you generate.
The file goes beside your resumes folder, not inside it (that folder never holds contact
details): `~/Documents/GigAI/header.json` (a GigAI home other than `~/.gigai`:
`<home>/header.json`). An example with made-up values:

```json
{
  "name": "Jane Example",
  "email": "jane@example.com",
  "phone": "555-0100",
  "location": "Springfield, IL",
  "github": "octocat",
  "linkedin": "octocat",
  "website": "example.com",
  "work_authorization": "VISA: H1B"
}
```

`github` and `linkedin` are the short form: just your id. The PDF prints
`github.com/octocat` and `linkedin.com/in/octocat`, each a clickable link. The older form, a
`links` list, still works, alone or beside the short form:

```json
{
  "name": "Jane Example",
  "links": [
    {"label": "GitHub", "url": "https://github.com/octocat"},
    {"label": "LinkedIn", "url": "https://www.linkedin.com/in/octocat"},
    {"label": "Talks", "url": "example.com/talks"}
  ]
}
```

- Every field is optional (a PDF made from the command line needs the name). Each value is
  one line of at most 200 characters; `links` holds at most 6 links.
- The form has a field each for GitHub, LinkedIn and Website. `github`, `linkedin` and
  `website` fill them. So does a `links` entry that is your GitHub or LinkedIn profile
  address (the form shows the id alone) or one labelled Website. Every other link gets a
  field of its own under its label. The PDF prints each link's address in the contact line.
- The PDF header is your name and ONE line under it: location | work authorization | links |
  email | phone. A field you leave empty leaves no gap. A line too long for the page is set
  in slightly smaller type first, and wraps only when that is not enough.
- `github` and `linkedin` take just your id (`github.com/<id>`, `linkedin.com/in/<id>`); a
  full address works too. `website` takes a site address. Each prints without `https://` or
  `www.` and is a clickable link. A link named both here and in `links` prints once.
- `work_authorization` prints in that line exactly as you wrote it. Leave the
  key out and it starts from your profile's sponsorship answer; set it to `""` to leave it out.
- What prints is decided in this order: what you edit in the form, then the file, then the
  profile's sponsorship answer.
- Scout reads the file only to fill the form, to show your header in the resume preview on a
  job's page, or to make a PDF with `gigai scout resume pdf`, and writes it only when you press
  the Save button described below. The preview shows it to your own browser only, as page
  pictures: an agent that asks for the preview gets a grey placeholder header ("Your Name")
  instead, and so do you until the file has a name. The placeholder is never in a PDF. It is never copied into GigAI's store, a log, a record, a suggestion, a job brief,
  the resumes folder or a model prompt, and Scout's agent API does not return it.
- Keep it to yourself: `chmod 600 ~/Documents/GigAI/header.json`. The form and the command
  say so when other users of the computer can read it.
- A file that is missing, cannot be read or is not valid is one plain line in the form
  (what is wrong and where the file goes); you can still type the values.

**Save these details.** You do not have to write the file by hand. Type your details in the
Generate PDF form and press "Save these details to `~/Documents/GigAI/header.json`" (the
button shows the real path). Scout then writes the file, once, with what is in the form:

- Only on your click. Opening the form, typing and generating a PDF never write it.
- It writes the short form: `"github": "octocat"`, `"linkedin": "octocat"` and
  `"website": "example.com"` for the fields you filled. A full address typed in the GitHub or
  LinkedIn field is saved as the id. `links` holds only your other links. A field you left
  empty is left out of the file; `work_authorization` is the exception (`""` means no line).
- The file is yours alone (mode 600) and is written whole or not at all: if the write
  fails, the file you had is still there.
- A file that is already there is never replaced without asking. The form says "Replace the
  existing header.json?"; Cancel leaves your file as it was.
- The form shows the path it wrote. If the folder is missing or cannot be written, it says
  so in one plain line. `~/Documents/GigAI` is created when it is not there yet; no other
  folder is.
- The details go into that file and nowhere else: not GigAI's store, a log, a record, a
  job brief, the resumes folder or a model prompt. An agent cannot use the button: the
  server answers Scout's own page only.

**Placeholder values.** If you start from a template, any value that still starts with
`REPLACE` (for example `"REPLACE: your phone"`) is skipped, and so is an empty value:
neither fills the form nor prints in a PDF.

- A file that holds nothing but placeholders fills nothing. The form says "header.json
  still has placeholder values: replace the REPLACE: fields (or save your details here)".
- A file with some real values fills those, and the form lists the fields it skipped (their
  names only).
- A name that is missing or still a placeholder is flagged: "header.json has no name yet".
  The form still needs a name before it generates, and `gigai scout resume pdf` stops with
  that same plain sentence instead of making a PDF without your name.

A skill the posting asks for that your resume does not name, and that one of your
answers says you have, is added to Skills with that answer as its source. An answer
that says you do not have it adds nothing.

A resume made by the tailoring step (switched off by default, see above; not a picked
resume) is kept to 2 pages. Scout measures the pages itself and, when the
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

With [your header file](#your-header-file) the command makes the finished PDF itself, without
the browser: pass `--out FILE` and the header is filled from `~/Documents/GigAI/header.json`
(or from `--header FILE`). A PDF with your details is written only to `--out`, never to the
resumes folder; without `--out` the PDF has no header, as before. `--no-header` makes it
without one.

```sh
gigai scout resume pdf --job-url https://boards.greenhouse.io/acme/jobs/1 --out ~/Desktop/acme.pdf
gigai scout resume pdf --in resume.md --out resume.pdf --header ~/Documents/GigAI/header.json
```

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

**Education.** A resume picked from the master prints your education only when the master
holds it. When it holds none, Scout says so: "Your master has no education" on the Master
page (with **Add education**, which opens the form for a school), on a job's resume card when
that resume prints none, and in `gigai scout resume master show`. From a terminal, add it with
`gigai scout resume master add --heading SCHOOL --role "DEGREE | YEAR" --section education`.
When `master init` reads a resume whose lines look like education (a degree with a school and
a year, or a line that is only the word Education inside another section) and none of them
became an Education entry, it says which lines, by number, and which section holds them. It
moves nothing and never makes up an entry: the lines stay where it read them until you add
the degree yourself.

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
