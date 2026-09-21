"""Verify frozen schema resources from an installed GigAI distribution."""

from __future__ import annotations

import hashlib
from importlib import resources
import json


EXPECTED_SHA256 = {
    "scout-public-import-input.schema.json": "074aba64851bd1578e0fdee0bdba3629539b4a0ab5bb71beea191fc578eee374",
    "scout-public-import-progress.schema.json": "1c27e5f098de39c27f6014c5bcbce6677d0e2680742671c9bf338c0b4807d8ef",
    "application-event.schema.json": "310347dea9d43f0ba496c8a9c86abf41108f98eb2e7b1e940ce5ac71f215bd54",
    "external-recording-run-v2.schema.json": "6ca64700fd31945de68c138a5f4f6b739077be19da5cd340e6545fa1ee3a862e",
    "external-recording-plan-v2.schema.json": "e341a79cf759505ced0833b88ab27ccb3e8ee79f2f92990f0204984fe9fadaf0",
    "external-recording-invocation-v2.schema.json": "b6177225a1505a3dbd9a2047b1b1f015ea29c6f21a42c52e086047a539df929a",
    "external-recording-checkpoint-v2.schema.json": "b4ba7ca89ded2151d8b1ea4c78e4f3ac98a636480068268d920a28081ddec084",
    "external-recording-receipt-v2.schema.json": "d4cac228be408cac1ea13d6c7222911555cc8ce077f43e4be18ce21e515ee4ca",
    "native-record-content.schema.json": "cfde09a97b395f50fbeeedfccb2d8ca7c01be699208e2f26842418c482cb1dfe",
    "external-recording-checkpoint.schema.json": "d4c7cefa5fbf64f43b8342d5951216e991d37bee6c51cd26b441675a55f31d5f",
    "external-recording-invocation.schema.json": "87074566f311913b4aea92a3700dd3833d83220c25c5f2650571f85074d27a8a",
    "external-recording-plan.schema.json": "2961b6eddfee7bf966bf49d9fbdd2fc144d5a76275cebb32ad49541b3396c2bd",
    "external-recording-receipt.schema.json": "70eea424969cac96800b72ffda7229eaefaad04d7f84428e431f926fbd46d60b",
    "external-recording-run.schema.json": "03ea51f28622862799f4cfa3ab9a2162a4a4c89985b818a5d672459fad3210d8",
    "addressed-artifact.schema.json": "93a3a991de2fec812beb42c9b25760504ab5a6229bb13edbdda30998c141bc90",
    "adjudication.schema.json": "9b6d3f489dcff15b510e4c041be5bcef0afcd10d6e9d584a21ef79c9842f49fe",
    "active-gig-version.schema.json": "634af1729f4dbfe1f8564fd9d31b6c5cb8c56a2e1c7b80393e59ae877f76f5e1",
    "active-gig-version-v2.schema.json": "95220e21bd6e6b0eb26eee9229b00d9fee94a172b563cce21d7176ff72089178",
    "gig-graph-set.schema.json": "82fffb4f786bdef460a4f096dc3fc4f909f970ecf6c572d46b9a18538cb62174",
    "graph-selection-record.schema.json": "bfd0ca7985d8555d7ff8b423158b57ab463b630bb3fa7e6dde173961b39015ba",
    "graph-selection-record-v2.schema.json": "cc3245839389a6c29e96c3829a21b2d596de92b25be58601bf09fb6db38de3c5",
    "capability-installation.schema.json": "c21641988e728cd94a8617a994ec1e3f5ffa9ae38ca020a2f7406916d6d083c0",
    "capability-manifest.schema.json": "2d36b8e0552c810f1ec17e50d4edbc68be39cc1572c0f535073bf7f59731b5a7",
    "capability-review-decision.schema.json": "fcb3129c06166aaae657adcc84a5f53459100b9fbf1b8fb8c9bc99e5d9aade15",
    "capability-successor-binding.schema.json": "392422209e81fc86bb6259afda18ffa0d2f99c94df496469d4310c4afe4a2bf8",
    "common.schema.json": "9b872c605c49cafbe25cfe5b9ea295d4d8c7b8cc0eab1d0eb1d65f09da59a0e4",
    "feedback.schema.json": "c89cda74feb86d34448a3e8afbfcded2554e3c077622ecfaa64e525951502461",
    "finding.schema.json": "4444e0cfec3a32bb83b016172331072a369afb2699b093ad670dc36dfbcdd8f7",
    "gig-builder-session.schema.json": "40e8042c69c33cf8fb763ff9a3074e9118c40d7c5e2711e6c23497a86b16b247",
    "gig-discovery-manifest.schema.json": "6a3618eed54963d85e42a68bd28976f291e4003a00f8aa9291066229b5870ca8",
    "gig-package.schema.json": "921e2a481cdb61103f91cad6045e860a432101af50cf3a5b14e4108d8eda4e04",
    "gig-proposal.schema.json": "515f16368059c7d8d4bf88cb47d8fc0df63afc50a51e13c8c75601c013f134b3",
    "gig-proposal-v2.schema.json": "fe1e712aa86b83f60b48de95bfbedbe5ff411e4427ebfb0f85b7b6fc9b640cf2",
    "goal-graph.schema.json": "669115492bfed52f4738cb9cbbac626a10f80f6965da3d1f70eb20e4c2e264cf",
    "handoff-frontmatter.schema.json": "126e608ed9e9bfdcf2fb7fad1514005f8ca886fdfaf30522d93234a02d3a8247",
    "improvement-manifest.schema.json": "7055101d0a11cdb8aa1563056c0f22a655b7088fdd41f51997e655af3e505e71",
    "learning-record.schema.json": "ffc112c25fb0984d09d076b25756b162f64b042675ec62cebca93c38a77de608",
    "gig-comparison.schema.json": "3e1c281fcf9c8fb79076073a2104151f7fc445eded2e31eee8439fff95c038be",
    "gig-occurrence.schema.json": "9620157b7d028534968347c9cbb7b133e4521bfcb353ec3a2f8e70b7a8e60364",
    "model-exchange.schema.json": "d0f57224c2c75fa1e140d380810fe92fc381e619f8105954ce9989a27911501c",
    "model-invocation.schema.json": "756ca9eb7a746e3f0b6700b028c4807ed98050e15df29d182aeed73335e51bd6",
    "model-invocation-v2.schema.json": "6037f3fd13b5572fdbd8554de30f9d4f99ec76e7fe7d5fb343335259ec21e940",
    "model-invocation-v3.schema.json": "277cbb03a8e96df956dcc8047db2a4a94dce3b7385362351e2accd6879e2830a",
    "scout-proposal-revision.schema.json": "fe4681b55f08d341ce650191ff3ac4cc0912ca5de40efa23f4403fbcb44088d1",
    "scout-answer-association.schema.json": "025a7705720a8cb9c41fb336d37c5a7f323e4047c3fd13ecd18305e4bbd75e11",
    "scout-proposal-discovery-job.schema.json": "801aa28169e4539ede39ef6659f6a24e5cd6460b2f1ad9c94bebd0379d402067",
    "scout-tailor-selection-v2.schema.json": "8d83684e99e3779466b43bf9bdd39cac9386688497f0dec2bb8fd4f70b5a46c7",
    "scout-document-revision-v1.schema.json": "9f9c4aa72fd9c5850903decf7077c2014483d8385305f9ef824e23e467bde22b",
    "scout-document-selection-v1.schema.json": "855bd05694f2b71532919bcd2b617d7ee705e40021ccb862795988e009e51549",
    "scout-document-selection-v2.schema.json": "cadf8ac07149fdc9ef8f0f9ec886c0fb81df3c5b3e2aefe830e4d24e1135a9f3",
    "proposal-interview.schema.json": "980ce753e050f8cb953957075b2f2c7591aaf88c8f0aa7088a622ca5c7d6a8da",
    "proposal-draft-manifest.schema.json": "cbc0fc6d3cafbdea3bafad0002302839dc3b7b08a2f98e6214728e3aacfa1a1a",
    "report.schema.json": "e4f952561b484d49376ae39e279f197b67527405f38e673d21bc5c655cba48d4",
    "review-bundle.schema.json": "ab60331eaf6095aa2c70690592f1b66769012aa6973a03e6cb4a1d36f904b531",
    "review-contract.schema.json": "d7cc23e267ce07e071138e62c65accba9fc0b64ff967880fa05bf5cc5a4626f1",
    "review-input-record.schema.json": "815698e6374000ae560de5554135a41e04693ab3e02d8bafbb41f333545cf040",
    "requirements-baseline-approval.schema.json": "781f9b621f9a0e75d90cfd2f32c8238f9808b3c8b5b0d28c28722352418001fb",
    "provider-review-closeout-receipt.schema.json": "49d04d92e1b35c1aebe06323f9bee6f5bce47b2fbb13b06ccb7d178b3874086a",
    "scout-operation-receipt.schema.json": "a387d8d258dda7f86612e52b4698990062de8ae5ee846f4d3f154a91427e1bcd",
    "template-instance-binding.schema.json": "23b99113a000faabbce21c2b97e8734ea8baec2abbc887f5d6e1f1321d12ed40",
    "reference-record.schema.json": "c4073840184179b71561efadeac20e02ff089024e7ea917dac44bae876fde90e",
    "run-input-record.schema.json": "d11f5e7550d7d97832243dab4e325ffc0d7075caa3eab602051c69587307c3d7",
    "private-record-revision.schema.json": "b60c3998e00f0d2c234a2af5a79573a9d7f0b800acc6dfc74ca7808d7e127674",
    "workpad-layout.schema.json": "f41fbeecfcc4febdd0efb3684b6c27bbb6aa03e7e3d9e6e9b64cce649936fcd6",
    "run-brief-frontmatter.schema.json": "481118d7c49f97d00c389f8f4d4216cc1baf6ff96e8c16c3343006ad019369e3",
    "run-details.schema.json": "c2388d917e08cfcc0860ecd3a20b389be4f434aadde6b21ffa18ee4d6457111f",
    "run-manifest.schema.json": "a14126ac4943e71980371eb215fbc191434cfb0fb2f2761259a0faabb36af24f",
    "run-manifest-v2.schema.json": "22ecf518ec800da457fa1bf19749d8db6d58ea7abb776a1c076304fb4ef13ce8",
    "run-plan.schema.json": "0c3ba1cc9c6095e0468dc3b7476878d1ee55bf579b0ddc8fd46b1f7d82d3b1cb",
    "run-plan-v2.schema.json": "1e8b203c1ae2ac900f44c8d80b31228e243508a9021608857a8bbe1a9f8f85ea",
    "review-loop.schema.json": "8d87e00d4b16e4c535ce7c9645a5a9ef8a34f2ddc480b6616dcebba5e903f90b",
    "role-reference.schema.json": "dd29599c5d494480c6e9e52df2ebd815fdbc4bb9ccee897ef96d660376cc50a7",
    "target-effect.schema.json": "ccdee728551d5e43958ca1faa0bdf923cc986d080678c89209864464212e1f42",
    "trace.schema.json": "d1b5a8970e26b753fbbb8275cd30321a3fe0bc2bb56c4443c6d6306b42ca29ef",
    "verification-record.schema.json": "2a02f52a8fadcf8be2d525c4bb20e9bb48e5b029928bb6b0ffce791d749c3070",
    "runtime-evaluation-pack.schema.json": "c43f43f8c1536c37d8bfcc6124709621a2e0c069da445c595cb0f12e7febe87c",
    "runtime-comparison.schema.json": "5d052380cda93a5921f14a5878a071c16cdbb2f1c3ed018ed4442d2cb1eed7bc",
    "runtime-comparison-attempt.schema.json": "ef6f345b5675f9fad7f1729a01c9b5208e04bfdf023e968a46626bb0cfe04dc1",
    "runtime-comparison-intent.schema.json": "ca6555c454a4193c5d34c5837e572e5c7ddf627d1cb3e7e29a20d3eb6a67c8b3",
    "scout-interview-preparation.schema.json": "a2e7548187730e41a6de490244b1f36955f2e7c97227d85090ddc40742b0217f",
    "scout-private-transfer-manifest.schema.json": "a06f2be2b90fe695fd57213168e7da648055d7ccb4f0830732ad4c7f92a7d040",
    "scout-definition-export-manifest.schema.json": "8ff08ba0e111e0d79d188407d0a75c83b670cf94f7637abf3b06caad8c6a5a05",
}

