---
title: Development
description: Run the tests and work from a source checkout.
---

Contributions and bug reports are welcome: see
[CONTRIBUTING.md](https://github.com/karthik446/gigai/blob/main/CONTRIBUTING.md)
and the [issues](https://github.com/karthik446/gigai/issues).

```bash
uv sync --locked --extra test
make test              # source + behavior + wheel-resource suites
make test-source       # source, unit, integration, and CLI tests only
make api-e2e           # Scout's HTTP API end to end, against a real server
uv run --locked pytest tests/behaviors/scout_find_jobs -q   # a focused slice
```

`make api-e2e` drives the Scout find-jobs API only through HTTP, against the
real supervised server, a temp home, and a real managed workpad; only the
network edges (Exa/ATS, the local model) are faked. It's localhost-only,
makes no live provider calls, and takes a couple of minutes.

- `src/gigai/`: core runtime: setup, config, journal, proposal/approval
  lifecycle, catalog, package boundary. Meant not to import a Gig (some Scout
  imports remain today).
- `src/gigai/scout/`: the Scout Gig: `find_jobs/` (acquire/assess/present),
  bundled goal-graph data, and `ui/` (the separate Vite/React checkout below).

## From a source checkout (contributors)

The Scout UI ships prebuilt inside the wheel (`gigai scout run` serves it
directly; nothing in the quickstart needs a source checkout or a Vite dev server). If
you're editing `src/gigai/scout/ui/` itself, run its dev server against a
`gigai scout run` (or standalone `present_api`) instance for hot reload:

```bash
cd src/gigai/scout/ui
yarn install
yarn dev
```

Point it at the running API's loopback URL (`gigai scout status` prints it).
Rebuild the shipped bundle with `yarn build` before committing UI changes;
CI's `scout-ui-freshness` job fails the PR if `src/gigai/scout/ui/dist` is
stale.

## This documentation site

The CLI, API and changelog pages are generated from the code. Run `make docs-gen`
and commit the result; CI runs `make docs-check`. `make docs-dev` serves the site locally.
