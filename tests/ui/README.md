# Browser tests (`tests/ui`)

Real Chromium (Playwright for Python) against the real Scout server, on a small synthetic home
built through the real CLI and the fixture transports (`tools/media/demo_home.py`). No network, no
model, and never `~/.gigai`: HOME is a fresh temporary directory, and the harness refuses to run
if it resolves to the real home (`support.refuse_real_home`).

## Run

```sh
make ui-test        # installs Chromium once, builds the home (about 25 s), runs every `ui` test on the small home
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

## The operator-sized home (`make ui-test-full`)

```sh
make ui-test-full   # the release profile: everything above, plus the `operator_sized` tests (about 2 minutes)
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
  must not depend on the order unless it says so.

`test_operator_sized_jobs.py` is the 0110-9-01 flow: Jobs loads (the "preparing" message, then the
rows), a job is opened, Back, the list is still there; at most one request in flight for the list,
the peek and the status; one build; zero console errors. It collects every failed check and fails
once, at the end, with all of them.

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
The home and server are shared by the whole session: a flow that changes the home runs last.

## No retries. Ever.

No retry decorator, plugin, `--reruns`, loop or "try again" in any test or helper. A retry turns a
wrong budget or a real race into a pass and hides it. A flaky step is fixed by changing what it
asserts (below), not by running it again. A step that fails on timing prints wall, CPU seconds and
the request list, so "the runner was slow" and "the server did more work" are told apart.

## Budgets, in this order

1. **Structure** (cannot flake): requests in flight per resource (`no_more_than_one_in_flight`),
   requests after a step (`requests_after(step) <= n`), what the page shows.
2. **Server CPU seconds** between two steps (`server_cpu_seconds_between(a, b) <= x`): about 3x the
   measured value. A busy runner moves it by under 10%.
3. **Wall-clock**, last and loose: 4 to 10x what a laptop needs. A 500 ms wall budget on a list
   request failed 3 of 6 runs on a loaded machine with the server doing the same work.

Write down the measured numbers next to the ceilings, as `test_smoke_flow.py` does.
