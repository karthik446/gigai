SHELL := /bin/sh

UV ?= uv
WHEEL_PYTHON ?= .wheel-venv/bin/python
TEST_XDIST_WORKERS ?= auto
TEST_XDIST_MAX_WORKERS ?= 14
TEST_XDIST_DIST ?= worksteal

.PHONY: test test-macos-smoke test-operator-home test-source test-behavior test-wheel test-installed test-live test-debian-offline unit-tests api-e2e eval-live

# Complete portable offline coverage: one source discovery pass, the existing
# deterministic behavior evaluation, and a fresh wheel plus every installed
# verifier and installed test node.  Live/provider/UAT and Debian-container
# gates are deliberately separate targets below.
test: test-source test-behavior test-wheel

# test-gap-001: an API end-to-end suite that drives Scout only through
# HTTP, against the real supervisor (the same entry `gigai scout run
# --no-browser` uses), a temp --home, and a real managed workpad. Fakes only
# the network edges (a fixture ATS/Exa transport, a fake model adapter --
# both existing, already-inert-unless-set bindings.py seams). Own lane, not
# part of `make unit-tests` (see that target's own comment): each test
# spawns a real child server process, so it is naturally excluded by the
# fast_unit AST classifier (tests/conftest.py) rather than needing a
# separate pytest marker or testpaths change. Localhost-only; no live
# provider/network calls. Run at wave ends and before release, per the
# coordinator's plan.
api-e2e:
	$(UV) run --locked --extra test pytest tests/api_e2e -q

# Fast inner-loop lane: only tests tests/conftest.py's AST classifier marks
# fast_unit (no filesystem, process, network, or mutable-workpad seam,
# tracing same-file helpers and cross-file test-to-test imports). Measured
# at 719 tests / ~6.6s wall on the 14-CPU reference host with -n 0; xdist
# start-up cost exceeded its benefit at this lane's size, so it runs
# unparallelized. Does not replace `make test`; see
# S19-test-suite-diet spike's revision note (kept in the maintainers' local
# orchestrator docs).
# --ignore=tests/api_e2e: test-gap-001's suite spawns a real child server
# process per test, and every -m fast_unit selector here is otherwise
# marker-based (never a path), so without this a handful of its tests that
# happen to have no filesystem/process/network seam of their own (the
# route-inventory AST-scan tests) would still get collected and run here,
# even though the suite as a whole belongs in its own `make api-e2e` lane.
unit-tests:
	$(UV) run --locked --extra test pytest -m fast_unit -q --durations=25 -n 0 --ignore=tests/api_e2e

# The unfiltered invocation is authoritative for source, unit, integration,
# behavior-directory, CLI, and source-installed test discovery.  It uses a
# capped resource-aware xdist plan by default; override TEST_XDIST_WORKERS for
# measurement, e.g. TEST_XDIST_WORKERS=14.  The cap matches the measured
# 14-CPU host while the runner clamps actual workers to os.cpu_count().  Do not
# add overlapping G28 file selectors here; they were previously run and then
# repeated by this full invocation in the default PR lane.
test-source:
	$(UV) run --locked --extra test python tools/run_ci_tests.py source \
		--xdist-workers "$(TEST_XDIST_WORKERS)" \
		--xdist-max-workers "$(TEST_XDIST_MAX_WORKERS)" \
		--xdist-dist "$(TEST_XDIST_DIST)"

# ci-spike: the release gate's macOS check. The files that failed only on macOS
# in 0.1.8-0.1.9: Scout server health/port takeover, installed init scenarios,
# and the setup -> config -> run journey. Not part of `make test`.
test-macos-smoke:
	$(UV) run --locked --extra test python -m pytest -n 3 --dist $(TEST_XDIST_DIST) \
		tests/behaviors/scout_find_jobs/test_scout_port_takeover.py \
		tests/behaviors/scout_find_jobs/test_scout_run_supervisor.py \
		tests/behaviors/installed_release/test_g04_installed_scenarios.py \
		tests/api_e2e/test_setup_config_run_poll_results.py

