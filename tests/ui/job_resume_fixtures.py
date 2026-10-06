"""0.1.11 N6: what the job page's flows answer for the server, where this tree's server does not serve it yet.

The job page of 0.1.11 reads things the small home's server cannot give it (its fixture model assesses in the v8 shape
and its pipeline is still the 0.1.10 one until packet N4):

- the v9 fields of a stored assessment (N3): each row's `id`, `class_basis`, `alternatives` and `sources`, the
  assessment's `structured_suggestions`, and the item's `resume_gate`;
- the job's suggestions, the suggestion actions and the code-only pick (N5's routes, which the real server now has: it
  holds no v9 record for a small-home job, so they are answered here, in the real shapes);
- a job resume whose `producer.callable` is `scout.pick` (N3 / N4: this tree's server still tailors).

`JobResumeFixture` answers exactly those, for ONE job, and nothing else:

- `GET /api/assessments`, `GET /api/tailored-resumes` and the three writes of the stored resume (`/selection`,
  `/length`, `/lines`) are the REAL server's answers, fetched by the route, with the fields above laid over them
  (the way `test_tailored_resume_panel.py` lays a length record over the real stored resume). So an Add is a real
  write of the real store, and the page shows the real lines of the small home's master;
- the three N5 routes are answered here, from a record built in the stored shape (`SuggestionRecord.to_json`,
  `scout-job-suggestions:1`) and served as the routes serve it (`job_actions`). `ready` is worked out from what the
  REAL stored resume prints (SPEC 2.3), so it follows a real Add or Remove. Every record built is read through the
  product's own parser (`gigai.scout.suggestions`).

The page asks for the suggestions only for a job whose assessment carries a `resume_gate`; the fixture lays one over the
real assessment, so it asks, and the fixture answers in the shapes the real routes use (N5, `job_actions`):
GET /api/jobs/suggestions {gate, counts, suggestions}; POST /api/job-resumes/pick {job_url, profile_id, action} ->
the job's stored view. The N5 routes are the server's own and are proven by N5's tests; the small home cannot reach
the states they need (a v9 assessment), which is why they are answered here.

Synthetic only: the requirement words, the suggestion sentences and the posting phrases below are made up; the
resume lines are the demo persona's.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

try:  # N3's module: not in this tree until it merges
    from gigai.scout import suggestions as _suggestions
except ImportError:  # pragma: no cover - the state of this tree
    _suggestions = None

RECORD_KEYS = ("schema_version", "profile_id", "job_identity", "created_at", "updated_at", "stored_path", "basis", "gate", "requirements", "selection", "proposed", "suggestions")
NOW = "2026-10-05T10:00:00+00:00"

#: The fixture's requirement rows beyond the assessment's own (ids in the product's shape, `req-` plus hex).
ROW_LOST = "req-c0ffee"
ROW_ANSWER_ONLY = "req-a17e21"
ROW_GAP = "req-9a9a9a"
LOST_REQUIREMENT = "Operating a Postgres fleet in production"
ANSWER_ONLY_REQUIREMENT = "A document store in production"
GAP_REQUIREMENT = "Kubernetes in production"
ALTERNATIVES = ["Cassandra", "MongoDB"]
CLASS_BASIS = "What we are looking for: production experience with the stack below"
POSTING_PHRASE = "run Postgres at scale"


def row_id(place: int) -> str:
    return f"req-a{place:05x}"


def printed_ids(stored: dict | None) -> list[str]:
    """The master line ids a stored resume prints (SPEC 2.3): a copied line's own id, every id a changed line cites."""

    ids: list[str] = []
    for section in ((stored or {}).get("result") or {}).get("sections", []):
        lines = list(section.get("lines", [])) + [line for entry in section.get("entries", []) for line in entry.get("bullets", [])]
        for line in lines:
            base = line.get("edited_from") if line.get("kind") == "custom" and isinstance(line.get("edited_from"), dict) else line
            for ref in base.get("refs", []):
                if ref.get("kind") == "resume" and ref.get("item_id") and ref["item_id"] not in ids:
                    ids.append(ref["item_id"])
    return ids


