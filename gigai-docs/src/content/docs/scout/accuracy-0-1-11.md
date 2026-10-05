---
title: How accurate is the picked resume (0.1.11)
description: The standard, the method and the results of the 0.1.11 accuracy test on one real person's resume and real postings, and how to run it again.
---

> **Skeleton: results not filled in.** The tables below hold placeholders. They are filled from the
> result files when the last run is done, and nothing on this page is a result until then.

## The standard

A resume is livelihood for the person it belongs to. We need it accurate, not usually accurate.

So the bar for this test is strict, and it is not "most lines are fine":

- **One invented fact fails the run.** A skill, tool, employer, title, date, outcome or number that
  the person's master resume does not state, on either model CLI, in any picked resume, fails the
  whole run. It is not averaged away.
- **A job counts as accurate only when every point of a fixed rubric holds** for it: the verdict
  is right, the held or suggested decision is right, the picked resume shows the evidence for every
  required requirement the master has, it shows nothing the master does not state, and a recruiter
  reading the PDF would see the right roles and dates.
- **A correctly held job counts as accurate.** A job the person should not get a resume for, held
  with the right question or the right gap, is a pass.

### What the test can show, and what it cannot

It can show that, on this person's master and these postings, with these two model CLIs, the picked
resumes were or were not accurate by that rubric, and where they failed.

It cannot show:

- that the next person's resume, or the next posting's wording, gives the same result;
- anything about accuracy a different judge or a different reading of the rubric would change (see
  the method: the key records contested points);
- that a resume gets someone an interview. The test is about not saying anything untrue, and about
  showing the evidence the person has. It is not about outcomes.

## Method

- **One real person's master resume.** The test uses one real master, with its stored answers and
  preferences, kept outside the repository. No resume text, posting text or company name is on this
  page or in the repository.
- **Ten real postings per set.** Each set of ten is varied and none is chosen to fail.
- **Three sets, and a fourth sealed.** The first sets were used while the prompt and the code were
  being changed. The fourth set stays unopened until the prompt and the code are frozen, and is run
  once. [FILL: which set each result below comes from, and whether the fourth set was run]
- **Both model CLIs.** Every posting is assessed once on `claude_cli` and once on `codex_cli`,
  through GigAI's own assess path, in a scratch home.
- **A blind judge and a gold key, scored by code.** A judge who has not seen the answers' origin
  reads each picked resume against the master and the posting. A gold key of the correct verdicts,
  gate decisions and evidence lines was written from two blind passes and reconciled, with the
  contested points listed. A script, not a person, scores each result against the key. The judge is
  never the model that produced the answer.
- **Every picked resume was also rendered to PDF** and read as a recruiter would read it.

### What is not proven

- [FILL: points the key marks as contested, and which way each was decided]
- One person's master is one sample. [FILL: anything else the final run shows]
- The requirement lists the two CLIs return for the same posting can differ. [FILL: how many
  rows differed, from the report]
- A real agent session through the brief and the hand-back is a separate test and is not in these
  numbers.

## Results

Run on [FILL: date range], GigAI 0.1.11 [FILL: build], prompt [FILL: version].

### `claude_cli`

| Set | Postings | Accurate on every point | Invented facts | Stretched wording | Verdict right | Seconds per assessment (median) |
| --- | --- | --- | --- | --- | --- | --- |
| [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] |

### `codex_cli`

| Set | Postings | Accurate on every point | Invented facts | Stretched wording | Verdict right | Seconds per assessment (median) |
| --- | --- | --- | --- | --- | --- | --- |
| [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] | [FILL FROM RESULT FILES] |

### Result

[FILL FROM RESULT FILES: met or not met, per CLI, in one sentence each. If it was not met, say so
here and say what failed.]

## How to run it again

The harness is `tests/evals/run_assess_real_eval.py` in the GigAI repository. It reads an external
data directory that you provide and writes everything under `--out`, never into the repository:

```sh
GIGAI_ASSESS_EVAL_LIVE=1 uv run python tests/evals/run_assess_real_eval.py \
  --data-dir PATH/TO/YOUR/DATA --label after --home PATH/TO/A/SCRATCH/HOME
```

The data directory holds your master resume, your answers and preferences, and the postings (see the
harness's own header for the file layout). Use a scratch home made by `gigai setup --non-interactive`,
never your own. `--max-calls` is a hard cap on model calls; `--model-target` picks one CLI instead of
both; `--fake-model` runs offline to check the harness only, and says nothing about accuracy. The
harness reports facts. Judging them against your master is yours to do.
