import { useMemo, useState } from "react";
import JobCard from "./JobCard.jsx";
import { displayCompanyName } from "../display.js";
import { EMPTY_FILTERS, filterJobs, hasActiveFilter, sortJobs } from "../jobModel.js";

// Q4a: the card grid that replaces the Find-jobs postings list
// (FindJobsPostingsBoard.jsx), per mockups/cards-and-job-page.html.
// Filters are client-side over the job model: Jev fit (incl. unscored),
// sponsorship (only when find-jobs.json visa_sponsorship_required is true),
// assessed (by verdict group), company, search (title/company/location/
// requirements), and "show postings Jev hides by default"
// (RankScore.hidden_by_default). Sort: verdict group, then Jev score
// (operator answer 1; jobModel.sortJobs).
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
  ["unknown", "Unknown"],
];
const ASSESSED_OPTIONS = [
  ["all", "All"],
  ["matched_above_threshold", "Matched"],
  ["pending_user_answers", "Needs your answers"],
  ["not_a_match", "Not a match"],
  ["not_assessed", "Not assessed"],
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

export default function JobsGrid({ jobs, visaRequired, runLabel, emptyMessage }) {
  const [filters, setFilters] = useState(EMPTY_FILTERS);
  const setFilter = (key, value) => setFilters((prev) => ({ ...prev, [key]: value }));
  // The sponsorship filter exists only alongside the chip (operator
  // amendment); when the config turns it off, any stale selection resets.
  const effectiveFilters = visaRequired ? filters : { ...filters, sponsorship: "all" };

  const companies = useMemo(() => {
    const set = new Set(jobs.map((job) => job.posting.company).filter(Boolean));
    return [...set].sort((a, b) => displayCompanyName(a).localeCompare(displayCompanyName(b)));
  }, [jobs]);

  const visible = useMemo(() => sortJobs(filterJobs(jobs, effectiveFilters)), [jobs, effectiveFilters]);
  const hiddenCount = useMemo(() => jobs.filter((job) => job.rank && job.rank.hidden_by_default).length, [jobs]);

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
            <ChipGroup label="Assessed" options={ASSESSED_OPTIONS} value={filters.assessed} onChange={(value) => setFilter("assessed", value)} />
          </div>
          <div className="result-count">
            <span>
              {visible.length} of {jobs.length} postings
              {runLabel ? ` · ${runLabel}` : ""} · sorted by verdict, then Jev score
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
            <label className="filter-toggle">
              <input type="checkbox" checked={filters.showHidden} onChange={(event) => setFilter("showHidden", event.target.checked)} />{" "}
              Show postings Jev hides by default
            </label>
          </div>
        </div>
      </section>

      <div className="card-grid">
        {visible.length === 0 ? (
          <div className="empty-state">{jobs.length === 0 ? emptyMessage || "No postings acquired yet." : "No postings match these filters."}</div>
        ) : (
          visible.map((job) => <JobCard key={job.id} job={job} visaRequired={visaRequired} />)
        )}
      </div>
    </div>
  );
}