def check_selection(rows: list[dict], printed: list[str], conflicts: list[dict]) -> tuple[list[dict], bool, list[dict]]:
    """SPEC 2.3 (`suggestions.check_selection`), on the record's own row shape: the rows with their coverage, `ready`, the reasons."""

    out, reasons = [], []
    for row in rows:
        entry = {"id": row["id"], "class": row["class"], "status": row["status"], "sources": list(row["sources"]), "in_resume": [], "coverage": None}
        if row["status"] == "met":
            lines = [source for source in row["sources"] if not source.startswith("A ")]
            kept = [source for source in lines if source in printed]
            entry["in_resume"] = kept
            entry["coverage"] = "kept" if kept else "lost" if lines else "answer_only" if row["sources"] else "none"
            if entry["coverage"] == "lost" and row["class"] in ("hard", "askable", None):
                reasons.append({"code": "lost_mandatory_evidence", "requirement": row["id"]})
        out.append(entry)
    reasons += [{"code": "selection_conflict", "requirement": conflict.get("requirement")} for conflict in conflicts]
    return out, not reasons, reasons


class JobResumeFixture:
    """The answers for one job (the module text). Set the attributes, `install(ui)`, then open the job."""

    def __init__(self, ui, *, profile_id: str, job_identity: str) -> None:
        self.ui = ui
        self.profile_id = profile_id
        self.job_identity = job_identity
        #: The gate of the assessment: `suggest`, `hold_unmet`, ... and its reasons (row ids).
        self.decision = "suggest"
        self.gate_reasons: list[dict] = []
        #: The assessment's rows beyond the real ones (each a full v9 row), and the real rows' sources (by place).
        self.extra_rows: list[dict] = []
        self.real_sources: dict[int, list[str]] = {}
        #: The stale list the suggestion route serves.
        self.stale: list[str] = []
        #: Whether the job has a stored resume at all ("none": the list is answered empty), and whose it is.
        self.resume = "stored"
        self.picked = True  # the stored resume reads as the pick's (`producer.callable: scout.pick`)
        #: Lines of the stored selection's Picked list that read as the assessment's pick: line id -> {code, reason}.
        #: (This tree's server has no pick: a flow puts the lines on the resume through the real Add, and they are
        #: served with the code a pick gives them instead of `added_by_you`.)
        self.relabel: dict[str, dict] = {}
        self.selection: dict | None = {}  # laid over the record's selection; None: the record has none
        self.proposed: dict | None = None
        self.conflicts: list[dict] = []
        self.suggestions: list[dict] = []
        self.structured: list[dict] = []  # the assessment's own structured suggestions
        self.master: dict | None = {"revision_id": "rev-fixture", "revision": 1, "content_sha256": "sha256:" + "0" * 64}
        self.record_served = True  # False: the route answers `record: null`
        #: ASSUMED, not the server's today: GET /api/jobs/suggestions carries the pick view's stale list, pages, conflicts and proposed.
        self.serve_view = True
        #: What the page sent to the three N5 routes, in order.
        self.picks: list[dict] = []
        self.actions: list[dict] = []
        self.reads = 0
        self._stored: dict | None = None  # the real stored resume, as last served
        self._real_rows: list[dict] = []  # the real assessment's rows, by id (set when the fixture is installed)

    # ------------------------------------------------------------------ what is served

    def rows(self) -> list[dict]:
        """The record's requirement rows: the real assessment's (ids by place) and the fixture's."""

        return [*self._real_rows, *({"id": row["id"], "class": row.get("class"), "status": row["status"], "sources": list(row.get("sources", []))} for row in self.extra_rows)]

    def record(self) -> dict | None:
        if not self.record_served:
            return None
        printed = printed_ids(self._stored) if self.resume == "stored" else []
        selection = None
        if self.selection is not None and self.resume == "stored":
            selection = {
                "picked_by": "model", "fallback": None, "draft": False, "pick_rules_version": "pick-rules:1", "selector_version": "sel-4", "made_at": NOW,
                "made_from": {"result_digest": "sha256:" + "1" * 64, "master_revision_id": "rev-fixture"}, "model_pick": None, "problems": [],
                "added_by_code": [], "line_marks": [{"id": item, "mark": "0" * 16} for item in printed], "pages": 2, "max_pages": 2,
                "conflicts": copy.deepcopy(self.conflicts), "resume": {"stored_path": "resumes/fixture.json", "markdown_sha256": "sha256:" + "2" * 64, "origin": "pick"},
                **self.selection,
            }
        rows, ready, reasons = check_selection(self.rows(), printed, self.conflicts if selection else [])
        record = {
            "schema_version": "scout-job-suggestions:1", "profile_id": self.profile_id, "job_identity": self.job_identity, "created_at": NOW, "updated_at": NOW,
            "stored_path": "suggestions/fixture.json",
            "basis": {"assessment": {"stored_path": "quick_assess/fixture.json", "assessed_at": NOW, "result_digest": "sha256:" + "1" * 64, "prompt_version": "assess-prompt-v9",
                                     "instructions_digest": "sha256:" + "3" * 64, "model": "fixture", "constraints_digest": "sha256:" + "4" * 64, "story_bank_digest": None},
                      "posting_sha256": "sha256:" + "5" * 64, "requirements": {"rules_version": "req-rules:1", "digest": "sha256:" + "6" * 64, "list": "stored"},
                      "master": self.master},
            "gate": {"decision": self.decision, "ready": bool(self.decision == "suggest" and selection is not None and ready), "reasons": [*self.gate_reasons, *(reasons if selection else [])]},
            "requirements": rows, "selection": selection, "proposed": copy.deepcopy(self.proposed), "suggestions": copy.deepcopy(self.suggestions),
        }
        assert tuple(record) == RECORD_KEYS
        if _suggestions is not None:  # the product's own reader, once N3 is in the tree
            _suggestions.SuggestionRecord.from_json(json.loads(json.dumps(record)))
        return record

    def suggestions_body(self) -> dict:
        """GET /api/jobs/suggestions as the server answers it (`job_actions._suggestions_body`)."""

        record = self.record() or {"gate": {"decision": self.decision, "ready": None, "reasons": []}, "suggestions": [], "updated_at": NOW}
        items = record["suggestions"]
        body = {
            "schema_version": "scout-job-suggestions-view:1", "job_identity": self.job_identity, "profile_id": self.profile_id, "updated_at": record["updated_at"],
            "gate": record["gate"], "counts": {status: sum(1 for item in items if item["status"] == status) for status in ("open", "done", "dismissed")}, "suggestions": items,
        }
        if self.serve_view:
            # NOT what the server of this tree answers: it serves these fields only in the answer of a pick step. The page reads
            # them from the list when they are there (jobResumeModel.suggestionsAnswer); this flag is the assumption, in one place.
            view = self.pick_view()
            body.update({key: view[key] for key in ("stale", "picked", "conflicts", "added_by_code", "proposed", "selection_error", "resume")})
        return body

    def _users(self) -> bool:
        """Whether the user changed the stored resume (an Add, a Remove, a changed line): then a re-pick may not replace it (SPEC 2.4)."""

        stored = self._stored or {}
        selection = stored.get("selection") or {}
        # (a line a flow relabels as the pick's own is not the user's: that is how the flow stages a resume the pick made)
        moved = any(line.get("code") == "added_by_you" and line["id"] not in self.relabel for line in selection.get("picked", [])) or any(line.get("code") == "removed_by_you" for line in selection.get("left_out", []))
        lines = [line for section in (stored.get("result") or {}).get("sections", []) for line in [*section.get("lines", []), *(b for e in section.get("entries", []) for b in e["bullets"])]]
        return moved or any(line.get("origin") == "user" or line.get("kind") == "custom" for line in lines)

    def pick_view(self, action: str | None = None) -> dict:
        """The answer of POST /api/job-resumes/pick as the server gives it (`job_actions.pick_view` + `action`)."""

        record = self.record() or {}
        selection = record.get("selection")
        view = {
            "schema_version": "scout-job-pick:1", "job_identity": self.job_identity, "profile_id": self.profile_id, "verdict": "matched_above_threshold",
            "gate": record.get("gate"), "stale": list(self.stale),
            "resume": {"updated_at": NOW, "made_by": "scout.pick" if self.picked else "scout.tailor", "edited": None, "replaceable": self.picked and not self._users(), "lines": 0, "counts": None, "folder_path": None, "markdown": ""}
            if self.resume == "stored" else None,
            "picked": None if selection is None else {key: selection.get(key) for key in ("picked_by", "fallback", "draft", "made_at", "pages", "max_pages", "pick_rules_version", "selector_version")},
            "problems": [], "added_by_code": list((selection or {}).get("added_by_code", [])), "conflicts": list((selection or {}).get("conflicts", [])),
            "selection_error": None,
            "proposed": None if self.proposed is None else {key: self.proposed.get(key) for key in ("picked_by", "fallback", "draft", "made_at", "pages", "conflicts")},
        }
        return {**view, "action": action} if action else view

    def _assessment(self, item: dict) -> dict:
        """The real stored assessment with the v9 fields laid over it."""

        item = copy.deepcopy(item)
        result = item["result"]
        real = []
        for place, row in enumerate(result["matrix"], start=1):
            row.setdefault("class", "hard")
            row.update({"id": row_id(place), "class_basis": CLASS_BASIS})
            if row["status"] == "met":
                row["sources"] = list(self.real_sources.get(place, []))
            real.append({"id": row["id"], "class": row["class"], "status": row["status"], "sources": list(row.get("sources", []))})
        self._real_rows = real
        result["matrix"] = [*result["matrix"], *copy.deepcopy(self.extra_rows)]
        if self.structured:
            result["structured_suggestions"] = copy.deepcopy(self.structured)
            result["suggestions"] = [entry["why"] for entry in self.structured]
        item["resume_gate"] = {"decision": self.decision, "reasons": copy.deepcopy(self.gate_reasons)}
        if self.decision == "hold_unmet" and isinstance(item.get("job_state"), dict):
            item["job_state"] = {**item["job_state"], "state": "has_gap"}  # what N3's `derive_job_state` serves for this gate
        return item

    def _resume(self, item: dict) -> dict:
        item = copy.deepcopy(item)
        if self.picked:
            item["producer"] = {**item.get("producer", {}), "callable": "scout.pick"}
        for line in (item.get("selection") or {}).get("picked", []):
            line.update(self.relabel.get(line["id"], {}))
        return item

    # ------------------------------------------------------------------ the routes

    def _assessments(self, route) -> None:
        if route.request.method != "GET":
            route.continue_()
            return
        body = route.fetch().json()
        body["items"] = [self._assessment(item) if item.get("job", {}).get("job_identity") == self.job_identity else item for item in body.get("items", [])]
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def _resumes(self, route) -> None:
        request = route.request
        path = urlsplit(request.url).path
        if path == "/api/tailored-resumes" and request.method == "GET":
            query = parse_qs(urlsplit(request.url).query)
            if query.get("job_identity", [""])[0] != self.job_identity:
                route.continue_()
                return
            body = route.fetch().json()
            if body.get("items"):
                self._stored = body["items"][0]
            body["items"] = [self._resume(item) for item in body.get("items", [])] if self.resume == "stored" else []
            route.fulfill(status=200, content_type="application/json", body=json.dumps(body))
            return
        if path in ("/api/tailored-resumes/selection", "/api/tailored-resumes/length", "/api/tailored-resumes/lines") and request.method == "PUT":
            response = route.fetch()
            if response.status != 200:
                route.fulfill(response=response)
                return
            body = response.json()
            self._stored = {key: value for key, value in body.items() if key != "selection_change"}
            route.fulfill(status=200, content_type="application/json", body=json.dumps(self._resume(body)))
            return
        route.continue_()  # the PDF, and anything else, is the real server's

    def _suggestion_route(self, route) -> None:
        request = route.request
        if request.method == "GET":
            query = parse_qs(urlsplit(request.url).query)
            assert query.get("url") == [self.job_identity] and query.get("profile_id") == [self.profile_id], query
            self.reads += 1
        else:
            body = json.loads(request.post_data or "{}")
            self.actions.append(body)
            for item in self.suggestions:
                if item["id"] == body.get("suggestion_id"):
                    how = "dismissed" if body["action"] == "dismiss" else body.get("how")
                    item.update({"status": "dismissed" if body["action"] == "dismiss" else "done", "resolved": {"by": body.get("actor", "operator"), "at": NOW, "how": how, "ref": None}})
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.suggestions_body()))

    def _pick_route(self, route) -> None:
        body = json.loads(route.request.post_data or "{}")
        self.picks.append(body)
        action = body.get("action")
        if action == "refresh":
            self.stale = []
        elif action == "draft":
            self.resume = "stored"
            self.selection = {"picked_by": "code", "fallback": "draft_requested", "draft": True}
        elif action == "use_proposed":
            self.proposed, self.picked, self.stale = None, True, []
            self.selection = {}
        elif action == "dismiss_proposed":
            self.proposed = None
        route.fulfill(status=200, content_type="application/json", body=json.dumps(self.pick_view(action)))

    def install(self) -> "JobResumeFixture":
        # What the record is built from is read first (the server asked directly): the page may ask for the record
        # before it asks for the assessment or the resume.
        items = self.ui.server_json(f"/api/assessments?profile_id={quote(self.profile_id, safe='')}")["items"]
        own = [item for item in items if item.get("job", {}).get("job_identity") == self.job_identity]
        assert own, f"the server holds no assessment of {self.job_identity} for {self.profile_id}"
        self._assessment(own[0])
        self._stored = stored_resume(self.ui, self.profile_id, self.job_identity)
        page = self.ui.page
        # The routes below are answered for ONE job; the real server has them since N5 but holds no v9 record for the
        # small home's jobs (its fixture model assesses in the v8 shape). A page that already runs holds the assessments it
        # read before: the next document reads them through the fixture.
        if page.url != "about:blank":
            self.ui.settle()
            page.goto("about:blank")
        page.route("**/api/assessments*", self._assessments)
        page.route("**/api/tailored-resumes**", self._resumes)
        page.route("**/api/jobs/suggestions*", self._suggestion_route)
        page.route("**/api/job-resumes/pick", self._pick_route)
        return self


