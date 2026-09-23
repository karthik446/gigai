import { useMemo, useState } from "react";
import AssessmentCard from "./AssessmentCard.jsx";
import SponsorshipBadge from "./SponsorshipBadge.jsx";
import { displayCompanyName, notAssessedReasonLabel, resumeDisplayLabel } from "../display.js";

const OUTCOME_FILTERS = [
  { value: "new", label: "New" },
  { value: "edited", label: "Edited" },
  { value: "unchanged", label: "Unchanged" },
  { value: "all", label: "All" },
];

const LOCATION_FILTERS = [
  { value: "all", label: "Any location" },
  { value: "ok", label: "Country ok" },
  { value: "location_mismatch", label: "Location mismatch" },
];

const SPONSORSHIP_FILTERS = [
  { value: "all", label: "Any sponsorship" },
  { value: "offered", label: "Offered" },
  { value: "unknown", label: "Unknown" },
  { value: "not_offered", label: "Not offered" },
];

const ASSESSED_FILTERS = [
  { value: "all", label: "All" },
  { value: "assessed", label: "Assessed" },
  { value: "not_assessed", label: "Not assessed" },
];

// Outcomes that sort ahead of the rest (U24: new/edited buried under a wall
// of unchanged rows). Everything else keeps its relative published_at order.
const OUTCOME_SORT_RANK = { new: 0, edited: 0 };

function postingsByUrl(payload) {
  const map = new Map();
  for (const row of payload.rows) {
    map.set(row.posting.normalized_url, row.posting);
  }
  return map;
}

// One row per posting merging outcome + not-assessed-reason + assessment
// presence, so search/filter/sort operate over a single flat list instead of
// three separately-shaped arrays.
function buildRows(payload) {
  const notAssessedByUrl = new Map(
    payload.not_assessed.map((entry) => [entry.posting.normalized_url, entry.reason]),
  );
  const assessedByUrl = new Map(
    payload.assessments.map((assessment) => [assessment.posting.normalized_url, assessment]),
  );
  return payload.rows.map((row) => {
    const url = row.posting.normalized_url;
    return {
      posting: row.posting,
      outcome: row.outcome,
      notAssessedReason: notAssessedByUrl.get(url) || null,
      assessment: assessedByUrl.get(url) || null,
    };
  });
}

function matchesSearch(row, query) {
  if (!query) {
    return true;
  }
  const needle = query.toLowerCase();
  const { posting } = row;
  const haystacks = [
    posting.title,
    posting.company,
    displayCompanyName(posting.company),
    posting.location,
    posting.text,
  ];
  return haystacks.some((field) => field && field.toLowerCase().includes(needle));
}

function matchesOutcome(row, outcomeFilter) {
  if (outcomeFilter === "all") {
    return true;
  }
  if (outcomeFilter === "new") {
    return row.outcome === "new" || row.outcome === "edited";
  }
  return row.outcome === outcomeFilter;
}

function matchesLocation(row, locationFilter) {
  if (locationFilter === "all") {
    return true;
  }
  if (locationFilter === "location_mismatch") {
    return row.notAssessedReason === "location_mismatch";
  }
  // "ok": anything not explicitly flagged as a location mismatch.
  return row.notAssessedReason !== "location_mismatch";
}

function rowSponsorship(row) {
  return row.assessment?.sponsorship || row.posting.sponsorship || null;
}

function matchesSponsorship(row, sponsorshipFilter) {
  if (sponsorshipFilter === "all") {
    return true;
  }
  const value = rowSponsorship(row) || "unknown";
  return value === sponsorshipFilter;
}

function matchesAssessed(row, assessedFilter) {
  if (assessedFilter === "all") {
    return true;
  }
  if (assessedFilter === "assessed") {
    return Boolean(row.assessment);
  }
  return Boolean(row.notAssessedReason);
}

function sortRows(rows) {
  return [...rows].sort((a, b) => {
    const rankA = OUTCOME_SORT_RANK[a.outcome] ?? 1;
    const rankB = OUTCOME_SORT_RANK[b.outcome] ?? 1;
    if (rankA !== rankB) {
      return rankA - rankB;
    }
    const publishedA = a.posting.published_at || "";
    const publishedB = b.posting.published_at || "";
    return publishedB.localeCompare(publishedA);
  });
}

function ChipGroup({ label, options, value, onChange }) {
  return (
    <div className="chip-group">
      <div className="chip-group-label">{label}</div>
      <div className="chip-list">
        {options.map((option) => (
          <button
            key={option.value}
            type="button"
            className={`chip${value === option.value ? " active" : ""}`}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        ))}
      </div>
    </div>
  );
}

