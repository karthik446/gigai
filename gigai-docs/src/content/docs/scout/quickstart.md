---
title: Quickstart
description: From zero to a running Scout, in three steps.
---

One path from zero to a running Scout: find jobs, assess them, tailor a resume.

## 1. Prepare your resume, with your personal info removed

Use a Markdown (`.md`) or plain-text (`.txt`) file with your name, email, phone,
street address and links removed. Assessment and tailoring send your resume text
to the model provider you picked (see [Privacy and security](../privacy/)).

```bash
pdftotext resume.pdf resume.txt          # Linux, or macOS with poppler
textutil -convert txt resume.docx        # macOS built-in, .docx only
```

## 2. Install

See [Install](../../install/) for requirements, then:

```bash
uv tool install gigai
```

## 3. Run

```bash
gigai scout run      # starts Scout and opens the browser
```

The first run creates `~/.gigai/config.toml` with defaults. In the browser, the
setup wizard asks for the model ("Model for Scout"), your resume, and your roles,
location and work mode. Then:

1. **Update sources** (Settings) fills the company store from the public job boards.
2. **Run find jobs** (Jobs page) ranks the stored postings and assesses the top ones.
3. **Assess all new** assesses the rest in the background.
4. **Tailor resume** on a posting drafts a resume for it; **Download PDF** saves it.

`gigai scout stop` stops Scout; `gigai scout run --port 9000` picks another port.
Every `gigai scout` command is in the [Scout CLI reference](../reference/cli/).