def resume_key(profile_id: str, job_identity: str) -> str:
    return f"profile_id={quote(profile_id, safe='')}&job_identity={quote(job_identity, safe='')}"


def stored_resume(ui, profile_id: str, job_identity: str) -> dict | None:
    """The job's stored resume as the server holds it (asked directly), or None."""

    items = ui.server_json(f"/api/tailored-resumes?{resume_key(profile_id, job_identity)}")["items"]
    return items[0] if items else None


def fresh_resume(ui, profile_id: str, job_url: str) -> dict:
    """The job's resume made again by the server, from the master as it is now: no line of it is the user's.

    A flow that needs a resume nobody has edited asks for one here, whatever an earlier flow left on the shared home
    (an Add, a changed line). Like `ensure_stored_resume`, this is the route of this tree; with packet N4 it is the
    code-only pick (`POST /api/job-resumes/pick`).
    """

    made = ui.server_json("/api/tailored-resumes", {"job": {"job_url": job_url}, "resume": {"profile_id": profile_id}}, timeout=180)
    assert made.get("selection"), "with a master stored, the job's resume carries Picked / Left out"
    stored = stored_resume(ui, profile_id, job_url)
    assert stored is not None
    return stored


def ensure_stored_resume(ui, profile_id: str, job_url: str) -> dict:
    """The job's stored resume; made first, through the server's own route, when the home holds none for it.

    ONE place to change with packet N4: this tree's server makes a job resume with `POST /api/tailored-resumes` (the
    fixture model; that route answers 410 once tailoring is removed). From then on a resume is stored when the job is
    assessed, and `POST /api/job-resumes/pick` makes one on request.
    """

    stored = stored_resume(ui, profile_id, job_url)
    if stored is None:
        ui.server_json("/api/tailored-resumes", {"job": {"job_url": job_url}, "resume": {"profile_id": profile_id}}, timeout=180)
        stored = stored_resume(ui, profile_id, job_url)
    assert stored is not None, f"no stored resume for {job_url}"
    return stored