# 0110-9-01: the operator-sized timing gate (REQUIRED in the release pre-check: the `operator-home` job of
# pull_request.yaml, profile `release`). The real server process on a synthetic home the size of the operator's
# (290,000 postings, 10,350 companies, 2 profiles; tests/support/operator_home.py: no request, no real home is read):
# one build for 8 requests at once, progress and responsive routes during it, warm reads under 500 ms, bounded
# memory, a warm fresh process. About 2 minutes. Without GIGAI_OPERATOR_GATE=1 (the normal suite) the same test
# runs on a tenth of that home.
test-operator-home:
	GIGAI_OPERATOR_GATE=1 $(UV) run --locked --extra test python -m pytest -n 0 -q -s \
		tests/behaviors/scout_pipeline/test_operator_sized_home.py

# G28's deterministic evaluator is not a pytest test and therefore remains an
# explicit offline phase in the aggregate command.
test-behavior:
	$(UV) run --locked --extra test python tools/run_ci_tests.py behavior

# Build into a disposable directory and run the wheel-installed lane once.
# The runner enumerates every tools/verify_installed_*.py script, including
# newly added verifiers, then executes direct AST-derived installed-test ids.
# xdist options are intentionally source-lane-only and do not alter this lane.
test-wheel:
	$(UV) run --locked --extra test python tools/run_ci_tests.py wheel --wheel-python "$(WHEEL_PYTHON)"

# CI's wheel job can call this after its setup/install steps without rebuilding
# the wheel.  It still runs the complete installed verifier/test selection.
test-installed:
	$(UV) run --locked --extra test python tools/run_ci_tests.py installed --wheel-python "$(WHEEL_PYTHON)"

# Real local-model/provider/UAT execution requires explicit operator consent
# and configuration.  It never runs from `make test`.
test-live:
	@if [ "$${GIGAI_G30_UAT:-}" != "1" ]; then \
		echo "refusing live/provider UAT: set GIGAI_G30_UAT=1 explicitly" >&2; \
		exit 2; \
	fi
	$(UV) run --locked --extra test pytest -m g30_live

# P7 (v0.1.9): the assess eval against the operator's REAL configured model
# target (tests/evals/run_assess_eval.py), reading ~/.gigai (or $GIGAI_HOME)
# read-only and writing one report under ../orchestrator/research/evals/.
# Same consent gate shape as test-live: it never runs from `make test`, and
# the offline checks (tests/evals/test_eval_fixtures.py in unit-tests,
# tests/evals/test_eval_harness.py with the fake model in the source lane)
# never call a model.  Pass flags through EVAL_ARGS, e.g.
#   GIGAI_ASSESS_EVAL_LIVE=1 make eval-live EVAL_ARGS="--max-calls 30 --with-jev"
eval-live:
	@if [ "$${GIGAI_ASSESS_EVAL_LIVE:-}" != "1" ]; then \
		echo "refusing the live assess eval: set GIGAI_ASSESS_EVAL_LIVE=1 explicitly" >&2; \
		exit 2; \
	fi
	$(UV) run --locked --extra test python tests/evals/run_assess_eval.py $(EVAL_ARGS)

# Debian's direct-mount/read-only container contract is a platform-specific
# CI gate.  It remains explicit because it cannot be truthfully run on every
# developer host and is not silently treated as a portable local pass.
test-debian-offline:
	docker build --tag gigai-debian-offline:local --file containers/debian-offline/Dockerfile .
	docker run --rm \
		--network none \
		--read-only \
		--user 10001:10001 \
		--tmpfs /tmp:rw,exec,nosuid,nodev,mode=1777 \
		--tmpfs /audit/home:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
		--tmpfs /audit/target:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
		--tmpfs /audit/workpad:rw,nosuid,nodev,uid=10001,gid=10001,mode=0700 \
		--env HOME=/audit/home \
		--env GIGAI_AUDIT_HOME=/audit/home \
		--env GIGAI_AUDIT_TARGET=/audit/target \
		--env GIGAI_AUDIT_WORKPAD=/audit/workpad \
		gigai-debian-offline:local

