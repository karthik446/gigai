"""Verify the G27 discovery-manifest path from an installed distribution."""

from __future__ import annotations

from pathlib import Path
import tempfile

from gigai.canonical import parse_json_bytes
from gigai.config import load_config
from gigai.lifecycle import persist_discovery_manifest, start_interview
from gigai.question_generation import G27_DISCOVERY_PROMPT, generate_model_questions
from gigai.setup import build_config, run_setup
from gigai.target_binding import initialize_target
from gigai.validators import SCHEMA_NAMES, validate_serialized_contract


def main() -> int:
    if len(SCHEMA_NAMES) != 31:
        raise SystemExit(f"installed G27 schema inventory is {len(SCHEMA_NAMES)}, expected 31")
    with tempfile.TemporaryDirectory(prefix="gigai-g27-installed-") as directory:
        root = Path(directory)
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
        initialize_target(home_root=home, requested_target=target)
        started = start_interview(
            home_root=home,
            requested_target=target,
            name="installed-g27",
            request="Build a reusable repository review Gig.",
            reference_paths=(),
        )
        config = load_config(home)
        session = generate_model_questions(
            config=config,
            model_target="offline-default",
            session=started.session,
            reference_bytes={},
            prompt_name=G27_DISCOVERY_PROMPT,
        )
        persist_discovery_manifest(
            start=started,
            session=session,
            config=config,
            model_target="offline-default",
            reference_bytes={},
        )
        manifest_path = started.workpad / "manifests/gig-discovery-manifest.json"
        manifest_bytes = manifest_path.read_bytes()
        report = validate_serialized_contract(
            "gig-discovery-manifest.schema.json", manifest_bytes
        )
        if not report.valid:
            raise SystemExit(f"installed G27 manifest failed validation: {report.as_dict()}")
        manifest = parse_json_bytes(manifest_bytes)
        if not isinstance(manifest, dict) or len(manifest["question_rounds"][1]["questions"]) != 3:
            raise SystemExit("installed G27 replay did not retain the dynamic question round")
        if (started.workpad / "manifests/gig-proposal.json").exists():
            raise SystemExit("G27 discovery replay created proposal authority")
    print("verified installed GigAI G27 adaptive discovery manifest")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
