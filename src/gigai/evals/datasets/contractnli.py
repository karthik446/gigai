"""ContractNLI (Koreeda & Manning, 2021): decide 17 fixed hypotheses against one non-disclosure agreement.

Licence CC-BY-4.0, (c) Hitachi America, Ltd.; attribution in NOTICES.md. Each item is one test-split document with
its 17 hypotheses, labelled Entailment, Contradiction or NotMentioned, with evidence spans as character offsets. The
authors publish no inter-annotator agreement, so every item carries the ``audit_pending`` tag until our own audit.
"""

from __future__ import annotations

import json
import random
import zipfile
from pathlib import Path

from . import Item, length_band, stratified

URL = "https://stanfordnlp.github.io/contract-nli/resources/contract-nli.zip"
SOURCES = {"contract-nli.zip": (URL, "e03fc77bbf8b53e2976a250e81d8a294bc3d5e5fb014521e477dee9340d6287b")}
COUNTS = {"ci": {"<8k": 3, "8-24k": 5, "24-60k": 2}, "release": {"<8k": None, "8-24k": None, "24-60k": None}}
RECIPES = {
    "ci": "Test split, documents by id, banded by text length (<8k/8-24k/24-60k chars); 3/5/2 per band (the 20/33/7 "
    "base proportions) with random.Random(20261109).sample; one item per document with all 17 hypotheses.",
    "release": "Test split: all 123 documents, grouped by length band (<8k/8-24k/24-60k chars), by id inside a band; "
    "one item per document with all 17 hypotheses.",
}
ASK = (
    "Read the non-disclosure agreement, then decide for every hypothesis whether the agreement entails it, "
    'contradicts it, or does not mention it. Answer with bare JSON: {"labels": {"<hypothesis id>": "<label>"}}, '
    "one entry per hypothesis id, each label Entailment, Contradiction or NotMentioned."
)


def items(raw: Path) -> list[Item]:
    with zipfile.ZipFile(raw / "contract-nli.zip") as archive:
        data = json.loads(archive.read("contract-nli/test.json"))
    keys = sorted(data["labels"], key=lambda key: int(key.split("-")[1]))
    hypotheses = "\n".join(f"{key}: {data['labels'][key]['hypothesis']}" for key in keys)
    out = []
    for doc in sorted(data["documents"], key=lambda doc: doc["id"]):
        notes = doc["annotation_sets"][0]["annotations"]
        labels = {key: notes[key]["choice"] for key in keys}
        spans = {key: [doc["spans"][i] for i in notes[key]["spans"]] for key in keys if notes[key]["spans"]}
        out.append(
            Item(
                id=f"contractnli-test-{doc['id']}",
                prompt=f"{ASK}\n\nAGREEMENT:\n{doc['text']}\n\nHYPOTHESES:\n{hypotheses}",
                expected={"labels": labels, "spans": spans},
                cluster_id=str(doc["id"]),
                completion=json.dumps({"labels": labels}),
                source_ids={"split": "test", "document_id": doc["id"], "file_name": doc["file_name"]},
                tags=("audit_pending", length_band(len(doc["text"]))),
            )
        )
    return out


def select(pool: list[Item], suite: str, rng: random.Random) -> list[Item]:
    return stratified(pool, lambda item: item.tags[1], COUNTS[suite], rng)
