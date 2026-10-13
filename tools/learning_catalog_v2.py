"""Rewrite a shipped catalog ``scout-learning-shipped-catalog:1`` as ``:2`` (learning center M2), ids unchanged.

A developer tool for the one-off move; a new role's catalog is written as ``:2`` by ``learning_export_shipped.py
--catalog-only``. It adds the ``site`` block, ``prerequisites: []`` and ``depth: null`` per lesson and the two
version numbers, and touches nothing else.

    python tools/learning_catalog_v2.py src/gigai/scout/data/learning/shipped/<role-key>/catalog.json [...]
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from gigai.scout import learning_shipped as shipped  # noqa: E402


def upgrade(catalog: dict) -> dict:
    if catalog.get("schema_version") != shipped.CATALOG_SCHEMA_V1:
        raise SystemExit(f"not a {shipped.CATALOG_SCHEMA_V1}: {catalog.get('schema_version')}")
    found = dict(catalog)
    found["schema_version"] = shipped.CATALOG_SCHEMA_VERSION
    found["lessons"] = [{**lesson, "prerequisites": [], "depth": None} for lesson in catalog["lessons"]]
    found["site"] = {
        "base_url": shipped.SITE_BASE_URL, "site_version": shipped.SITE_VERSION, "lessons_path": shipped.SITE_LESSONS_PATH,
        "practice_path": shipped.SITE_PRACTICE_PATH, "pathways_path": shipped.SITE_PATHWAYS_PATH,
    }
    found["pathway_version"], found["concept_keys_version"] = shipped.PATHWAY_VERSION, shipped.CONCEPT_KEYS_VERSION
    return found


def main(argv: list[str]) -> int:
    for name in argv:
        path = Path(name)
        path.write_bytes(shipped._dump(upgrade(json.loads(path.read_text(encoding="utf-8")))))
        print(f"rewrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
