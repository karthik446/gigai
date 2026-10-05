---
title: How accurate is the picked resume (0.1.11)
description: The standard, the method and the measured results of the 0.1.11 accuracy test on one real person's resume and real postings, and how to run it again.
---

A resume is livelihood for the person it belongs to. We need it accurate, not usually accurate. This
page says what "accurate" means here, what was measured on 15 postings the prompt and the code had
never seen, and what that does not prove. GigAI 0.1.11 is an alpha.

Nothing on this page is posting text, resume text, an answer or a company name. It holds numbers and
kinds of role only.

## The standard

The test asks one question of every posting: **is this result fully correct?** Fully correct means
every point below holds. One miss and the result is not counted.

**When a requirement counts as met.** The standard is a reasonable reader. A requirement is met when a
recruiter reading the cited master-resume lines would accept them as evidence of it. A scope or
strength word the posting adds ("at scale", "large, complex", "deep", "strong", "proven") does not
turn work the master states into a question.

**When GigAI may ask a question.** Only when the master is silent on the thing; or the posting states a
number the master does not reach (years in a named area); or the posting names a tool or a named set
the master does not show (a master that shows one data format does not meet a posting that names three
others).

**Fully correct means:**

- the verdict is right (matched, or held for answers);
- the gate is right (a resume is made, or it is held);
- the location answer is right;
- no unnecessary question, and no missing question;
- every must-have row is right: its class and its status;
- the resume carries the evidence of every must-have the key calls met, every line is verbatim from
  the master, it fits two pages, and all skills are kept.

**And one rule outside the count: zero invented facts.** A skill, tool, employer, title, date, outcome
or number the master does not state fails the run, whatever the count. A sentence GigAI writes about the
person that the master and the answers do not state, whether invented or stretched, is counted and
shown below.

### How a result is scored

Before any run, an answer key is written for the postings, blind to every result: two independent
passes by a model, then a reconcile, then a ruling on each disputed point under the reasonable-reader
standard above. A script, not a person, scores every result against that key (verdict, gate, location,
each question, each requirement row, the resume checks). Two things need a reader, so a judge model
reads them: whether a resume would be sent as it is, and whether any sentence GigAI wrote states
something the master does not. The judge is never the model that produced the answer.

## Results: the fourth set (15 unseen postings)

The fourth set is 15 real postings of one person, kept sealed until the prompt and the code were
frozen, then run once on all three rows. Ten are postings of the kind this person would apply to; five
were chosen from jobs that needed answers.

**Fully correct, out of 15:**

| Row | Fully correct | Bar of 12 |
| --- | --- | --- |
| Claude Code, `claude-opus-5-5` (pinned) | **14 of 15** | met |
| Claude Code, its default (`claude-sonnet-5-5`) | **12 of 15** | at the bar |
| Codex CLI, its default (`gpt-6-astra`; the model is not reported by the adapter) | **10 of 15** | below |

The bar was set before the run: 12 of 15. "Accurate" for the Opus row means fully correct by the key,
the resume covering every must-have, and no invented or stretched sentence: its 14 are all three.

Each row is 15 postings, 16 calls (one posting was retried once by the product).

| Per point | Opus 5.5 (pinned) | Claude default (Sonnet 5.5) | Codex default |
| --- | --- | --- | --- |
| Fully correct | **14 of 15** | **12 of 15** | **10 of 15** |
| Failed calls | 1 | 1 | 0 |
| Verdict right | 14 | 13 | 13 |
| Over-asks (unnecessary questions) | 0 | 4 | 9 |
| Under-asks (a question owed, not asked) | 0 | 0 | 0 |
| Soft asks: held on a defensible question | 2 | 0 | 2 |
| Must-have rows right | 14 | 12 | 12 |
| Requirement list exact | 14 | 12 | 12 |
| Posting lines covered | 109/109 | 109/109 | 110/110 |
| Resumes checked | 11 | 11 | 7 |
| Resume covers every must-have | 11/11 | 11/11 | 7/7 |
| Resume lines verbatim from the master | 11/11 | 11/11 | 7/7 |
| Resume within two pages, skills kept | 11/11 | 11/11 | 7/7 |
| Resume keeps the lines the model picked | 11/11 | 11/11 | 7/7 |
| Wrong citation, must-have rows | 0 | 1 | 0 |
| Wrong citation, nice-to-have rows | not measurable | not measurable | not measurable |
| Wording: invented | 0 | 0 | 0 |
| Wording: stretched | 0 (1 ambiguous) | 1 defect, seen in 11 results | 0 |
| Send-as-is (judge, advisory) | 10 as is, 1 after a fix, 3 held | 8 as is, 3 after a fix, 3 held | 7 of 7 resumes as is |
| Verdicts | 11 matched, 3 held | 11 matched, 3 held | 7 matched, 8 held |