# Historical validators intentionally expose this frozen 64-resource identity;
# newer Scout resources are validated by name and digest above but are not part
# of SCHEMA_NAMES. Installed G20+ verifiers must check this identity, not only a
# count that could pass after an arbitrary replacement.
EXPECTED_LEGACY_SCHEMA_NAMES = (
    "application-event.schema.json",
    "addressed-artifact.schema.json",
    "adjudication.schema.json",
    "active-gig-version.schema.json",
    "active-gig-version-v2.schema.json",
    "capability-installation.schema.json",
    "capability-manifest.schema.json",
    "capability-review-decision.schema.json",
    "capability-successor-binding.schema.json",
    "common.schema.json",
    "feedback.schema.json",
    "finding.schema.json",
    "gig-builder-session.schema.json",
    "gig-comparison.schema.json",
    "gig-discovery-manifest.schema.json",
    "gig-occurrence.schema.json",
    "gig-proposal.schema.json",
    "gig-proposal-v2.schema.json",
    "gig-graph-set.schema.json",
    "graph-selection-record.schema.json",
    "graph-selection-record-v2.schema.json",
    "goal-graph.schema.json",
    "handoff-frontmatter.schema.json",
    "improvement-manifest.schema.json",
    "learning-record.schema.json",
    "model-exchange.schema.json",
    "model-invocation.schema.json",
    "proposal-interview.schema.json",
    "proposal-draft-manifest.schema.json",
    "gig-package.schema.json",
    "report.schema.json",
    "review-bundle.schema.json",
    "review-contract.schema.json",
    "review-input-record.schema.json",
    "requirements-baseline-approval.schema.json",
    "provider-review-closeout-receipt.schema.json",
    "private-record-revision.schema.json",
    "native-record-content.schema.json",
    "reference-record.schema.json",
    "run-input-record.schema.json",
    "scout-operation-receipt.schema.json",
    "template-instance-binding.schema.json",
    "workpad-layout.schema.json",
    "external-recording-invocation.schema.json",
    "external-recording-invocation-v2.schema.json",
    "external-recording-plan.schema.json",
    "external-recording-plan-v2.schema.json",
    "external-recording-run.schema.json",
    "external-recording-run-v2.schema.json",
    "external-recording-checkpoint.schema.json",
    "external-recording-checkpoint-v2.schema.json",
    "external-recording-receipt.schema.json",
    "external-recording-receipt-v2.schema.json",
    "run-plan.schema.json",
    "run-plan-v2.schema.json",
    "verification-record.schema.json",
    "run-brief-frontmatter.schema.json",
    "run-details.schema.json",
    "run-manifest.schema.json",
    "run-manifest-v2.schema.json",
    "review-loop.schema.json",
    "role-reference.schema.json",
    "target-effect.schema.json",
    "trace.schema.json",
)


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

    print(f"verified {len(EXPECTED_SHA256)} installed GigAI schemas")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
