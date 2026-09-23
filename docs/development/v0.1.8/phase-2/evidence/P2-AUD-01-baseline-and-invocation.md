# P2-AUD-01 — v0.1.7 baseline and S10 local invocation audit

**Audit status:** completed-audit (producer evidence complete; release,
publication, current installed-caller, live/provider, and human-UAT gates remain
unresolved). **Observed:** 2026-09-22. **Owner role:** Luna/max audit writer;
root reconciles release and later packet decisions; Terra reviews this report
after producer completion.

This is a read-only audit. No product source, test, configuration, schema,
release workflow, tag, package, daemon, model, provider, private workpad, or
runtime state was changed or executed. The machine-readable claim matrix and
read-only command receipts are in
[`P2-AUD-01-claims.json`](P2-AUD-01-claims.json).

## Executive disposition

The current checkout is at `b01675d0b7ef39b49853a26df61aadeea2064a6a`, the
commit peeled from an annotated local `v0.1.7` tag. A fresh read-only
`git ls-remote` query shows the same annotated tag object and peeled commit on
`origin`; the earlier publication-preparation report's “tag absent” observation
is therefore historical, not current truth. That tag presence proves only the
tag/ref identity, not a successful release.

Current public metadata queries did not observe a publication: the GitHub Release
API, PyPI JSON API, and TestPyPI JSON API each returned HTTP 404 for `v0.1.7`.
These time-scoped observations leave publication status unknown beyond the
queries. The public GitHub
Actions run for the tag (`35660274375`, head `b01675d…`) completed with failure:
the exact-tag “Built-wheel resources” job failed at
`tools/verify_installed_g03.py`; build/attestation, artifact smoke, TestPyPI,
PyPI, provenance, and GitHub Release jobs were skipped. Exact-tag Debian and
the Python 3.11/3.12/3.13 G28 matrix jobs in that run succeeded, but that does
not clear the failed release workflow or publication prerequisite.

The S10 route is implemented in source for the explicit local proposal path:
the public `gigai proposal` command seals selectors and direct consent, the Run
authority allocates and journals a Goal, `execute_local_proposal` resolves
authenticated source bytes, `run_model_invocation` resolves the configured
adapter and emits invocation artifacts, the Ollama adapter enforces loopback
identity/bounds, and the host validates/publishes the result and immutable
proposal revision. Existing tests use injected `httpx.MockTransport`, temporary
Git workpads, and monkeypatched factories; they are offline/injected proof, not
current installed-CLI or live-Ollama proof. The adapter and proposal caller
have meaningful local/no-hosted-fallback guards, while general harness routing,
daemon readiness, live identity, cancellation over a real endpoint, and private
data safety remain unproven.

## 1. Scope, method, and evidence lanes

The ticket's requested release claims were reconciled against the current
checkout, tracked workflow, historical R7 reports, and read-only public metadata
queries. Source was inspected with line-numbered reads; no test suite or
production command was run. Existing dirty work was preserved; the only files
owned by this story are this report and `P2-AUD-01-claims.json`.

Evidence is classified as follows:

| Lane | Current result | What it can prove | What it cannot prove |
| --- | --- | --- | --- |
| Source | Current functions and guards inspected | Route shape, validation order, ownership, bounded error semantics | Runtime success, package installation, endpoint identity, caller privacy |
| Injected/offline | Existing focused tests inspected; not rerun | Mock transport, deterministic fixture, negative-case intent | Current installed CLI, actual Ollama, package/release behavior |
| Normally installed | Historical disposable R7 environments only; current exact-tag workflow failed before downstream release jobs | Historical artifact/package checks for their recorded candidate | Current `b01675d` installed-wheel acceptance |
| Exact-tag CI | Current public run attempted; one required job failed | Tag-bound workflow job outcomes and failure boundary | Release acceptance when any required job fails |
| Live/provider | Not run; no daemon/model/provider calls | Nothing current | Local runtime readiness, model identity, live caller, quality |
| Publication/UAT | GitHub/PyPI/TestPyPI not observed in current metadata; checklist remains unchecked | Time-scoped query observations and explicit gate ordering | Human approval or release completion |

## 2. Exact current checkout baseline

### 2.1 Source, branch, tag, and worktree identity