"Soft ask" is a question on a row the key calls a close call where both readings are defensible. It is
not counted as an over-ask. "Held on a defensible question" is how the Opus row's two are said.

A "resume" is checked only where the result produced one (a held job has none). The resume checks use
the key and the master; the send-as-is line is a judge's opinion and is not in the count.

### Notes on each row

**Opus 5.5, 14 of 15.**

- Held on a defensible question: 2. In both, the model asked one question on a row where either
  reading is reasonable. The key accepts either verdict there, so they count as correct.
- Ambiguous wording: 1. One suggestion calls something "a payment system for a payroll platform"; read
  as the posting's platform, it changes no words and adds no claim. It is counted as ambiguous, not as a
  stretch.
- The one failed call. One posting has only two requirement bullets. The model answered correctly, twice
  (matched, two correct rows). GigAI's guard that refuses an answer with too few requirements rejected
  that correct answer both times, so a person would have seen an error. This is fixed: such a posting is
  now stored, with a visible note. **It stays a failure in these numbers**: the 14 are 14 of 15 including
  it. The re-run after the fix is a separate check and is not in the table.

**Claude default (Sonnet 5.5), 12 of 15.** Four unnecessary questions. One must-have row cited lines that
did not prove it (a wrong citation). One wording defect, counted once though it appeared in 11 results:
the sentence about location evidence, which the model wrote itself. The location sentence in the other
rows is GigAI's own wording, fixed separately.

**Codex default, 10 of 15.** Nine unnecessary questions, no missing ones. Most of the shortfall is
questions asked about things the master already states. It had no failed call.

## Time and tokens

Per assessment, fourth set:

| Row | Seconds per call, mean (max) | Output tokens per call (mean) | Input tokens per call incl. cache (mean) |
| --- | --- | --- | --- |
| Opus 5.5 (pinned) | 37.0 (77.6) | 4,707 | 24,774 |
| Claude default (Sonnet 5.5) | 24.1 (50.7) | 4,008 | 23,796 |
| Codex default | 38.5 (68.5) | 1,605 | 20,249 |

The Claude rows ran on a pinned model; the resolved model was recorded on every call. The Codex CLI's
event stream does not say which model answered: its configured default at the time was `gpt-6-astra`
at medium effort.

## The third set: a tuning set, not a result

The third set is 10 real postings of the same person. **The prompt and the code were tuned on it, so its
numbers are not a result.** They are here as history of how the stack moved, and for one thing the
fourth set cannot show (below). Fully correct, out of 10, by the final key:

| Stack | Claude Code | Codex CLI |
| --- | --- | --- |
| Baseline (before 0.1.11 changes) | 4 | 6 |
| Arm 0 | 6 | 5 |
| Arm 0B | 6 | 8 |
| Arm 2B (Opus 5.5 pinned) | 8 (0 over-asks, 0 under-asks) | not run |
| Sol (Codex pinned to a Sol model; two pins tried) | not run | 5 of 10 on one pin (2 calls timed out), 7 of 10 on the other |

Over-asks and under-asks of the same runs, in order of the rows above: Claude 2 and 4 (baseline), 1 and 4
(arm 0), 0 and 5 (arm 0B), 0 and 0 (arm 2B); Codex 6 and 1, 8 and 0, 1 and 0; the two Sol pins 1 and 1
(8 results read), 2 and 0.

## What is not proven

- **One person's master, 15 unseen postings.** Ten are of the kind this person would apply to; five were
  chosen from jobs that needed answers. The next person's master, or the next posting's wording, can give
  a different result.
- **The ask side is thin in the fourth set.** The key firmly holds one posting out of 15. The fourth set
  mostly tests unnecessary questions, the shape of the requirement list, resume coverage and wording.
  The evidence for the ask side (questions a person must answer before a resume is made) comes from the
  third set: the key holds three of its ten postings firmly and accepts either verdict on a fourth, and
  Opus 5.5 on the full stack had 0 under-asks there. That is a tuning set.
- **The wrong-citation column for nice-to-have rows could not be measured.** The key records no lines
  for them, so 28 to 31 such rows per run were unchecked. Only must-have rows are checked.
