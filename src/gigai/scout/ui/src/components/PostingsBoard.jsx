import { useMemo, useState } from "react";
import PostingCard from "./PostingCard.jsx";
import { displayCompanyName } from "../display.js";

// B4: the single flat row model both the live /progress view and the final
// sealed /results view render through, so cards never thrash layout when a
// run finishes -- the same PostingsBoard keeps rendering, just with rows
// that have gained an `assessment`. A row's shape is always:
//   { posting, status, assessment, notAssessedReason }
// where `status` is one of "acquired" | "assessing" | "assessed" | "failed"
// | "not_assessed", and `posting` always has at least
// { normalized_url, title, company, location, source_kind, sponsorship,
// url, countries }.

const OUTCOME_STATUS_RANK = { assessing: 0, acquired: 1, assessed: 2, not_assessed: 3, failed: 3 };

const SPONSORSHIP_FILTERS = [
  { value: "all", label: "Any sponsorship" },
  { value: "offered", label: "Offered" },
  { value: "unknown", label: "Unknown" },
  { value: "not_offered", label: "Not offered" },
];

const ASSESSED_FILTERS = [
  { value: "all", label: "All" },
  { value: "assessed", label: "Assessed" },
  { value: "pending", label: "Pending / in progress" },
  { value: "not_assessed", label: "Not assessed" },
];

function matchesSearch(row, query) {
  if (!query) {
    return true;
  }
  const needle = query.toLowerCase();
  const { posting } = row;
  const haystacks = [posting.title, posting.company, displayCompanyName(posting.company), posting.location];
  return haystacks.some((field) => field && field.toLowerCase().includes(needle));
}

function rowSponsorship(row) {
  return row.assessment?.sponsorship || row.posting.sponsorship || "unknown";
}

function matchesSponsorship(row, filter) {
  if (filter === "all") {
    return true;
  }
  return rowSponsorship(row) === filter;
}

function matchesAssessed(row, filter) {
  if (filter === "all") {
    return true;
  }
  if (filter === "assessed") {
    return row.status === "assessed";
  }
  if (filter === "pending") {
    return row.status === "acquired" || row.status === "assessing";
  }
  return row.status === "not_assessed" || row.status === "failed";
}

function matchesCompany(row, company) {
  if (!company) {
    return true;
  }
  return row.posting.company === company;
}

function sortRows(rows) {
  return [...rows].sort((a, b) => {
    const rankA = OUTCOME_STATUS_RANK[a.status] ?? 4;
    const rankB = OUTCOME_STATUS_RANK[b.status] ?? 4;
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

export default function PostingsBoard({ rows, cap }) {
  const [search, setSearch] = useState("");
  const [sponsorshipFilter, setSponsorshipFilter] = useState("all");
  const [assessedFilter, setAssessedFilter] = useState("all");
  const [companyFilter, setCompanyFilter] = useState("");

  const companies = useMemo(() => {
    const set = new Set(rows.map((row) => row.posting.company).filter(Boolean));
    return [...set].sort((a, b) => displayCompanyName(a).localeCompare(displayCompanyName(b)));
  }, [rows]);

  const companyOptions = useMemo(
    () => [
      { value: "", label: "Any company" },
      ...companies.map((company) => ({ value: company, label: displayCompanyName(company) })),
    ],
    [companies],
  );

  const visibleRows = useMemo(() => {
    const filtered = rows.filter(
      (row) =>
        matchesSearch(row, search) &&
        matchesSponsorship(row, sponsorshipFilter) &&
        matchesAssessed(row, assessedFilter) &&
        matchesCompany(row, companyFilter),
    );
    return sortRows(filtered);
  }, [rows, search, sponsorshipFilter, assessedFilter, companyFilter]);

  const assessedCount = rows.filter((row) => row.status === "assessed").length;

  return (
    <div className="panel">
      <h2>
        Postings ({visibleRows.length} of {rows.length})
      </h2>

      {cap && (
        <p className="muted">
          Assessing {Math.min(cap.cap, cap.candidateCount ?? cap.cap)} of {cap.candidateCount ?? "?"} matches
          (cap {cap.cap}); {assessedCount} assessed so far.
          {cap.notAssessedCounts && Object.keys(cap.notAssessedCounts).length > 0 && (
            <>
              {" "}
              Not assessed:{" "}
              {Object.entries(cap.notAssessedCounts)
                .map(([reason, count]) => `${count} ${reason}`)
                .join(", ")}
              .
            </>
          )}
        </p>
      )}

      <div className="search-row">
        <input
          type="search"
          className="search-box"
          placeholder="Search title, company, or location…"
          value={search}
          onChange={(event) => setSearch(event.target.value)}
          aria-label="Search postings"
        />
      </div>

      <div className="filter-chips">
        <ChipGroup label="Sponsorship" options={SPONSORSHIP_FILTERS} value={sponsorshipFilter} onChange={setSponsorshipFilter} />
        <ChipGroup label="Assessed" options={ASSESSED_FILTERS} value={assessedFilter} onChange={setAssessedFilter} />
        <div className="chip-group">
          <div className="chip-group-label">Company</div>
          <select value={companyFilter} onChange={(event) => setCompanyFilter(event.target.value)}>
            {companyOptions.map((option) => (
              <option key={option.value || "any"} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </div>
      </div>

      {visibleRows.length === 0 ? (
        <p className="muted">
          {rows.length === 0 ? "No postings acquired yet." : "No postings match the current search/filters."}
        </p>
      ) : (
        <div className="posting-card-list">
          {visibleRows.map((row) => (
            <PostingCard key={row.posting.normalized_url} row={row} />
          ))}
        </div>
      )}
    </div>
  );
}
