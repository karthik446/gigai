"""Contract tests every scaffolded gig starts with. `gigai gig check` runs the same checks."""

from __future__ import annotations

import json
import tomllib
from importlib import resources
from importlib.metadata import entry_points

from click.testing import CliRunner

PKG = "gigai_baseline_behavior"
NAME = "baseline-behavior"


def manifest() -> dict:
    return tomllib.loads(resources.files(PKG).joinpath("gig.toml").read_text(encoding="utf-8"))


def test_manifest_names_match_entry_point() -> None:
    (ep,) = [e for e in entry_points(group="gigai.gigs") if e.name == NAME]
    m = manifest()
    assert m["gig"]["name"] == ep.name
    assert m["commands"]["group"] == ep.value


def test_group_help_and_describe() -> None:
    from gigai_baseline_behavior.cli import baseline_behavior

    assert baseline_behavior.name == NAME
    runner = CliRunner()
    assert runner.invoke(baseline_behavior, ["--help"]).exit_code == 0
    out = runner.invoke(baseline_behavior, ["describe"])
    assert out.exit_code == 0
    assert json.loads(out.output) == manifest()


def test_provides_are_real_commands() -> None:
    from gigai_baseline_behavior.cli import baseline_behavior

    for verb in manifest()["commands"]["provides"]:
        assert verb in baseline_behavior.commands
