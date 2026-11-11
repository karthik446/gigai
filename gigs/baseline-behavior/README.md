# gigai-baseline-behavior

A gig for [gigai](https://github.com/karthik446/gigai): capture what an HTTP service answers today as immutable
snapshots, diff two snapshots, report. Started from `gigai gig new baseline-behavior`; it lives in the gigai
repository under `gigs/` and ships as its own distribution, never inside the `gigai` wheel.

```
gigai gig check .                # manifest, entry point, help text, docs, tests
gigai gig install .              # into the gigai you run (a `uv tool` install or a venv)
gigai baseline-behavior init --target http://127.0.0.1:8000
gigai baseline-behavior capture --snapshot before --requests requests.jsonl
gigai baseline-behavior capture --snapshot after --requests requests.jsonl
gigai baseline-behavior diff before after          # exit 1 when they differ
gigai baseline-behavior report --snapshot after --json
gigai baseline-behavior generate-tests --snapshot before --dry-run
```

`requests.jsonl` holds one request per line: `{"method": "GET", "path": "/users"}`,
`{"method": "POST", "path": "/users", "body": {"name": "ada"}}`.

Layout: `src/gigai_baseline_behavior/gig.toml` is the contract with core (read without importing this package);
`cli.py` is the Click group mounted as `gigai baseline-behavior`; `baseline.py` does the work (standard library
only: `urllib.request`, `sqlite3`); `prompts/draft-tests.md` is the prompt of model step `draft-tests`; `docs/`
ships the user page and the CLI manual; `tests/` is what `gigai gig check` runs.
