# Browser tests (`tests/ui`)

Real Chromium (Playwright for Python) against the real Scout server, on a small synthetic home
built through the real CLI and the fixture transports (`tools/media/demo_home.py`). No network, no
model, and never `~/.gigai`: HOME is a fresh temporary directory, and the harness refuses to run
if it resolves to the real home (`support.refuse_real_home`).

## Run

```sh
make ui-test        # installs Chromium once, builds the home (about 25 s), runs every `ui` test on the small home (about 80 s in all)
```

- The `ui` dependency group (`uv run --group ui ...`) holds Playwright and psutil. No pip.
- `ui` tests are **deselected by default** (`-m "not ui"` in `pyproject.toml`), so the CI shards
  and `pytest` alone never start a browser. `make ui-test` selects them (`-m ui`) and sets
  `GIGAI_UI_REQUIRED=1`, which turns a missing Chromium into a failure instead of a skip.
- The browser-free helper tests (`test_harness_support.py`) are not marked and run in every shard.
- Run with `-n 0`: one home and one Scout server per session.
- On a failed test, `build/ui-artifacts/<test>/` (`GIGAI_UI_ARTIFACTS` overrides it) holds
  `screenshot.png`, `trace.zip` (`playwright show-trace`), `requests.json` (every `/api/` request
  and step), `console.txt`, `problems.txt`, `server-log-tail.txt`, `samples.csv` (server CPU
  seconds and RSS, and requests in flight, four times a second). CI uploads that folder.

## The flows

The 11 flows of the UI-testing spike (REPORT.md 5.3), and the flows of the features 0.1.10.9 added. "Small" runs in
`make ui-test` (every PR); "operator-sized" only in `make ui-test-full` (the release profile).

| Flow | Small home | Operator-sized home |
|---|---|---|
| 1. Jobs loads, rows shown | `test_smoke_flow.py` | `test_operator_sized_jobs.py` (cold), `test_operator_sized_pages.py` (warm) |
| 2. Open a job, Back, the list is still there | `test_jobs_open_and_back.py` | `test_operator_sized_jobs.py`; from page 2 in `test_operator_sized_pages.py` |
| 3. Chips: profile, New / 7 / 30 days, state | `test_jobs_chips.py` | `test_operator_sized_pages.py` |
| 4. "Assess these": the dialog, approve on the fixture model | `test_jobs_assess_these.py` (changes the home); the low-ranked second question in `test_jobs_weak_fit.py` | - |
| 5. Job page: questions, answer one, the timeline runs, the tailored resume it stored shows without a reload | `test_job_page_questions.py` (changes the home) | a job page in a second tab WHILE the list is prepared: `test_operator_sized_jobs.py` |
| 6. Generate PDF: six fields, a download, nothing stored | `test_generate_pdf.py` | - |
| 7. Answers and stories | `test_answers_stories.py` | the agent writes one: `test_operator_sized_pages.py` |
| 8. Settings > Background updates | `test_settings_background.py` | Settings in a second tab WHILE the list is prepared: `test_operator_sized_jobs.py` |
| 9. Past runs | `test_past_runs.py` | - |
| 10. Delete a profile | `test_profile_delete.py` (last: it changes the home for good) | - |
| 11. The one-time network notice | `test_network_notice.py` | - |
| Real pages (0110-10-01) | `test_jobs_pagination.py` (an answered list of 130) | `test_operator_sized_pages.py`: the real list, page 2 = rows 51-100, a job, Back lands on page 2 |
| Weak-fit chip off by default (0110-10-02) | `test_jobs_weak_fit.py` (an answered list) | the real list: `test_operator_sized_pages.py` |
| Answers: "Written by your agent" with its source | `test_answers_stories.py` | `test_operator_sized_pages.py` |
| Tailored resume: "Cut for length" + Restore, the resumes-folder line (0110-10-05) | `test_tailored_resume_panel.py`; the folder in Settings: `test_resumes_folder.py` | - |
| Master resume page: the migration with its question, by role, add / edit / retire / restore, the profiles' "refresh?" offer and Refresh, a near-duplicate asked about and "Add it anyway" (0.1.10.9 master P5, P6) | `test_master_page.py` (changes the home) | - (the routes are timed on the operator-sized home by hand: see the P5 worker report) |
| Job page: Picked / Left out with reasons, Add and Remove, the "keep 2 pages" question, "Save this wording to your master" | `test_picked_left_out.py` (changes the home) | - |