- **The judges are models.** Judge variance was seen: one judge model found 26 stretches on one row and
  none on another before every row was judged again with one ruler. A person read every finding. The
  ruler: a row's status is not the judge's to rule on; it reports only a sentence GigAI wrote that the
  master or the answers do not state. Under that ruler the count was 0 invented and 0 stretched on
  Opus and Codex, and the one Sonnet defect above.
- **The key is ruled by a standard.** It was built blind by two passes and a reconcile, and ruled under
  the reasonable-reader standard above. A stricter reader would call some met rows questions.
- **The Codex model is not reported** by the adapter. The row is measured on the configured default.
- **The Claude rows ran on a pinned model.** Opus is asked for by default; a run on another model is a
  different measurement.
- **One master.** One person's resume, one set of stored answers and preferences.
- **A real agent session** through the brief and the hand-back is a separate test and is not in these
  numbers.
- **A failed call is a failure here**, including the one that is now fixed.

## The model notice

GigAI's results are for specific models, so an assessment made with any other model says so, in the job
page, in `scout jobs assess`, in the agent brief and in `gigai doctor`. The rule has no threshold in it.
Per CLI, the model with the best measured result is the reference and carries no notice (unless it is
itself below the bar). Any other measured model carries its own number and the reference's. A model
nobody measured carries the plain notice. The notice and this page are written from one table in the
product (`src/gigai/scout/evaluated_models.py`), so a number is never typed in two places:

| CLI | Model | Fully correct, of 15 | State | Reference |
| --- | --- | --- | --- | --- |
| Claude Code | `claude-opus-5-5` | 14 | meets the bar | yes |
| Claude Code | `claude-sonnet-5-5` | 12 | meets the bar | no |
| Codex CLI | `gpt-6-astra` | 10 | below the bar | yes |

What it says:

- Another measured Claude model: "Assessed with claude-sonnet-5-5: accurate on 12 of 15 jobs in GigAI's
  accuracy run, mostly from unnecessary questions; claude-opus-5-5 reached 14 of 15."
- A Claude model nobody measured: "Assessed with <model>. GigAI's accuracy results are for
  claude-opus-5-5; this assessment may be less accurate."
- The Codex CLI (its model is not reported, and it is below the bar): "Assessed with the Codex CLI (model
  not reported; measured with gpt-6-astra: accurate on 10 of 15 jobs, mostly from unnecessary questions).
  Claude Code with claude-opus-5-5 reached 14 of 15."

## How to run it again

The harness is `tests/evals/run_assess_real_eval.py` in the GigAI repository. It reads a data folder
that you provide, outside the repository, and writes everything under `--out`, never into the repository.

The folder holds:

- `master.md`: your master resume, in GigAI's format, with ids on every line;
- `answers.json`: your stored answers;
- `setup.json`: your preferences (countries, sponsorship, city, work mode, roles);
- `profiles.json`: your profiles;
- `postings/*.md`: one file per posting, a four-line header (`title:`, `company:`, `location:`,
  `url:`), a blank line, the posting text.

```sh
GIGAI_ASSESS_EVAL_LIVE=1 uv run python tests/evals/run_assess_real_eval.py \
  --data-dir PATH/TO/YOUR/DATA --label mine --home PATH/TO/A/SCRATCH/HOME
```

Use a scratch home made by `gigai setup --non-interactive`, never your own. `--max-calls` is a hard cap
on model calls; `--model-target` picks one CLI instead of both; `--fake-model` runs offline to check the
harness only and says nothing about accuracy.

It writes, under `<data-dir>/results/<label>/` unless you pass `--out`: per posting and CLI, every
prompt and raw answer, the stored assessment, the picked resume as markdown and PDF, and the timings and
tokens; and `report.json` and `report.md` for the whole run. It reports facts. It does not say whether
they are right.

To score them you need an answer key built blind by you: read each posting before you look at any
result, and write down the correct verdict, the gate, the questions that are owed and the requirement
rows. Then:

```sh
uv run python tests/evals/score_against_gold.py \
  --key PATH/TO/YOUR/DATA/gold/key.json --results PATH/TO/YOUR/DATA/results/mine
```

The scorer writes `score.json` and `score.md` next to the key (`<key folder>/scores/<label>/`) unless
you pass `--out`, and refuses an `--out` inside the repository. Reading the resume and the wording
against your master is still yours to do, or a judge model's, as above.
