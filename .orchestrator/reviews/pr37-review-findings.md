# PR #37 code review — findings for the v0.1.9 coordinator (2026-09-24)

Source: `/code-review high 37` run from a reviewer session, then re-checked by that session.
Status labels: CONFIRMED = reproduced or traced in code by the reviewer; PLAUSIBLE = read in code, not run.
Verify each yourself before dispatching (repro first, then fix).

## P0: fix before operator UAT

1. **Country filter drops US postings** (CONFIRMED, ran it). `src/gigai/scout/find_jobs/filters.py:96`, the pycountry alias table.
   Alpha-3 codes and bare alpha-2 codes match as whole words in location text, so ordinary English words map to countries.
   `country_match(s, ('US',))` returns False for "New York City and Remote" (AD), "Hybrid (3 days per week)" (PE),
   "Remote - EST timezone" (EE) and "Remote, must be in office 2 days" (BE). Acquire drops those postings.
   Fix direction: only match codes when they are uppercase or in a delimited position (for example after a comma), and don't treat common words as codes; add regression cases.
2. **Saving setup, then Run, gives 409** (CONFIRMED by code trace). `src/gigai/scout/ui/src/App.jsx:~141` `handleSaveSetup` only calls `setupState.reload()`.
   `PUT /api/setup` also rewrites find-jobs.json (present_api.py:~667), so the UI's config and config_digest go stale.
   After the first-run interview, Run workflow → `POST /api/run` → 409 `config_digest_mismatch`. Fix: reload the config after a save; add a UI test. Rebuild dist.
3. **Assess progress is written under the project target, not the workpad** (PLAUSIBLE). `src/gigai/scout/proposal_execution.py:~99` `_assess_progress_writer` prefers `Path(target)` over `context.workpad_path`.
   bindings.py:326 passes `target` (the project root?). present_api.run_progress reads `<workpad>/runs/<id>/progress`.
   Effects: a stray `runs/` directory in the operator's repo, and cards stay "waiting" until the sealed results land. `present_api._sealed_selection_cap` has the same root issue.
   The docstring says it uses workpad_path, which contradicts the code. Verify with a real run.
4. **Resume update under the same filename fails** (PLAUSIBLE). `src/gigai/scout/scout_cli.py:~204` `operation_key=f"scout-resume-add:{file.name}"`.
   Re-adding an edited resume.pdf hits `private_operation_conflict` (private_records.py:231). Include the content digest in the key, or treat it as a replace.
5. **Discover panel is blank during a run** (PLAUSIBLE). `present_api.py:~752` `_capture_progress` replaces the discovery snapshot with the raw progress event.
   `GET /api/discover/latest` during a run then lacks status and cost ("Cost $undefined", "No new companies found").

## P1: fix before release

6. **CSRF on paid endpoints** (CONFIRMED by reading). `present_api.py:~1139` `POST /api/discover` (and probably `/api/run`) checks only for a loopback peer.
   Any web page can `fetch('http://127.0.0.1:8765/api/discover', {method:'POST', mode:'no-cors'})` and spend the OpenAI budget.
   Fix: require an `Origin`/`Host` match plus a JSON content-type (that forces a CORS preflight), or a per-run token.
7. **Discovery is hard-coded to the US** (CONFIRMED by reading). `discovery/merge.py:156,166,220` `_minimal_config` has countries=('US',) and `_row_is_us`; `h1b_source.py` has verbatim copies.
   Non-US prefs → every board is skipped as `no_matching_us_postings`, but the budget is still spent. Use prefs.countries, and dedupe the copies.
8. **`--home` is ignored for secrets** (PLAUSIBLE). credentials.py:69, exa_client.py:135 and openai_source.py:112 call `secrets_store.get(name)` with no home_root.
   `secrets add --home X` followed by `scout run --home X` → exa_missing_key. Only hits non-default homes.
9. **Stale pid in the state file** (PLAUSIBLE). `run_supervisor.py:~364` stop/status trust the saved pid with only `os.kill(pid, 0)`.
   After pid reuse, `scout stop` could send SIGTERM and then SIGKILL to an unrelated process. Check the process identity or health before killing.
10. **Empty source_url rejected** (PLAUSIBLE). `discovery/merge.py:~226` `_verify_source_url` returns False for an empty source even when visa sponsorship isn't required.
    That contradicts openai_source's documented rule, and usable boards get skipped as `evidence_unverifiable`.

## Backlog (lower severity)

- **CLI tracebacks:** `scout prep` only catches InterviewPrepError, so WorkpadError or a bad find-jobs.json prints a traceback. `scout discover` crashes when OPENAI_DISCOVERY_MODEL has no price entry.
- **`/api/setup` 500s:** `GET` and `PUT /api/setup` return 500 when saved prefs or find-jobs.json are invalid.
- **openai_source can raise:** a non-JSON body, or a `retry-after` header given as a date, raises an error despite the "never raises" rule. After the sixth 429 it still sleeps about 55 seconds before giving up.
- **Acquire fails too broadly:** the `not rows and failures` check fails the run when Exa errors but ATS succeeded with zero postings.
- **Selection falls short:** the per-company cap of 2 leaves selection under the requested cap when there are few companies.
- **Setup save loses tuning:** it overwrites hand-tuned `merged_queries` with the roles.
- **UI poll leak:** the progress poll can keep firing after the run ends if a request is in flight.
- **`wait.sh` loop:** the new `EMPTY` branch never counts toward MAX_WAITS and never acks, so it can busy-loop.
- **Duplicated code:** `_row_is_us`/`_minimal_config` (h1b_source and merge); `_atomic_write_json` vs discovery.storage.atomic_write; `_days_ago` vs `_relative_days_ago`; two minimal YAML parsers (ci_changes, release_notes).
