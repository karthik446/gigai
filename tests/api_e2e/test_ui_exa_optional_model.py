"""uat-bug-033: the Settings toggle for the optional Exa source, under node.

``ui/src/exaSourceModel.js`` is pure JavaScript, so it runs under the system
``node`` like ``test_ui_wizard_finish_model.py`` (LOUD skip without node). What
lives in JSX is checked statically by reading the source.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from gigai.scout.find_jobs.api import static as static_module

UI_SRC = Path(static_module.__file__).resolve().parents[2] / "ui" / "src"

NODE_SCRIPT = """
const input = JSON.parse(process.argv[1]);
const model = await import(input.modelUrl);
console.log(JSON.stringify({
  label: model.EXA_TOGGLE_LABEL,
  onNoKey: model.exaKeyHint({ exaOn: true, keys: { exa: false } }),
  onWithKey: model.exaKeyHint({ exaOn: true, keys: { exa: true } }),
  offNoKey: model.exaKeyHint({ exaOn: false, keys: { exa: false } }),
  keysUnknown: model.exaKeyHint({ exaOn: true, keys: null }),
  isOn: model.exaIsOn({ sources: { exa: true, ats: true } }),
  isOff: model.exaIsOn({ sources: { exa: false, ats: true } }),
  noSources: model.exaIsOn({}),
  noConfig: model.exaIsOn(null),
}));
"""


@pytest.fixture(scope="module")
def out() -> dict:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the Settings Exa toggle model was not run")
    completed = subprocess.run(
        [node, "--input-type=module", "-e", NODE_SCRIPT, "--", json.dumps({"modelUrl": (UI_SRC / "exaSourceModel.js").as_uri()})],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_the_toggle_says_what_it_does(out: dict) -> None:
    assert out["label"] == "Also search the open web with Exa (needs an Exa key)"


def test_the_key_hint_shows_only_when_exa_is_on_and_the_key_is_not_set(out: dict) -> None:
    assert out["onNoKey"] == {"text": "Exa key not set", "command": "gigai secrets add exa"}
    assert out["onWithKey"] is None
    assert out["offNoKey"] is None
    assert out["keysUnknown"] is None


def test_the_toggle_reads_the_saved_source(out: dict) -> None:
    assert out["isOn"] is True
    assert out["isOff"] is False
    assert out["noSources"] is False
    assert out["noConfig"] is False


def test_settings_renders_the_toggle_and_saves_through_the_sources_route() -> None:
    settings = (UI_SRC / "views" / "SettingsView.jsx").read_text(encoding="utf-8")
    assert "ExaSourceToggle" in settings
    toggle = (UI_SRC / "components" / "ExaSourceToggle.jsx").read_text(encoding="utf-8")
    assert "putConfigSources({ exa: next })" in toggle
    assert 'type="checkbox"' in toggle
    api = (UI_SRC / "api.js").read_text(encoding="utf-8")
    assert '"PUT", "/api/config/sources"' in api


def test_no_wizard_screen_asks_for_an_exa_key() -> None:
    for path in sorted((UI_SRC / "wizard").glob("*.jsx")):
        text = path.read_text(encoding="utf-8")
        assert "secrets add exa" not in text, path.name
        assert "Exa key" not in text, path.name
