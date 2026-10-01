import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import JobCard from "./JobCard.jsx";
import { displayCompanyName } from "../display.js";
import { EMPTY_FILTERS, filterJobs, hasActiveFilter, sortByAssessedAt, sortJobs } from "../jobModel.js";
import { stateOptions } from "../jobStateModel.js";
import { filtersKey, hasMore, initialShown, pageOf, rowsWanted, showMoreLabel, showMoreShown, showingLine } from "../pageModel.js";
import { RANK_HONEST_NOTE, RANK_ORDER_NOTE, sameOrder, streamOrder } from "../rankModel.js";
import { canFindOlder, findOlderHint, findOlderLabel, postedOptions, searchLine } from "../postedWindowModel.js";

// Q4a: the card grid that replaces the Find-jobs postings list
// (FindJobsPostingsBoard.jsx), per mockups/cards-and-job-page.html.
// Filters are client-side over the job model: the model's rank band (incl.
// blocked and not ranked), sponsorship (only when find-jobs.json
// visa_sponsorship_required is true), state, company, search
// (title/company/location/requirements). Sort: verdict group, then the
// model's rank (likely fits first · likely no-matches last, a posting with a
// blocker at the end, never hidden; jobModel.sortJobs).
//
// SCOPE-ADD-3 D (streaming): while a run ranks, each poll can re-order the
// list as batches land. While the pointer is over the list or focus is in
// it, the cards already shown keep their places (their tiles still update)
// and new cards join at the end (rankModel.streamOrder), so the card the
// operator is on never jumps; the list re-sorts when they leave it.
//
// N33 (pagination): the grid draws ~50 cards ("Showing 50 of 500 postings")
// and "Show more" adds the next 50 (pageModel.js). The rows arrive from the
// server a page at a time in this same order (`onWantRows(count)` asks the
// view for `count` rows; earlier pages are never read again), so the top
// `shown` cards are the top of the order and a later page only adds cards
// at the end. A filter reads every row of the run (the view loads the rest
// when one is used) and goes back to page 1.
//
// uat-bug-018: the "State" chips filter by the job's derived state
// (job.state, jobStateModel.js) and each says how many jobs are in it,
// counted over the jobs every OTHER filter leaves, so a chip's number is
// what clicking it shows. Only the states some job is in have a chip. They
// took the place of the "Assessed" chips (the verdicts are states).
//
// 0110-019: the "Posted" chips (7d / 10d / 30d / 60d / Any, beside Rank)
// filter the cards by the posting's own date, at once and with no request
// (postedWindowModel.js). When the chosen window is wider than what the run
// searched (`postedWindow.searched_days`), one button asks the view to
// search the stored boards for the older postings (`onFindOlder(days)`).
//
// uat-batch2 (uat-bug-016): the Assessments page is this same grid with
// `from="assessments"`: newest assessment first (jobModel.sortByAssessedAt).
const FIT_OPTIONS = [
  ["all", "All"],
  ["strong", "Likely fit"],
  ["maybe", "Possible fit"],
  ["no", "Likely no-match"],
  ["blocked", "Blocker"],
  ["unranked", "Not ranked"],
];
const SPONSORSHIP_OPTIONS = [
  ["all", "All"],
  ["offered", "Sponsors visas"],
  ["not_offered", "No sponsorship"],
  ["unknown", "Not stated"],
];

function ChipGroup({ label, options, value, onChange }) {
  return (
    <div className="filter-group">
      <div className="chip-group-label">{label}</div>
      <div className="chip-list">
        {options.map(([optionValue, optionLabel]) => (
          <button
            key={optionValue}
            type="button"
            className={`chip${value === optionValue ? " active" : ""}`}
            onClick={() => onChange(optionValue)}
          >
            {optionLabel}
          </button>
        ))}
      </div>
    </div>
  );
}

