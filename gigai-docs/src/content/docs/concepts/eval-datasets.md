---
title: Eval datasets
description: The public datasets GigAI evals draw on, what each one tests, and how its small ci slice and full release slice are built.
---

Some GigAI eval steps score a model on a public dataset instead of on synthetic items.
`gigai.evals.datasets` holds one loader per dataset. A loader turns each upstream item into
one prompt string plus its gold answer, keeps the upstream ids so a reader can find the item in
the source files, and records the recipe that picked it. The module uses only the Python
standard library, with one exception: several sets ship their upstream files as parquet, and
building their `release` slice reads them with `pyarrow`, an optional install
(`uv tool install --with pyarrow gigai`). Without it that build exits with code 2 and prints that
hint. The committed `ci` slices need nothing extra.

Each dataset has two slices, both drawn with `seed = 20261109`:

- **`ci`**: at most 60 items, committed to the repository under `tests/fixtures/evals/ci/<set>.jsonl`
  next to a `<set>.sha256` file. Every row carries a canned completion, so a `ci` run makes no
  model calls. Loading a slice checks it against its `.sha256` file first. Items whose text holds
  an e-mail address outside the `example.com` and `example.test` domains, or a macOS home-folder
  path (`/Users/<name>`), are dropped before sampling; this is the same check the repository's
  fixture guard runs.
- **`release`**: the larger slice a real eval scores. It is never committed. `download_release`
  builds it at `datasets/<set>-release.jsonl` under the data dir the eval step passes, from
  upstream files it keeps in `datasets/raw/<set>/` there. A missing file is downloaded once and
  checked against a pinned sha256. With no network, or a file that does not match, GigAI exits
  with code 2 and prints the URL and the path to put the file in by hand.

Every prompt is at most 60,000 characters. The licences and attribution are in
`src/gigai/evals/datasets/NOTICES.md`, which ships in the wheel. The **K** score below is the
contamination line of the dataset scoring sheet: 0 means the data was public before current models'
training cut-off and nobody has checked whether models saw it; 1 sits between that and 2 (newer than
the cut-off, or private), and as the scoring sheets apply it, it means a check was reported or part
of the data is newer than some models' cut-off. Every set scores 0 or 1, so treat a high score as an
upper bound.

## ContractNLI

