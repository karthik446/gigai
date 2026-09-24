# C0 — find-jobs contracts extension (v0.1.8.1 hotfix)

## READ

- `/Users/kar/orca/workspaces/gigai/v0.1.8-uat.md` — U12, U19, U20, U22, U25 (sponsorship filter, country
  filter, Exa/ATS dedupe context, model-output-invalid root cause, missing posting text).
- `src/gigai/scout/find_jobs/contracts.py` (full file, 1782 lines pre-edit) — all DTOs, `_object` helper,
  `canonical_json_digest` semantics via `src/gigai/canonical.py`.
- `tests/behaviors/scout_find_jobs/test_contracts.py` (full file, pre-edit) — fixture round-trip harness,
  closed-set mutation tests, enum mutation tests.
- `tests/behaviors/scout_find_jobs/fixtures/fixture-find-jobs-config-v1.json`,
  `fixture-acquire-batch-v1.json`, `fixture-assessment-v1.json` (existing old-shape fixtures).
- Read-only grep across `src/` and `tests/` for other `FindJobsConfig(`/`PostingRow(`/`AssessmentResult(`
  call sites and other `find_jobs.contracts` importers, to confirm nothing outside this packet breaks
  from the additive change (not executed — those files/tests are out of scope for this packet).

## EXECUTED

- `src/gigai/scout/find_jobs/contracts.py`:
  - Added `_object_with_optional(value, required, optional, name)` — a `_object` variant where keys in
    `optional` may be entirely absent (old payload), while any key outside `required | optional` is still
    rejected (closed-set safety preserved).
  - Added `SponsorshipStatus` StrEnum: `offered` / `not_offered` / `unknown`.
  - `NotAssessedReason`: added `location_mismatch`, `sponsorship_excluded`, `model_output_invalid` (all
    existing values kept).
  - `FindJobsConfig` (schema stays `find-jobs-config:1` — chose **optional keys under :1**, not a new
    `:2` version, since the new fields are pure additions with safe defaults and don't change the meaning
    of any existing key; a `:2` would have forced dual-version branching for no behavioral gain). Added
    `countries: tuple[str, ...] = ()` (validated as ISO-3166 alpha-2 via a new `_country_codes` helper —
    2 uppercase letters) and `visa_sponsorship_required: bool = False`. `to_json()` omits both keys when
    at their default, so an old file's `to_json()` output — and therefore its digest — is byte-identical
    to before. A non-default value adds the key and changes the digest, which is correct: the logical
    config changed.
  - `PostingRow`: added `text: str | None = None` and `sponsorship: SponsorshipStatus | None = None`,
    same omit-at-`None`-default `to_json()` pattern, same digest-stability argument.
  - `AssessmentResult`: added `sponsorship: SponsorshipStatus | None = None`, same pattern.
  - Added `SponsorshipStatus` to `__all__`.
- `tests/behaviors/scout_find_jobs/fixtures/fixture-find-jobs-config-v1-sponsorship.json` (new): new-shape
  config fixture with `countries: ["US"]` and `visa_sponsorship_required: true`.
- `tests/behaviors/scout_find_jobs/test_contracts.py`:
  - Imported `NotAssessedReason`, `SponsorshipStatus`.
  - Added 11 new tests: old-shape config parses with defaults; old-shape config digest is byte-for-byte
    unchanged (pinned to the exact `sha256:e14f80...` digest so a future accidental `to_json()` change
    fails loudly); new config fixture round-trips and its digest differs from the old one;
    `countries`/`visa_sponsorship_required` reject bad input; old-shape `PostingRow` (no `text`/
    `sponsorship`) parses and round-trips; `PostingRow` with `text`+`sponsorship` round-trips and rejects
    a bad enum; old-shape `AssessmentResult` (no `sponsorship`) parses and round-trips;
    `AssessmentResult.sponsorship` round-trips; all three new `NotAssessedReason` values accepted and
    round-trip.
  - Did **not** modify the three existing old-shape fixtures in place — they stay exactly as originally
    shipped, which is itself the backward-compat proof (the existing 188 fixture/mutation-parametrized
    tests already run unmodified against them and all still pass).

## VERIFIED

```
uv run --locked --extra test pytest tests/behaviors/scout_find_jobs/test_contracts.py -q
  199 passed   (was 188 before this packet; +11 new)

uv run --locked python -c "from gigai.scout.find_jobs import contracts"
  (clean import, no output)
```

Confirmed by direct interpreter check (not just tests) that parsing the *unmodified* old fixtures produces
`to_json() == original_json` for `FindJobsConfig`, `PostingRow`, and `AssessmentResult`, and that
`FindJobsConfig.digest()` on the unmodified old config fixture is unchanged at
`sha256:e14f80205b4dda9cdcd2f594c2eb341c224c4ee2a10d5765e0848df1db6e4dee`.

## NOT DONE / handed to other workers

- Did not touch `exa_client.py`, `ats_board_clients.py`, `market_acquisition.py`, `proposal_execution.py`,
  or any other consumer — out of scope for C0. Grepped confirmed their existing `PostingRow(...)` /
  positional-arg call sites remain valid (new fields are appended with defaults), so they keep compiling
  and passing whatever tests already cover them; they simply don't populate `text`/`sponsorship` yet.
  That's expected — populating them (U25 posting text, U12/U19 sponsorship + country filtering logic) is
  downstream work for other packets, not C0.
- Did not add `NotAssessedReason.location_mismatch` / `sponsorship_excluded` / `model_output_invalid`
  *producers* (i.e., no filtering/assess logic emits them yet) — C0 is contracts-only, per task scope.
- No git add/commit — left for the coordinator to verify and commit.
