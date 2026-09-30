---
title: Resume and PDF
description: Prepare a resume, tailor it to a posting, and download a PDF.
---

## Resume input

`gigai scout resume add` accepts `.txt`, `.md` and
`.markdown` files up to 1 MB (the setup wizard's upload takes the same three
types); a `.pdf` or `.docx` resume is rejected (`media_type_unsupported`).
Convert it first, e.g.:

```bash
pdftotext resume.pdf resume.txt          # Linux, or macOS with poppler
textutil -convert txt resume.docx        # macOS built-in, .docx only
```

Scout removes your name and contact lines before a model sees the text, but
keep other personal details out: see [Privacy and security](../privacy/).

```bash
gigai scout resume add ./resume.txt --json    # import + wrap your resume for find-jobs
```

## Tailor a resume and download the PDF

**Tailor resume**, on a posting's page (or `gigai scout resume tailor`, `POST
/api/tailored-resumes`), drafts a resume for that posting. Tailored resumes are
drafts: review each line; every line shows its sources. **Download PDF** saves
the result as a PDF.

## Resume display

Under Settings > Profiles, **Resume display** holds the name, title and contact
line (location, work authorization, LinkedIn, GitHub, other links, email, phone)
printed at the top of the PDF. They are stored only on your computer, never sent
to a model, and added to the PDF locally. The Resume display fields (name, title,
contact line) are never sent to a model or the network: only the PDF renderer and
the settings/PDF API read them.

The PDF renderer is Typst; see [Known limitations](../limitations/) for the
platforms it does not support.