| Observation | Result | Receipt/evidence |
| --- | --- | --- |
| Branch | `karthik446/gigai-v0.1.8` | `rtk git branch --show-current` |
| `HEAD` | `b01675d0b7ef39b49853a26df61aadeea2064a6a` | `rtk git rev-parse HEAD` |
| Description | `v0.1.7-dirty` | `rtk git describe --tags --always --dirty` |
| Local tag ref | tag object `19cf24c6710c565228344f5cdca378c085c7792f` | `rtk git show-ref --tags`, `git cat-file -t` |
| Local tag target | annotated tag `v0.1.7` peels to `b01675d…`; tag message `Release GigAI 0.1.7` | `git cat-file -p refs/tags/v0.1.7`, `git rev-parse 'v0.1.7^{}'` |
| Tag commit subject | `Ship GigAI 0.1.7 with bundled Scout and local runtime foundations (#34)` | `rtk git show --no-patch ... refs/tags/v0.1.7` |
| Worktree | dirty before this report; prior v0.1.8 docs, `pyproject.toml`, S11 evidence/tools/tests and S12 discussion were present | `rtk git status --short --branch` |

The commit title is not used as shipment evidence. The local tag is a current
Git observation, and the remote tag below confirms matching ref identity; neither
one proves that the release workflow completed or that a package was published.

### 2.2 Version and lock identity

`pyproject.toml:5-28` reports package `gigai`, version `0.1.7`, Python
`>=3.11`, and runtime dependency `questionary>=2.1.1` alongside Click/httpx/
jsonschema/referencing. `uv.lock:1-2` reports `requires-python = ">=3.11"`;
`uv.lock:65-92` contains exactly one editable `gigai` project record at version
`0.1.7`, with the same runtime and `test` extra dependencies. The current
working `pyproject.toml` has three pre-existing marker additions
(`git diff --numstat -- pyproject.toml` = `3 0`); `uv.lock` has no current diff.

Read-only content digests captured for audit reproducibility:

```text
sha256  pyproject.toml  d9a81709a2ca558ea76c8e97fff3880efa79859b7994435d6e88934cd7993045
sha256  uv.lock         bee6e19997640180c6738f5b524fc828919871acdeca75602c3b07af32b96f42
```

These are dirty-checkout file digests, not release artifact digests.

## 3. R7 candidate, tag, CI, publication, and UAT reconciliation

### 3.1 Historical R7 candidate artifacts are not silently adopted

The historical final-candidate report records a different checkout at
`fda48574f8642e66c0e7d53e7303ec04f04d7bb8` and disposable artifacts:

```text
wheel  0371662823de1b963455f50a967d226c95cfe2fadbefe03557630b3f844ca5b7
sdist  11fb92ba696cc1ffc67808930857dee6c0c7f5f91f1972e5cc5f867ec638a9a5
```

That report says the bounded local matrix and historical installed verifiers
passed, while publication was not approved; exact-tag CI, publication,
attestation, clean-index installs, and UAT were unproven
(`SCOUT-R7-final-candidate-verification-20260921.md:3-7,11-18,30-64,66-93,95-111,130-146`).
The separate release-integration report records another disposable artifact
pair (`da75dde1…` / `d89629aa…`) and explicitly says no commit, tag, push,
upload, publication, or full matrix was run
(`SCOUT-R7-release-integration-20260921.md:3-5,61-79,137-143`). Neither report
proves an installed artifact for current remote tag commit `b01675d…`.

The publication-preparation report's 2026-09-21 absence observation (local tag,
remote tag, and GitHub Release not found) is retained as historical evidence at
`SCOUT-R7-publication-preparation-20260921.md:30-41`. It is superseded only for
the tag fields by the current read-only observations below; its warnings about
dirty mixed ownership, exact-tag workflow gates, Debian limitations, and
unresolved publication remain relevant (`:65-99,101-129`). The historical report itself is
not rewritten.

### 3.2 Current remote and public metadata observations

