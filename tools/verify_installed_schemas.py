"""Verify frozen schema resources from an installed GigAI distribution."""

from __future__ import annotations

import hashlib
from importlib import resources
import json


EXPECTED_SHA256 = {
    "adjudication.schema.json": "9b6d3f489dcff15b510e4c041be5bcef0afcd10d6e9d584a21ef79c9842f49fe",
    "active-gig-version.schema.json": "77a2f9df0928a8cfe60b496f63b981ab941268cdfc8c557902549f645e4a76f6",
    "common.schema.json": "825a15da8f61348cc16afe315c2aca0e3218c78c0bf0f93394f74fe78cb7b53a",
    "feedback.schema.json": "c89cda74feb86d34448a3e8afbfcded2554e3c077622ecfaa64e525951502461",
    "finding.schema.json": "4444e0cfec3a32bb83b016172331072a369afb2699b093ad670dc36dfbcdd8f7",
    "gig-proposal.schema.json": "515f16368059c7d8d4bf88cb47d8fc0df63afc50a51e13c8c75601c013f134b3",
    "goal-graph.schema.json": "669115492bfed52f4738cb9cbbac626a10f80f6965da3d1f70eb20e4c2e264cf",
    "handoff-frontmatter.schema.json": "de27d69529ae7cce07063fb67dcecc48aff79012ef72c66f3ed077367b9bd09e",
    "report.schema.json": "e6c42cdf1787df06bb2ddd0169bf44ca62b9279b7f3c3bcfde97ddabe5b764d4",
    "review-bundle.schema.json": "ab60331eaf6095aa2c70690592f1b66769012aa6973a03e6cb4a1d36f904b531",
    "review-contract.schema.json": "d7cc23e267ce07e071138e62c65accba9fc0b64ff967880fa05bf5cc5a4626f1",
    "run-brief-frontmatter.schema.json": "481118d7c49f97d00c389f8f4d4216cc1baf6ff96e8c16c3343006ad019369e3",
    "run-details.schema.json": "c2388d917e08cfcc0860ecd3a20b389be4f434aadde6b21ffa18ee4d6457111f",
    "run-manifest.schema.json": "a14126ac4943e71980371eb215fbc191434cfb0fb2f2761259a0faabb36af24f",
    "trace.schema.json": "d1b5a8970e26b753fbbb8275cd30321a3fe0bc2bb56c4443c6d6306b42ca29ef",
}


def main() -> int:
    schema_root = resources.files("gigai.schemas")
    found = {
        item.name
        for item in schema_root.iterdir()
        if item.name.endswith(".schema.json")
    }
    expected = set(EXPECTED_SHA256)
    if found != expected:
        missing = sorted(expected - found)
        additional = sorted(found - expected)
        raise SystemExit(
            f"schema resource set mismatch: missing={missing}, additional={additional}"
        )

    for name, expected_digest in sorted(EXPECTED_SHA256.items()):
        payload = schema_root.joinpath(name).read_bytes()
        json.loads(payload)
        actual_digest = hashlib.sha256(payload).hexdigest()
        if actual_digest != expected_digest:
            raise SystemExit(
                f"schema digest mismatch for {name}: "
                f"expected {expected_digest}, got {actual_digest}"
            )

    print("verified 15 installed GigAI schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
