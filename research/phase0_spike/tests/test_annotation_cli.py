from click.testing import CliRunner

from ..annotation_cli import review


def test_help_is_derived_from_annotations() -> None:
    result = CliRunner().invoke(review, ["--help"])

    assert result.exit_code == 0
    assert "TARGET" in result.output
    assert "--kind [code|design|plan|db-schema]" in result.output
    assert "--challenge / --no-challenge" in result.output
    assert "--references / --no-references" in result.output
    assert "--context PATH" in result.output
    assert "--max-findings INTEGER" in result.output
    assert "Additional context path; repeatable." in result.output


def test_valid_values_are_parsed_and_validated() -> None:
    result = CliRunner().invoke(
        review,
        [
            "LM-123",
            "--kind",
            "code",
            "--workspace",
            "../service",
            "--challenge",
            "--no-references",
            "--context",
            "one.md",
            "--context",
            "two.md",
            "--max-findings",
            "12",
        ],
    )

    assert result.exit_code == 0, result.output
    assert '"target": "LM-123"' in result.output
    assert '"challenge": true' in result.output
    assert '"references": false' in result.output
    assert '"one.md"' in result.output
    assert '"two.md"' in result.output


def test_pydantic_constraints_fail_before_workflow_execution() -> None:
    result = CliRunner().invoke(
        review,
        ["LM-123", "--kind", "code", "--max-findings", "0"],
    )

    assert result.exit_code != 0
    assert "greater than or equal to 1" in result.output