| Gate | Current observation | Status/disposition |
| --- | --- | --- |
| Remote annotated tag | `git ls-remote --tags origin` returned tag object `19cf24c…` and peeled commit `b01675d…`, matching local | Proven current ref identity only |
| GitHub Actions | Run `35660274375`, `head_sha=b01675d…`, status `completed`, conclusion `failure` | Attempted and failed; no release acceptance |
| Exact-tag CI | Debian 12 offline and all six G28 Python/OS jobs succeeded; “Built-wheel resources” failed at `tools/verify_installed_g03.py` | Partial; required workflow failure remains |
| Build/attestation and artifact smoke | Jobs skipped after failed built-wheel resources | Unproven |
| TestPyPI | `https://test.pypi.org/pypi/gigai/0.1.7/json` returned HTTP 404 on 2026-09-22 | No current TestPyPI publication observed |
| PyPI | `https://pypi.org/pypi/gigai/0.1.7/json` returned HTTP 404 on 2026-09-22 | No current PyPI publication observed |
| GitHub Release | `https://api.github.com/repos/karthik446/gigai/releases/tags/v0.1.7` returned HTTP 404 on 2026-09-22 | No current GitHub Release observed |
| Human UAT | Checklist says planned/not executed and requires publication first | Unproven; no human decision |

The workflow receipt is a public metadata observation, not a rerun: preflight,
tag-bound source matrix, Debian job, and six G28 matrix jobs completed; the
failed built-wheel resource verifier prevented build/publication stages. The
workflow definition confirms the ordering: tag/preflight and named environments
(`.github/workflows/release.yml:1-44`), exact-tag CI (`:46-52`), build and
attestation (`:54-83`), TestPyPI and clean install (`:115-156`), PyPI and clean
install (`:158-211`), and provenance/GitHub Release creation (`:213-278`).

The release prerequisite therefore remains explicit and blocked: the current
tag exists and exact-tag CI was attempted, but one required installed verifier
failed, artifact build/publication did not run, package indexes return 404, and
human UAT is not executed. This story does not fix the G03 failure or claim
release readiness.

### 3.3 Claim matrix summary

| Claim ID | Claim | Evidence | Status | Conflict/disposition | Smallest next proof (not run) |
| --- | --- | --- | --- | --- | --- |
| `baseline.source` | Current source is `b01675d…`, branch `karthik446/gigai-v0.1.8`, dirty | Git receipts above | Proven | Dirty worktree is not a clean release input | Owner-approved clean checkout manifest |
| `baseline.version` | Project and lock both identify `gigai 0.1.7`, Python `>=3.11` | `pyproject.toml:5-28`; `uv.lock:1-92` | Proven | `pyproject.toml` has pre-existing marker additions | `verify-lockfile` and clean diff on release checkout |
| `baseline.local_tag` | Local annotated `v0.1.7` points to `b01675d…` | Local tag receipts | Proven | Tag is not publication | Exact-tag workflow tied to same commit |
| `baseline.remote_tag` | Remote annotated `v0.1.7` matches object/commit | `git ls-remote` receipt in JSON | Proven | Earlier absence report is historical | None for ref identity; retain release gates |
| `candidate.artifacts` | R7 candidate wheels/sdists were verified for other recorded candidate snapshots | Final-candidate and integration reports above | Historical only | Hashes/source identities do not establish current `b01675d` artifact | Build exact current tag after CI passes |
| `ci.exact_tag` | Current tag workflow ran but failed built-wheel resources | Actions run/jobs metadata | Failed/partial | Source/Debian/G28 successes do not override one required failure | Resolve owner-approved G03 issue and rerun exact workflow |
| `publication.github` | No GitHub Release for `v0.1.7` observed | GitHub API 404 | Unproven/absent | Historical absence corroborated; absence is time-scoped | Successful workflow GitHub Release job plus metadata query |
| `publication.pypi` | No PyPI `gigai==0.1.7` observed | PyPI API 404 | Unproven/absent | No artifact download was attempted | Workflow PyPI publish + clean-index install |
| `publication.testpypi` | No TestPyPI `gigai==0.1.7` observed | TestPyPI API 404 | Unproven/absent | No artifact download was attempted | Workflow TestPyPI publish + clean install |
| `uat.human` | Human UAT is not executed | `SCOUT-12-user-uat-checklist.md:3-17,69-87` | Unproven | Publication is a prerequisite | Explicit publication then operator UAT |
| `s10.source_route` | Public proposal route reaches local source execution and persistence | Source trace in §4 | Proven source | Source is not runtime proof | Focused installed/local proof under separate authorization |
| `s10.injected_route` | Mock/injected transport tests exercise local route and negatives | `tests/test_ollama_local_adapter.py:45-86`; `tests/test_ollama_local_integration.py:69-167`; proposal tests | Offline/injected only | Tests were inspected, not rerun in this audit | Bounded focused run in authorized checkout |
| `s10.installed_caller` | Current installed public CLI caller is proven | No current exact installed pass; built-wheel G03 failed | Unproven | Historical disposable installs are candidate-specific | Exact current wheel clean install and `gigai proposal` synthetic proof |
| `s10.live_local` | Current Ollama identity, daemon, and real endpoint route are proven | No live call; S10 evidence says untested | Unproven | No model/daemon/provider work authorized | One synthetic local probe with explicit authorization |
| `s10.no_hosted_fallback` | Proposal path rejects non-Ollama targets and offline remote policy; general harness locality remains unknown | `scout_proposal_execution.py:138-155`; `model_execution.py:241-245`; S10 evidence | Source guard proven; caller/harness boundary open | Adapter is not whole harness policy | Installed caller/harness trace with explicit target and receipt |
| `s10.cancellation` | Keyboard interrupt maps to typed model cancellation; proposal domain maps non-success to failure | `ollama_local.py:238-239`; `model_execution.py:292-320,385-408`; `scout_proposal_execution.py:1153-1165`; `run.py:522-557` | Source semantics proven | Real endpoint cancellation and recovery untested | Focused injected cancellation receipt, then separate live proof if needed |

