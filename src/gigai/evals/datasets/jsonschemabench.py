"""JSONSchemaBench (Geng et al., 2025): write one JSON value that validates against a real-world JSON Schema.

Licence MIT (dataset card), the JSONSchemaBench authors; attribution in NOTICES.md. Each item is one schema of the
``Github_medium`` test split. The gold answer is the schema itself: any value it accepts is correct. Items ask for
``output_mode = schema``, so the request carries the schema and a text-only adapter appends it to the prompt (the
prompt does not repeat it). The canned completion is ``{}``, which only some schemas accept.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from . import Item, parquet_rows

REVISION = "5bd0f4640badc6f3f02df796421d21cb0ca0b141"
_FILE = "test-00000-of-00001.parquet"
SOURCES = {
    _FILE: (
        f"https://huggingface.co/datasets/epfl-dlab/JSONSchemaBench/resolve/{REVISION}/Github_medium/{_FILE}",
        "75faffa85c98edf2db596e3e1d2b5bf580b864389f578599c38c6512daf9854d",
    ),
}
COUNTS = {"ci": 10, "release": 100}
_DRAW = "schemas by unique_id, random.Random(20261109).sample (not the datasets library's shuffle, which needs numpy)."
RECIPES = {
    "ci": f"All 586 Github_medium test {_DRAW} 10 schemas.",
    "release": f"All 586 Github_medium test {_DRAW} 100 schemas.",
}
ASK = (
    "Write one JSON value that a real system could send and that validates against the JSON Schema this request "
    "carries. Fill in realistic values, not placeholders."
)


def items(raw: Path) -> list[Item]:
    return [
        Item(
            id=f"jsonschemabench-{row['unique_id']}",
            prompt=ASK,
            expected={"schema": json.loads(row["json_schema"])},
            cluster_id=row["unique_id"],
            completion="{}",
            source_ids={"unique_id": row["unique_id"], "subset": "Github_medium", "split": "test"},
            tags=("output_mode:schema",),
        )
        for row in sorted(parquet_rows(raw / _FILE), key=lambda row: row["unique_id"])
    ]


def select(pool: list[Item], suite: str, rng: random.Random) -> list[Item]:
    return rng.sample(pool, COUNTS[suite])
