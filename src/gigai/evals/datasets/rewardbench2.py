"""RewardBench 2 (Malik et al., 2025): score four responses to one prompt so the one correct response ranks first.

Licence ODC-By-1.0, Allen Institute for AI; the responses are third-party model outputs under their own terms;
attribution in NOTICES.md. Each item is one prompt of the ``Focus`` or ``Factuality`` subset with its one chosen
and three rejected responses, shown as A-D in a per-prompt seeded order. The gold answer is the chosen letter; a
verdict is right when the chosen response gets the strictly highest score. Upstream ids repeat across the two
subsets (40-49 are in both), so the item id and cluster carry the subset: ``rewardbench2-focus-40``.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from . import SEED, Item, parquet_rows, stratified

REVISION = "7ff08853b0d5686e79b13fda8677024f566a104a"
_FILE = "test-00000-of-00001.parquet"
SOURCES = {
    _FILE: (
        f"https://huggingface.co/datasets/allenai/reward-bench-2/resolve/{REVISION}/data/{_FILE}",
        "c8ec60efbd75d2f9dcba4121e6101f7a6015abc38a34e034ae2c7ae886265958",
    ),
}
SUBSETS = ("Focus", "Factuality")
COUNTS = {"ci": dict.fromkeys(SUBSETS, 5), "release": dict.fromkeys(SUBSETS, 50)}
_DRAW = (
    "Focus (495) and Factuality (475) test prompts by id; responses ordered by random.Random(f'20261109:{id}')"
    ".sample; random.Random(20261109) draws per subset"
)
RECIPES = {"ci": f"{_DRAW} 5 + 5 prompts.", "release": f"{_DRAW} 50 + 50 prompts."}
LETTERS = "ABCD"
ASK = (
    "Below are a user's prompt and four responses to it, A to D. Score each response from 0 to 100 for how well it "
    "answers the prompt: correct, on topic, and doing what was asked. Answer with bare JSON, one entry per response: "
    '[{"id": "A", "score": <0-100>, "reasons": ["..."], "blockers": ["<what makes it wrong, if anything>"]}, ...]'
)


def items(raw: Path) -> list[Item]:
    rows = [row for row in parquet_rows(raw / _FILE, "rewardbench2") if row["subset"] in SUBSETS]
    out = []
    for row in sorted(rows, key=lambda row: int(row["id"])):
        responses = list(zip([*row["chosen"], *row["rejected"]], row["models"], strict=True))
        order = random.Random(f"{SEED}:{row['id']}").sample(range(len(responses)), len(responses))
        shown = "\n\n".join(f"RESPONSE {LETTERS[i]}:\n{responses[k][0]}" for i, k in enumerate(order))
        chosen = LETTERS[order.index(0)]
        key = f"{row['subset'].lower()}-{row['id']}"
        gold = [{"id": LETTERS[i], "score": 100 * (k == 0), "reasons": [], "blockers": []} for i, k in enumerate(order)]
        out.append(
            Item(
                id=f"rewardbench2-{key}",
                prompt=f"{ASK}\n\nPROMPT:\n{row['prompt']}\n\n{shown}",
                expected={"chosen": chosen, "rejected": [letter for letter in LETTERS if letter != chosen]},
                cluster_id=key,
                completion=json.dumps(gold),
                source_ids={"id": row["id"], "subset": row["subset"]}
                | {"models": {LETTERS[i]: responses[k][1] for i, k in enumerate(order)}},
                tags=(row["subset"], f"chosen:{chosen}"),
            )
        )
    return out


def select(pool: list[Item], suite: str, rng: random.Random) -> list[Item]:
    return stratified(pool, lambda item: item.tags[0], COUNTS[suite], rng)