[ContractNLI](https://stanfordnlp.github.io/contract-nli/) tests judgment with evidence: given a
real non-disclosure agreement, decide for each of 17 fixed hypotheses whether the agreement
entails it, contradicts it, or does not mention it, and point at the clauses that show it. One
item is one agreement with all 17 hypotheses. It is CC BY 4.0 (Hitachi America, Ltd.). The test
split has 123 agreements and 2,091 (agreement, hypothesis) pairs: 968 Entailment, 903 NotMentioned,
220 Contradiction. No agreement figure is published, so the labels wait on a 100-pair audit and
every item is tagged `audit_pending`. K = 0 (released 2021). The `ci` slice is 10 agreements
(3 under 8k characters, 5 of 8k–24k, 2 of 24k–60k); `release` is all 123.

## RAGTruth

[RAGTruth](https://github.com/ParticleMedia/RAGTruth) tests faithfulness: given a task with its
source passages and a model's response, find the parts of the response the sources do not support.
One item is one response; the gold answer is the human-marked spans (none means the response is
faithful). It is MIT (Particle Media). The corpus has 17,790 annotated responses; GigAI uses the
`test` split's question-answering and summarization tasks, good-quality responses only. Annotator
consistency was 91.8 % and 78.8 % before adjudication; no κ is published. K = 0 (released 2024-01).
The `ci` slice is 20 responses (5 with spans and 5 without, per task); `release` is 400 (100 and
100 per task, at most two responses per source and label, because the test split has only 83
question-answering sources with a marked span). Within a source the responses to keep are drawn at
random rather than by id: upstream ids list the GPT models first, so taking the first id made the
faithful class almost all GPT (194 of 200) while GPT wrote 40 % of the faithful responses; the
random pick gives 98 of 200. Responses with marked spans are mostly from the smaller open models in
the corpus itself (GPT wrote 14 of 330), which no recipe can change.

## QuALITY

[QuALITY](https://github.com/nyu-mll/quality) tests long-document reading: a four-option question
that needs the whole of an 11k–34k character article. One item is one question with its full
article. It is CC BY 4.0 ([nyu-mll.github.io/quality](https://nyu-mll.github.io/quality/)); each
article also keeps its own source licence (Project Gutenberg, the Open American National Corpus and
others). The dev split has 2,086 questions over 115 articles, 1,065 of them marked hard. Validator
agreement is a median Krippendorff's α of 0.71. GPT-4 scored 89.2 against 93.5 for humans. K = 0
(released 2021; Gutenberg texts are certainly in pretraining data). The `ci` slice is 10 questions
from 2 articles; `release` is 300 questions from 60 articles, 5 per article with at least 2 hard.

## IFEval

[IFEval](https://huggingface.co/datasets/google/IFEval) tests instruction following: prompts with
formatting constraints that code can check, such as "no commas" or "exactly 3 sections". One item
is one prompt; the gold answer is its list of instruction ids and their arguments. It is
Apache-2.0 (Google LLC). It has 541 prompts. Labels need no agreement figure: Google's deterministic
verifiers score each response (strict and loose variants). The verifiers are not part of this
module: scoring IFEval lands with the eval steps (a later release) and needs `langdetect`, `nltk`
and `absl-py` as an optional extra. K = 0 (public since November 2023, and models are known to
overfit its constraint set), so pair it with IFBench (ODC-BY-1.0) as an overfitting check. The
canned `ci` completions are the GPT-4 responses Google published with the data. The `ci` slice is
10 prompts; `release` is 100. Both are drawn with Python's `random.Random(20261109).sample` over
the prompts sorted by key, not with the Hugging Face `datasets` shuffle the scoring sheet names
(that library needs numpy), so the 100 prompts differ from a `shuffle(seed=20261109)` draw.

## JSONSchemaBench

[JSONSchemaBench](https://huggingface.co/datasets/epfl-dlab/JSONSchemaBench) tests structured
output: write one JSON value that validates against a real-world JSON Schema. GigAI uses the
`Github_medium` test split, 586 schemas taken from GitHub (draft-4 361, 2020-12 161, draft-7 40,
draft-6 24), all of them valid against their metaschema. One item is one schema; the gold answer is
the schema itself, since any value it accepts is correct, so labels need no agreement figure. Items
are tagged for the schema output mode and carry the schema only in the gold answer; the prompt holds
only a fixed instruction, so a run must send the schema with the request, which is the eval runner's
job, not the loader's. That instruction replaces the lm-eval 2-shot template the scoring sheet names.
Checking a value against the schema is the scorer's job too. It is MIT according to the dataset
card, which has no licence file (the JSONSchemaBench authors). K = 0 (the schemas are public GitHub
files). The `ci` slice is 10 schemas; `release` is
100. The canned `ci` completion is `{}`, which only some schemas accept (3 of the 10, 34 of the
100), so a `ci` run exercises both a pass and a fail.

## RewardBench 2

[RewardBench 2](https://huggingface.co/datasets/allenai/reward-bench-2) tests ranking: given a
prompt and four responses of which exactly one is right, score each so the right one ranks first.
GigAI uses the `Focus` subset (on-topic, high-quality answers against off-topic ones, 495 prompts)
and the `Factuality` subset (answers against ones with factual errors, 475 prompts). One item is one
prompt with its chosen and three rejected responses, shown as A to D in an order seeded per prompt
(`random.Random("20261109:<id>")`); the model answers with a 0–100 score per response, and the
gold answer is the chosen letter and the three rejected ones, so ranking accuracy (the chosen
response scored strictly highest) is computable. It is ODC-By-1.0 (Allen Institute for AI); the
responses are outputs of many models, each under its own terms, listed in `NOTICES.md`. Published
results: Claude Opus 4 scores 82.7 on Factuality and 86.2 on Focus, GPT-4.1 82.9 on Factuality,
Claude 3.7 Sonnet 92.1 on Focus. K = 1 (new prompts released June 2025, no contamination check).
The `ci` slice is 10 prompts (5 per subset); `release` is 100 (50 per subset). Length does not give
the answer away: the chosen response is the longest of the four in 18 of the 100 `release` items.
Claude wrote the chosen response in 12 of them, all in Factuality; each item records which model
wrote each response. Factuality's chosen and rejected responses were filtered by LM judges, so a
Claude-family scorer may inherit those judges' bias: report the two subsets separately and treat
Focus as the primary figure. Upstream ids repeat across the two subsets (40 to 49 are in both), so
item ids and clusters carry the subset, as in `rewardbench2-focus-40`.

## JudgeBench

[JudgeBench](https://huggingface.co/datasets/ScalerLab/JudgeBench) tests judging: given a hard
question and two responses by the same model, pick the one that reaches the correct answer. GigAI
uses the `gpt` split: 350 pairs, both responses by GPT-4o, so a Claude judge never grades its own
family. Questions come from MMLU-Pro (154 pairs), LiveBench reasoning (98) and maths (56), and
LiveCodeBench (42). One item is one pair; the gold answer is the pair's label (`A>B` or `B>A`).
Labels come from the source benchmarks' answer keys, not from votes, so no agreement figure
applies; the paper reports that GPT-4o judges barely above chance. It is MIT according to the
dataset card, which has no licence file (the JudgeBench authors). The upstream labels lean 193 to
157 towards `A>B`, so A and B are swapped when `random.Random("20261109:<pair_id>")` draws below
0.5; each item keeps the source label and whether it was swapped, so either order can be rebuilt.
K = 1 (released October 2024; the LiveBench and LiveCodeBench questions are newer than some models'
cut-off). The `ci` slice is 20 pairs and `release` 300, both in proportion to the four source
families (9/6/3/2 and 132/84/48/36). The strata are families, not the 17 upstream sources: the 14
MMLU-Pro subject sources count as one. All 17 sources are in `release`, but each MMLU-Pro subject
has only 8 to 11 pairs, so check per-source accuracy per family, pooling MMLU-Pro. The correct
response is the longer one in 132 of the 300 `release` pairs.

## MT-Bench human judgments

[MT-Bench human judgments](https://huggingface.co/datasets/lmsys/mt_bench_human_judgments) tests
judging against people: given two assistants' answers to the same MT-Bench question, pick the one
expert humans preferred. One item is one (question, turn, model A, model B) that two or more
judges compared, all choosing the same side, with no tie and no Claude model on either side. That
leaves 293 of the 2,396 groups in the `human` split, over 73 questions. The prompt shows both
conversations up to the judged turn; the gold answer is the winning side, in the source's order
(A won 144, B 149). It is CC BY 4.0 (LMSYS). The paper reports 85 % agreement between GPT-4 and
humans and 81 % between humans, ties excluded; over the 761 items judged at least twice, pooled
Cohen's κ is 0.646 for two-way and 0.458 for three-way (with ties) verdicts. K = 0 (released 2023,
answers from 2023 models). The `ci` slice is 20 items; `release` is all 293. Expect a length bias:
the winner wrote the longer answer in 217 of the 293 items (74 %), so a judge that prefers longer
answers scores well here without judging content.

## tinyGSM8k

[tinyGSM8k](https://huggingface.co/datasets/tinyBenchmarks/tinyGSM8k) tests grade-school maths
with 100 problems chosen to stand in for the GSM8K test set. One item is one problem; the prompt
asks for a final `ANSWER: <number>` line, and the gold answer is the number after `####` in the
GSM8K solution, commas dropped. It is MIT (tinyBenchmarks, Felipe Maia Polo; GSM8K, OpenAI); the
dataset card has no licence tag. K = 0 (GSM8K released 2021). The canned `ci` completions are the
published solutions without their calculator annotations. Each problem is its own cluster, item ids
are the upstream row number (`tinygsm8k-000` to `tinygsm8k-099`, likewise for tinyMMLU), and the
prompt puts a fixed answer-format instruction before the bare question. The `ci` slice is 10
problems; `release` is all 100. tinyBenchmarks estimates full-benchmark scores with per-item
weights; the items here do not carry them, so a score is plain accuracy on the 100.

## tinyMMLU

[tinyMMLU](https://huggingface.co/datasets/tinyBenchmarks/tinyMMLU) tests broad knowledge with 100
four-choice MMLU questions over 46 subjects, chosen to stand in for the full MMLU test set. One item
is one question with its choices lettered A to D; the gold answer is the correct letter (A 19, B 26,
C 27, D 28) and the cluster is the subject. It is MIT (tinyBenchmarks, Felipe Maia Polo; MMLU, Dan
Hendrycks); the dataset card has no licence tag. K = 0 (MMLU released 2020). The `ci` slice is 10
questions; `release` is all 100.
