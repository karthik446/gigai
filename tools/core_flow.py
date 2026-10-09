"""0110-10-hf2: THE CORE-FLOW SMOKE. `gigai scout new` and what goes with it, through the installed command.

    make test-core-flow          (python tools/core_flow.py --out build/core-flow)

A standing release rule: before a release is called ready, the commands an operator types every day run
to the end on a home the size of the operator's, from the wheel that will be published. 0.1.10.9 shipped
`gigai scout new` crashing on a first build over a large store: every test ran it from the source tree,
on a small home, or on a read model the server had already built.

WHAT RUNS

1. `uv build` the wheel; `uv venv` a clean environment; `uv pip install` the wheel into it (uv only).
2. The operator-sized synthetic home (tests/support/operator_home.py: 290,000 postings, 10,350 companies,
   2 profiles; under the temporary directory, nothing is read from a real home). With
   GIGAI_OPERATOR_HOME_PREBUILT=<root> a fresh copy of the home built once in the job is taken instead.
3. The flow, COLD (no server, the read model never built), then the server is started on the home
   and the same flow runs again, WARM. Each step is the installed `gigai` command in its own process.
   `gigai scout sources update` alone runs through tests/support/core_flow_boards.py: the same installed
   CLI with the board answers served from local fixtures (no request leaves the process). Each update
   finds one more posting on 600 boards, so the next `gigai scout new` matches 500 or more boards again
   (the "preparing your postings" lines start at 500).
4. Every step must exit 0, print no traceback and no "Error" on stderr, and print valid JSON on stdout.
   Each also has one check of its own (the update found the changed boards, `scout new` said its
   progress, `--yes` assessed postings, ...), so a command that exits 0 having done nothing fails too.

The table is printed and written to <out>/table.md (paste it into the "ready" message); every step's
stdout and stderr are in <out>/logs/. Exit 1 when any step failed. No model and no board is called: the
model is the product's deterministic fixture target, as in the API end-to-end suite.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from tests.support import core_flow_boards, operator_home  # noqa: E402 - after the repo root is importable

#: A line of stderr that is a failure whatever the exit code: a traceback, or an error by name ("NameError: ...", "Error: ...").
_BAD_STDERR = re.compile(r"Traceback|Error")
_PROGRESS = re.compile(r"^preparing your postings: (\d+)% \((\d+) of (\d+) companies\)$")
STEP_TIMEOUT_SECONDS = 1800.0

RESUME_A = """## Summary

Staff engineer. Python services on Kubernetes for nine years; Terraform; platform and ML infrastructure.

## Experience

### Staff Engineer, Northwind Logistics (2021 - 2026)

- Led the inference platform team of 6 engineers.
- Ran the Kubernetes migration for 40 services.
- Cut the deploy time from 45 minutes to 8 with a Terraform module library.

### Senior Engineer, Fabrikam Freight (2017 - 2021)

- Built the Python ingestion services that take 2 million events a day.
- Wrote the on-call runbooks and led incident reviews.

## Skills

- Python, Go, Kubernetes, Terraform, GCP, PostgreSQL

## Education

