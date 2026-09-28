import { useMemo, useState } from "react";
import JobCard from "./JobCard.jsx";
import { displayCompanyName } from "../display.js";
import { EMPTY_FILTERS, filterJobs, hasActiveFilter, sortByAssessedAt, sortJobs } from "../jobModel.js";
import { stateOptions } from "../jobStateModel.js";

// Q4a: the card grid that replaces the Find-jobs postings list
// (FindJobsPostingsBoard.jsx), per mockups/cards-and-job-page.html.
// Filters are client-side over the job model: Jev fit (incl. unscored),
// sponsorship (only when find-jobs.json visa_sponsorship_required is true),
// state, company, search (title/company/location/requirements), and "show
// postings Jev hides by default" (RankScore.hidden_by_default). Sort:
// verdict group, then Jev score (operator answer 1; jobModel.sortJobs).
//
// uat-bug-018: the "State" chips filter by the job's derived state
// (job.state, jobStateModel.js) and each says how many jobs are in it,
// counted over the jobs every OTHER filter leaves, so a chip's number is
// what clicking it shows. Only the states some job is in have a chip. They
// took the place of the "Assessed" chips (the verdicts are states).
//
// uat-batch2 (uat-bug-016): the Assessments page is this same grid with
// `from="assessments"`: newest assessment first (jobModel.sortByAssessedAt),
// and nothing hidden by Jev (the operator asked for each of these
// assessments; hiding one because Jev scored it low would lose it).
const FIT_OPTIONS = [
  ["all", "All"],
  ["strong", "Strong"],
  ["maybe", "Maybe"],
  ["no", "No"],
  ["unscored", "Unscored"],
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

export default function JobsGrid({ jobs, visaRequired, runLabel, emptyMessage, from }) {
  const assessments = from === "assessments";
  const noun = assessments ? "assessments" : "postings";
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));
  // The sponsorship filter exists only alongside the chip (operator
  // amendment); when the config turns it off, any stale selection resets.
  const shownFilters = visaRequired ? filters : { ...filters, sponsorship: "all" };
  const effectiveFilters = assessments ? { ...shownFilters, showHidden: true } : shownFilters;

  const companies = useMemo(() => {
    const set = new Set(jobs.map((job) => job.posting.company).filter(Boolean));
    return [...set].sort((a, b) => displayCompanyName(a).localeCompare(displayCompanyName(b)));
  }, [jobs]);

  const visible = useMemo(() => {
    const matching = filterJobs(jobs, effectiveFilters);
    return assessments ? sortByAssessedAt(matching) : sortJobs(matching);
  }, [jobs, effectiveFilters, assessments]);
  const hiddenCount = useMemo(() => (assessments ? 0 : jobs.filter((job) => job.rank && job.rank.hidden_by_default).length), [jobs, assessments]);
  const states = useMemo(
    () => stateOptions(filterJobs(jobs, { ...effectiveFilters, state: "all" }), effectiveFilters.state),
    [jobs, effectiveFilters],
  );

  return (
    <div>
      <section className="panel" style={{ padding: "12px 16px" }}>
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
            <ChipGroup label="Jev fit" options={FIT_OPTIONS} value={filters.fit} onChange={(value) => setFilter("fit", value)} />
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
          <div className="result-count">
            <span>
              {visible.length} of {jobs.length} {noun}
              {runLabel ? ` · ${runLabel}` : ""} · {assessments ? "newest first" : "sorted by verdict, then Jev score"}
              {!filters.showHidden && hiddenCount > 0 ? ` · ${hiddenCount} hidden by Jev` : ""}
              {hasActiveFilter(effectiveFilters) && (
                <>
                  {" · "}
                  <button type="button" className="link-button" onClick={() => setFilters(EMPTY_FILTERS)}>
                    Clear filters
                  </button>
                </>
              )}
            </span>
            {!assessments && (
              <label className="filter-toggle">
                <input type="checkbox" checked={filters.showHidden} onChange={(event) => setFilter("showHidden", event.target.checked)} />{" "}
                Show postings Jev hides by default
              </label>
            )}
          </div>
        </div>
      </section>

      <div className="card-grid">
        {visible.length === 0 ? (
          <div className="empty-state">{jobs.length === 0 ? emptyMessage || "No postings acquired yet." : `No ${noun} match these filters.`}</div>
        ) : (
          visible.map((job) => <JobCard key={job.id} job={job} visaRequired={visaRequired} from={from} />)
        )}
      </div>
    </div>
  );
}
