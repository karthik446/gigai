# Browser tests (`tests/ui`)

Real Chromium (Playwright for Python) against the real Scout server, on a small synthetic home
built through the real CLI and the fixture transports (`tools/media/demo_home.py`). No network, no
model, and never `~/.gigai`: HOME is a fresh temporary directory, and the harness refuses to run
if it resolves to the real home (`support.refuse_real_home`).

## Run

```sh
make ui-test        # installs Chromium once, builds the home (about 25 s), runs every `ui` test
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
