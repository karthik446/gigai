"""Build the synthetic demo home the release screenshots are taken from, and start Scout on it.

No network and no model: the real `gigai` CLI and the real Scout server (the supervised child,
the same entry `gigai scout run --no-browser` uses), on the fixture transports the api-e2e suite
uses (`GIGAI_SCOUT_FIND_JOBS_TEST_HTTP` / `..._TEST_MODEL`). Postings come in through the real
sources updater, on a transport that serves only our made-up Lever boards.

What the fixture model can and cannot show (it is a fixture, not a model): every assessment has
the same two requirements (Python, GCP) and asks the one GCP question until that answer is saved;
every rank is 60. The data around it (companies, roles, places, pay, answers, stories) is ours.

The root must be outside any git repository (`gigai init` refuses a target inside one) and is
never the operator's real home: build.py points HOME at a temporary directory first, and
`assert_synthetic_home` refuses to run otherwise.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import os
from pathlib import Path
import socket
import tempfile
import time

from . import persona

SEAM_ENV = {
    "GIGAI_SCOUT_FIND_JOBS_TEST_HTTP": "1",
    "GIGAI_SCOUT_FIND_JOBS_TEST_MODEL": "1",
    "GIGAI_SCOUT_AUTO_REFRESH": "0",
    "GIGAI_SCOUT_OPEN_FOLDER_TEST": "1",  # "Open folder" writes <home>/open-folder-test.log instead of opening a window
    "EXA_API_KEY": "demo-key",
}
PIPELINE_WAIT_SECONDS = 90


class DemoHomeError(RuntimeError):
    pass


@dataclass(frozen=True)
class DemoHome:
    url: str
    root: str
    home: str
    target: str
    hero_job: str  # the posting whose job page shows the timeline, Scout label and Scout ATS score
    hero_profile_id: str
    profiles: dict[str, str]  # label -> profile id
    counts: dict[str, int]

    def to_json(self) -> dict[str, object]:
        return asdict(self)


def assert_synthetic_home(root: Path) -> None:
    """Refuse to build anywhere near real data: HOME must be a temporary directory that holds `root`."""

    home = Path(os.environ.get("HOME", "")).resolve()
    temp = Path(tempfile.gettempdir()).resolve()
    if not home.is_relative_to(temp):
        raise DemoHomeError(f"HOME must be a temporary directory during the media build, not {home}")
    if not root.resolve().is_relative_to(home):
        raise DemoHomeError("the demo root must be inside the temporary HOME")
    if Path.home().resolve() != home:
        raise DemoHomeError("Path.home() does not follow HOME; refusing to build")
    if (home / ".gigai").exists():
        raise DemoHomeError("the temporary HOME already has a .gigai folder; use a fresh one")


def demo_gigai_home(root: Path) -> Path:
    """The demo's GigAI home: `<temporary HOME>/.gigai`, the folder a default install has.

    Every path the UI derives from the home then reads as a reader's own: the resumes folder
    `~/Documents/GigAI/resumes` and the PDF header file `~/Documents/GigAI/header.json` (0.1.11.3's
    "Filled from <path>" and Save button). A home named `<root>/home` showed `~/demo/home/header.json`,
    which the privacy gate reads as `/home/header.json` (a home path) and failed the 0.1.11.3 release's
    screenshots. The gate is not loosened.
    """

    return root.parent / ".gigai"


def media_resumes_folder() -> Path:
    """Where the screenshots' demo home keeps its resume files: `Documents/GigAI/resumes` under the temporary HOME.

    A home that is not `~/.gigai` defaults to `<home>/resumes`, which the UI would show as
    `~/demo/home/resumes`: a made-up path nobody has, and one the privacy gate rightly reads as
    a home path (`/home/<name>`; 0110-10-07). The demo sets the folder a default install has
    instead, so the screenshots show `~/Documents/GigAI/resumes`, as a reader's own Scout does.
    """

    return Path.home() / "Documents" / "GigAI" / "resumes"


def _lever_body(company: persona.Company, seen: datetime) -> list[dict[str, object]]:
    body: list[dict[str, object]] = []
    for number, posting in enumerate(company.postings, start=1):
        posting_id = f"{company.slug}-{number:04d}"
        row: dict[str, object] = {
            "id": posting_id,
            "text": posting.title,
            "hostedUrl": f"https://jobs.lever.co/{company.slug}/{posting_id}",
            "categories": {"location": posting.place},
            "country": "US",
            "workplaceType": posting.mode,
            "descriptionPlain": f"{company.blurb}\n\n{posting.about}\n\n{persona.REQUIREMENTS}",
            "createdAt": int(seen.timestamp() * 1000),
        }
        if posting.pay is not None:
            row["salaryRange"] = {"min": posting.pay[0], "max": posting.pay[1], "currency": "USD", "interval": "per-year-salary"}
        body.append(row)
    return body


def build(root: Path, *, log=print, resumes_folder: Path | None = None, master: bool = False) -> DemoHome:
    """Create the home under `root`, start Scout, fill it. The caller stops it with `stop(root)`.

    `resumes_folder` saves that folder as the home's resumes folder (the real `gigai scout resume
    folder --set`) before anything is written to it; `master` makes the master resume from the
    profiles' resumes (the Master page's own route) before the first job is tailored, so the hero
    job's resume is picked from the master. Both are off for the browser tests (tests/ui), which
    pin the default folder and make the master themselves; `make media` turns both on.
    """

    assert_synthetic_home(root)
    os.environ.update(SEAM_ENV)
    os.environ.pop("GIGAI_SCOUT_PIPELINE", None)

    # Imported here, after HOME points at the temporary directory.
    from click.testing import CliRunner
    import httpx

    from gigai.cli import cli
    from gigai.scout import run_supervisor
    from gigai.scout.find_jobs.bindings import TEST_MODEL_DIGEST, TEST_MODEL_NAME
    from gigai.scout.find_jobs.company_index import CompanyIndex
    from gigai.scout.find_jobs.sources_update import board_cache_for_home, update_sources
    from gigai.scout.find_jobs.watchlist import add_company_from_url

    home = demo_gigai_home(root)
    target = home / "scout"  # where Scout lives by default, so the terminal frames need no --target
    root.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    runner = CliRunner()

    def gigai(*args: str) -> None:
        result = runner.invoke(cli, list(args))
        if result.exit_code != 0:
            raise DemoHomeError(f"gigai {' '.join(args[:3])} failed: {result.output[-600:]}")

    gigai(
        "setup", "--non-interactive", "--home", str(home), "--workpad-root", str(root / "workpads"), "--editor", "/usr/bin/true",
        "--endpoint", "ollama_local=ollama_local:http://127.0.0.1:11434",
        "--model-target", f"ollama_local=ollama_local:{TEST_MODEL_NAME}@{TEST_MODEL_DIGEST}",
        "--create-model-target", "ollama_local", "--json",
    )
    gigai("init", "--home", str(home), "--target", str(target), "--username", "demo-user", "--json")
    if resumes_folder is not None:
        gigai("scout", "resume", "folder", "--set", str(resumes_folder), "--home", str(home), "--json")
    resume = root / "resume.md"
    resume.write_text(persona.RESUME_MARKDOWN, encoding="utf-8")
    gigai("scout", "resume", "add", str(resume), "--home", str(home), "--target", str(target), "--json")

    config_path = target / "find-jobs.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(
        roles=list(persona.ROLES_DEFAULT), merged_queries=list(persona.ROLES_DEFAULT), location=persona.PERSONA_LOCATION,
        sources={"exa": False, "ats": False, "hiringcafe": False},
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")

    # The REAL updater (`gigai scout sources update`'s own function) on a transport that serves
    # only our made-up Lever boards: postings are fetched, parsed, indexed and stamped as on a
    # real machine, so the Jobs page says "up to date". Preferences are saved after this, and
    # the server never runs an update itself (automatic checks are off): either would seed the
    # bundled catalog of real companies.
    bodies = {company.slug: company for company in persona.COMPANIES}

    def lever(request: "httpx.Request") -> "httpx.Response":
        slug = request.url.path.rstrip("/").rsplit("/", 1)[-1]
        if request.url.host == "api.lever.co" and slug in bodies:
            company = bodies[slug]
            seen = datetime.now(UTC) - timedelta(days=company.age_days)
            return httpx.Response(200, json=_lever_body(company, seen), request=request)
        return httpx.Response(404, json={"error": "not a demo board"}, request=request)

    boards_client = httpx.Client(transport=httpx.MockTransport(lever), follow_redirects=False, trust_env=False)
    cache = board_cache_for_home(home)
    index = CompanyIndex.for_home(home)

    def seed(wave: int) -> None:
        boards = [add_company_from_url(f"https://jobs.lever.co/{company.slug}", home, target) for company in persona.COMPANIES if company.wave == wave]
        result = update_sources(boards, cache=cache, index=index, client=boards_client, home_root=home)
        if result.status != "succeeded":
            raise DemoHomeError(f"the demo boards did not update: {result.summary}")

    seed(1)

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    started = run_supervisor.start(
        home_root=home, requested_target=target, port=port, foreground=False, open_browser=False, allow_test_seams=True,
    )
    client = httpx.Client(base_url=started.state.url, timeout=120)

    def ok(response: "httpx.Response", status: int = 200) -> dict:
        if response.status_code != status:
            raise DemoHomeError(f"{response.request.method} {response.request.url.path}: {response.status_code} {response.text[:600]}")
        return response.json()

    # Preferences are saved AFTER the boards are in place (saving them first seeds the full catalog).
    ok(client.put("/api/setup", json={
        "roles": list(persona.ROLES_DEFAULT), "titles_to_avoid": [], "countries": ["US"], "work_mode": "any",
        "city": persona.PERSONA_LOCATION, "visa_sponsorship_required": False, "exclude_companies": [], "watch_companies": [],
        "company_stage_size": None, "industries_include": [], "industries_exclude": [], "must_have_stack": [],
        "dealbreaker_stack": [], "cadence_days": 7, "budget_usd_per_session": 0.5,
    }))
    ok(client.post("/api/profiles", json={
        "label": persona.SECOND_PROFILE_LABEL, "titles": list(persona.SECOND_PROFILE_TITLES), "queries": list(persona.SECOND_PROFILE_TITLES[:1]),
    }), 201)
    profiles = {item["label"]: item["profile_id"] for item in ok(client.get("/api/profiles"))["profiles"]}
    log(f"profiles: {sorted(profiles)}")

    if master:
        # The Master page's "make it" button: the profiles hold one resume, so the merge asks nothing.
        plan = ok(client.get("/api/master/migration"))
        if plan.get("questions"):
            raise DemoHomeError(f"the demo master should need no answer, not {len(plan['questions'])} questions")
        made = ok(client.post("/api/master/migration", json={"answers": {}, "actor": "operator"}), 201)
        if not made.get("written"):
            raise DemoHomeError(f"the demo master was not made: {made.get('status')}")
        log(f"master: revision {ok(client.get('/api/master'))['master']['revision']}")

    # One job of a trigger runs by itself; the rest wait for an approval (the Background panel shows it).
    ok(client.put("/api/settings/background", json={"pipeline": {"enabled": True, "auto_jobs_per_trigger": 1}}))

    def rows() -> list[dict]:
        return ok(client.get("/api/postings"))["postings"]["rows"]

    def assess(job_identities: list[str], *, again: bool = False) -> object:
        asked = ok(client.post("/api/postings/assess", json={"jobs": job_identities, "again": again}))
        done = ok(client.post("/api/postings/assess", json=asked["question"]["yes"]["api"]["body"]))
        if done["assessed"]["failed"]:
            raise DemoHomeError(f"assessing failed: {done['assessed']['failed']}")
        return done["assessed"]["assessed"]

    def of(slug: str) -> list[str]:
        return [row["job_identity"] for row in rows() if f"/{slug}/" in row["job_identity"]]

    # Before the GCP answer exists: these need the user's answer.
    first = of("tallgrass-health")[:1] + of("quillon-robotics")
    log(f"assessed before the answer: {assess(first)}")
    ok(client.post("/api/new/seen", json={}))

    # New since that check.
    time.sleep(1.1)
    seed(2)

    states = {row["job_identity"]: row["state"] for row in rows()}
    hero = first[0]
    if states.get(hero) != "needs_answers":
        raise DemoHomeError(f"the hero job should be waiting for an answer, not {states.get(hero)}")
    for number, answer in enumerate(persona.ANSWERS):
        body: dict[str, object] = dict(answer, actor="agent" if number else "operator")
        if number == 0:
            body["reassess"] = {"job_identity": hero}
        response = client.post("/api/answers", json=body)
        # The reassess half fetches the posting's link, which does not exist for a synthetic board
        # (409 job_fetch_failed); the answer itself is saved and the pipeline is triggered.
        if response.status_code not in (200, 201, 409):
            raise DemoHomeError(f"POST /api/answers: {response.status_code} {response.text[:400]}")
    for story in persona.STORIES:
        ok(client.post("/api/stories", json=dict(story, actor="agent")), 201)

    def master_line(text: str) -> tuple[dict, dict]:
        """(the master now, its line with exactly this text)."""

        current = ok(client.get("/api/master"))["master"]
        line = next((item for item in current["items"] if item["text"] == text), None)
        if line is None:
            raise DemoHomeError(f"the demo master has no line: {text[:60]}")
        return current, line

    if master:
        # Each story backs the master line that tells the same thing (the line's evidence, as
        # `gigai scout resume master edit --from-story` sets it): the Master page marks it "backed".
        told = {item["title"]: item["story_id"] for item in ok(client.get("/api/stories"))["stories"]}
        for title, text in persona.MASTER_BACKED.items():
            current, line = master_line(text)
            ok(client.put("/api/master/lines", json={
                "revision": current["revision"], "actor": "agent", "id": line["id"], "use": "edit", "backed": [told[title]],
            }))

    def pipeline_done(previous: str | None = None) -> dict:
        """Wait until the hero job's pipeline is done (again, when `previous` is its last `updated_at`)."""

        deadline = time.monotonic() + PIPELINE_WAIT_SECONDS
        status: dict = {}
        while time.monotonic() < deadline:
            status = ok(client.get("/api/pipeline"))
            counts = status["counts"]["jobs"]
            job = next((item for item in status["jobs"] if item["job_identity"] == hero), None)
            if job and job["state"] == "done" and job["updated_at"] != previous and not counts.get("running") and not counts.get("waiting"):
                return status | {"hero": job}
            time.sleep(0.3)
        raise DemoHomeError(f"the pipeline did not finish the hero job in {PIPELINE_WAIT_SECONDS} s: {status.get('counts')}")

    done_job = pipeline_done()["hero"]

    # After the answer: these come back with every requirement met, and so does the hero job.
    later = of("tallgrass-health")[1:] + of("brightwater-ledger")[:2] + of("halcyon-grid")[:1]
    log(f"assessed after the answer: {assess(later)} + the hero job again: {assess([hero], again=True)}")
    # The hero's first label was made while its assessment was stale (the answer had just changed).
    # Process it once more, as the job page's "Process again" does, so the label is the settled one.
    ok(client.post("/api/pipeline/process", json={"job_identity": hero, "profile_id": done_job["profile_id"], "force": True}), 202)
    pipeline = pipeline_done(done_job["updated_at"])
    done_job = pipeline["hero"]
    log(f"hero job: {done_job['label']}")

    if master:
        # The fixture model's "tailored" resume shows none of the master's lines. "You" add four with
        # the job page's own Add (the same route), so Picked and Left out both have lines to show.
        resume_of = {"profile_id": done_job["profile_id"], "job_identity": hero}
        stored = max(ok(client.get("/api/tailored-resumes", params=resume_of))["items"], key=lambda item: item["updated_at"])
        if not stored.get("selection"):
            raise DemoHomeError("the hero job's resume was not tailored from the master")
        for text in persona.MASTER_ADDED:
            stored = ok(client.put("/api/tailored-resumes/selection", json={
                **resume_of, "updated_at": stored["updated_at"], "use": "add", "item_id": master_line(text)[1]["id"],
            }))
            if not stored["selection_change"]["changed"]:
                raise DemoHomeError(f"the line was not added to the hero job's resume: {stored['selection_change']}")
        pipeline = pipeline_done()
        done_job = pipeline["hero"]
        log(f"hero resume: picked {stored['selection']['counts']['picked']}, left out {stored['selection']['counts']['left_out']}")

    final = ok(client.get("/api/postings"))
    counts = {key: int(value) for key, value in final["counts"].items() if isinstance(value, int)}
    log(f"postings: {counts}; pipeline: {pipeline['counts']['jobs']}; approvals pending: {pipeline['approvals']['pending']}")
    return DemoHome(
        url=started.state.url, root=str(root), home=str(home), target=str(target),
        hero_job=done_job["job_identity"], hero_profile_id=done_job["profile_id"], profiles=profiles, counts=counts,
    )


def stop(root: Path) -> None:
    from gigai.scout import run_supervisor

    run_supervisor.stop(home_root=demo_gigai_home(root), requested_target=demo_gigai_home(root) / "scout")
