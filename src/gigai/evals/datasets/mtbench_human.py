"""MT-Bench human judgments (Zheng et al., 2023): pick the better of two assistants' answers, as expert humans did.

Licence CC BY 4.0, LMSYS; attribution in NOTICES.md. Each item is one (question, turn, model A, model B) that two or
more human judges compared, all picking the same side (no tie), with no Claude model on either side, so a Claude
judge never grades its own family. The gold answer is that unanimous winner, in the source's A/B order.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from . import Item, parquet_rows

REVISION = "f7d2896d2cc5d80f8b55c2bbc722613555233c25"
_FILE = "human-00000-of-00001-25f4910818759289.parquet"
SOURCES = {
    _FILE: (
        f"https://huggingface.co/datasets/lmsys/mt_bench_human_judgments/resolve/{REVISION}/data/{_FILE}",
        "4877bc46a40929f4082c3c79593700fb897b1d6c7f4c473032694a01322f5769",
    ),
}
COUNTS = {"ci": 20, "release": 293}
_FILTER = (
    "human split grouped by (question_id, turn, model_a, model_b): >= 2 judgments, all the same non-tie winner, "
    "no claude model on either side (293 of 2,396 groups); random.Random(20261109).sample"
)
RECIPES = {"ci": f"{_FILTER} 20 items.", "release": f"{_FILTER} all 293 items."}
ASK = (
    "Below is a conversation between a user and two AI assistants, A and B, who each answered on their own. Decide "
    "which assistant answers the last user question better: helpful, relevant, accurate, deep, and detailed enough. "
    'Length and position must not sway you. Answer with bare JSON: {"winner": "A"} or {"winner": "B"}.'
)


def items(raw: Path) -> list[Item]:
    groups: dict[tuple, list[dict]] = {}
    for row in parquet_rows(raw / _FILE):
        groups.setdefault((row["question_id"], row["turn"], row["model_a"], row["model_b"]), []).append(row)
    out = []
    for (question, turn, model_a, model_b), rows in sorted(groups.items()):
        winners = {row["winner"] for row in rows}
        if len(rows) < 2 or len(winners) > 1 or winners == {"tie"} or "claude" in f"{model_a} {model_b}":
            continue
        winner = {"model_a": "A", "model_b": "B"}[winners.pop()]
        sides = (_transcript(rows[0]["conversation_a"], turn), _transcript(rows[0]["conversation_b"], turn))
        out.append(
            Item(
                id=f"mtbench_human-{question}-t{turn}-{model_a}-{model_b}",
                prompt=f"{ASK}\n\nASSISTANT A:\n{sides[0]}\n\nASSISTANT B:\n{sides[1]}",
                expected={"winner": winner},
                cluster_id=str(question),
                completion=json.dumps({"winner": winner}),
                source_ids={"question_id": question, "turn": turn, "model_a": model_a, "model_b": model_b}
                | {"judges": sorted(row["judge"] for row in rows)},
                tags=(f"turn:{turn}", f"winner:{winner}"),
            )
        )
    return out


def select(pool: list[Item], suite: str, rng: random.Random) -> list[Item]:
    return rng.sample(pool, COUNTS[suite])


def _transcript(conversation: list[dict], turn: int) -> str:
    return "\n\n".join(f"{message['role'].upper()}: {message['content']}" for message in conversation[: 2 * turn])
