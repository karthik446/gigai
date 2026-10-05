"""0110-10-10 item 3: ONE rule for when the master is the basis of a job's resume, the same in behaviour and in every text.

The UAT finding: the job page's header said "from resume Staff Software Engineer" while the line under it said
"GigAI picked the candidate lines from your whole master", and a profile with no selection was reported as "its
resume is its own" while its tailorings were picked from the master.

The rule (``tailor_master.tailoring_basis``): with a master stored, a profile's resume for a job is made from the
master unless the profile's resume was replaced by hand after its selection was made.  A profile with NO selection
is on the master too.  Here, on a temp home with a master and four resumes:

* ``selected``: a profile that holds a selection (the migration's);
* ``bare``: a profile with NO selection (created after the master, with a resume of its own);
* ``detached``: a profile whose resume was replaced by hand after its selection;
* a pasted resume.

For each, one real tailoring (``gigai scout resume tailor``, a model that copies the lines it is shown) and then:
what the tailor call READ (the lines of its prompt), what the stored resume RECORDS (``sources.master``, the
master ids on its lines), and what every text SAYS: the CLI's summary, the job page's header and the line under
it (``ui/src/masterModel.js`` under node, fed the stored JSON), the selection status of the CLI and of
``GET /api/master/selection``, and the pipeline's tailor digest.  All of them name the same basis.

Synthetic data only (``tests/evals/fixtures/master``, the spike's invented person).
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess

from click.testing import CliRunner
import pytest

from gigai.canonical import canonical_json_bytes
from gigai.cli import cli
from gigai.scout import profile_records
from gigai.scout.find_jobs.api import static as static_module
from gigai.scout.find_jobs.api.master import selection_response
from gigai.scout.find_jobs.contracts import PinnedResume
from gigai.scout.resume_import import import_resume_file
from gigai.scout.tailored_resume import list_tailored_resumes
from gigai.scout.target_resolution import home_scout_target
from gigai.workpad import resolve_workpad

from tests.support.master_tailor import copies_what_it_is_shown, install_prompt_model, listed
from tests.support.scout_profile_fixtures import default_find_jobs_config
from tests.support.setup_home import setup_home
from tests.support.tailor_cases import ollama_config

FIXTURES = Path(__file__).resolve().parents[2] / "evals" / "fixtures" / "master"
AI_RESUME = FIXTURES / "legacy-ai.md"  # the newer resume: it holds lines the older one does not
SWE_RESUME = FIXTURES / "legacy-swe.md"  # the older one: what the three profiles' own resume is
UI = Path(static_module.__file__).resolve().parents[2] / "ui"
MODEL_JS = UI / "src" / "masterModel.js"
PANEL_JSX = UI / "src" / "components" / "TailoredResumePanel.jsx"

MASTER, PROFILE_RESUME, PASTED_RESUME = "master", "profile_resume", "pasted_resume"
#: The profile (or pasted resume) and what its job's resume is made from, by the rule.
CASES = (("selected", MASTER), ("bare", MASTER), ("detached", PROFILE_RESUME), ("pasted", PASTED_RESUME))
MASTER_LINE = "A resume for a job is picked from your whole master resume."
OWN_RESUME_LINE = "A resume for a job is made from this profile's own resume, not from your master resume."


def _posting() -> dict[str, str]:
    head, _, body = (FIXTURES / "postings" / "p1-staff-ai-agent-platform.md").read_text(encoding="utf-8").partition("\n\n")
    return {**dict(line.split(": ", 1) for line in head.splitlines()), "text": body.strip() + "\n"}


class _Home:
    """A Scout home with a master and three profiles: one with a selection, one with none, one detached."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.runner = CliRunner()
        setup_home(self.home, workpad_root=tmp_path / "workpads")
        assert self.runner.invoke(cli, ["scout", "install", "--home", str(self.home), "--json"]).exit_code == 0
        self.scout = home_scout_target(self.home)
        older = import_resume_file(home_root=self.home, requested_target=self.scout, source=SWE_RESUME)
        self.older = PinnedResume(older.record_id, older.revision_id, older.content_sha256)
        self.cli("scout", "resume", "add", str(AI_RESUME))
        (self.scout / "find-jobs.json").write_bytes(canonical_json_bytes(default_find_jobs_config().to_json()))
        self.resolved = resolve_workpad(home_root=self.home, requested_target=self.scout, gig_id=None, allow_semantic_state=True)
        default = profile_records.selected_profile(self.resolved, home_root=self.home, target=self.scout)
        assert default is not None
        self.ids = {"detached": default.profile_id, "selected": self._profile("Staff Software Engineer")}
        # The master, made the way the operator makes it: from the profiles' own resumes. Both get a selection.
        asked = self.cli("scout", "resume", "master", "init")
        answers = [part for question in asked.get("questions", []) for part in ("--answer", f"{question['question_id']}=a")]
        assert (self.cli("scout", "resume", "master", "init", *answers) if answers else asked)["status"] == "created"
        # A profile made AFTER the master with a resume of its own: it holds no selection.
        self.ids["bare"] = self._profile("Platform Engineer")
        # The default profile's resume is replaced by hand: its selection is no longer what it shows.
        self.cli("scout", "resume", "add", str(SWE_RESUME), "--profile", self.ids["detached"])
        self.labels = {record.profile_id: record.label for record in profile_records.list_profiles(self.resolved)}
        posting = _posting()
        self.posting_file = tmp_path / "posting.txt"
        self.posting_file.write_text(posting["text"], encoding="utf-8")
        self.job = ["--job-text", str(self.posting_file), "--title", posting["title"], "--company", posting["company"]]

    def _profile(self, label: str) -> str:
        return profile_records.create_profile(
            self.resolved, label=label, titles=("staff software engineer",), titles_to_avoid=(), queries=("staff software engineer",),
            resume_ref=self.older,
        ).profile_id

    def cli(self, *args: str) -> dict:
        result = self.runner.invoke(cli, [*args, "--home", str(self.home), "--json"])
        assert result.exit_code == 0, result.output
        return json.loads(result.output.strip().splitlines()[-1])

    def text(self, *args: str) -> str:
        result = self.runner.invoke(cli, [*args, "--home", str(self.home)])
        assert result.exit_code == 0, result.output
        return result.output

    def record(self, name: str) -> object:
        return next(record for record in profile_records.list_profiles(self.resolved) if record.profile_id == self.ids[name])

    def tailor(self, port, *resume: str) -> tuple[str, dict, list[str]]:  # noqa: ANN001 - the fixture model's port
        """One tailoring through the CLI: its summary, the stored JSON, and the resume lines the model was shown."""

        before = len(port.prompts)
        summary = self.text("scout", "resume", "tailor", *self.job, *resume)
        assert len(port.prompts) == before + 1, "one tailor call"
        profile_id = resume[1] if resume[0] == "--profile" else None
        stored = next(item for item in list_tailored_resumes(self.home, self.scout) if item.resume.profile_id == profile_id)
        on_disk = json.loads(Path(stored.stored_path).read_text(encoding="utf-8"))
        return summary, on_disk, [text for _number, text in listed(port.prompts[-1])]


