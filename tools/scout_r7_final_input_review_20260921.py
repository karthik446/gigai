"""Bounded, offline R7 input and subprocess verification probes.

This helper is intentionally disposable evidence tooling for the 2026-09-21
independent review.  It invokes the public Click commands with synthetic
temporary workpads and performs a source-tree AST inventory; it never runs a
provider/model or touches the checkout's .gigai state.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import tempfile
import uuid

from click.testing import CliRunner

from gigai.cli import cli
from gigai.lifecycle import approve_offline, create_offline
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target


def _fixture(root: Path) -> tuple[Path, Path, str]:
    home = root / "home"
    target = root / "target"
    target.mkdir()
    run_setup(
        build_config(
            home_root=home,
            workpad_root=root / "workpads",
            editor_argv=("/usr/bin/true",),
            open_with_target=False,
        )
    )
    initialize_target(
        home_root=home,
        requested_target=target,
        uuid_factory=lambda: uuid.UUID("12345678-1234-4234-9234-123456789abc"),
    )
    values = iter(
        uuid.UUID(f"00000000-0000-4000-8000-{index:012x}")
        for index in range(1, 80)
    )
    created = create_offline(
        home_root=home,
        requested_target=target,
        name="r7-final-input-review",
        open_editor=False,
        uuid_factory=lambda: next(values),
    )
    approve_offline(
        home_root=home,
        requested_target=target,
        proposal_id=created.proposal_id,
        uuid_factory=lambda: next(values),
    )
    return home, target, created.gig_id


def _create(
    runner: CliRunner,
    home: Path,
    target: Path,
    gig_id: str,
    paths: tuple[Path, ...],
    artifact_class: str | None = None,
) -> dict[str, object]:
    args = [
        "run-plan",
        "create",
        "--gig",
        gig_id,
        "--class",
        "code_review",
        "--reason",
        "operator declared the supplied artifact class",
    ]
    if artifact_class is not None:
        args.extend(("--artifact-class", artifact_class))
    for path in paths:
        args.extend(("--input", str(path)))
    args.extend(("--home", str(home), "--target", str(target), "--json"))
    result = runner.invoke(cli, args)
    if result.exit_code != 0:
        raise AssertionError(f"create {artifact_class}: {result.output}")
    payload = json.loads(result.output)
    if not isinstance(payload, dict) or not isinstance(payload.get("plan"), dict):
        raise AssertionError(f"create {artifact_class}: malformed payload")
    return payload


def _public_input_probes() -> None:
    runner = CliRunner()
    with tempfile.TemporaryDirectory(prefix="gigai-r7-final-input-") as raw:
        root = Path(raw)
        home, target, gig_id = _fixture(root)
        cases = {
            "code": (("source.py", b"print('opaque')\n\xff"),),
            "structured_data": (("payload.json", b'{"answer":42}\n'),),
            "mixed": (
                ("source.py", b"print('mixed')\n"),
                ("payload.json", b'{"mixed":true}\n'),
            ),
        }
        for artifact_class, entries in cases.items():
            paths: list[Path] = []
            for filename, data in entries:
                path = root / f"{artifact_class}-{filename}"
                path.write_bytes(data)
                paths.append(path)
            payload = _create(
                runner, home, target, gig_id, tuple(paths), artifact_class
            )
            plan = payload["plan"]
            assert isinstance(plan, dict)
            sealed = plan["sealed_sources"]
            assert isinstance(sealed, list)
            inputs = [item for item in sealed if "/inputs/" in str(item.get("path"))]
            assert len(inputs) == len(entries)
            for ref, (_filename, data) in zip(inputs, entries, strict=True):
                assert isinstance(ref, dict)
                assert ref["size_bytes"] == len(data)
                assert ref["media_type"] == "application/octet-stream"
            if artifact_class == "code":
                shown = runner.invoke(
                    cli,
                    [
                        "run-plan",
                        "show",
                        str(plan["run_plan_id"]),
                        "--gig",
                        gig_id,
                        "--home",
                        str(home),
                        "--target",
                        str(target),
                        "--json",
                    ],
                )
                assert shown.exit_code == 0, shown.output
                shown_payload = json.loads(shown.output)
                assert shown_payload["plan"]["content_sha256"] == plan["content_sha256"]
                assert shown_payload["plan"]["sealed_sources"] == plan["sealed_sources"]
                print("historical_plan_show=PASS sealed_sources_unchanged")
            print(
                f"ordinary_{artifact_class}=PASS inputs={len(inputs)} "
                f"opaque_media=application/octet-stream"
            )

        real = root / "real.py"
        real.write_bytes(b"print('real')\n")
        link = root / "input-link.py"
        link.symlink_to(real)
        result = runner.invoke(
            cli,
            [
                "run-plan",
                "create",
                "--gig",
                gig_id,
                "--input",
                str(link),
                "--home",
                str(home),
                "--target",
                str(target),
                "--json",
            ],
        )
        assert result.exit_code != 0 and "run_plan_input_mismatch" in result.output
        print("ordinary_leaf_symlink=PASS run_plan_input_mismatch")

        baseline_bad = root / "requirements.py"
        baseline_bad.write_bytes(b"print('not a baseline')\n")
        result = runner.invoke(
            cli,
            [
                "run-plan",
                "approve-baseline",
                "--gig",
                gig_id,
                "--input",
                str(baseline_bad),
                "--confirm",
                "--home",
                str(home),
                "--target",
                str(target),
                "--json",
            ],
        )
        assert result.exit_code != 0 and "requirements_baseline_invalid" in result.output
        print("baseline_nontext=PASS requirements_baseline_invalid")

        baseline = root / "requirements.md"
        baseline.write_bytes(b"# Requirements\n")
        approved = runner.invoke(
            cli,
            [
                "run-plan",
                "approve-baseline",
                "--gig",
                gig_id,
                "--input",
                str(baseline),
                "--confirm",
                "--home",
                str(home),
                "--target",
                str(target),
                "--json",
            ],
        )
        assert approved.exit_code == 0, approved.output
        approval = json.loads(approved.output)["approval"]["approval_id"]
        subject = root / "subject.py"
        subject.write_bytes(b"print('subject')\n")
        result = runner.invoke(
            cli,
            [
                "run-plan",
                "create",
                "--gig",
                gig_id,
                "--review-subject",
                str(subject),
                "--requirements-baseline-approval",
                approval,
                "--home",
                str(home),
                "--target",
                str(target),
                "--json",
            ],
        )
        assert result.exit_code != 0 and "review_input_roles_invalid" in result.output
        print("review_subject_nontext=PASS review_input_roles_invalid")


def _subprocess_ast_inventory() -> None:
    calls = 0
    violations: list[str] = []
    shell_true: list[str] = []
    for path in sorted(Path("src/gigai").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not (
                isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "subprocess"
                and node.func.attr == "run"
            ):
                continue
            calls += 1
            shell = next((item for item in node.keywords if item.arg == "shell"), None)
            if shell is not None and isinstance(shell.value, ast.Constant) and shell.value.value is True:
                shell_true.append(f"{path}:{node.lineno}")
            argv = node.args[0] if node.args else None
            if not isinstance(argv, ast.List) or shell is None or not (
                isinstance(shell.value, ast.Constant) and shell.value.value is False
            ):
                violations.append(f"{path}:{node.lineno}")
    print(f"subprocess_run_call_count={calls}")
    print(f"subprocess_shell_false_violations={violations}")
    print(f"subprocess_shell_true_sites={shell_true}")
    if violations or shell_true:
        raise AssertionError("subprocess shell inventory is unsafe")


def main() -> None:
    _public_input_probes()
    _subprocess_ast_inventory()
    print("overall=PASS")


if __name__ == "__main__":
    main()