## 4. Actual CLI/Run → proposal → local model → persisted output route

### 4.1 Public entry points and authority gates

| Entry | Source and behavior | Boundary |
| --- | --- | --- |
| Installed/public `gigai proposal` | `src/gigai/cli.py:3048-3107` parses one model target, one posting selector, one or more private selectors, requires `--confirm`, and constructs `ProposalRunRequest` for `launch_run` | Public CLI shape is source-verified; installed console-script execution is not current proof |
| Copied per-Gig Scout wrapper | `src/gigai/data/scout/gig.py:88-95` exposes the same explicit proposal arguments; `:337-345,415-438` authenticates the registered wrapper/workpad and calls `launch_run` | Wrapper ownership/path guards are source-verified; copied installed wrapper route is not executed here |
| Programmatic helper | `src/gigai/scout_proposal_cli.py:19-42` calls `execute_local_proposal` then `record_proposal_revision` | Direct helper exists; top-level `cli.py` reaches `run.py` directly, so helper reachability is not assumed |
| Run authority | `src/gigai/run.py:162-247` resolves workpad/projection/authority, selects the sealed graph, loads config, validates the proposal entry, and requires operator consent; `:303-354` allocates and journals `run_started` | Canonical IDs alone are not authority; committed Run/Goal/graph membership is required |

### 4.2 Source-to-output stages

1. **Sealed request and Goal:** `launch_run` validates one proposal request,
   seals selectors/configuration into Run artifacts, records `run_started`, and
   marks the proposal Goal running (`run.py:363-383`). The sealed request is
   rejected if malformed, combined with another domain request, or missing
   direct consent (`run.py:211-249,367-375`).
2. **Authenticated source join:** `execute_local_proposal` validates Run/Goal
   IDs, requires an active committed Goal, resolves only a configured
   `ollama_local` target with a digest and explicit local permission, and
   requires the registered invocation role (`scout_proposal_execution.py:115-161`).
   `_resolve_sources` reads the discovery posting through its journal resolver,
   snapshots `records/`, `references/`, and `run-inputs/`, then resolves bounded
   private selectors and verifies exact content digests
   (`scout_proposal_execution.py:908-957,995-1098`).
3. **Invocation request:** The host creates source descriptors and calls
   `run_model_invocation` with `offline=True`, `local_allowed=True`, selected
   source IDs, and a host-owned policy (`scout_proposal_execution.py:178-230`).
   `run_model_invocation` validates IDs/role/prompt/selection, resolves the
   configured target and adapter, checks references and bytes, applies local or
   remote network policy, builds a digest-bound provider input, and refuses
   disallowed network/credential/reference states
   (`model_execution.py:131-245`).