@pytest.fixture()
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> _Home:
    made = _Home(tmp_path)
    monkeypatch.setattr("gigai.scout.tailored_resume.load_config", lambda _home_root: ollama_config(made.home))
    return made


def _page(response: dict, label: str | None, status: dict | None) -> dict:
    """What the pages say (node): the job page's "from ..." header and the line under it for a stored tailored
    resume, and the profile's row on the Master page for its selection status."""

    node = shutil.which("node")
    if node is None:
        pytest.skip("node not found on PATH; the pages' texts are not checked")
    script = (
        f"import * as model from {json.dumps(MODEL_JS.resolve().as_uri())};\n"
        "const input = JSON.parse(process.argv[1]);\n"
        # Before the fix the header was written in the panel itself and always named the profile's resume.
        "const from = model.madeFrom ? model.madeFrom(input.response, input.label) : { lead: 'from resume', name: input.label || 'a pasted resume' };\n"
        "const below = model.pickedByLine(model.pickedLeftOut(input.response, null));\n"
        "const row = input.status ? model.selectionRow(input.status).line : null;\n"
        "process.stdout.write(JSON.stringify({ header: `${from.lead} ${from.name}`, basis: from.basis || null, below, row }));\n"
    )
    payload = json.dumps({"response": response, "label": label, "status": status})
    done = subprocess.run([node, "--input-type=module", "-e", script, "--", payload], capture_output=True, text=True, timeout=120, check=False, cwd=UI)
    assert done.returncode == 0, f"node failed:\n{done.stderr}"
    return json.loads(done.stdout)


def _resume_line(summary: str) -> str:
    return next(line.strip() for line in summary.splitlines() if line.strip().startswith("Resume:"))


