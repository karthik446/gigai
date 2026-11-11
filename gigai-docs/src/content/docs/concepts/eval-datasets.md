---
title: Eval datasets
description: The public datasets GigAI evals draw on, what each one tests, and how its small ci slice and full release slice are built.
---

Some GigAI eval steps score a model on a public dataset instead of on synthetic items.
`gigai.evals.datasets` holds one loader per dataset. A loader turns each upstream item into
one prompt string plus its gold answer, keeps the upstream ids so a reader can find the item in
the source files, and records the recipe that picked it. The module uses only the Python
standard library.

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
training cut-off and nobody has checked whether models saw it. All four sets score 0, so treat a
high score as an upper bound.

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
