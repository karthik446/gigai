# gigai-docs

The public GigAI docs site: https://karthik446.github.io/gigai/ (one folder per release,
plus `latest/`, `next/` and `dev/`). Astro Starlight, Node 22. Not part of the Python package.

```bash
make docs-gen      # regenerate reference pages (CLI, Scout API, changelog) from the code
make docs-dev      # local preview with hot reload
make docs-build    # what CI runs: freshness check, build, offline link check
```

- Write pages in `src/content/docs/` (Markdown). Link with relative URLs (`../install/`).
- Never edit files marked `GENERATED`; change the code or `src/gigai/data/cli/commands.yaml`.
- One folder per Gig (`scout/`). Core pages link to a Gig only from `index.md`
  (`make docs-check` enforces it), so a Gig's folder can move to its own repo.
- Publishing: `.github/workflows/docs.yml` publishes `next` on a push to a `karthik446/gigai-v*`
  branch, `dev` on a push to main, and each release as `<version>` + `latest` (called from
  `release.yml`). It deploys nothing until the repo variable `DOCS_PUBLISH` is `true` and the
  `gh-pages` branch exists (see the header of `docs.yml`). Pull requests only build and check.