export default function ResultsView({ payload }) {
  const postingByUrl = postingsByUrl(payload);
  const allRows = useMemo(() => buildRows(payload), [payload]);

  const [search, setSearch] = useState("");
  const [outcomeFilter, setOutcomeFilter] = useState("new");
  const [locationFilter, setLocationFilter] = useState("all");
  const [sponsorshipFilter, setSponsorshipFilter] = useState("all");
  const [assessedFilter, setAssessedFilter] = useState("all");

  const visibleRows = useMemo(() => {
    const filtered = allRows.filter(
      (row) =>
        matchesSearch(row, search) &&
        matchesOutcome(row, outcomeFilter) &&
        matchesLocation(row, locationFilter) &&
        matchesSponsorship(row, sponsorshipFilter) &&
        matchesAssessed(row, assessedFilter),
    );
    return sortRows(filtered);
  }, [allRows, search, outcomeFilter, locationFilter, sponsorshipFilter, assessedFilter]);

  const visibleAssessments = useMemo(() => {
    const visibleUrls = new Set(visibleRows.map((row) => row.posting.normalized_url));
    return payload.assessments.filter((assessment) => visibleUrls.has(assessment.posting.normalized_url));
  }, [visibleRows, payload.assessments]);

  const visibleNotAssessed = useMemo(() => {
    const visibleUrls = new Set(visibleRows.map((row) => row.posting.normalized_url));
    return payload.not_assessed.filter((entry) => visibleUrls.has(entry.posting.normalized_url));
  }, [visibleRows, payload.not_assessed]);

  return (
    <section>
      {payload.pinned_resume && (
        <div className="panel">
          <h2>Resume used</h2>
          <div className="field-row">
            <div className="field">
              <div className="label">Resume</div>
              <div className="value">{resumeDisplayLabel(payload.pinned_resume)}</div>
            </div>
          </div>
        </div>
      )}

      <div className="panel">
        <h2>Postings ({visibleRows.length} of {payload.rows.length})</h2>

        <div className="search-row">
          <input
            type="search"
            className="search-box"
            placeholder="Search title, company, location, or posting text…"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            aria-label="Search postings"
          />
        </div>

        <div className="filter-chips">
          <ChipGroup label="Outcome" options={OUTCOME_FILTERS} value={outcomeFilter} onChange={setOutcomeFilter} />
          <ChipGroup label="Location" options={LOCATION_FILTERS} value={locationFilter} onChange={setLocationFilter} />
          <ChipGroup
            label="Sponsorship"
            options={SPONSORSHIP_FILTERS}
            value={sponsorshipFilter}
            onChange={setSponsorshipFilter}
          />
          <ChipGroup label="Assessed" options={ASSESSED_FILTERS} value={assessedFilter} onChange={setAssessedFilter} />
        </div>

        {visibleRows.length === 0 ? (
          <p className="muted">No postings match the current search/filters.</p>
        ) : (
          <table className="postings-table">
            <thead>
              <tr>
                <th>Title</th>
                <th>Company</th>
                <th>Location</th>
                <th>Source</th>
                <th>Outcome</th>
                <th>Sponsorship</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row) => (
                <tr key={row.posting.normalized_url}>
                  <td>
                    <a href={row.posting.url} target="_blank" rel="noreferrer">
                      {row.posting.title}
                    </a>
                  </td>
                  <td>{displayCompanyName(row.posting.company)}</td>
                  <td>{row.posting.location || "—"}</td>
                  <td>{row.posting.source_kind}</td>
                  <td>{row.outcome}</td>
                  <td>
                    <SponsorshipBadge sponsorship={rowSponsorship(row)} />
                  </td>
                  <td>
                    {row.assessment ? (
                      <span className="muted">assessed</span>
                    ) : row.notAssessedReason ? (
                      <span className="muted" title={notAssessedReasonLabel(row.notAssessedReason)}>
                        not assessed: {row.notAssessedReason}
                      </span>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <h2>Assessments ({visibleAssessments.length} of {payload.assessments.length})</h2>
        {visibleAssessments.length === 0 ? (
          <p className="muted">No postings were assessed.</p>
        ) : (
          visibleAssessments.map((assessment) => (
            <AssessmentCard
              key={assessment.posting.normalized_url}
              assessment={assessment}
              posting={postingByUrl.get(assessment.posting.normalized_url)}
            />
          ))
        )}
      </div>

      <div className="panel">
        <h2>Not assessed ({visibleNotAssessed.length} of {payload.not_assessed.length})</h2>
        {visibleNotAssessed.length === 0 ? (
          <p className="muted">Every candidate posting was assessed.</p>
        ) : (
          <ul className="not-assessed-list">
            {visibleNotAssessed.map((entry) => (
              <li key={entry.posting.normalized_url}>
                <strong>{entry.posting.title}</strong> ({displayCompanyName(entry.posting.company)}):{" "}
                {notAssessedReasonLabel(entry.reason)}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="panel">
        <h2>Failures ({payload.failures.length})</h2>
        {payload.failures.length === 0 ? (
          <p className="muted">No acquisition failures.</p>
        ) : (
          <ul className="failures-list">
            {payload.failures.map((failure, index) => (
              <li key={`${failure.source_kind}-${failure.query_key}-${index}`}>
                <strong>{failure.source_kind}</strong> / {failure.query_key}: {failure.message}
              </li>
            ))}
          </ul>
        )}
      </div>
    </section>
  );
}