### B.S. Computer Science, State University (2013 - 2017)
"""
RESUME_B = RESUME_A.replace("Led the inference platform team of 6 engineers.", "Led the platform team of 6 engineers and its roadmap.").replace(
    "- Wrote the on-call runbooks and led incident reviews.", "- Designed the service mesh rollout for the backend teams."
)


class CoreFlowError(RuntimeError):
    pass


@dataclass
class Row:
    command: str
    mode: str
    exit: int
    seconds: float
    stderr_lines: int
    problems: list[str]
    note: str = ""

    @property
    def ok(self) -> bool:
        return not self.problems


def _run(argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float = STEP_TIMEOUT_SECONDS) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, check=False, timeout=timeout)


def _must(argv: list[str], *, cwd: Path, env: dict[str, str], what: str) -> str:
    done = _run(argv, cwd=cwd, env=env)
    if done.returncode != 0:
        raise CoreFlowError(f"{what} failed (exit {done.returncode}):\n{done.stdout[-1500:]}\n{done.stderr[-3000:]}")
    return done.stdout


class Flow:
    def __init__(self, *, out: Path, scratch: Path, postings: int, companies: int) -> None:
        self.out, self.scratch = out, scratch
        self.postings, self.companies = postings, companies
        self.logs = out / "logs"
        self.rows: list[Row] = []
        self.facts: list[str] = []
        self.venv = scratch / "venv"
        self.python = self.venv / "bin" / "python"
        self.gigai = self.venv / "bin" / "gigai"
        self.built: operator_home.OperatorHome | None = None
        self.env: dict[str, str] = {}
        self._step = 0

    # --- 1. the wheel, in a clean environment -----------------------------------------------------------

    def install_wheel(self) -> None:
        base = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "VIRTUAL_ENV", "GIGAI_HOME")}
        dist = self.scratch / "dist"
        _must(["uv", "build", "--wheel", "--out-dir", str(dist)], cwd=REPO, env=base, what="uv build")
        wheels = sorted(dist.glob("gigai-*.whl"))
        if len(wheels) != 1:
            raise CoreFlowError(f"expected one wheel in {dist}, found {[wheel.name for wheel in wheels]}")
        version = f"{sys.version_info.major}.{sys.version_info.minor}"
        _must(["uv", "venv", "--python", version, str(self.venv)], cwd=self.scratch, env=base, what="uv venv")
        _must(["uv", "pip", "install", "--python", str(self.python), str(wheels[0])], cwd=self.scratch, env=base, what="uv pip install")
        # From the repo root, as the fixture boards run: `tests.support` is this checkout, `gigai` must be the wheel.
        where = _must(
            [str(self.python), "-c", "import gigai, sys; print(gigai.__file__); print(sys.version.split()[0])"], cwd=REPO, env=base, what="import gigai",
        ).split()
        if not Path(where[0]).resolve().is_relative_to(self.venv.resolve()):
            raise CoreFlowError(f"the environment imports gigai from {where[0]}, not from the installed wheel")
        said = _must([str(self.gigai), "--version"], cwd=self.scratch, env=base, what="gigai --version").strip()
        self.facts.append(f"wheel `{wheels[0].name}` installed with uv into a clean environment (Python {where[1]}); `gigai --version`: `{said}`")

    # --- 2. the home ------------------------------------------------------------------------------------

    def take_home(self) -> None:
        base = {key: value for key, value in os.environ.items() if key not in ("PYTHONPATH", "VIRTUAL_ENV", "GIGAI_HOME")}
        built = operator_home.take_prebuilt(postings=self.postings, companies=self.companies, log=print)
        if built is None:
            root = self.scratch / "op"
            _must(
                [str(self.python), "-m", "tests.support.operator_home", str(root), "--postings", str(self.postings), "--companies", str(self.companies)],
                cwd=REPO, env=base, what="building the operator-sized home",
            )
            built = operator_home.OperatorHome(**json.loads((root / "operator-home.json").read_text(encoding="utf-8")))
            how = f"built in {built.build_seconds} s"
        else:
            how = f"a fresh copy of the home built once for this job ({operator_home.PREBUILT_ENV})"
        operator_home.assert_synthetic_root(Path(built.root))
        self.built = built
        self.facts.append(f"home: {built.postings:,} postings x {built.companies:,} companies, {len(built.profiles)} profiles, synthetic ({how})")
        # Nothing of the caller's environment but PATH: no real home can be a default, no source tree is importable.
        self.env = {
            "PATH": os.environ.get("PATH", ""), "HOME": built.root, "LANG": os.environ.get("LANG", "C.UTF-8"), **operator_home.SEAM_ENV,
            "GIGAI_SCOUT_ATS_MIN_INTERVAL_SECONDS": "0", "GIGAI_SCOUT_ATS_PROVIDER_FLOORS": "0", "GIGAI_SCOUT_ATS_CONCURRENCY": "8",
        }
        for name in ("TMPDIR", "GIGAI_TEST_LATENCY_SCALE"):
            if os.environ.get(name):
                self.env[name] = os.environ[name]

    # --- 3. one step ------------------------------------------------------------------------------------

    def step(
        self, mode: str, shown: str, argv: list[str], check: Callable[[dict[str, object], list[str]], str | None], *, cwd: Path | None = None,
    ) -> dict[str, object] | None:
        """Run one command; a row for the table. ``check`` gets the JSON of stdout and stderr's lines and returns what is wrong, or None."""

        assert self.built is not None
        self._step += 1
        name = f"{self._step:02d}-{mode}-" + re.sub(r"[^a-z0-9]+", "-", shown.lower()).strip("-")[:60]
        print(f"[{mode}] {shown} ...", flush=True)
        started = time.monotonic()
        try:
            done = _run([*argv, "--home", self.built.home, "--target", self.built.target], cwd=cwd or self.scratch, env=self.env)
            code, stdout, stderr = done.returncode, done.stdout, done.stderr
        except subprocess.TimeoutExpired as late:
            code, stdout, stderr = 124, str(late.stdout or ""), str(late.stderr or "") + f"\ncore-flow: no answer in {STEP_TIMEOUT_SECONDS:.0f} s"
        seconds = time.monotonic() - started
        (self.logs / f"{name}.stdout").write_text(stdout, encoding="utf-8")
        (self.logs / f"{name}.stderr").write_text(stderr, encoding="utf-8")
        lines = stderr.splitlines()
        problems: list[str] = []
        if code != 0:
            problems.append(f"exit {code}")
        bad = [line for line in lines if _BAD_STDERR.search(line)]
        if bad:
            problems.append(f"stderr: {bad[0][:160]}")
        payload: dict[str, object] | None = None
        try:
            found = json.loads(stdout)
            payload = found if isinstance(found, dict) else None
        except ValueError:
            pass
        if payload is None:
            problems.append("stdout is not a JSON object")
        elif payload.get("status") == "error" or "error" in payload and payload.get("error"):
            problems.append(f"the command answered an error: {json.dumps(payload.get('error'))[:160]}")
        note = ""
        if payload is not None and not problems:
            try:
                wrong = check(payload, lines)
            except (KeyError, TypeError, IndexError, ValueError) as shape:
                wrong = f"the answer does not have the expected shape ({type(shape).__name__}: {shape})"
            if wrong and wrong.startswith("ok: "):
                note = wrong[4:]
            elif wrong:
                problems.append(wrong)
        self.rows.append(Row(shown, mode, code, seconds, len(lines), problems, note))
        print(f"    exit {code}, {seconds:.1f} s, {len(lines)} stderr line(s)" + (f"  FAILED: {'; '.join(problems)}" if problems else f"  {note}"), flush=True)
        return payload if not problems else None

    # --- the flow ---------------------------------------------------------------------------------------

    def flow(self, mode: str, round_number: int) -> None:
        built = self.built
        assert built is not None
        gigai = str(self.gigai)
        resumes = {operator_home.DEFAULT_LABEL: self.scratch / "resume-default.md", operator_home.SECOND_LABEL: self.scratch / "resume-platform.md"}
        resumes[operator_home.DEFAULT_LABEL].write_text(RESUME_A, encoding="utf-8")
        resumes[operator_home.SECOND_LABEL].write_text(RESUME_B, encoding="utf-8")
        for label, path in resumes.items():
            self.step(
                mode, f"gigai scout resume add {path.name} --profile <{label}> --json",
                [gigai, "scout", "resume", "add", str(path), "--profile", built.profiles[label], "--json"],
                lambda answer, _lines: None if answer.get("ok") is True else "not ok",
            )

        changed = min(core_flow_boards.CHANGED_PER_ROUND, built.companies)

        def updated(answer: dict[str, object], _lines: list[str]) -> str | None:
            boards, companies, postings = answer["boards"], answer["companies"], answer["postings"]
            if answer["status"] != "succeeded" or boards["failed"]:  # type: ignore[index]
                return f"status {answer['status']}, {boards['failed']} boards failed"  # type: ignore[index]
            if companies["with_new"] != changed or postings["new"] != changed:  # type: ignore[index]
                return f"{companies['with_new']} companies with new postings, {postings['new']} new postings: expected {changed}"  # type: ignore[index]
            return f"ok: {boards['checked']:,} boards checked, {changed} with a new posting, {postings['live']:,} postings live"  # type: ignore[index]

        self.step(
            mode, "gigai scout sources update --json  (local fixture boards)",
            [str(self.python), "-m", "tests.support.core_flow_boards", "update", str(round_number), str(Path(built.root) / "operator-home.json"),
             "scout", "sources", "update", "--json"],
            updated, cwd=REPO,
        )

        def said_progress(answer: dict[str, object], lines: list[str]) -> str | None:
            if answer["schema_version"] != "scout-new:1":
                return f"schema {answer['schema_version']}"
            said = [found for found in map(_PROGRESS.match, lines) if found]
            if not said:
                return "no 'preparing your postings' line on stderr: 500 or more boards were not matched again"
            total = int(said[-1].group(3))
            floor = built.companies if mode == "cold" else changed
            if total < max(500, floor) or said[0].group(2) != "0" or said[-1].group(1) != "100":
                return f"progress ran from '{said[0].group(0)}' to '{said[-1].group(0)}': expected 0% to 100% of at least {max(500, floor)} companies"
            return f"ok: matched {total:,} companies ({len(said)} progress lines); {answer['counts']['new']} new postings"  # type: ignore[index]

        asked = self.step(mode, "gigai scout new --no-assess --json", [gigai, "scout", "new", "--no-assess", "--json"], said_progress)

        def assessed(answer: dict[str, object], _lines: list[str]) -> str | None:
            result = answer["assessed"]
            if not isinstance(result, dict) or result["failed"] or not result["assessed"] or result["assessed"] != result["requested"]:
                return f"assessed: {json.dumps(result)[:200]}"
            return f"ok: assessed {result['assessed']} of {result['requested']} new postings (fixture model)"

        # The yes measures "new" from the since the first call answered with (that call moved the anchor). When the first
        # call failed there is none: the yes then measures from the anchor, and fails or passes on its own.
        since = ["--since", str(asked["since"])] if asked else []
        self.step(mode, "gigai scout new --yes --since <the since it asked with> --json", [gigai, "scout", "new", "--yes", *since, "--json"], assessed)

        def listed(answer: dict[str, object], _lines: list[str]) -> str | None:
            rows = answer["postings"]["rows"]  # type: ignore[index]
            if answer["schema_version"] != "scout-postings:1" or not rows or not answer["counts"]["matched"]:  # type: ignore[index]
                return "no matched posting listed"
            return f"ok: {answer['counts']['matched']} matched, {len(rows)} listed"  # type: ignore[index]

        self.step(mode, "gigai scout jobs list --json", [gigai, "scout", "jobs", "list", "--json"], listed)
        # One job for the two single-job commands: this round's first new matched posting, a stored one.
        job_url = core_flow_boards.matched_urls(round_number, built.companies)[0]
        def verdict(answer: dict[str, object], _lines: list[str]) -> str | None:
            if answer["job"]["normalized_url"] != job_url or not answer["result"]["verdict"]:  # type: ignore[index]
                return f"no verdict for {job_url}"
            return f"ok: verdict {answer['result']['verdict']}"  # type: ignore[index]

        self.step(mode, "gigai scout assess --job-url <one stored posting> --json", [gigai, "scout", "assess", "--job-url", job_url, "--json"], verdict)

        # 0.1.11: `gigai scout resume tailor` is switched off (the resume is picked at assessment); its step is out of the
        # release rule until 0.1.11.1 reworks the pipeline.

        def merge(answer: dict[str, object], _lines: list[str]) -> str | None:
            plan, master = answer.get("migration"), answer.get("master")
            if isinstance(plan, dict):
                return f"ok: would merge {plan['resumes']} resumes, {plan['lines_in']} lines into {plan['lines_out']}" if plan["resumes"] == len(built.profiles) else f"merged {plan['resumes']} resumes"
            return f"ok: the master is stored (revision {master['revision']})" if isinstance(master, dict) else "neither a merge plan nor a master"

        self.step(mode, "gigai scout resume master init --dry-run --json", [gigai, "scout", "resume", "master", "init", "--dry-run", "--json"], merge)

        def stored(answer: dict[str, object], _lines: list[str]) -> str | None:
            master = answer.get("master")
            return f"ok: master revision {master['revision']}, {master['counts']['items']} lines" if isinstance(master, dict) and master["revision"] >= 1 else "no master stored"

        self.step(mode, "gigai scout resume master init --json", [gigai, "scout", "resume", "master", "init", "--json"], stored)
        self.step(
            mode, "gigai scout resume master selection refresh --all --json", [gigai, "scout", "resume", "master", "selection", "refresh", "--all", "--json"],
            lambda answer, _lines: None if isinstance(answer.get("master"), dict) and answer.get("dry_run") is False else "no selection made",
        )
        self.step(
            mode, "gigai scout status --json", [gigai, "scout", "status", "--json"],
            lambda answer, _lines: f"ok: master.md {answer['master_file']['state']}" if answer.get("ok") is True else "not ok",  # type: ignore[index]
        )

    # --- the server -------------------------------------------------------------------------------------

    def with_server(self, run: Callable[[], None]) -> None:
        built = self.built
        assert built is not None
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        log_path = self.logs / "server.log"

        def get(path: str, timeout: float = 120.0) -> tuple[int, dict[str, object]]:
            try:
                with urllib.request.urlopen(base + path, timeout=timeout) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read() or b"{}")

        with open(log_path, "w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [str(self.python), "-m", "gigai.scout.find_jobs.present_api", "--home", built.home, "--target", built.target, "--port", str(port), "--allow-test-seams"],
                cwd=self.scratch, env=self.env, stdout=log, stderr=subprocess.STDOUT,
            )
            try:
                started = time.monotonic()
                while True:
                    try:
                        if get("/api/health", 2)[0] == 200 and get("/api/postings?limit=1")[0] == 200:
                            break
                    except OSError:
                        pass
                    if server.poll() is not None or time.monotonic() - started > 600:
                        raise CoreFlowError(f"the server did not answer /api/postings: {log_path.read_text(encoding='utf-8')[-2000:]}")
                    time.sleep(0.2)
                self.facts.append(f"warm: the server (the wheel's `present_api`, pid {server.pid}) answered /api/postings {time.monotonic() - started:.1f} s after its start, then the flow ran again")
                run()
                # The server lived through the CLI's writes: it still answers, and its log has no failure.
                started = time.monotonic()
                problems: list[str] = []
                health, _ = get("/api/health")
                status, postings = get("/api/postings?limit=1")
                deadline = time.monotonic() + 300
                while status == 202 and time.monotonic() < deadline:
                    time.sleep(0.5)
                    status, postings = get("/api/postings?limit=1")
                if health != 200 or status != 200 or not postings.get("counts", {}).get("matched"):  # type: ignore[union-attr]
                    problems.append(f"/api/health {health}, /api/postings {status}")
                log.flush()
                bad = [line for line in log_path.read_text(encoding="utf-8").splitlines() if "Traceback" in line or "unhandled exception" in line or " 500 " in line]
                if bad:
                    problems.append(f"server log: {bad[0][:160]}")
                self.rows.append(Row("the server after the flow: GET /api/health, /api/postings; its log", "warm", 1 if problems else 0, time.monotonic() - started, len(bad), problems, "200, 200; no traceback, no 500"))
            finally:
                server.terminate()
                try:
                    server.wait(20)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(20)

    def a_day_later(self) -> None:
        assert self.built is not None
        said = _must(
            [str(self.python), "-m", "tests.support.core_flow_boards", "a-day-later", str(Path(self.built.root) / "operator-home.json")],
            cwd=REPO, env=self.env, what="moving the boards' last-checked stamps back a day",
        )
        self.facts.append(f"between the two updates every board's last-checked stamp was moved back 25 hours ({json.loads(said)['boards_aged']:,} boards), so the second update asks them all again")

    # --- the table --------------------------------------------------------------------------------------

    def table(self) -> str:
        failed = [row for row in self.rows if not row.ok]
        lines = [
            f"### Core-flow smoke: {'FAILED (' + str(len(failed)) + ' of ' + str(len(self.rows)) + ' steps)' if failed else 'passed (' + str(len(self.rows)) + ' steps)'}",
            "",
            *[f"- {fact}" for fact in self.facts],
            "",
            "| # | command | mode | exit | seconds | stderr lines | result |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for number, row in enumerate(self.rows, 1):
            result = "FAILED: " + "; ".join(row.problems) if row.problems else ("ok" + (f": {row.note}" if row.note else ""))
            lines.append(f"| {number} | `{row.command}` | {row.mode} | {row.exit} | {row.seconds:.1f} | {row.stderr_lines} | {result.replace('|', '/')} |")
        lines += ["", f"Total: {sum(row.seconds for row in self.rows):.0f} s in {len(self.rows)} steps. A step fails on a non-zero exit, a traceback or `Error` on stderr, stdout that is not JSON, or its own check."]
        return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The core-flow smoke: the installed `gigai` command on the operator-sized synthetic home, cold and warm.")
    parser.add_argument("--out", type=Path, default=REPO / "build" / "core-flow")
    parser.add_argument("--postings", type=int, default=operator_home.OPERATOR_POSTINGS)
    parser.add_argument("--companies", type=int, default=operator_home.OPERATOR_COMPANIES)
    parser.add_argument("--keep", action="store_true", help="keep the scratch directory (the environment and the home)")
    args = parser.parse_args(argv)

    out = args.out.resolve()
    if (out / "logs").exists():
        shutil.rmtree(out / "logs")
    (out / "logs").mkdir(parents=True)
    # Under /tmp: the home's generator refuses any root outside the temporary directory.
    scratch = Path(tempfile.mkdtemp(prefix="gigai-core-flow-", dir="/tmp")).resolve()
    flow = Flow(out=out, scratch=scratch, postings=args.postings, companies=args.companies)
    crashed: str | None = None
    try:
        flow.install_wheel()
        flow.take_home()
        flow.flow("cold", 1)
        flow.a_day_later()
        flow.with_server(lambda: flow.flow("warm", 2))
    except (CoreFlowError, operator_home.OperatorHomeError, OSError, subprocess.SubprocessError) as stopped:
        crashed = f"{type(stopped).__name__}: {stopped}"
    finally:
        if not args.keep:
            shutil.rmtree(scratch, ignore_errors=True)
            prebuilt = operator_home.prebuilt_root()
            if prebuilt is not None and prebuilt.exists():
                shutil.rmtree(prebuilt, ignore_errors=True)  # the copy this run took; the pristine home stays for the next taker
    table = flow.table()
    if crashed:
        table += f"\nThe smoke stopped before its end: {crashed}\n"
    (out / "table.md").write_text(table, encoding="utf-8")
    print("\n" + table)
    print(f"table: {out / 'table.md'}; every step's stdout and stderr: {out / 'logs'}")
    return 1 if crashed or not flow.rows or any(not row.ok for row in flow.rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
