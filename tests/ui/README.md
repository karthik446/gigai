# Browser tests (`tests/ui`)

Real Chromium (Playwright for Python) against the real Scout server, on a small synthetic home
built through the real CLI and the fixture transports (`tools/media/demo_home.py`). No network, no
model, and never `~/.gigai`: HOME is a fresh temporary directory, and the harness refuses to run
if it resolves to the real home (`support.refuse_real_home`).

In this file: [Run](#run) · [The flows](#the-flows) ·
[The operator-sized home](#the-operator-sized-home-make-ui-test-full) · [Add a flow](#add-a-flow) ·
[No retries](#no-retries-ever) · [Budgets](#budgets-in-this-order) ·
[In CI, and reading a red run](#in-ci-githubworkflowspull_requestyaml) ·
[What is not covered](#what-is-not-covered) · [Before a release](#before-a-release)

## Run

```sh
make ui-test        # installs Chromium once, builds the home (about 25 s), runs every `ui` test on the small home (25 tests: 137 s on a busy laptop, 2026-10-04)
```

| Command | Home | What runs | Where it runs |
|---|---|---|---|
| `make ui-test` | small (13 postings) | every `ui` test that is not `operator_sized` | every PR (the `ui` job), and before you push a UI change |
| `make ui-test-full` | small, then operator-sized (290,000 postings) | every `ui` test | a laptop, before a release: the two below in one run |
| `make ui-test-operator` | operator-sized | the `operator_sized` tests alone | the release pre-check (the `operator-home` job) |

One file, while writing a flow (the last `-m` wins over the default `-m "not ui"`):

```sh
GIGAI_UI_REQUIRED=1 uv run --locked --group ui --extra test pytest tests/ui/test_jobs_chips.py -m ui -n 0 -q
```

A subset run builds its own fresh home, so it does not see what an earlier flow changed.

- The `ui` dependency group (`uv run --group ui ...`) holds Playwright and psutil. No pip.
- `ui` tests are **deselected by default** (`-m "not ui"` in `pyproject.toml`), so the CI shards
  and `pytest` alone never start a browser. `make ui-test` selects them (`-m ui`) and sets
  `GIGAI_UI_REQUIRED=1`, which turns a missing Chromium into a failure instead of a skip.
- The browser-free helper tests (`test_harness_support.py`) are not marked and run in every shard.
- Run with `-n 0`: one home and one Scout server per session.
- On a failed test, `build/ui-artifacts/<test>/` (`GIGAI_UI_ARTIFACTS` overrides it) holds
  `screenshot.png`, `trace.zip` (`playwright show-trace`), `requests.json` (every `/api/` request
  and step), `console.txt`, `problems.txt`, `server-log-tail.txt`, `samples.csv` (server CPU
  seconds and RSS, and requests in flight, four times a second), and `timeout.txt` when the
  failure was a wait that ran out of patience (below). CI uploads that folder.

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
| Master resume page: the migration with its question and its count of every resume line (the left-out line listed by number and reason), by role, add / edit / retire / restore, the profiles' "refresh?" offer and Refresh, a near-duplicate asked about and "Add it anyway" (0.1.10.9 master P5, P6) | `test_master_page.py` (changes the home) | - (the routes are timed on the operator-sized home by hand: see the P5 worker report) |
| Master resume page and the file in the resumes folder: the line about `master.md`, "changes not imported yet" after the user edits the file, **Import the file** (one write), a page write that leaves the edited file alone (0.1.10.9 master P8) | `test_master_file.py` (changes the home) | - (timed on the operator-sized home by hand: see the M8 worker report) |
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
- `make ui-test-operator` runs the `operator_sized` tests alone (the half `make ui-test` leaves
  out). The release pre-check uses it, on a home built ONCE for the timing gate and these flows:
  `python -m tests.support.operator_home <temporary HOME>/op --pristine` leaves the home, never
  read, at `op.pristine`; with `GIGAI_OPERATOR_HOME_PREBUILT=<temporary HOME>/op` each of the two
  takes a fresh copy of it at `op` (`operator_home.take_prebuilt`), so both start on a server that
  has read nothing. The copy goes back to the path the home was built for and nowhere else: the
  home holds its own absolute path (`config.toml`, the assessment records). Without the variable
  every run builds its own home, as before.
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
add the test id to the UI if the element has none (the rule in full, and its one exception:
"Selectors", below).

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

### Selectors: the UI's hooks

A flow finds an element, and waits for it, by a **hook**: an attribute the UI carries so that a
test or a screenshot can find the element whatever its wording, class or place.

| Hook | The UI puts it on | In a flow |
|---|---|---|
| `data-testid` | a thing a flow or a release screenshot looks for | `tid("job-row")`, `tid("step-timeline")` |
| `data-role` | a part of a page: a panel, a line, a value | `[data-role="postings-count"]`, `[data-role="job-state"]` |
| `data-action` | a control a person clicks | `[data-action="open-job"]`, `[data-action="delete-profile"]` |

The rule:

- **Wait on a hook. Never on text, never on a sleep.** Text changes with the wording and a sleep
  passes or fails with the machine. There is no `wait_for_timeout` and no `time.sleep` in a flow.
- **Use the hook that is there.** Most elements have a `data-role` or a `data-action` already.
- **If the element has none, add one to `ui/src`** rather than selecting by role and name, by
  text or by a class chain. Adding it is part of writing the flow, and it is an **inert attribute** only: no
  behaviour, no style, no wording (the three `data-action` attributes on the profile's Delete, its
  confirmation and Cancel were added this way). A `ui/src` change ships with `ui/dist` rebuilt
  once, at the end; the bundle job in CI fails when the two differ.
- **Inside a hooked element, plain structure is fine** for reading: `row.locator("input")`,
  `box.locator("label")`.
- **Text is what a flow asserts, not what it waits on.** Read it from a hooked element; where the
  server knows the value (a count, a title), compare it with what the server said
  (`ui.server_json`), not with a number written down when the test was made.

The one exception: **text may pick ONE element among hooked ones when its identity is data, and
never to wait for something.** A row by the title the server gave
(`locator(tid("job-row") + " [data-action='open-job']", has_text=job["title"])`), a stat tile by
its label (`.stat-tile:has-text("Postings") .stat-value`: the tiles are one component). The wait
before it is still on a hook. There is no other exception: a control with no hook gets one.

### The fixtures

| Fixture | Lives for | What it gives |
|---|---|---|
| `ui` | one test | One page (1280x800, reduced motion) on the small home's server, with its books: every `/api/` request, the console, the named steps, the server's CPU and memory sampled four times a second, the heartbeat, a trace. On a failure it writes the artifacts; at the end it fails the test for any console error, page error, HTTP 400+ or failed request. Waits have 20 s of patience (`GIGAI_UI_PATIENCE_MS`). |
| `operator_ui` | one test | The same on the operator-sized home. Taking it marks the test `operator_sized`. |
| `scout_server` | the session | The small home (`tools/media/demo_home.py`, built through the real CLI in a temporary HOME) and its real server on a free port, fixture transports only: `.url`, `.pid`, `.log_path`, `.target`. |
| `operator_server` | the session | The operator-sized home and its real server process, cold: `.url`, `.built` (postings, companies, profiles), `.build_seconds`, `.start_seconds`, `.prebuilt`, `.log_text()`. |
| `ui_artifacts` | the session | The artifact folder (`build/ui-artifacts`), emptied at the start of a run. |
| `ui_browser` | the session | Headless Chromium. A flow rarely needs it: a second tab is `ui.page.context.new_page()`. |

A test that takes any of them except `ui_artifacts` is marked `ui` by the harness. Helpers that need no browser go in
`support.py` with a test in `test_harness_support.py` (not marked: it runs in every shard).

### Before the flow is done

1. It waits on hooks only, and asks the server for what it expects.
2. It has a structural assertion (requests after a step, one in flight, what the page shows, no
   write from a page that only reads) and a budget for each step a person waits on
   (`ui.wall_budget`, and `ui.cpu_budget` where the server does work), the limits as named constants
   with the measured numbers beside them.
3. Each structural assertion was seen red once: revert the behaviour it guards, run the flow, put
   it back. An assertion that has never failed is not known to check anything.
4. If it changes the home it says so (`UI_ORDER`), and it cleans up what it wrote.
5. If the test answers for the server anywhere, the docstring names the requests and the test that
   proves the real route, and the list under "The flows" gets a line.
6. It has a row in the table under "The flows".
7. `make ui-test` is green twice in a row, and once more with the machine busy if a budget is
   tight. No retry made it green.

## No retries. Ever.

No retry decorator, plugin, `--reruns`, loop or "try again" in any test or helper. A retry turns a
wrong budget or a real race into a pass and hides it. A flaky step is fixed by changing what it
asserts (below), not by running it again. A step that fails on timing prints wall, CPU seconds and
the request list, so "the runner was slow" and "the server did more work" are told apart. The CI
jobs have no retry either: no rerun flag, no retry action (`test_ui_ci_job.py` checks).

Why, from what the rule has already caught:

- **A wrong budget.** In the spike a 500 ms wall budget on a list request failed 3 of 6 runs on a
  busy machine, with the server doing the same work (its CPU time within 10%). A retry would have
  passed all 6 and kept a budget that measures the runner, not the product.
- **A real race.** The Past runs flow failed 2 of 10 runs with every core busy. It was a product
  bug: a late answer to an earlier read replaced the selected profile's runs. A retry would have
  shipped it. The fix came with a test that forces the order, so it now fails every time or never.
- **A stall nobody could read.** One run in 20 stalled in the browser for 57 s. It was not retried
  and not explained, so every timeout now writes `timeout.txt` (below).

`test_no_retry_anywhere_in_the_ui_tests` (in `test_harness_support.py`) reads every file in this
folder for the words of a retry (`reruns`, `flaky`, `tenacity`, `backoff`, `@retry`, ...). In a
review, a red run that went green on a second push with no change is the same thing: say what
failed the first time.

### A wait that times out explains itself (`timeout.txt`)

U3 saw one browser stall in 20 runs with every core busy: the server idle, the page silent for
57 s, the next click timing out after 20 s. It was not explained, and it was not retried. So that
the next one can be read afterwards, a test that fails on a timeout (Playwright's `TimeoutError`,
or a failure that names one) gets a `timeout.txt` in its artifact folder, and its first lines in
the test's output:

- a one-line reading, said to be a guess: the page was waiting for the server (requests open), the
  browser stalled (the page's script had not run), the browser stopped drawing (the script ran, no
  frame), or the page was alive and never showed what the test waited for;
- the requests in flight at that moment, with how long each had been open, and the last ten that ended;
- the page's heartbeat: every page tells the harness twice a second, from its own timer, that its
  script runs and that it has drawn a frame (`support.Heartbeat`, `ui.heartbeat`). The report says
  when the last beat and the last frame were, and the longest silence in the last minute;
- whether the server answered `/api/health` when asked directly (2 s), the server's CPU over the
  last 10 s of samples, the machine's load average;
- the browser console and the last 60 lines of the server log.

The page is not asked anything at that moment, on purpose: with tracing on (always, here) a
Playwright call into a page whose script is stuck does not time out, it waits for the page
(measured: a `wait_for_function` with a 1 s timeout came back after the page's 6 s busy loop).
`test_page_heartbeat.py` plants a 4 s stall on a real page and reads it back from the heartbeat.

## Budgets, in this order

1. **Structure** (cannot flake): requests in flight per resource (`no_more_than_one_in_flight`),
   requests after a step (`requests_after(step) <= n`), what the page shows. **Blocks.**
2. **Server CPU seconds** between two steps: `ui.cpu_budget(name, LIMIT, a, b)`, about 3x the
   measured value (and not under 1 s: the background threads share the process). A busy runner
   moves it by under 10% on a large step and up to 3x on the smallest. **Measured and reported;
   blocks only with `GIGAI_UI_BUDGETS=enforce`.**
3. **Wall-clock**, last and loose: `ui.wall_budget(name, LIMIT, a, b)`, 4 to 10x what a laptop
   needs; the user-facing ones are `support.INTERACTIVE_WALL_SECONDS` (2 s: a click) and
   `support.FIRST_LOAD_WALL_SECONDS` (10 s: a page). A 500 ms wall budget on a list request failed
   3 of 6 runs on a loaded machine with the server doing the same work. **Measured and reported;
   blocks only with `GIGAI_UI_BUDGETS=enforce`.**

Every limit is a named constant at the top of its test, with the measured numbers beside it. Never
`assert` a wall time directly (a test in `test_harness_support.py` refuses it).

### What blocks, and the report (the first week, 0.1.10.9)

Structure and console / page / HTTP problems fail a test from day one, here and in CI: they cannot
flake. No timing ceiling has been measured on a CI runner yet, so for the first week ALL of them are
only reported: wall-clock, server CPU seconds, and the server's memory ceiling on the operator-sized
home (`ui.memory_budget`, `operator_home_ui.SERVER_RSS_MB`). One switch decides:

| `GIGAI_UI_BUDGETS` | A timing ceiling that is over |
|---|---|
| `report` (the default, also when unset; what both CI jobs set) | a line in the report, `OVER (reported, not enforced)` |
| `enforce` | fails its test, with the step's wall time, the server's CPU seconds and its requests |

Any other value stops the run before it starts: a misspelt `enforce` would enforce nothing.

Every run ends with a `ui budgets` section (each ceiling: measured, limit, `ok` or `OVER`) and
writes the same to `build/ui-artifacts/budgets.json` (`mode`; per ceiling `kind`: `wall`, `cpu` or
`rss`, `measured`, `limit`, `unit`, `over`, `blocking`; `run`: what the run and the home builds took).

```sh
GIGAI_UI_BUDGETS=enforce make ui-test     # a ceiling that is over fails its test
```

Once the CI jobs have a week of `budgets.json`, set the limits from it and write `enforce` in the two
jobs of `pull_request.yaml` (one line each).

### Flipping to enforce

"The first week" starts with the first `ui` job on a GitHub runner, not with a date in this file.
Until the flip, a green job can hold a ceiling that is over: read the summary.

1. Collect the week: each run's `ui-budgets-<job>-attempt-<n>` artifact holds `budgets.json`
   (`gh run download <run> -n ui-budgets-small-home-attempt-1`). The job summary of a run shows the
   same numbers.
2. For each ceiling take the largest `measured` of the week on the runner. Keep the rule above:
   CPU about 3 times that (not under 1 s), wall 4 to 10 times, memory 1.5 times. Change the named
   constant at the top of its test and write the runner's numbers beside it. A ceiling that was
   over in the week is looked at first: a limit is not raised to make a real slowdown pass.
3. Write `GIGAI_UI_BUDGETS: enforce` in the `ui` job and in the `operator-home` job
   (`test_ui_ci_job.py` pins what the two jobs set: change it in the same commit).
4. Run `GIGAI_UI_BUDGETS=enforce make ui-test-full` once locally, then watch the first PR run.
5. Replace "What blocks, and the report" above with one sentence: every ceiling blocks.

To go back for one run, set `report` in the job again. There is no per-test switch, on purpose.

## In CI (`.github/workflows/pull_request.yaml`)

| Job | When | Runs | Step limit |
|---|---|---|---|
| `ui` (Browser tests, small home) | every PR, every release pre-check; not the post-release sweep | `make ui-test` | 10 min (the job: 20) |
| `operator-home` (timing gate and browser flows) | the release pre-check only (profile `release`) | the home built once, `make test-operator-home`, then `make ui-test-operator` | 5, 10 and 5 min (the job: 30) |

- Both set `GIGAI_UI_BUDGETS: report`. A failed structural or console check fails the job.
- The step limits are the smallest multiple of 5 minutes that is at least 3 times the time on a
  busy laptop (2026-10-04: `make ui-test` 137 s, the home build 44 to 50 s, the browser flows on the
  prebuilt home 61 s; the timing gate took 42 s and gets 10 minutes, it is the gate that blocks a
  release). A step that reaches its limit is a failed step, so the summary and the artifacts are
  still uploaded. None of this has run on a runner yet: the first runs replace the numbers.
- Chromium is cached in `~/.cache/ms-playwright`, keyed on the installed Playwright version (a new
  Playwright in `uv.lock` downloads its own browsers). `playwright install --with-deps chromium`
  runs before the timed step on every run: on a cache hit it only installs the system libraries
  (they are not in the cache).
- Every run: `budgets.json` is rendered into the job summary (`tools/ui_budgets_summary.py`: what is
  over, the ten ceilings closest to their limit, all of them folded) and uploaded as
  `ui-budgets-<job>-attempt-<n>` (30 days).
- A failed run also uploads `build/ui-artifacts/` as `ui-failure-<job>-attempt-<n>` (14 days):
  per failed test the screenshot, the trace, the requests, the console, the server log tail, the
  samples, and `timeout.txt` when it was a timeout.
- No retry: no rerun flag, no retry action, no `continue-on-error`.

### Reading a run

**A green run.** The job page's summary ("UI budgets: ...") opens with `N ceilings measured, M over`
and the mode. In `report` mode a ceiling that is over does not fail the job, so look at that line
on every run you rely on. Under it: what is over, the ten ceilings closest to their limit, and
every ceiling folded. `budgets.json` in the `ui-budgets-...` artifact is the same data;
`python3 tools/ui_budgets_summary.py <path>/budgets.json` prints the summary from it. The
`operator-home` artifact also holds `operator-sized-jobs.json`: the cold load, Back, requests in
flight, builds and peak memory of the 0110-9-01 flow.

**A red run.** In this order:

1. The step's log. The failing assertion says what the page showed, the address and the requests
   in flight. A line `[ui] artifacts for <test>: <folder> (...)` names the folder; after a timeout a
   block `[ui] <test>: A wait ran out of patience ...` is the head of `timeout.txt`, so the reading
   is in the log even if the upload failed.
2. Download `ui-failure-<job>-attempt-<n>`. One folder per failed test, named after the test.
3. In that folder:

| File | Read it for |
|---|---|
| `reason.txt` | why the folder was kept: the test failed, it could not start, or it passed its own checks and the browser reported problems |
| `problems.txt` | the console errors, page errors, HTTP statuses of 400 or more and failed requests. A test with none of its own failures and one line here is red for that line |
| `timeout.txt` | only after a timeout: start here (below) |
| `screenshot.png` | the page at the end of the test (`screenshot-failed.txt` instead when the page could not be photographed: that is itself a sign of a stalled browser) |
| `requests.json` | `steps` (each `ui.step` with its time, the server's CPU seconds so far and the requests before it) and `requests` (every `/api/` request in order: start, seconds, status). A request with `seconds: null` never ended |
| `console.txt` | everything the page logged |
| `server-log-tail.txt` | the server's last 200 lines: a traceback, a 500, how often it built the postings |
| `samples.csv` | four lines a second: the server's CPU seconds and memory (MB), the requests in flight and the age of the oldest. A flat CPU column with requests open is a server that waits; a rising one is a server that works |
| `trace.zip` | `uv run --group ui playwright show-trace <folder>/trace.zip`: the page frame by frame with each action and request |

**`timeout.txt`.** Its second paragraph is `reading (a guess from the lines below): ...`. The lines
under it are the evidence; the reading is one of:

| The reading | What to look at next |
|---|---|
| "the page was waiting for the server: N request(s) open" | the open requests and their age, then `server-log-tail.txt` and the CPU column of `samples.csv`. This is the 0110-9-01 shape: a product slowdown until shown otherwise |
| "the page's script had not run for N s: the browser stalled" (or "sent no heartbeat at all") | the heartbeat lines and the machine's load. The server is not the cause. Open requests listed here may have been answered: a stopped page cannot finish them, and the server log says |
| "no frame was drawn for N s: the browser stopped drawing" | the same, and the trace: the last frame is older than the last action |
| "it waited for something the page never showed" | the test or the page: a hook that was renamed, a state that never came. `screenshot.png` and the failure's own message |
| "the server did not answer when asked directly" | the server is stuck or gone: `server-log-tail.txt` |

A browser stall is still a red run. It is reported with its `timeout.txt`, not re-run: the stall
seen once in 20 busy runs is not explained yet, and the next one is the evidence. `timeout.txt` is
written at the end of the test, so for a flow that collects its failed checks and goes on (the cold
operator-sized flow) it describes the end of the test, not the first timeout.

**A step that reached its time limit** is killed before pytest prints its summary: the summary step
says `budgets.json` was not written, and the folders of tests that had already failed are still
uploaded (what is expected; not yet seen on a runner). **No artifact at all** on a red run means
the failure was before the tests (the sync, the Chromium install) or after them: the step's log is
all there is.
### Known, pinned as ceilings (reported in the U3 worker report)

- The page asks `/api/assessments` twice per profile on a Jobs load (`test_smoke_flow.py`,
  `ASSESSMENT_REQUESTS`).

## What is not covered

A green run says the pages load, show what the server holds and do not pile up requests, on two
synthetic homes, in one browser. It does not say:

- **How a page looks.** Layout, spacing, colour, dark mode. A hook being present says nothing about
  what a person sees. The release screenshots (`make media`, `make operator-ui-check`) are still
  looked at by a person.
- **What a real model writes.** The fixture model returns the same requirements and rank for every
  posting. Wording, verdict quality, real latency and real cost are not tested here.
- **The network and real boards.** Every source is a made-up board answered in-process. The one-time
  network notice and a first update are answered by the test, not by a real update.
- **The operator's real home.** Its mix of providers, its history across upgrades, more than 2
  profiles, hours of uptime, several tabs for a day. The operator-sized home has the size, not the
  history. The first run after a real upgrade on a real home stays a person's check.
- **Other browsers, small screens, accessibility.** Headless Chromium at 1280x800 only.
- **A past run's own page** (`#/runs/<id>`): the small home has no run, so the links are checked and
  the page behind them is not.
- **States the small home does not have,** where a test answers for the server (the list under
  "The flows"): a list of 130, weak fits, a three-page resume, two past runs.
- **The 500 ms list target.** It is asserted in-process by the timing gate
  (`make test-operator-home`, CPU time), not in a browser: in a browser on a busy runner it failed
  3 of 6 runs for the same server work.
- **Per-request server cost.** CPU seconds are measured per step for the whole server process, so
  background work in the same seconds is counted in.
- **A GitHub runner, until the first runs.** Every time and ceiling in this file is from a laptop.
  The flows had not run on Linux when this was written; the first `ui` runs replace the numbers.
- **The operator-sized home under load.** The busy-core series (10 of 10) was the small home only.

## Before a release

The release checklist (kept with the release notes, outside this repository) asks for these, on the
release-candidate commit, with the numbers quoted:

1. the `ui` job green, with the first line of its budgets summary and every ceiling that is over;
2. the `operator-home` job green in the release pre-check: the timing gate's lines, the browser
   flows' budgets summary, and `operator-sized-jobs.json`;
3. `make operator-ui-check` run by a person: `build/operator-ui-check/operator-ui-check.json` and
   the five screenshots beside it (a green CI run keeps no screenshot);
4. every read path that is new or changed, timed on the operator-sized home: a row of the timing
   gate, an `operator_sized` flow here, or a number taken by hand with its command.

A flow added here for a new page is what makes item 4 cheap: take `operator_ui` instead of `ui`.
