# P3 UI — search, filters, sort, sponsorship/company/resume/reason display (v0.1.8.1)

## Files touched (all owned: `src/gigai/scout/ui/src/**`)

- `src/gigai/scout/ui/src/display.js` (new) — display-only helpers: `displayCompanyName`,
  `resumeDisplayLabel`, `notAssessedReasonLabel`, `sponsorshipLabel`.
- `src/gigai/scout/ui/src/components/SponsorshipBadge.jsx` (new) — badge for
  `offered` / `not_offered` / `unknown` (null/undefined renders as `unknown`).
- `src/gigai/scout/ui/src/components/ResultsView.jsx` — search box, filter chips, sort,
  new table column, updated not-assessed reason labels, company display names.
- `src/gigai/scout/ui/src/components/AssessmentCard.jsx` — sponsorship badge in the
  card summary; company display name in the summary line.
- `src/gigai/scout/ui/src/components/ConfigPanel.jsx` — resume line now goes through
  `resumeDisplayLabel` (same rendering as the results view, instead of ad hoc ids).
- `src/gigai/scout/ui/src/components/NodeStatusList.jsx` — failure message under a
  failed step is now a red callout block (`.node-failure-message`) instead of small
  muted gray text, so a step's failure cause (U21/U22, now the real error from P2) is
  hard to miss.
- `src/gigai/scout/ui/src/styles.css` — styles for the search box, filter chips,
  sponsorship badge colors, and the node failure callout.

No Python touched. No `package.json` / `yarn.lock` changes (only `yarn install` was run
locally against the existing lockfile to make `yarn build` runnable; nothing in
`package.json` was edited).

## UX description

**Postings panel.** Above the table: a full-width search input
("Search title, company, location, or posting text…"). It filters instantly
(no debounce needed — the dataset is a single run's postings, hundreds at
most) and matches case-insensitively against title, raw company, the
display-cased company, location, and `posting.text` (C0's new field; still
optional so older rows with no text just don't match on it).

Below the search box, four filter-chip groups, each a row of pill buttons
(single-select per group, click to switch, active chip filled in the accent
color):
- **Outcome** — New / Edited / Unchanged / All. Default: **New** — this
  merges `new` and `edited` outcomes into one "New" filter per the task's
  "default new+edited" instruction, since U24 asked for new+edited to not be
  buried by unchanged. Selecting "Edited" or "Unchanged" narrows to exactly
  that outcome; "All" clears the filter.
- **Location** — Any location / Country ok / Location mismatch. "Country ok"
  shows everything except rows whose not-assessed reason is
  `location_mismatch`; "Location mismatch" isolates them. (Acquire only
  emits `location_mismatch` as a not-assessed reason today, not a
  per-posting flag, so that's the signal this filters on.)
- **Sponsorship** — Any / Offered / Unknown / Not offered. Reads
  `assessment.sponsorship` when the posting was assessed, else
  `posting.sponsorship` (C0's acquire-time read), defaulting to "unknown"
  when neither is set.
- **Assessed** — All / Assessed / Not assessed.

The panel header shows "Postings (N of M)" so filtering never looks like
data loss. The table gained two columns: **Sponsorship** (badge) and
**Status** (`assessed`, `not assessed: <reason>` with the full reason
sentence in a tooltip, or `—` for rows that are neither, e.g. still
pending). Company cells go through `displayCompanyName`.

**Sort.** Rows with outcome `new` or `edited` sort before everything else;
within each tier, sort is `published_at` descending (rows with no
`published_at` sort last within their tier). This runs after search/filter,
so scrolling a filtered view still sees new/edited first.

**Company display names.** `displayCompanyName` only rewrites a value that
looks like a raw slug — lowercase letters/digits/hyphens, no spaces (e.g.
`customerio`, `onetrust`, `gongio`, confirmed against real acquire.json
output under `~/.gigai-scout/workpads/...`). It title-cases per hyphen
segment (`some-co` → "Some Co"). A value that already has spacing or mixed
case (an ATS row's real company name) passes through unchanged, so this
never mangles a legitimately-cased name. It's a display transform only —
never writes back, never changes what's searched against (search checks
both the raw and display forms).

**Sponsorship badge.** Small pill, colored by state: green "Sponsorship
offered", red "No sponsorship", neutral gray "Sponsorship unknown". Shown
per posting row and in the assessment card's `<summary>` line next to the
title/company.

**Resume label (U17).** `PinnedResume` (contracts.py) has only `record_id`,
`revision_id`, `content_sha256` — no label or source filename field exists
today. `resumeDisplayLabel` renders `record_id (revision_id)`, same as
before, just centralized in one helper so ConfigPanel and ResultsView render
it identically. **Field needed for the real fix:** `PinnedResume` would need
something like `label: str | None` or `source_filename: str | None`
threaded from the resume record/reference through `PinnedResume.to_json()`;
today nothing upstream captures the resume's original filename or a
human-assigned label at pin time. That's a contracts.py + resume-record
change, out of scope here (C2's API is owned by nobody in this hotfix, and
contracts.py is C0's).

**Not-assessed reasons.** Added label sentences for the three new
`NotAssessedReason` values C0 added: `location_mismatch` ("Location did not
match the configured country/location filter."), `sponsorship_excluded`
("Posting does not offer visa sponsorship, which this config requires."),
`model_output_invalid` ("The model's answer for this posting could not be
parsed."). Same rendering pattern as the existing reasons (a lookup table,
falls back to the raw code if a future reason is ever added without a UI
update).

**Run failure cause (U21/U22).** `NodeStatusList` already read
`receipt.failure.message`; it was rendered as small muted gray text easy to
miss next to a step pill. Restyled as a red-bordered callout block under the
failed step's pill, matching the app's existing `.callout.danger` visual
language, so a failed step's real cause (now populated by P2) is
immediately visible instead of looking like secondary metadata.

## Verification

- READ: `src/gigai/scout/find_jobs/contracts.py` (PostingRow, AssessmentResult,
  NotAssessedReason, FindJobsConfig, PinnedResume, NodeFailure/NodeReceipt) to confirm
  field names/shapes before wiring the UI to them.
- READ: two real operator run outputs under
  `~/.gigai-scout/workpads/projects/*/gigs/*/runs/run_*/outputs/acquire.json`
  (read-only) to confirm real `company` slugs (`customerio`, `onetrust`, `gongio`,
  etc.) and that `text`/`sponsorship` are absent on pre-C0 rows (confirms the
  optional-field handling needs no back-compat shimming beyond what contracts.py
  already does).
- READ: `v0.1.8-uat.md` findings U12, U17, U19, U20, U21, U22, U24, U25 for the
  acceptance criteria behind each UI change.
- EXECUTED: `yarn install` (against the existing, unedited lockfile) then
  `cd src/gigai/scout/ui && yarn build` — succeeded (`✓ built in 228ms`,
  `dist/assets/index-*.js` 237.96 kB, no warnings/errors).
- No test files were in this task's owned scope; none were run.

## What's left / not done here

- The resume label/filename gap (see above) needs a contracts.py +
  resume-record field, not owned by this task.
- Sponsorship filter/column reflects `posting.sponsorship` (acquire-time
  read) or `assessment.sponsorship` (model's read) — both optional and can
  legitimately disagree; no reconciliation UI was requested or added.
- No screenshots taken per the task's acceptance note (coordinator checks
  visually).
