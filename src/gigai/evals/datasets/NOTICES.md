# Eval dataset notices

The loaders in this package read public datasets. gigai's repository commits a small `ci` slice of each (under
`tests/fixtures/evals/ci/`, not shipped in the wheel); a `release` slice is built on the user's machine from the
upstream files. The upstream files and their pinned sha256 are listed in each loader's `SOURCES`. Each slice row
keeps the upstream ids in `source_ids`. Items are reformatted into one prompt string; their text is not altered.

## contractnli

- Name: ContractNLI: A Dataset for Document-level Natural Language Inference for Contracts
- SPDX: CC-BY-4.0
- Licence: Creative Commons Attribution 4.0 International, https://creativecommons.org/licenses/by/4.0/
- URL: https://stanfordnlp.github.io/contract-nli/ (file `contract-nli.zip`, test split)
- Fetched: 2026-11-10
- Attribution: Copyright Hitachi America, Ltd., made available under the Creative Commons Attribution 4.0
  International Public License (the `LICENSE` and `TERMS` files in the archive). Yuta Koreeda and Christopher D.
  Manning, "ContractNLI: A Dataset for Document-level Natural Language Inference for Contracts", Findings of EMNLP 2021.
- Changes made: sliced and flattened. A seeded subset of test documents is kept; each document and its 17
  hypotheses are rendered as one prompt; span indices become character offsets.

## ragtruth

- Name: RAGTruth: A Hallucination Corpus for Developing Trustworthy Retrieval-Augmented Language Models
- SPDX: MIT
- URL: https://github.com/ParticleMedia/RAGTruth (files `dataset/response.jsonl`, `dataset/source_info.jsonl`)
- Fetched: 2026-11-10
- Attribution: Cheng Niu et al., ACL 2024. The source passages come from MS MARCO, CNN/Daily Mail and Yelp as
  redistributed by RAGTruth.
- Changes made: sliced and flattened. A seeded subset of test responses is kept; the task prompt and the response
  are joined into one prompt; `implicit_true` spans are dropped.
- Licence (the repository's `LICENSE`, verbatim):

  ```text
  MIT License

  Copyright (c) 2023 Particle Media

  Permission is hereby granted, free of charge, to any person obtaining a copy
  of this software and associated documentation files (the "Software"), to deal
  in the Software without restriction, including without limitation the rights
  to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
  copies of the Software, and to permit persons to whom the Software is
  furnished to do so, subject to the following conditions:

  The above copyright notice and this permission notice shall be included in all
  copies or substantial portions of the Software.

  THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
  IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
  FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
  AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
  LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
  OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
  SOFTWARE.
  ```

## quality

- Name: QuALITY: Question Answering with Long Input Texts, Yes! (v1.0.1, `htmlstripped` dev split)
- SPDX: CC-BY-4.0
- Licence: Creative Commons Attribution 4.0 International, https://creativecommons.org/licenses/by/4.0/
- URL: https://github.com/nyu-mll/quality (file `data/v1.0.1/QuALITY.v1.0.1.htmlstripped.dev`); licence stated at
  https://nyu-mll.github.io/quality/
- Fetched: 2026-11-10
- Attribution: NYU Machine Learning for Language, CC BY 4.0. Richard Yuanzhe Pang, Alicia Parrish, Nitish Joshi,
  Nikita Nangia, Jason Phang, Angelica Chen, Vishakh Padmakumar, Johnny Ma, Jana Thompson, He He and Samuel R.
  Bowman, NAACL 2022. Each article keeps its own source licence in the upstream `license` field (Project Gutenberg
  licence, the Open American National Corpus licence, and others).
- Changes made: sliced and flattened. A seeded subset of articles and questions is kept; article, question and the
  four options are rendered as one prompt; the gold label becomes a letter.

## ifeval

- Name: IFEval: Instruction-Following Evaluation for Large Language Models
- SPDX: Apache-2.0
- URL: https://huggingface.co/datasets/google/IFEval at revision 966cd89545d6b6acfd7638bc708b98261ca58e84 (file
  `ifeval_input_data.jsonl`); canned responses from
  https://github.com/google-research/google-research/tree/e6890f85757dd84e27ca6df2dd30651dafad28e0/instruction_following_eval
  (`data/input_response_data_gpt4_20231107_145030.jsonl`)
- Fetched: 2026-11-10
- Attribution: Copyright Google LLC (the dataset card's `license: apache-2.0`; the google-research repository's
  `LICENSE`). Jeffrey Zhou, Tianjian Lu, Swaroop Mishra, Siddhartha Brahma, Sujoy Basu, Yi Luan, Denny Zhou and Le
  Hou, arXiv:2311.07911, 2023.
- Changes made: sliced. A seeded subset of prompts is kept unchanged; the published GPT-4 response is attached as
  each item's canned completion.
- Licence notice:

  ```text
  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use these files except in compliance with the License.
  You may obtain a copy of the License at

      https://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
  ```