4. **Target/factory/local transport:** `resolve_model_target` rejects unknown or
   disabled targets and missing endpoints (`model_targets.py:20-31`). The factory
   binds `ollama_local` only when endpoint URL and model digest are present and
   passes model, digest, context, output, and response bounds
   (`adapters/factory.py:61-104`). The adapter requires numeric loopback
   `http://127.0.0.1:<port>`, rejects credentials/path/query/fragment and remote
   model markers, and bounds timeout/context/output/response bytes
   (`adapters/ollama_local.py:62-176`).
5. **Identity and response checks:** Before chat, the adapter checks
   `/api/version` and `/api/tags`, requires exactly one selected model and exact
   digest, then posts non-streaming `/api/chat`; it repeats identity checks after
   the response to detect replacement races (`ollama_local.py:189-237,241-284`).
   HTTP, timeout, malformed JSON, oversized response, incomplete assistant
   message, unexpected `done_reason`, usage overrun, and identity drift are
   typed failures (`ollama_local.py:306-419`).
6. **Invocation receipt:** `run_model_invocation` closes the adapter on both
   invoke and denied paths, builds request/record/response artifacts, validates
   the invocation record, and returns the artifacts to the proposal host
   (`model_execution.py:262-408`). The proposal call intentionally passes
   `commit_goal_transition=False` so generic invocation evidence does not
   terminalize the domain Goal prematurely (`scout_proposal_execution.py:226-230`).
7. **Host validation and Goal publication:** `_publish_invocation_attempt`
   commits the non-terminal invocation receipt before domain parsing
   (`scout_proposal_execution.py:236-246,285-365`). `_host_result` accepts only
   a successful adapter result whose output passes `validate_proposal_output`;
   otherwise it returns a bounded failed result (`:1129-1177`). `_publish_result`
   rechecks active committed authority and publishes the result plus updated
   `run-details.json` in one Goal transition (`:803-905`).
8. **Immutable proposal revision:** A complete result causes `launch_run` to
   call `record_proposal_revision` only after host validation
   (`run.py:401-431`). The writer validates lineage, source revisions, model
   digest, proposal shape, and operation-key replay, then commits
   `records/scout-proposals/<record>/revisions/<revision>.json`
   (`scout_proposal_records.py:356-437`). A failed/invalid result does not
   create a successful proposal revision.
9. **Readers and views:** `read_proposal_revision` authenticates exact immutable
   bytes from a pinned journal snapshot (`scout_proposal_records.py:440-470`).
   Report readers consume that snapshot and expose proposal/run rows
   (`scout_report_readers.py:1-6,150-190,296-343,435-454`). The projection says
   committed journal/record artifacts are authority; `state.sqlite` is a cache,
   and the query projection is in-memory/disposable (`scout_projection.py:1-8,
   273-315`). Generated report selectors are views and report staleness against
   the current journal HEAD (`scout_report.py:249-317,320-340`).

### 4.3 Local-only and no-fallback boundary

The proposal caller enforces the local route before transport: a target whose
endpoint adapter is not `ollama_local` raises `local_target_required`, missing
digest raises `local_target_invalid`, and absent direct local permission raises
`local_runtime_denied` (`scout_proposal_execution.py:138-155`). The invocation
policy is explicitly offline/local for this route; generic remote adapters are
denied when `policy.offline` or `policy.network_allowed` disallows network
(`model_execution.py:241-245`). The Ollama adapter itself has no daemon
lifecycle, credentials, tool execution, or cloud fallback, and rejects remote
markers (`adapters/ollama_local.py:1-6,99-111,280-304`).

This proves source guards for this proposal path, not all product callers. The
general adapter factory still supports other adapter families
(`adapters/factory.py:72-124`), and S10 explicitly says harness tools, file
access, subprocesses, cancellation, persistence, telemetry, and locality are
untested (`spikes/evidence/S10-ollama-invocation-and-harness-onboarding-research.md:118-141`).
No hosted fallback is inferred from a model label or a compatible endpoint.

## 5. Failure, cancellation, and recovery semantics