# ---------------------------------------------------------------------------- screenshots for a review

#: With `GIGAI_UI_EVIDENCE=<folder>` the job-page flows also write screenshots of their steps there (PNG, the page's own
#: text beside each in `text/`, for `python -m tools.media.privacy_scan <folder>`). Unset, they write nothing.
EVIDENCE_ENV = "GIGAI_UI_EVIDENCE"


def evidence_folder() -> Path | None:
    return Path(os.environ[EVIDENCE_ENV]).resolve() if os.environ.get(EVIDENCE_ENV) else None


#: The one line of the job page that names a folder of the machine: the jobs folder, inside this run's temporary
#: HOME (`~/op/home/resumes/...`). It is no user's path, and the privacy gate rightly reads any `/home/<name>` as one:
#: the line is masked in the picture and left out of its text, and the picture's text says that it was.
FOLDER_LINE = '[data-testid="jobs-folder-file"]'
TEXT_JS = """(selector) => {
  const body = document.body.cloneNode(true);
  body.querySelectorAll(selector).forEach((node) => { node.textContent = '[the jobs-folder line: masked in this picture]'; });
  document.body.appendChild(body);  // innerText needs a rendered node
  const text = body.innerText;
  body.remove();
  return text;
}"""


def shot(ui, folder: Path | None, name: str) -> None:
    """A screenshot of the page with its own text beside it (`text/<name>.txt`), for the media privacy gate."""

    if folder is None:
        return
    (folder / "text").mkdir(parents=True, exist_ok=True)
    ui.page.evaluate("() => window.scrollTo(0, 0)")  # the page's top bar is drawn where the page is scrolled to
    ui.page.screenshot(path=str(folder / f"{name}.png"), full_page=True, mask=[ui.page.locator(FOLDER_LINE)])
    values = ui.page.evaluate("() => Array.from(document.querySelectorAll('input, textarea')).map((field) => field.value).filter(Boolean)")
    (folder / "text" / f"{name}.txt").write_text(ui.page.evaluate(TEXT_JS, FOLDER_LINE) + "\n" + "\n".join(values) + "\n", encoding="utf-8")
