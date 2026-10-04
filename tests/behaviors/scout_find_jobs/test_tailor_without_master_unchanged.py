"""0.1.10.9 master P4: WITHOUT a master resume, a tailoring is stored byte for byte as it was before.

The master resume changes what a tailoring reads only when a master is stored.
This file pins the other half: on a home with no master, the stored tailored
resume (its JSON and its markdown) is exactly what the commit before P4
(``eabd3725``) stored for the same resume, posting, answers and model answer.

``GOLDEN`` holds the digests of those stored files, taken by running this very
file on that commit's source (it uses nothing P4 adds).  Three synthetic
cases of the tailor eval's own fixtures (``tests/evals/fixtures/tailor_cases.json``),
so that every path P4 touched is on the way: a resume that fits (``control``),
an answer that adds a Skills line (``helm_terse_answer``), and a 3-page resume
with old roles (``over_long``: the old-role trim, the whole-role cut and its
record).  The model copies the resume (``tests.support.tailor_cases``).

What cannot be the same twice is taken out before the digest and nothing
else: the clock is frozen (which roles are "old" depends on the year), and
the home's own paths, the resume record's ids and the answers' revision ids
are replaced by placeholders.
"""

from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path

import pytest

from gigai.canonical import digest_imported_bytes
from gigai.scout.experience_answers import record_answer
from gigai.scout.find_jobs.assess_contracts import AssessJobInput
from gigai.scout.tailored_resume import TailorRequest, run_tailored_resume

from tests.support.scout_profile_fixtures import build_gig_with_resume
from tests.support.tailor_cases import CASES, copy_everything, install_scripted_model, ollama_config

#: sha256 of the normalized stored JSON and of the stored markdown, as ``eabd3725`` stores them.
GOLDEN: dict[str, tuple[str, str]] = {
    "control": (
        "sha256:51f997bc2c97c2583c875c6eda28645e5ae855f2e283a131cd9eb548a7a8ab2e",
        "sha256:4ece1e982a259e3b2d004b486d21f8cccc4b0af466ad2dc332fdd9e81a3489e8",
    ),
    "helm_terse_answer": (
        "sha256:f00c4f4342cb6c2c0122af3c9773db810e1cbf6d51d33f2816cc5d4fe79f195c",
        "sha256:6faa69d073dd991179abb5aa6ddd3f31b38c11f19102d6bd788b9ba820187a82",
    ),
    "over_long": (
        "sha256:4a3e6dbf8a39bdb732e2c827862e14a5d4ae099d8b2e3224e2176fa78f546875",
        "sha256:e2023da068e589dc654ac7616b9eb380f5322a4b2146cb5b65fed47d5aa2af59",
    ),
}


class _Frozen(datetime):
    """The clock a tailoring reads, stopped: 2026-10-04, noon UTC."""

    @classmethod
    def now(cls, tz=None):  # noqa: ANN001, ANN206 - datetime.now's own signature
        return datetime(2026, 10, 4, 12, 0, 0, tzinfo=tz)


def _normalized(on_disk: dict) -> bytes:
    """The stored JSON without what differs between two homes: paths, the resume record's ids, the answers' revision ids."""

    value = json.loads(json.dumps(on_disk))
    for key in ("stored_path", "markdown_path"):
        assert isinstance(value[key], str) and value[key]
        value[key] = "<path>"
    resume = value["resume"]
    assert resume["profile_id"] and resume["pinned"]["record_id"] and resume["pinned"]["revision_id"]
    resume["profile_id"] = "<profile>"
    resume["pinned"]["record_id"], resume["pinned"]["revision_id"] = "<record>", "<revision>"
    for answer in value["sources"]["answers"]:
        assert answer["revision_id"]
        answer["revision_id"] = "<revision>"
    return json.dumps(value, indent=2, sort_keys=True).encode("utf-8")


def _tailor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case_id: str) -> tuple[str, str]:
    case = CASES[case_id]
    monkeypatch.setattr("gigai.scout.tailored_resume.datetime", _Frozen)
    fx = build_gig_with_resume(tmp_path, resume_text=case["resume"].encode("utf-8"))
    for answer in case["answers"]:
        record_answer(
            home_root=fx.home_root, requested_target=fx.target, gig_id=fx.resolved.gig_id,
            question_id=answer["question_id"], prompt=answer["prompt"], answer=answer["answer"],
        )
    install_scripted_model(monkeypatch, [copy_everything(case["resume"])])
    response = run_tailored_resume(
        TailorRequest(job=AssessJobInput(job_text=case["posting"], title=case["title"], company=case["company"])),
        home_root=fx.home_root, target=fx.target, config=ollama_config(fx.home_root),
    )
    on_disk = json.loads(Path(response.stored_path).read_text(encoding="utf-8"))
    markdown = Path(response.markdown_path).read_bytes()
    assert markdown.decode("utf-8") == on_disk["markdown"]
    return digest_imported_bytes(_normalized(on_disk)), digest_imported_bytes(markdown)


@pytest.mark.parametrize("case_id", sorted(GOLDEN))
def test_without_a_master_the_stored_tailoring_is_byte_for_byte_what_it_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case_id: str) -> None:
    assert _tailor(tmp_path, monkeypatch, case_id) == GOLDEN[case_id]


def test_the_cases_cover_the_length_rule_and_an_added_skill(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The pin is only worth what its cases exercise: say so in a test, so a fixture change cannot hollow it out."""

    monkeypatch.setattr("gigai.scout.tailored_resume.datetime", _Frozen)
    case = CASES["over_long"]
    fx = build_gig_with_resume(tmp_path, resume_text=case["resume"].encode("utf-8"))
    install_scripted_model(monkeypatch, [copy_everything(case["resume"])])
    response = run_tailored_resume(
        TailorRequest(job=AssessJobInput(job_text=case["posting"], title=case["title"], company=case["company"])),
        home_root=fx.home_root, target=fx.target, config=ollama_config(fx.home_root),
    )
    on_disk = json.loads(Path(response.stored_path).read_text(encoding="utf-8"))
    length = on_disk["result"]["length"]
    assert length["status"] == "cut" and length["cut"] and length["trimmed"], "over_long must exercise the role cut and the old-role trim"
    # Nothing of the master path is in a tailoring made without a master.
    assert "selection" not in on_disk and "master" not in on_disk["sources"]
    assert '"item_id"' not in Path(response.stored_path).read_text(encoding="utf-8")
