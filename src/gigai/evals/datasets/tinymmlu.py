"""tinyMMLU (Maia Polo et al., 2024): 100 four-choice MMLU questions that stand in for the full MMLU test set.

Licence MIT: tinyBenchmarks (c) 2024 Felipe Maia Polo, MMLU (c) 2020 Dan Hendrycks; attribution in NOTICES.md. Each
item is one question with its choices lettered A-D; the gold answer is the correct letter (upstream stores a 0-based
index) and the cluster is the MMLU subject. The canned completion is ``ANSWER: <gold letter>``.
"""

from __future__ import annotations

import random
from pathlib import Path

from . import Item, parquet_rows

REVISION = "cd6a8ee9e1e6f3f18f683535f6c096c386a85da3"
_FILE = "test-00000-of-00001.parquet"
SOURCES = {
    _FILE: (
        f"https://huggingface.co/datasets/tinyBenchmarks/tinyMMLU/resolve/{REVISION}/all/{_FILE}",
        "256f81ffdd6e64699df096c02afafba33626bfca08baed484c275e351993f317",
    ),
}
COUNTS = {"ci": 10, "release": 100}
_DRAW = "config all, test rows in file order (ids tinymmlu-000..099), random.Random(20261109).sample"
RECIPES = {"ci": f"All 100 {_DRAW} 10 questions.", "release": f"All 100 {_DRAW} all 100 questions."}
LETTERS = "ABCD"
ASK = (
    "Answer the following multiple choice question. The last line of your response should be of the form "
    "'ANSWER: $LETTER' (without quotes) where LETTER is one of A,B,C,D."
)


def items(raw: Path) -> list[Item]:
    out = []
    for index, row in enumerate(parquet_rows(raw / _FILE, "tinymmlu")):
        choices = "\n".join(f"{LETTERS[i]}) {choice}" for i, choice in enumerate(row["choices"]))
        out.append(
            Item(
                id=f"tinymmlu-{index:03d}",
                prompt=f"{ASK}\n\n{row['question'].strip()}\n\n{choices}",
                expected=LETTERS[row["answer"]],
                cluster_id=row["subject"],
                completion=f"ANSWER: {LETTERS[row['answer']]}",
                source_ids={"row": index, "subject": row["subject"]},
                tags=(row["subject"],),
            )
        )
    return out


def select(pool: list[Item], suite: str, rng: random.Random) -> list[Item]:
    return rng.sample(pool, COUNTS[suite])
