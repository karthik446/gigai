SHELL := /bin/sh

UV ?= uv
WHEEL_PYTHON ?= .wheel-venv/bin/python
TEST_XDIST_WORKERS ?= auto
TEST_XDIST_MAX_WORKERS ?= 14
TEST_XDIST_DIST ?= worksteal

.PHONY: test test-source test-behavior test-wheel test-installed test-live test-debian-offline unit-tests api-e2e eval-live

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
