"""S14 F3: render front matter with journal._render_handoff and validate it.

Run from the repo root:  uv run python <this file>
"""
import json, uuid
from gigai import journal
from gigai.canonical import canonical_json_bytes
from gigai.validators import validate_serialized_contract

def check(label, front_matter):
    doc = journal._render_handoff(1, "gig_" + str(uuid.uuid4()), "handoff_" + str(uuid.uuid4()),
                                  "run_started", "body\n", None, front_matter=front_matter).decode()
    meta, _ = json.JSONDecoder().raw_decode(doc[doc.index("{"):])
    report = validate_serialized_contract("handoff-frontmatter.schema.json", canonical_json_bytes(meta))
    print(label, "valid=" + str(report.valid), [f.message[:90] for f in report.findings][:3])

check("default_metadata_only:", None)
check("with_artifact_refs:", {"artifact_refs": [{"path": "runs/x.json", "content_sha256": "0" * 64,
                                                  "media_type": "application/octet-stream", "size_bytes": 1}]})
