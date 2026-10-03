"""0110-047: the Profiles view's Delete, run under node.

``ui/src/profileDeleteModel.js`` is pure JavaScript, so it runs under the system ``node``
(LOUD skip when absent); what lives in JSX is read statically.

Pinned: the default profile and the only profile are blocked before any call; any other profile
can be deleted; the confirm text names what happens (leaves the list, history stays and is
hidden, story bank untouched, which profile is selected next); the view wires Delete to
``DELETE /api/profiles/{id}``.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"
MODEL_JS = UI_SRC / "profileDeleteModel.js"

NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const m = await import(input.url);
const [dflt, other, gone] = input.profiles;
console.log(JSON.stringify({
  default: m.deleteBlockedReason(input.profiles, dflt),
  other: m.deleteBlockedReason(input.profiles, other),
  alone: m.deleteBlockedReason([other], other),
  onlyArchivedElse: m.deleteBlockedReason([other, { ...gone, state: "archived" }], other),
  none: m.deleteBlockedReason(input.profiles, null),
  confirm: m.deleteConfirmText(input.profiles, other),
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the profile delete model was not run")
    profiles = [
        {"profile_id": "p1", "label": "Backend", "is_default": True, "state": "active"},
        {"profile_id": "p2", "label": "Director", "is_default": False, "state": "active"},
        {"profile_id": "p3", "label": "Old", "is_default": False, "state": "active"},
    ]
    completed = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps({"url": MODEL_JS.as_uri(), "profiles": profiles})],
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert completed.returncode == 0, f"node failed (rc {completed.returncode}):\n{completed.stderr}"
    return json.loads(completed.stdout)


def test_the_default_and_the_only_profile_are_blocked(out: dict) -> None:
    assert "default profile" in out["default"] and "cannot be deleted" in out["default"]
    assert out["other"] is None
    assert out["alone"] == "This is the only profile; add another one first."
    assert out["onlyArchivedElse"] == "This is the only profile; add another one first."
    assert out["none"] == "No profile is selected."


def test_the_confirm_names_what_happens(out: dict) -> None:
    text = out["confirm"]
    assert 'Delete "Director"?' in text
    assert "leaves the profile list, the switcher and background searching" in text
    assert "stay in history, hidden from the Jobs list" in text
    assert "story bank and answers are not touched" in text
    assert 'Scout switches to "Backend".' in text


def test_the_view_calls_the_delete_route_after_a_confirm() -> None:
    view = (UI_SRC / "views" / "ProfilesView.jsx").read_text(encoding="utf-8")
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert 'request("DELETE", `/api/profiles/${encodeURIComponent(profileId)}`)' in api
    assert "deleteConfirmText(profiles, selected)" in view and "deleteProfile(selected.profile_id)" in view
    assert "setConfirmingDelete(" in view  # nothing is deleted before the confirm