# Public docs site (gigai-docs/, Astro Starlight; Node 22). Reference pages (CLI, Scout API,
# changelog) are generated from the code; commit what docs-gen writes. CI runs docs-check.
# docs-gen replaced 0110-008's `cli-manual` / docs/cli.md: one generated manual, one freshness gate.
.PHONY: docs-gen docs-check docs-dev docs-build
docs-gen:
	$(UV) run --locked python tools/docs_gen.py

docs-check:
	$(UV) run --locked python tools/docs_gen.py --check

docs-dev: docs-gen
	cd gigai-docs && npm ci && npm run dev

docs-build: docs-check
	cd gigai-docs && npm ci && npm run build

# Release screenshots (tools/media; 0110-049). Builds a synthetic demo home in a temporary HOME
# (never ~/.gigai), runs the real Scout server on fixture transports (no network, no model),
# takes the UI screenshots (Playwright, 1280x800, light + dark) and the terminal frames, then
# runs the privacy gate (DOM text + OCR; a hit exits non-zero). About 80 s; timings are printed.
# One-time tools, no sudo, no pip:  brew install tesseract   (Linux: apt-get install tesseract-ocr)
# `playwright install chromium` below downloads the browser once (about 95 MB, cached per user).
#   make media            build into $(MEDIA_OUT) (git-ignored)
#   make media-publish    build, then copy the set to gigai-docs/public/media/ (commit that)
#   make media-check      the committed set matches its manifest (no browser needed)
.PHONY: media media-publish media-check operator-ui-check
MEDIA_OUT ?= build/media
media:
	$(UV) run --locked --group media playwright install chromium
	$(UV) run --locked --group media python -m tools.media.build --out "$(MEDIA_OUT)"

media-publish: media
	$(UV) run --locked python -m tools.media.manifest publish "$(MEDIA_OUT)"

media-check:
	$(UV) run --locked python -m tools.media.manifest check gigai-docs/public/media

# Browser tests (tests/ui; the `ui` dependency group, Playwright + Chromium). A small synthetic
# home (tools/media/demo_home.py) in a temporary HOME, the real Scout server on fixture
# transports, no network, no model, never ~/.gigai. About a minute, most of it building the home.
# The default pytest run and the CI shards deselect `ui` tests (-m "not ui" in pyproject.toml).
# GIGAI_UI_REQUIRED=1: a missing browser is a failure here, not a skip. On a failed test the
# screenshot, trace, requests, console, server log tail and CPU/RSS samples are written to
# $(UI_ARTIFACTS)/<test>/. One-time: `playwright install chromium` below (no sudo, no pip).
# No retry anywhere: see tests/ui/README.md.
.PHONY: ui-test
UI_ARTIFACTS ?= build/ui-artifacts
ui-test:
	$(UV) run --locked --group ui playwright install chromium
	GIGAI_UI_REQUIRED=1 GIGAI_UI_ARTIFACTS="$(UI_ARTIFACTS)" $(UV) run --locked --group ui --extra test pytest tests/ui -m ui -n 0 -q --tb=short --durations=5

# 0110-9-01, a standing release rule: load the UI in a REAL browser on the operator-sized synthetic home before every
# release (tools/media/operator_ui_check.py; Playwright + Chromium, as `make media` installs them; about 2 minutes).
# The Jobs page loads on a cold server (the "preparing" message, then the rows), one job is opened, BACK shows the
# rows at once, the Background panel opens. Exits non-zero on a slow step, more than one request in flight for a
# resource, or a console error; the numbers and screenshots are written to $(OPERATOR_UI_OUT) (git-ignored).
OPERATOR_UI_OUT ?= build/operator-ui-check
operator-ui-check:
	$(UV) run --locked --group media playwright install chromium
	$(UV) run --locked --group media python -m tools.media.operator_ui_check --out "$(OPERATOR_UI_OUT)"