**Where a test answers for the server.** Everything is the real server unless the test's docstring says otherwise,
and then only the named requests are answered by the test (the page, its stores and the router stay real):
the list of 130 and the weak fits (`test_jobs_pagination.py`, `test_jobs_weak_fit.py`: the small home has neither);
the length record of a tailored resume (`test_tailored_resume_panel.py`: a cut needs a resume that prints on three
pages, and the fixture model writes three lines); `/api/sources/update` of a home that never had an update
(`test_network_notice.py`: a real first update would seed the real company catalog into the shared home); two past
runs (`test_past_runs.py`: the small home has none); ONE answer of `PUT /api/tailored-resumes/selection`, an Add that
needs room (`test_picked_left_out.py`: the small home's resume is half a page; every other request of that flow is
the real server's). Each of those has the real route proven elsewhere, named in the docstring.

## The operator-sized home (`make ui-test-full`)

```sh
make ui-test-full   # the release profile: everything above, plus the `operator_sized` tests (about 2.5 minutes)
```

0.1.10.8 passed every test and its Jobs page never loaded on the operator's home (0110-9-01): no
test opened the UI on a home of that size. A test that takes `operator_ui` (or `operator_server`)
runs on one: 290,000 postings, 10,350 companies, 2 profiles, about 600 matched postings.

- The home is `tests/support/operator_home.py` (synthetic; the same generator as the timing gate
  and `make operator-ui-check`), built **once per session**, by a child process, in a fresh
  temporary HOME that is removed at the end. About 45 s on a laptop. Never `~/.gigai`: the fixture
  refuses a HOME outside the temporary directory, and the test asserts the server's own HOME.
- The server is the real process (`tools.media.operator_ui_check.start_server`, the one
  `make operator-ui-check` starts), with its background threads running, and **cold**: nothing has
  read the list before the first page does. Nothing is stubbed; the list is the real route.
- Such a test is marked `operator_sized` (automatically) as well as `ui`. `make ui-test` deselects
  it (`-m "ui and not operator_sized"`); `make ui-test-full` runs both.
- The build time, the server start time and the flow's numbers are printed, and written to
  `build/ui-artifacts/operator-sized-jobs.json`.
- The session's home is shared and cold only once: a second `operator_sized` test starts warm, and
  must not depend on the order unless it says so. The cold flow says so (`UI_ORDER = -1`: it runs
  first); the others open Jobs with the cold patience and apply warm budgets only on a warm server.
- The server's background rank lane ranks about 200 not-assessed postings every half minute for its
  first minutes, which MOVES rows in the list while the tests run. Compare a page with the answer
  the server gave that page (`jobs_page.click_chip`, `expect_response`), never with a second read.
- The generator's clock is fixed (`NOW`, 2026-10-03): the "7 days" chip lists nothing a week later
  and "30 days" a month later. The flows hold with an empty window; "New since last check" is
  relative to the home's own anchor and does not age.

`test_operator_sized_jobs.py` is the 0110-9-01 flow: Jobs loads (the "preparing" message, then the
rows; while it prepares, a second tab opens Settings and a job page), a job is opened, Back, the
list is still there; at most one request in flight for the list, the peek and the status; one
build; the server's memory under its ceiling; zero console errors. It collects every failed check
and fails once, at the end, with all of them.

### Showing a test red on an older release

`GIGAI_UI_SERVER_ROOT=<checkout>` builds and serves the operator-sized home with the product code
of another checkout (its `src/` first on `PYTHONPATH` of the two child processes; bytecode goes to
the temporary HOME, so nothing is written into that checkout). The tests, the generator and the
harness stay this tree's.

```sh
GIGAI_UI_SERVER_ROOT=/path/to/a/v0.1.10.8/checkout GIGAI_UI_REQUIRED=1 \
  uv run --locked --group ui --extra test pytest tests/ui/test_operator_sized_jobs.py -m ui -n 0 -q
```

On v0.1.10.8 that run fails (2026-10-03, two runs, 140 and 145 s). Both runs: the page never said
it was preparing (it said "Loading postings…" for 63 to 67 s); 51 and 76 server CPU seconds for the
first page (ceiling 45); **back from the job, the list was loading again** (one run: no row in
30 s, `/api/postings` and `/api/new` still in flight; the other: "Loading postings…", rows after
4 s, and two `/api/new` requests in flight at once); no single build logged. On this tree the same
run passes in about 60 s.

It applies to the operator-sized fixtures only: the small home is built in this process.

## Add a flow

A test function that takes the `ui` fixture (it is marked `ui` automatically; `pytestmark =
pytest.mark.ui` in the module is welcome too). Wait on a `data-testid`, never on text or a sleep;
add the test id to the UI if the element has none.

```python
def test_something(ui):
    ui.goto("/#/jobs")
    ui.wait_for_jobs_list()
    ui.step("loaded")                      # a named boundary for the measurements below
    ui.page.click(tid("time-chip-7d"))
    ui.step("chip")
    ui.no_more_than_one_in_flight("/api/postings")
    assert ui.requests_after("loaded", "/api/postings") <= 1
    assert ui.server_cpu_seconds_between("loaded", "chip") <= 1
    assert ui.wall_seconds_between("loaded", "chip") <= 4
```

Every test fails on its own for a console error, a page error, an HTTP status of 400 or more, or
a request that failed (a request cut short by a navigation is not a failure). Use `ui.reload()`,
not `page.reload()`: Playwright sends no event for the requests a reload drops.

The home and server are shared by the whole session, so:

- a flow that changes the home through the page says so with a module-level `UI_ORDER = <n>` (a
  positive number runs after every module without one, lowest first; `test_profile_delete.py` is
  the last);
- a flow that has to write something to look at it (an agent's answer) removes it again, in a
  `finally`;
- what a flow expects is asked of the server at the start of the test (`ui.server_json(path)`, the
  server asked directly, as an agent would; the page's books do not count it), not written down
  from what the fixture held when the test was written.

Useful on `ui`: `wait_for_job_page()` (the title, the state line and the timeline: a heading alone
passed in the spike with every request still open), `settle()` (nothing in flight, before counting
requests), `requests_between(a, b, resource)`, `writes_after(step)` (every request that is not a
GET: a page that only reads must have none), `job_titles()`; `tests/ui/jobs_page.py` for the Jobs
rows and chips.

## No retries. Ever.

No retry decorator, plugin, `--reruns`, loop or "try again" in any test or helper. A retry turns a
wrong budget or a real race into a pass and hides it. A flaky step is fixed by changing what it
asserts (below), not by running it again. A step that fails on timing prints wall, CPU seconds and
the request list, so "the runner was slow" and "the server did more work" are told apart.

## Budgets, in this order

1. **Structure** (cannot flake): requests in flight per resource (`no_more_than_one_in_flight`),
   requests after a step (`requests_after(step) <= n`), what the page shows. **Blocks.**
2. **Server CPU seconds** between two steps: `ui.cpu_budget(name, LIMIT, a, b)`, about 3x the
   measured value (and not under 1 s: the background threads share the process). A busy runner
   moves it by under 10%. **Blocks.**
3. **Wall-clock**, last and loose: `ui.wall_budget(name, LIMIT, a, b)`, 4 to 10x what a laptop
   needs; the user-facing ones are `support.INTERACTIVE_WALL_SECONDS` (2 s: a click) and
   `support.FIRST_LOAD_WALL_SECONDS` (10 s: a page). A 500 ms wall budget on a list request failed
   3 of 6 runs on a loaded machine with the server doing the same work. **Measured and reported;
   blocks only with `GIGAI_UI_BUDGETS=enforce`.**

Every limit is a named constant at the top of its test, with the measured numbers beside it. Never
`assert` a wall time directly (a test in `test_harness_support.py` refuses it).

### What blocks, and the report (the first week, 0.1.10.9)

Structure, console / page / HTTP problems, the CPU ceilings and the server's memory ceiling on the
operator-sized home (`ui.memory_budget`, `operator_home_ui.SERVER_RSS_MB`) fail a test from day
one. The wall-clock ceilings have not been measured on a CI runner yet, so for now they are only
reported: every run ends with a `ui budgets` section (each ceiling: measured, limit, `ok` or
`OVER`) and writes the same to `build/ui-artifacts/budgets.json` (`kind`: `wall`, `cpu` or `rss`;
`measured`, `limit`, `unit`, `over`, `blocking`).

```sh
GIGAI_UI_BUDGETS=enforce make ui-test     # a wall-clock ceiling that is over fails its test
```

An enforced wall-clock failure prints the step's wall time, the server's CPU seconds and its
requests, so "the runner was slow" and "the server did more work" are told apart. Once the CI job
has a week of `budgets.json`, set the limits from it and make `enforce` the default.

### Known, pinned as ceilings (reported in the U3 worker report)

- The page asks `/api/assessments` twice per profile on a Jobs load (`test_smoke_flow.py`,
  `ASSESSMENT_REQUESTS`).
