"""0.1.10.7-fix3: routing sha256 through gigai.canonical must not change any persisted digest value (pinned from the pre-change code)."""

from __future__ import annotations

from gigai.scout import postings
from gigai.scout.pipeline import steps


def test_pipeline_and_posting_digests_keep_their_pre_canonical_values() -> None:
    assert steps._digest("rank", "résumé", 1, {"b": 2, "a": [None]}) == "sha256:fdc2aa431520f4d8aa66a5bb95ed1e9ae1a44736fc2122c21864da9be47cf074"
    assert steps._bytes_digest(b"\x00\xffabc") == "sha256:24397706eb32f8691116fe4728d18eda7eacc40925e0ae26a5780cd8b8b13f80"
    assert steps._job_key("https://x.test/j?é=1") == "c63167ae96633b4d82d3d76bf3fd055121a89f138c15020ac1fb988aaa63fc5f"
    assert postings._digest("a", {"z": 1}, "ü") == "sha256:c7cc6d68c82f6176997d8bd1b3ddc1fd3a1b1ab07cdda698a36c912136f0e6be"
