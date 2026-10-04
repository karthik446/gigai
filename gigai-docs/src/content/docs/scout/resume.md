---
title: Resume and PDF
description: Prepare a resume, tailor it to a posting, and download a PDF.
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

## Tailor a resume and download the PDF

**Tailor resume**, on a posting's page (or `gigai scout resume tailor`, `POST
/api/tailored-resumes`), drafts a resume for that posting. Tailored resumes are
drafts: review each line; every line shows its sources.

> Every line comes from your resume, answers or stories. Read it before you send it.

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

The background pipeline can tailor a resume for a job by itself after you
answer one of its questions. It never replaces a tailored resume you made or
edited; to refresh that one, press **Tailor resume** again.

A line can also carry your own wording: an agent (or a script) sets it with `PUT
/api/tailored-resumes/lines` and `"use": "custom"`. The line is then shown as
**edited**: it cites no source, because the text is yours, and **Use original** (or
**Use rewrite**) brings back the line it replaced. A text that looks like a name line or
a contact detail is refused: GigAI stores none of those.

`gigai scout resume pdf` renders a PDF without the UI: `--tailored --job-url <url>`
for a stored tailored resume, or `--in resume.md` for resume markdown of your own
(`POST /api/resume/pdf` over the API). Both run on this computer only, with no model
call. A PDF made this way has no name and no contact details. The command prints an
"Open in Scout" link: open it, fill the Generate PDF form in your browser, and download
the finished PDF. [For agents](../agents/#change-a-resume-and-render-a-new-pdf) has a
worked example and the markdown format.

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
one, retire one, add a role. A retired line is never selected again; **History** lists
what is retired and puts it back. Each line shows how strong its evidence is: backed by a
story or an answer, states a number, or stated. Only your own facts belong here; an agent
writes through the API (`POST` and `PUT /api/master/lines`) and sends the revision it
read, so a change that crosses yours is refused instead of overwriting it.

**Profiles.** A profile's selection stays as it is until you refresh it. When the master
gains lines, the profile says so ("3 new master lines: refresh?") under Settings >
Profiles and on the Master page; **Refresh** selects again from the whole master, by
code and with no model call, and makes the result the profile's resume. When you edit
or retire a line a profile shows, its resume follows by itself.

**One job.** A resume tailored from the master shows **Picked (n)** and **Left out (m)**
on the job page, every line with the reason it is shown or not. **Remove** takes a line
off this job's resume; **Add** puts one on. If an added line would make the resume 3
pages, Scout names the line that would be cut to keep 2 and asks: cut it, or keep both.
A line that was edited on a tailored resume offers **Save this wording to your master**.

## Resume display

Under Settings > Profiles, **Resume display** holds the title printed under your
name and the PDF layout (spacing, auto fit). They are stored only on your
computer, never sent to a model, and added to the PDF locally. It no longer holds
a name or a contact line: those are typed in the Generate PDF form for one PDF.

The PDF renderer is Typst; see [Known limitations](../limitations/) for the
platforms it does not support.
