"""CLI provenance must describe the invocation, not its embedding process."""

import click
import pytest
from click.testing import CliRunner

from gigai.cli import InvocationGroup, _invocation_argv


@pytest.mark.parametrize("host_argv", [["pytest"], ["pytest", "-q", "unrelated.py"]])
def test_embedded_cli_captures_exact_arguments(monkeypatch, host_argv):
    monkeypatch.setattr("sys.argv", host_argv)
    captured = []

    @click.group(cls=InvocationGroup)
    def command():
        pass

    @command.command()
    @click.option("--name")
    def run(name):
        captured.append(_invocation_argv())

    runner = CliRunner()
    for name in ("first value", "second value"):
        args = ["run", "--name", name]
        result = runner.invoke(command, args, prog_name="gigai")
        assert result.exit_code == 0, result.output
        assert captured[-1] == ("gigai", *args)
