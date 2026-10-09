---
title: Your first 10 minutes
description: From nothing to your first picked resume PDF, with your own AI agent doing the typing.
---

The short path, by hand and with your agent: install, start Scout, set up a profile, ask your
agent what is new, answer a question, make a PDF. If you would rather have your agent do the
setup too, give it [Start here](../agents/start/) instead; it is the same path.

Two things before you start.

**Remove your name, email, phone, address and links from your resume before you give it to an agent.**

**Run GigAI on your own computer and your own network, not a work laptop or office Wi-Fi.** Scout checks about 15,000 public job boards (Greenhouse, Lever, Ashby and six more hiring systems): about 16,000 requests on the first update, and it keeps checking 8 times a day. An employer can see that traffic.

You can turn the background checks off under **Settings > Background updates**.

## 1. Install (2 minutes)

You need macOS or Linux, [`uv`](https://docs.astral.sh/uv/getting-started/installation/), and
one model CLI that is installed and logged in: Codex (`codex login`) or Claude Code (`claude`,
then `/login`). Scout does not start without one of the two installed.

```sh
uv tool install gigai
gigai --version
```

## 2. Start Scout and set up a profile (3 minutes)

```sh
gigai scout run
```

Your browser opens the setup wizard. It asks three things:

1. **The model** Scout should use (your Codex or Claude Code login).
2. **Your resume**, as a `.md` or `.txt` file. Scout stores it without the name and contact
   lines. To see what it would remove before you add it, run the check yourself:

   ```sh
   gigai scout resume check resume.md
   ```

   It lists the kind and the line number of each finding, on your computer, with no model. It
   works on patterns, so read your resume once for personal details inside a sentence.
3. **Your roles**: the job titles you want, where you can work, remote or hybrid or on-site,
   and whether you need sponsorship.

Then press **Update sources**. The first time, Scout shows the network notice above and asks
once. The first update takes 15 minutes or more; you can go on while it runs.

## 3. Ask your agent what is new (2 minutes)

Teach your agent the loop once ([Use it from your agent](../agents/#use-it-from-your-agent)
has the steps for Claude Code, Codex and other agents). Then, in your agent:

> What's new on Scout?

The agent runs:

```sh
gigai scout new --json
```

and tells you how many postings are new, and what assessing them would cost. Nothing is
assessed until you say yes. **The first run is the expensive one**: it catches up on everything
at once. After that a day is a few dozen new postings at most. [Token usage](../tokens/) has
real numbers.

## 4. Answer a question (1 minute)

An assessment leaves questions open when a posting asks for something your resume does not
state. Your agent asks you:

> Have you run Kafka in production?

Answer in your own words. A short fact is saved as an answer and reused for every later
posting. A longer reply, with a project and an outcome, can become a story, if you agree. Do
not put your name or contact details in an answer.

The answer is kept and reused for every later posting. Nothing is tailored or scored in the
background: the background pipeline is off by default in 0.1.11, and GigAI does not rewrite your
resume for a job. The resume for a job is picked from your master, word for word.

Your answer makes that job's first assessment old: it was made before the answer. Until the job
is assessed again it still reads "Needs your answers (old assessment: answers changed)", and its
Scout label reads "needs attention". To answer and assess again in one step, ask your agent to
save the answer with a re-assess of that job (`gigai scout answer <question-id> --answer-text
"..." --reassess <job-url> --as agent`, one model call), or type the answer in the question box
on the job's page and press **Re-assess**.

## 5. Make the PDF (2 minutes)

Ask your agent for the PDF of one job. It runs:

```sh
gigai scout resume pdf --job-url <job-url> --json
```

and gives you an **open in Scout** link. The agent's own PDF has no name and no contact
details, because GigAI has none. Open the link, type your details into the Generate PDF form in
your browser, and download the finished PDF. GigAI does not keep what you type there.

The PDF exists once the job has been assessed: the assessment picks the lines of your master,
word for word, and that picked resume is what the PDF shows. Before the job is assessed the
command says there is no resume for the job yet. `gigai scout resume pick --job-url <job-url>`
shows what was picked. (`gigai scout resume tailor` is switched off in 0.1.11 and answers
`tailoring_off`.)

Every line comes from your master resume. Read it before you send it.

## What next

- [For agents](../agents/): the whole daily loop, the security model, an example session.
- [Start fresh](../agents/start/#11-start-fresh-reset-the-data-keep-the-master-resume-file): stop Scout, move `~/.gigai` aside and start again from a resume file.
- [Token usage](../tokens/): what each step costs in tokens and time.
- [Privacy and security](../privacy/): what is stored, what is sent, and to whom.
- [What Scout's numbers and labels mean](../numbers/).