function StateChips({ options, value, onChange }) {
  return (
    <div className="filter-group" data-role="state-filter">
      <div className="chip-group-label">State</div>
      <div className="chip-list">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`chip${value === option.value ? " active" : ""}`}
            aria-pressed={value === option.value}
            data-state={option.value}
            onClick={() => onChange(option.value)}
          >
            {option.label} <span className="chip-count">{option.count}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export default function JobsGrid({
  jobs,
  visaRequired,
  runLabel,
  emptyMessage,
  from,
  total = null,
  onWantRows = null,
  loadingMore = false,
  postedWindow = null,
  onFindOlder = null,
  findingOlder = false,
  findOlderError = null,
}) {
  const assessments = from === "assessments";
  const noun = assessments ? "assessments" : "postings";
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));
  // The sponsorship filter exists only alongside the chip (operator
  // amendment); when the config turns it off, any stale selection resets.
  const shownFilters = visaRequired ? filters : { ...filters, sponsorship: "all" };
  const effectiveFilters = shownFilters;

  const companies = useMemo(() => {
    const set = new Set(jobs.map((job) => job.posting.company).filter(Boolean));
    return [...set].sort((a, b) => displayCompanyName(a).localeCompare(displayCompanyName(b)));
  }, [jobs]);

  const sorted = useMemo(() => {
    const matching = filterJobs(jobs, effectiveFilters);
    return assessments ? sortByAssessedAt(matching) : sortJobs(matching);
  }, [jobs, effectiveFilters, assessments]);
  // The streaming hold: the order last drawn, and whether the operator is
  // on the list (pointer over it, or focus in it).
  const [hold, setHold] = useState(false);
  // N33: how many cards may be drawn; back to one page when a filter changes.
  const paged = !assessments;
  const [shown, setShown] = useState(initialShown());
  const filterState = filtersKey(effectiveFilters);
  useEffect(() => {
    setShown(initialShown());
  }, [filterState]);
  const filtersActive = hasActiveFilter(effectiveFilters);
  const runTotal = Math.max(total ?? jobs.length, jobs.length);
  const wanted = paged ? rowsWanted({ shown, total: runTotal, filtersActive }) : 0;
  useEffect(() => {
    if (onWantRows && wanted > jobs.length) {
      onWantRows(wanted);
    }
  }, [onWantRows, wanted, jobs.length]);
  const drawnIds = useRef([]);
  const visible = useMemo(() => streamOrder(sorted, drawnIds.current, hold), [sorted, hold]);
  useLayoutEffect(() => {
    drawnIds.current = visible.map((job) => job.id);
  }, [visible]);
  const drawn = paged ? pageOf(visible, shown) : visible;
  const matching = filtersActive || !paged ? sorted.length : runTotal;
  const orderHeld = hold && !sameOrder(visible, sorted);
  const holdHandlers = {
    onPointerEnter: () => setHold(true),
    onPointerLeave: () => setHold(false),
    onFocus: () => setHold(true),
    onBlur: (event) => {
      if (!event.currentTarget.contains(event.relatedTarget)) {
        setHold(false);
      }
    },
  };
  const states = useMemo(
    () => stateOptions(filterJobs(jobs, { ...effectiveFilters, state: "all" }), effectiveFilters.state),
    [jobs, effectiveFilters],
  );

  return (
    <div>
      <section
        className="panel"
        style={{ padding: "12px 16px" }}
        onFocus={() => onWantRows && runTotal > jobs.length && onWantRows(runTotal)}
        onPointerDownCapture={() => onWantRows && runTotal > jobs.length && onWantRows(runTotal)}
      >
        <div className="filter-bar">
          <div className="filter-row">
            <div className="filter-group filter-search">
              <label className="chip-group-label" htmlFor="jobs-filter-search">
                Search
              </label>
              <input
                id="jobs-filter-search"
                type="search"
                placeholder="Title, company, location, requirement…"
                value={filters.search}
                onChange={(event) => setFilter("search", event.target.value)}
              />
            </div>
            <div className="filter-group filter-company">
              <label className="chip-group-label" htmlFor="jobs-filter-company">
                Company
              </label>
              <select id="jobs-filter-company" value={filters.company} onChange={(event) => setFilter("company", event.target.value)}>
                <option value="">All companies</option>
                {companies.map((company) => (
                  <option key={company} value={company}>
                    {displayCompanyName(company)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="filter-row">
            {!assessments && <ChipGroup label="Rank" options={FIT_OPTIONS} value={filters.fit} onChange={(value) => setFilter("fit", value)} />}
            {!assessments && (
              <div data-role="posted-filter">
                <ChipGroup
                  label="Posted"
                  options={postedOptions(postedWindow ? postedWindow.choices : undefined)}
                  value={filters.posted}
                  onChange={(value) => setFilter("posted", value)}
                />
              </div>
            )}
            {visaRequired && (
              <ChipGroup
                label="Sponsorship"
                options={SPONSORSHIP_OPTIONS}
                value={filters.sponsorship}
                onChange={(value) => setFilter("sponsorship", value)}
              />
            )}
          </div>
          <div className="filter-row">
            <StateChips options={states} value={filters.state} onChange={(value) => setFilter("state", value)} />
          </div>
          {!assessments && onFindOlder && (canFindOlder(postedWindow, filters.posted) || findingOlder || findOlderError || searchLine(postedWindow)) && (
            <div className="rank-bar" data-role="find-older-bar" style={{ margin: 0 }}>
              {canFindOlder(postedWindow, filters.posted) && (
                <button
                  type="button"
                  className="button small secondary"
                  data-action="find-older"
                  disabled={findingOlder}
                  title={findOlderHint(postedWindow)}
                  onClick={() => onFindOlder(filters.posted)}
                >
                  {findingOlder ? "Searching the stored boards…" : findOlderLabel(filters.posted)}
                </button>
              )}
              {canFindOlder(postedWindow, filters.posted) && !findingOlder && (
                <span className="muted" data-role="find-older-hint">
                  {findOlderHint(postedWindow)}
                </span>
              )}
              {!canFindOlder(postedWindow, filters.posted) && !findingOlder && searchLine(postedWindow) && (
                <span className="muted" data-role="find-older-result">
                  {searchLine(postedWindow)}
                </span>
              )}
              {findOlderError && <span className="muted">Find older postings: {findOlderError}</span>}
            </div>
          )}
          <div className="result-count">
            <span>
              {paged ? showingLine({ drawn: drawn.length, matching, noun }) : `${visible.length} of ${jobs.length} ${noun}`}
              {runLabel ? ` · ${runLabel}` : ""} ·{" "}
              {assessments ? (
                "newest first"
              ) : (
                <span data-role="rank-order-note" title={`Assessed postings are grouped by their verdict first. ${RANK_HONEST_NOTE}`}>
                  {RANK_ORDER_NOTE}
                </span>
              )}
              {orderHeld && (
                <span className="muted" data-role="order-held">
                  {" "}
                  · new ranks landed; the list re-orders when you move off it
                </span>
              )}
              {hasActiveFilter(effectiveFilters) && (
                <>
                  {" · "}
                  <button type="button" className="link-button" onClick={() => setFilters(EMPTY_FILTERS)}>
                    Clear filters
                  </button>
                </>
              )}
            </span>
          </div>
        </div>
      </section>

      <div className="card-grid" data-role="jobs-grid" {...holdHandlers}>
        {drawn.length === 0 ? (
          <div className="empty-state">{jobs.length === 0 ? emptyMessage || "No postings acquired yet." : `No ${noun} match these filters.`}</div>
        ) : (
          drawn.map((job) => <JobCard key={job.id} job={job} visaRequired={visaRequired} from={from} />)
        )}
      </div>
      {paged && hasMore({ drawn: drawn.length, matching }) && (
        <div className="show-more" data-role="show-more">
          <button
            type="button"
            className="button secondary"
            disabled={loadingMore && drawn.length < shown}
            onClick={() => setShown((current) => showMoreShown(Math.max(current, drawn.length)))}
          >
            {loadingMore && drawn.length < shown ? "Loading…" : showMoreLabel({ drawn: drawn.length, matching })}
          </button>
        </div>
      )}
    </div>
  );
}
