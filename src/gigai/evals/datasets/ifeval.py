"""IFEval (Zhou et al., 2023): prompts with formatting instructions that code can verify ("no commas", "3 sections").

Licence Apache-2.0, Google LLC; attribution in NOTICES.md. Each item is one prompt; the gold answer is its
``instruction_id_list`` with the matching ``kwargs``, which the upstream verifiers (``instruction_following_eval``)
check against a response. The canned completion is the GPT-4 response Google published beside the prompts.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from . import Item

#: Latest google-research commit touching instruction_following_eval (responses file and the verifiers V4 needs).
GOOGLE_COMMIT = "e6890f85757dd84e27ca6df2dd30651dafad28e0"
HF_REVISION = "966cd89545d6b6acfd7638bc708b98261ca58e84"
_GOOGLE = f"https://raw.githubusercontent.com/google-research/google-research/{GOOGLE_COMMIT}/instruction_following_eval"
_RESPONSES = "input_response_data_gpt4_20231107_145030.jsonl"
SOURCES = {
    "ifeval_input_data.jsonl": (
        f"https://huggingface.co/datasets/google/IFEval/resolve/{HF_REVISION}/ifeval_input_data.jsonl",
        "6a85310ca8ce15eff755aa08a3a4ff931c7e273e7515ebb3c492ea85fd8288f2",
    ),
    _RESPONSES: (f"{_GOOGLE}/data/{_RESPONSES}", "0cff1d1469b774e80296bee20ca696894df35f3c6d6539f2c886d67c07599dcb"),
}
COUNTS = {"ci": 10, "release": 100}
_DRAW = "prompts by key, random.Random(20261109).sample (not the datasets library's shuffle, which needs numpy)."
RECIPES = {"ci": f"All 541 {_DRAW} 10 prompts.", "release": f"All 541 {_DRAW} 100 prompts."}


def items(raw: Path) -> list[Item]:
    def rows(name: str) -> list[dict]:
        return [json.loads(line) for line in (raw / name).read_text(encoding="utf-8").splitlines()]

    responses = {row["prompt"]: row["response"] for row in rows(_RESPONSES)}
    return [
        Item(
            id=f"ifeval-{row['key']}",
            prompt=row["prompt"],
            expected={"instruction_id_list": row["instruction_id_list"], "kwargs": row["kwargs"]},
            cluster_id=str(row["key"]),
            completion=responses[row["prompt"]],
            source_ids={"key": row["key"]},
            tags=tuple(row["instruction_id_list"]),
        )
        for row in sorted(rows("ifeval_input_data.jsonl"), key=lambda row: row["key"])
    ]


def select(pool: list[Item], suite: str, rng: random.Random) -> list[Item]:
    return rng.sample(pool, COUNTS[suite])