def _item_ids(on_disk: dict) -> list[str]:
    lines = [line for section in on_disk["result"]["sections"] for line in (*section.get("lines", []), *(bullet for entry in section.get("entries", []) for bullet in entry["bullets"]))]
    return [ref["item_id"] for line in lines for ref in line["refs"] if ref.get("item_id")]


def test_the_header_the_line_under_it_and_every_status_name_the_basis_the_tailor_read(home: _Home, monkeypatch: pytest.MonkeyPatch) -> None:
    port = install_prompt_model(monkeypatch, copies_what_it_is_shown)
    master = home.cli("scout", "resume", "master", "show")["master"]
    master_texts = {item["text"] for item in master["items"] if item["kind"] != "skills"}
    own_resume = SWE_RESUME.read_text(encoding="utf-8")
    # For a profile on the older resume, these lines can only come from the master (the newer resume's lines are in it).
    only_in_the_master = {text for text in master_texts if text not in own_resume}
    assert len(only_in_the_master) > 10

    statuses = {item["profile_id"]: item for item in home.cli("scout", "resume", "master", "selection", "status")["profiles"]}
    status_text = home.text("scout", "resume", "master", "selection", "status")
    api = {item["profile_id"]: item for item in selection_response(home.home, home.scout)["profiles"]}
    assert [(statuses[home.ids[name]]["has_selection"], statuses[home.ids[name]]["attached"]) for name in ("selected", "bare", "detached")] == [
        (True, True), (False, None), (True, False),
    ], "the three profiles the rule tells apart"

    said: dict[str, dict] = {}
    read_by: dict[str, bool] = {}
    problems: list[str] = []
    for name, expected in CASES:
        profile_id = home.ids.get(name)
        resume = ("--profile", profile_id) if profile_id else ("--resume", str(SWE_RESUME))
        summary, on_disk, read = home.tailor(port, *resume)

        # --- what the tailor call READ: lines of the master, or the resume itself ---
        bullets = [text.removeprefix("- ") for text in read if text.startswith("- ")]
        read_the_master = any(text in only_in_the_master for text in bullets)
        if read_the_master:
            assert len([text for text in bullets if text not in master_texts]) <= 1, "every line but the Skills line code assembled is a master line"
        else:
            assert all(text in own_resume for text in bullets), "a resume that is not the master's is read line for line"
        reading = "the master" if read_the_master else "the resume itself"
        assert read_the_master == (expected == MASTER), f"{name}: the tailor call read {reading}"

        # --- what the stored resume RECORDS ---
        recorded = on_disk["sources"].get("master")
        assert (recorded is not None) == read_the_master, f"{name}: sources.master must say what was read"
        assert ("selection" in on_disk) == read_the_master and bool(_item_ids(on_disk)) == read_the_master
        if read_the_master:
            assert (recorded["revision_id"], recorded["revision"]) == (master["revision_id"], master["revision"])
            assert set(_item_ids(on_disk)) <= {item["id"] for item in master["items"]} | {entry["id"] for entry in master["entries"]}

        # --- what every text SAYS, against what was read (every disagreement is listed, not the first one) ---
        read_by[name] = read_the_master
        page = _page(on_disk, home.labels.get(profile_id) if profile_id else None, {**api[profile_id], "pending": False} if profile_id else None)
        cli_line = _resume_line(summary)
        said[name] = {"header": page["header"], "below": page["below"], "cli": cli_line, "basis": page["basis"], "row": page["row"]}
        if ("master" in page["header"]) != read_the_master or ("master" in page["below"]) != read_the_master:
            problems.append(f"{name}: the tailor call read {reading}; the header says {page['header']!r}, the line under it {page['below']!r}")
        if ("master" in cli_line) != read_the_master:
            problems.append(f"{name}: the tailor call read {reading}; the CLI's summary says {cli_line!r}")
        if profile_id is None:
            continue
        # The status of the profile, asked BEFORE the tailoring: the JSON, the text and the API.
        sentence = MASTER_LINE if expected == MASTER else OWN_RESUME_LINE
        own = next(block for block in _profile_blocks(status_text) if f"({profile_id})" in block)
        if sentence not in own or (MASTER_LINE in own) != read_the_master:
            problems.append(f"{name}: the tailor call read {reading}; the status says {own.strip()!r}")
        if not page["row"].endswith(sentence):
            problems.append(f"{name}: the tailor call read {reading}; the Master page's row says {page['row']!r}")
        for where, status in (("selection status --json", statuses[profile_id]), ("GET /api/master/selection", api[profile_id])):
            if (status.get("tailoring_basis"), status.get("tailoring_basis_line")) != (expected, sentence):
                problems.append(f"{name}: the tailor call read {reading}; {where} says tailoring_basis={status.get('tailoring_basis')!r}")
    assert not problems, "\n".join(problems)

    # The exact words, and the one rule behind them.
    from gigai.scout import tailor_master

    for name, expected in CASES:
        profile_id = home.ids.get(name)
        stored = next(item for item in list_tailored_resumes(home.home, home.scout) if item.resume.profile_id == profile_id)
        assert said[name]["basis"] == tailor_master.recorded_basis(stored) == expected
        if expected == MASTER:
            assert said[name]["header"] == f"from your master resume (revision {master['revision']}), picked for profile {home.labels[profile_id]}"
            assert said[name]["cli"] == f"Resume: your master resume, revision {master['revision']} (picked for profile {profile_id})"
            assert said[name]["below"].startswith("GigAI picked the candidate lines from your whole master")
        elif profile_id:
            assert (said[name]["header"], said[name]["below"]) == (f"from resume {home.labels[profile_id]}", "")
            assert said[name]["cli"] == f"Resume: the resume of profile {profile_id}"
        else:
            assert (said[name]["header"], said[name]["below"]) == ("from resume a pasted resume", "")
            assert said[name]["cli"] == "Resume: pasted resume (not stored as a profile)"
        record = home.record(name) if profile_id else None
        assert tailor_master.tailoring_basis(home.home, record, master_stored=True) == expected
        if record is not None:
            # The pipeline keys this profile's tailorings by the master exactly when they read it.
            assert bool(tailor_master.digest_parts(home.home, home.scout, record)) == read_by[name]
    # The ticket's two profiles say the same thing in both places, and the one with no selection is still told its resume is its own.
    assert said["bare"]["below"] == said["selected"]["below"]
    assert "its resume is its own" in next(block for block in _profile_blocks(status_text) if f"({home.ids['bare']})" in block)
    assert said["bare"]["row"] == f"Shows its own resume, not a selection of the master. {MASTER_LINE}"
    assert said["detached"]["row"].endswith(f"so it no longer shows this selection. {OWN_RESUME_LINE}")


