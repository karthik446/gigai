"""tinyGSM8k (Maia Polo et al., 2024): 100 grade-school maths problems that stand in for the GSM8K test set.

Licence MIT: tinyBenchmarks (c) 2024 Felipe Maia Polo, GSM8K (c) 2021 OpenAI; attribution in NOTICES.md. Each item is
one problem; the gold answer is the number after ``####`` in the GSM8K solution, commas dropped. The canned completion
is that published solution with its calculator annotations removed and ``ANSWER: <number>`` as the last line.
"""

from __future__ import annotations

import random
import re
from pathlib import Path

from . import Item, parquet_rows

REVISION = "b870449ed96ee3e308748b550908332450ec4098"
_FILE = "test-00000-of-00001.parquet"
SOURCES = {
    _FILE: (
        f"https://huggingface.co/datasets/tinyBenchmarks/tinyGSM8k/resolve/{REVISION}/main/{_FILE}",
        "07c2857ce7f4f4cf123066fede0f52f2aab7458c0c9bae3293c3f656a9eac53c",
    ),
}
COUNTS = {"ci": 10, "release": 100}
_DRAW = "test rows in file order (ids tinygsm8k-000..099), random.Random(20261109).sample"
RECIPES = {"ci": f"All 100 {_DRAW} 10 problems.", "release": f"All 100 {_DRAW} all 100 problems."}
ASK = (
    "Solve the following math problem step by step. The last line of your response should be of the form "
    "'ANSWER: $NUMBER' (without quotes) where $NUMBER is the answer to the problem."
)
_ANSWER = re.compile(r"####\s*(-?[\d,]+(?:\.\d+)?)\s*$")


def items(raw: Path) -> list[Item]:
    out = []
    for index, row in enumerate(parquet_rows(raw / _FILE, "tinygsm8k")):
        answer = row["answer"].strip()
        match = _ANSWER.search(answer)
        if not match:
            raise ValueError(f"tinygsm8k row {index}: no '####' answer")
        number = match.group(1).replace(",", "")
        steps = re.sub(r"<<[^>]*>>", "", answer[: match.start()]).strip()
        out.append(
            Item(
                id=f"tinygsm8k-{index:03d}",
                prompt=f"{ASK}\n\n{row['question'].strip()}",
                expected=number,
                cluster_id=f"tinygsm8k-{index:03d}",
                completion=f"{steps}\nANSWER: {number}",
                source_ids={"row": index},
            )
        )
    return out


def select(pool: list[Item], suite: str, rng: random.Random) -> list[Item]:
    return rng.sample(pool, COUNTS[suite])
