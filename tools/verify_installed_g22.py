"""Verify G22 create/approve through a freshly installed console script.

Original intent (as shipped in the tools/verify_installed_g22.py this file
replaces): prove that a freshly installed wheel can run an end-to-end G22
proposal-creation flow -- setup, target initialization, create a proposal,
answer its clarifying questions, approve it, and confirm no Run/authority was
created by mere approval -- using only the installed `gigai` console script.

Why this file was rewritten: the previous verifier drove `gigai create` with
`--reference PATH --request TEXT` and read a local interactive interview
HTTP server's questions/answers off `manifests/proposal-interview.json`
(started by `InterviewHTTPServer`, printing "GigAI local interview: <url>"
to stderr). That flow no longer exists on `create` in the shipped v0.1.7 CLI:

  - `gigai create` (src/gigai/cli.py:2277) now takes NAME plus an explicit
    `--invocation` JSON envelope (src/gigai/cli.py:2299-2304) and produces a
    proposal directly and deterministically (`create_offline`,
    src/gigai/cli.py:2362) -- there is no interview, no HTTP server, no
    question/answer loop, and no approval-over-HTTP for `create` any more.
  - `InterviewHTTPServer` (src/gigai/proposal_interview.py:627) is only
    reachable from `gigai internal improve` now (src/gigai/cli.py:2476);
    `create` never launches it (confirmed: only two call sites of
    `start_interview`/`InterviewHTTPServer` exist in src/gigai/cli.py, both
    under `internal improve`).
  - `--reference`/`--request` are not options of `create` any more (see
    `gigai create --help`); the closest current input path is the
    `--invocation` envelope's `input.proposal`/`input.intent` fields,
    validated by `load_invocation_bytes`/`parse_invocation`
    (src/gigai/invocation.py).

Removed: behavior no longer exists -- the interactive local-interview
question/answer loop that this verifier used to drive over HTTP for `create`
(scope/effect/privacy/capability clarifying questions, multi-round answer
convergence, and an HTTP `/events` approve step) has no current equivalent
for `create`; `create_offline` accepts a complete proposal in one call and
`approve` seals it directly via the CLI, both synchronously, with no
interview state to answer into.

Kept/mapped to their current equivalent:
  - installed console script existence check -> unchanged.
  - `setup --non-interactive` -> unchanged in kind, but now (like
    tools/verify_installed_g04.py) passes an explicit `--endpoint`/
    `--model-target`/`--create-model-target`/`--credential-ref` and pins
    HOME/PATH, so this verifier does not depend on Codex or Claude being
    discoverable on the runner. Without this, setup_command
    (src/gigai/cli.py:1213) raises "no usable model runtime is configured"
    on any runner without those CLIs on PATH -- the same root cause fixed in
    tools/verify_installed_g03.py for CI run 35660274375.
  - `init --home --target --username --json` -> unchanged.
  - launching a proposal from an explicit local input, with a proof-carrying
    identifier -> `create NAME --invocation <envelope> --json`, asserting
    `status == "proposed"` and `proposal_id` starts with `gp_`.
  - reaching an approved state -> `approve PROPOSAL_ID --json`, asserting
    `status == "approved"` and a sealed `version`/`tag` are present.
  - "installed G22 unexpectedly created a Run" -> unchanged: still asserts
    `workpads/**/runs` does not exist after approval, since `approve` alone
    must never create a Run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def _run(
    argv: list[str], *, env: dict[str, str]
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=30,
    )
    return result


def _diagnostics(result: subprocess.CompletedProcess[str]) -> str:
    """Keep machine-readable CLI failures visible without weakening assertions."""

    streams = []
    if result.stdout.strip():
        streams.append(f"stdout={result.stdout.strip()}")
    if result.stderr.strip():
        streams.append(f"stderr={result.stderr.strip()}")
    return "; ".join(streams) or "<no diagnostic output>"


def _require(label: str, result: subprocess.CompletedProcess[str]) -> dict[str, object]:
    if result.returncode != 0:
        raise SystemExit(f"installed G22 {label} failed: {_diagnostics(result)}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(
            f"installed G22 {label} did not emit JSON: {_diagnostics(result)}"
        ) from exc


def main() -> int:
    executable = Path(sys.prefix) / "bin" / "gigai"
    if not executable.is_file() or not os.access(executable, os.X_OK):
        raise SystemExit(f"installed gigai console script is missing: {executable}")
    with tempfile.TemporaryDirectory(prefix="gigai-g22-installed-") as raw_root:
        root = Path(raw_root)
        target = root / "target"
        home = root / "home"
        workpads = root / "workpads"
        target.mkdir()
        home.mkdir()
        (home / "tmp").mkdir()
        # See the module docstring: pinning HOME/PATH and configuring an
        # explicit remote endpoint/model target keeps this verifier
        # independent of whether Codex or Claude happen to be installed on
        # the runner -- the same fix applied to verify_installed_g03.py.
        env = {
            "HOME": os.fspath(home),
            "GIGAI_HOME": os.fspath(home),
            "PATH": os.pathsep.join(
                (os.fspath(executable.parent), "/usr/local/bin", "/usr/bin", "/bin")
            ),
            "TMPDIR": os.fspath(home / "tmp"),
            "GIGAI_G30_UAT": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        _require(
            "setup",
            _run(
                [
                    os.fspath(executable),
                    "setup",
                    "--non-interactive",
                    "--home",
                    os.fspath(home),
                    "--workpad-root",
                    os.fspath(workpads),
                    "--editor",
                    "/usr/bin/true",
                    "--no-open-with-target",
                    "--credential-ref",
                    "provider=environment:GIGAI_PROVIDER_TOKEN",
                    "--endpoint",
                    "remote=openai_api:provider:https://api.example.test",
                    "--model-target",
                    "remote=remote:gpt-test",
                    "--create-model-target",
                    "remote",
                    "--json",
                ],
                env=env,
            ),
        )
        _require(
            "init",
            _run(
                [
                    os.fspath(executable),
                    "init",
                    "--home",
                    os.fspath(home),
                    "--target",
                    os.fspath(target),
                    "--username",
                    "installed-verifier",
                    "--json",
                ],
                env=env,
            ),
        )

        envelope_path = root / "invocation.json"
        envelope_path.write_text(
            json.dumps(
                {
                    "protocol_version": "1",
                    "invocation_id": "inv_g22-installed-verify",
                    "trigger": "$gigai",
                    "actor": {
                        "kind": "agent",
                        "id": "codex",
                        "session_id": "g22-installed",
                    },
                    "command": "create",
                    "target": {
                        "home": os.fspath(home),
                        "project": "g22-installed",
                    },
                    "input": {
                        "intent": "Create an installed-wheel proposal.",
                        "proposal": {"summary": "Installed wheel proof"},
                    },
                    "requested": {"roles": ["gig_creator"], "models": [], "capabilities": []},
                    "consent": [],
                }
            ),
            encoding="utf-8",
        )
        create_payload = _require(
            "create",
            _run(
                [
                    os.fspath(executable),
                    "create",
                    "installed-proof",
                    "--home",
                    os.fspath(home),
                    "--target",
                    os.fspath(target),
                    "--invocation",
                    os.fspath(envelope_path),
                    "--json",
                ],
                env=env,
            ),
        )
        if create_payload.get("status") != "proposed":
            raise SystemExit(f"unexpected installed G22 create summary: {create_payload}")
        proposal_id = create_payload.get("proposal_id")
        if not isinstance(proposal_id, str) or not proposal_id.startswith("gp_"):
            raise SystemExit(f"unexpected installed G22 proposal id: {proposal_id!r}")

        approve_payload = _require(
            "approve",
            _run(
                [
                    os.fspath(executable),
                    "approve",
                    proposal_id,
                    "--home",
                    os.fspath(home),
                    "--target",
                    os.fspath(target),
                    "--json",
                ],
                env=env,
            ),
        )
        if approve_payload.get("status") != "approved" or not approve_payload.get("version"):
            raise SystemExit(f"unexpected installed G22 approve summary: {approve_payload}")

        if (workpads / "runs").exists() or list(workpads.rglob("runs")):
            raise SystemExit("installed G22 unexpectedly created a Run")
    print("verified installed GigAI G22 create and approve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
