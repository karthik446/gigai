"""0.1.10.10 FK: a contact check reads the text of what the product returned, never its ids.

PR run 37243515778 (ubuntu, Python 3.11, shard 1) failed ``test_master_init_keeps_the_summary_...`` on
``assert not [value for value in CONTACT if value in json.dumps(shown)]`` with ``['555']``: the master held no
contact data, its revision id held ``555``. ``tests/support/contact_scan.contact_values_in`` is the check that
cannot do that. Read here on a master shaped as ``gigai scout resume master show --json`` prints it (invented
lines; fictional ``example.test`` / ``555-01xx`` values).
"""

from __future__ import annotations

import copy
import json
import random
import uuid

import pytest

from tests.support.contact_scan import contact_values_in

CONTACT = ("Jordan", "example.test", "555", "linkedin", "Example State")
PHONE = "(555) 010-0142"
EMAIL = "jordan.example@example.test"


def _master() -> dict:
    """A clean master whose every id, digest and timestamp holds ``555``."""

    return {
        "content_sha256": "sha256:b789d31bdf3db2e70d52b2a2156a0219d618587c3594696c0f4a59f9bd3c5550",
        "counts": {"entries": 1, "ids": 3, "items": 2},
        "entries": [
            {
                "bullets": ["b-555f05"], "end": None, "heading": "Quillmark Systems", "id": "r-b74555", "ongoing": True, "order": 2,
                "section": "experience", "source": None, "start": 2020, "sublines": ["Staff Platform Engineer | Mar 2020 - Present"],
            },
        ],
        "format": 1,
        "items": [
            {
                "backed": [], "entry_id": None, "id": "sum-555dbb", "kind": "summary", "mark": "fa6f555eaf0381ed", "order": 1,
                "section": "summary", "tags": [], "text": "Platform engineer with 11 years of experience running Kubernetes control planes.",
            },
            {
                "backed": ["answer:skill:helm"], "entry_id": "r-b74555", "id": "b-555f05", "kind": "bullet", "mark": "efd927e959e05553",
                "order": 3, "section": "experience", "tags": ["reliability"],
                "text": "Runs the control plane for 3,200 customer clusters across three clouds at 99.98% availability.",
            },
        ],
        "parent_revision": "revision_19255523-0cc1-4c6e-b655-ddb4ea275f1c",
        "record_id": "record_db377c00-5555-495d-bfd0-5a2890ac5210",
        "revision": 2,
        "revision_id": "revision_2e315558-53d0-4925-be4a-d01b35ba0555",
        "sections": ["summary", "experience"],
        "updated_at": "2026-10-04T23:22:40.555208Z",
        "written_by": "operator",
    }


def _substring_check(master: dict) -> list[str]:
    """The check the failing test made: each value against the whole of the JSON text."""

    return [value for value in CONTACT if value in json.dumps(master)]


def test_an_id_a_digest_or_a_timestamp_that_holds_555_is_not_contact_data() -> None:
    master = _master()
    assert _substring_check(master) == ["555"], "the old check calls this clean master a leak"
    assert contact_values_in(master, CONTACT) == []
    # Each of them alone, and a timestamp with milliseconds, where 555 stands between a point and the Z.
    for key in ("content_sha256", "parent_revision", "record_id", "revision_id", "updated_at"):
        assert "555" in master[key] and contact_values_in({key: master[key]}, CONTACT) == [], key
    assert contact_values_in({"updated_at": "2026-10-04T23:22:40.555Z", "created_at": "2026-10-04T23:22:40.555+00:00"}, CONTACT) == []
    assert contact_values_in(["b-555f05", "r-b74555", "fa6f555eaf0381ed", "apv_0c555d3e53d04925be4ad01b35ba0555"], CONTACT) == []


def test_no_fresh_uuid_or_timestamp_is_ever_contact_data() -> None:
    """What a run makes new, 20,000 times over: the old check trips on some of them, this one on none."""

    chance = random.Random(555)
    tripped = 0
    for _ in range(20_000):
        made = {
            "record_id": f"record_{uuid.UUID(int=chance.getrandbits(128), version=4)}",
            "revision_id": f"revision_{uuid.UUID(int=chance.getrandbits(128), version=4)}",
            "updated_at": f"2026-10-04T23:22:40.{chance.randrange(10**6):06d}Z",
        }
        tripped += "555" in json.dumps(made)
        assert contact_values_in(made, CONTACT) == [], made
    assert tripped > 100, "about one in 80: the old check fails a clean master that often"


@pytest.mark.parametrize(
    ("path", "leak", "found"),
    [
        (("items", 0, "text"), f"Platform engineer. Call {PHONE}.", ["555"]),
        (("items", 1, "text"), f"Reach me at {EMAIL} for the runbooks.", ["example.test"]),
        (("items", 1, "tags"), ["reliability", "linkedin.com/in/jordan-example"], ["linkedin"]),
        (("items", 1, "backed"), [f"answer:{EMAIL}"], ["example.test"]),
        (("entries", 0, "heading"), "Jordan Example", ["Jordan"]),
        (("entries", 0, "sublines"), ["Northfield, Example State", "Staff Platform Engineer | Mar 2020 - Present"], ["Example State"]),
        (("entries", 0, "sublines"), [f"{EMAIL} | {PHONE} | linkedin.com/in/jordan-example"], ["example.test", "555", "linkedin"]),
        (("written_by",), "Jordan Example", ["Jordan"]),
        # Under a key that names an id: the value is not an id, so it is read.
        (("revision_id",), EMAIL, ["example.test"]),
        (("record_id",), f"record_{PHONE}", ["555"]),
        (("updated_at",), "555-010-0142", ["555"]),
        (("content_sha256",), "sha256:555", ["555"]),
        # An id inside a sentence is still read as text, and the phone number beside it is found.
        (("items", 0, "text"), f"Saved as revision_2e315558-53d0-4925-be4a-d01b35ba0b56, call {PHONE}.", ["555"]),
    ],
)
def test_a_contact_value_is_found_wherever_a_master_holds_text(path: tuple, leak: object, found: list[str]) -> None:
    master = _master()
    holder = master
    for step in path[:-1]:
        holder = holder[step]
    holder[path[-1]] = leak
    assert contact_values_in(master, CONTACT) == found


def test_a_key_is_read_and_a_number_is_not() -> None:
    master = _master()
    assert contact_values_in({**master, EMAIL: True}, CONTACT) == ["example.test"]
    assert contact_values_in({**master, "nested": {"deep": [{"note": ("x", f"call {PHONE}")}]}}, CONTACT) == ["555"]
    # 555 as a count is a number of the master, never a phone number.
    assert contact_values_in({**master, "counts": {"items": 555}, "seconds": 0.555}, CONTACT) == []
    # The values are given back in the order asked for, each once.
    both = copy.deepcopy(master)
    both["items"][0]["text"] = both["items"][1]["text"] = f"{PHONE} {EMAIL}"
    assert contact_values_in(both, ("555", "Jordan", "example.test")) == ["555", "example.test"]
