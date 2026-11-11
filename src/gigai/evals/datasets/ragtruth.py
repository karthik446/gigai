"""RAGTruth (Niu et al., ACL 2024): find the claims in an LLM's answer or summary that its source does not support.

Licence MIT, (c) 2023 Particle Media; attribution in NOTICES.md. Each item is one human-annotated response with the
task its model was given (source passages included). The gold answer is the response-level verdict plus the
annotated hallucination spans (character offsets into the response); ``implicit_true`` spans are not counted.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

from . import Item, stratified

_RAW = "https://raw.githubusercontent.com/ParticleMedia/RAGTruth/main/dataset"
SOURCES = {
    "response.jsonl": (f"{_RAW}/response.jsonl", "e4c2e4ac24fff676d8984cc61c35d791612fadc58015335d97dd632375e18073"),
    "source_info.jsonl": (
        f"{_RAW}/source_info.jsonl",
        "0dffc26ea9f3c1c3d7c7e8336b56ef1646e3cec876edffcca3c9c624d12d578b",
    ),
}
_STRATA = ("QA/spans", "QA/none", "Summary/spans", "Summary/none")
COUNTS = {"ci": dict.fromkeys(_STRATA, 5), "release": dict.fromkeys(_STRATA, 100)}
#: Responses kept per (source_id, label). RC1's one-per-source release recipe is short: QA has 83 sources with spans.
CAP = {"ci": 1, "release": 2}
_FILTER = 'split == "test", quality == "good", task_type QA or Summary, responses by id; random.Random(20261109)'
_DRAW = "with >= 1 span and the same number without, sampled with the same generator."
RECIPES = {
    "ci": f"{_FILTER} picks 1 response per source_id and label (any model); per task 5 {_DRAW}",
    "release": f"{_FILTER} picks 2 responses per source_id and label (any model); per task 100 {_DRAW}",
}
ASK = (
    "A model was given the task below and wrote the response after it. Find every part of the response that the "
    "task's source material does not support: claims it contradicts and claims it never makes. Answer with bare JSON: "
    '{"supported": true or false, "unsupported_spans": ["<exact quote from the response>", ...]}; supported is true '
    "only when the list is empty."
)


def items(raw: Path) -> list[Item]:
    sources = {row["source_id"]: row for row in map(json.loads, _lines(raw / "source_info.jsonl"))}
    responses = map(json.loads, _lines(raw / "response.jsonl"))
    out = []
    for row in sorted(responses, key=lambda row: int(row["id"])):
        source = sources[row["source_id"]]
        spans = [{key: span[key] for key in ("start", "end", "text")} for span in row["labels"]]
        spans = [span for span, label in zip(spans, row["labels"], strict=True) if not label["implicit_true"]]
        verdict = {"supported": not spans, "unsupported_spans": [span["text"] for span in spans]}
        out.append(
            Item(
                id=f"ragtruth-{row['id']}",
                prompt=f"{ASK}\n\nTASK:\n{source['prompt']}\n\nRESPONSE:\n{row['response']}",
                expected={"hallucinated": bool(spans), "spans": spans},
                cluster_id=row["source_id"],
                completion=json.dumps(verdict, ensure_ascii=False),
                source_ids={"response_id": row["id"], "source_id": row["source_id"], "model": row["model"]},
                tags=(source["task_type"], "spans" if spans else "none", row["split"], row["quality"]),
            )
        )
    return out


def select(pool: list[Item], suite: str, rng: random.Random) -> list[Item]:
    groups: dict[tuple[str, str], list[Item]] = {}
    for item in pool:
        if item.tags[0] in ("QA", "Summary") and item.tags[2:] == ("test", "good"):
            groups.setdefault((item.cluster_id, item.tags[1]), []).append(item)
    kept = [pick for group in groups.values() for pick in rng.sample(group, min(CAP[suite], len(group)))]
    return stratified(kept, lambda item: f"{item.tags[0]}/{item.tags[1]}", COUNTS[suite], rng)


def _lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()