def _profile_blocks(status_text: str) -> list[str]:
    """The status text, one block per profile (its line and the indented lines under it)."""

    blocks: list[str] = []
    for line in status_text.splitlines():
        if line.startswith("  ") and not line.startswith("    "):
            blocks.append(line)
        elif blocks and line.startswith("    "):
            blocks[-1] += "\n" + line
    return blocks


def test_the_rule_is_asked_in_one_place() -> None:
    """Behaviour and words come from ``tailoring_basis``: no reader decides the basis by itself, and the page writes no basis of its own."""

    from gigai.scout import assess_master, master_profiles, tailor_master

    scout = Path(tailor_master.__file__).parent
    rule = (scout / "tailor_master.py").read_text(encoding="utf-8").split("def tailoring_basis(", 1)[1].split("\ndef ", 1)[0]
    # ``detached`` is the rule's own input: it is called once in the product, inside the rule.
    calls = [
        (path.name, line.strip()) for path in sorted(scout.rglob("*.py")) if "node_modules" not in path.parts
        for line in path.read_text(encoding="utf-8").splitlines() if "detached(" in line and not line.lstrip().startswith("def ")
    ]
    assert len(calls) == 1 and calls[0][0] == "tailor_master.py" and calls[0][1] in rule, calls
    for reader in (tailor_master.master_tailoring, tailor_master.digest_parts, assess_master.reads_evidence, master_profiles._status):
        assert "tailoring_basis(" in Path(reader.__code__.co_filename).read_text(encoding="utf-8").split(f"def {reader.__name__}(", 1)[1].split("\ndef ", 1)[0], reader.__name__
    # The page: the header is the model's (the stored record), and the status line is the server's sentence.
    panel = PANEL_JSX.read_text(encoding="utf-8")
    assert "madeFrom(response, profileLabel)" in panel and "from resume <strong>" not in panel
    model = MODEL_JS.read_text(encoding="utf-8")
    assert "tailoring_basis_line" in model and tailor_master.BASIS_LINES[tailor_master.BASIS_MASTER] not in model, "the sentence has one home: the server"