| Boundary | Source behavior | Resulting claim |
| --- | --- | --- |
| Run/Goal authority | Missing/malformed committed Run, graph, Goal membership, active status, effect, or prior terminal handoff raises `execution_authority_refused` before model transport (`scout_proposal_execution.py:585-733`) | Authority guard is source-proven |
| Source selectors | Missing explicit private purpose, unavailable posting, unsupported family, path traversal, changed bytes, or digest mismatch raises typed source refusal (`scout_proposal_execution.py:908-1126`) | Authenticated source join is source-proven |
| Target and role | Unknown/disabled target, non-Ollama target, missing digest, denied local permission, or unregistered role refuses before prompt (`scout_proposal_execution.py:138-161`) | No-hosted-fallback guard is source-proven for proposal caller |
| Adapter identity | Version/tags missing, ambiguous model, digest mismatch, remote/cloud marker, or before/after identity drift raises typed identity failure (`ollama_local.py:241-304`) | Model identity policy is source-proven; no live identity receipt |
| Adapter response | Timeout, HTTP/transport error, malformed JSON, byte bound, wrong model, incomplete/empty/truncated assistant response, usage overrun or malformed usage raises typed error (`ollama_local.py:306-419`) | Failure visibility and bounds are source-proven |
| Invocation classification | `run_model_invocation` records `timeout`, `unavailable`, `failed`, `blocked`, or `cancelled` outcomes and always closes the binding (`model_execution.py:262-321`) | Record semantics are source-proven |
| Cancellation | `KeyboardInterrupt` becomes `ModelInvocationCancelled` (`ollama_local.py:238-239`); model record is `cancelled`, but the host sees non-success and returns a failed proposal result (`model_execution.py:292-295`; `scout_proposal_execution.py:1153-1165`) | Real endpoint cancellation/recovery is untested; proposal domain terminal is failure, not automatic retry |
| Invalid model output | `validate_proposal_output` failure leaves host result `status=failed`, and `_publish_result` uses `goal_failed` (`scout_proposal_execution.py:1159-1177,803-905`) | Invalid output cannot become a successful proposal revision |
| Journal conflict/interruption | Run catches `_RunInterrupted`/`JournalConflictError` and returns/reconstructs `interrupted` terminal state (`run.py:432-520,522-557`) | Recovery is bounded source behavior; no execution receipt was generated here |
| Retry/idempotency | Proposal revision uses an operation key derived from invocation/source/assessment bytes and replays an existing matching revision (`scout_proposal_records.py:342-353,414-437`) | Idempotent revision semantics are source-proven; no retry was run |

No code path here performs an automatic hosted fallback, model download,
daemon startup, or hidden retry. Whether an external harness obeys the same
policy is an open caller-level claim.

## 6. Exact residuals and smallest next proofs

1. **Release workflow:** the owner of the failed exact-tag job must disposition
   `tools/verify_installed_g03.py` for the exact `b01675d…` tag and obtain an
   owner-authorized successful rerun. Only then should the workflow's artifact
   build, attestation, smoke, TestPyPI, PyPI, provenance, and GitHub Release
   jobs be considered. This audit does not repair or rerun it.
2. **Current installed caller:** after an exact artifact is built and its digest
   is recorded, install that artifact in a clean environment and exercise the
   public `gigai proposal` route with synthetic inputs. A mock transport is not
   enough for a real installed/local endpoint claim; a real Ollama request needs
   a separate explicit local-runtime authorization and must remain synthetic.
3. **Current local runtime:** follow S10's smallest live proof: capture
   `ollama --version`, `ollama list`, `ollama ps`, `/api/version`, and `/api/tags`,
   then one bounded synthetic `/api/chat` with endpoint/model/digest, timing,
   response validation, and cancellation receipt. Do not use private data,
   download models, start a daemon, or call a hosted provider.
4. **Cancellation/recovery:** if needed, run the existing focused injected
   cancellation/identity/invalid-output cases once in an authorized test lane;
   this audit intentionally inspected but did not execute them.
5. **Human UAT:** only after explicit publication and a matching installed
   artifact may the operator complete `SCOUT-12-user-uat-checklist.md`; no UAT
   result is implied by this report.

## 7. Audit gate

P2-AUD-01 is complete as a documentation/source/public-metadata audit because
the report identifies exact current source/tag/lock state, reconciles historical
R7 candidate and publication claims with current remote observations, traces the
actual proposal invocation and persistence route, classifies each evidence lane,
and names every smallest next proof without fixing or hiding gaps. The audit
does **not** grant release, implementation, installed, live/provider, model,
daemon, publication, or human-UAT readiness. P2-FREEZE-04 must consume this
report only after Terra's independent review and reconciliation with the other
Phase 2 audit packets.
