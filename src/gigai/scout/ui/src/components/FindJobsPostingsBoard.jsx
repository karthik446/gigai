import { useMemo, useState } from "react";
import PostingCard from "./PostingCard.jsx";
import { displayCompanyName } from "../display.js";

// P9/F3's Find-jobs postings board: PostingsBoard.jsx's filters (sponsorship,
// assessed, company, search) plus two the mockup adds that ARE backed by
// real data: a Jev fit filter and the "no + strong mismatch" hidden-by-
// default filter (RankScore.hidden_by_default, jev_contracts.py) -- toggled
// off by default per the task spec ("hidden by default... visible under a
// filter, never deleted"). "New since last run" is DROPPED (no field
// computes it -- see FindJobsView.jsx's header note).

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

const FIT_FILTERS = [
  { value: "all", label: "Any fit" },
  { value: "strong", label: "Strong" },
  { value: "maybe", label: "Maybe" },
  { value: "no", label: "No" },
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
  return filter === "all" || rowSponsorship(row) === filter;
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
  return !company || row.posting.company === company;
}

function matchesFit(row, filter, rankByUrl) {
  if (filter === "all") {
    return true;
  }
  const score = rankByUrl.get(row.posting.normalized_url);
  return Boolean(score) && score.fit === filter;
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

export default function FindJobsPostingsBoard({ rows, cap, rankScores, profileId }) {
  const [search, setSearch] = useState("");
  const [sponsorshipFilter, setSponsorshipFilter] = useState("all");
  const [assessedFilter, setAssessedFilter] = useState("all");
  const [companyFilter, setCompanyFilter] = useState("");
  const [fitFilter, setFitFilter] = useState("all");
  const [showHidden, setShowHidden] = useState(false);

  const rankByUrl = useMemo(() => {
    const map = new Map();
    (rankScores || []).forEach((score) => map.set(score.normalized_url, score));
    return map;
  }, [rankScores]);

  const companies = useMemo(() => {
    const set = new Set(rows.map((row) => row.posting.company).filter(Boolean));
    return [...set].sort((a, b) => displayCompanyName(a).localeCompare(displayCompanyName(b)));
  }, [rows]);

  const companyOptions = useMemo(
    () => [{ value: "", label: "Any company" }, ...companies.map((company) => ({ value: company, label: displayCompanyName(company) }))],
    [companies],
  );

  const hiddenCount = useMemo(
    () => rows.filter((row) => rankByUrl.get(row.posting.normalized_url)?.hidden_by_default).length,
    [rows, rankByUrl],
  );

  const visibleRows = useMemo(() => {
    const filtered = rows.filter((row) => {
      const score = rankByUrl.get(row.posting.normalized_url);
      if (!showHidden && score?.hidden_by_default) {
        return false;
      }
      return (
        matchesSearch(row, search) &&
        matchesSponsorship(row, sponsorshipFilter) &&
        matchesAssessed(row, assessedFilter) &&
        matchesCompany(row, companyFilter) &&
        matchesFit(row, fitFilter, rankByUrl)
      );
    });
    return sortRows(filtered);
  }, [rows, search, sponsorshipFilter, assessedFilter, companyFilter, fitFilter, showHidden, rankByUrl]);

  const assessedCount = rows.filter((row) => row.status === "assessed").length;

  return (
    <div className="panel">
      <h2>
        Postings ({visibleRows.length} of {rows.length})
      </h2>

      {cap && (
        <p className="muted">
          Assessing {Math.min(cap.cap, cap.candidateCount ?? cap.cap)} of {cap.candidateCount ?? "?"} matches (cap {cap.cap});{" "}
          {assessedCount} assessed so far.
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
        <ChipGroup label="Fit" options={FIT_FILTERS} value={fitFilter} onChange={setFitFilter} />
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

      {hiddenCount > 0 && (
        <div className="callout info" style={{ fontSize: "0.85rem" }}>
          {hiddenCount} posting(s) hidden (Jev: no fit + a strong mismatch flag).{" "}
          <button type="button" className="button small secondary" onClick={() => setShowHidden((value) => !value)}>
            {showHidden ? "Hide them again" : "Show hidden postings"}
          </button>
        </div>
      )}

      {visibleRows.length === 0 ? (
        <p className="muted">{rows.length === 0 ? "No postings acquired yet." : "No postings match the current search/filters."}</p>
      ) : (
        <div className="posting-card-list">
          {visibleRows.map((row) => (
            <PostingCard
              key={row.posting.normalized_url}
              row={row}
              rankScore={rankByUrl.get(row.posting.normalized_url)}
              profileId={profileId}
              showPrep
            />
          ))}
        </div>
      )}
    </div>
  );
}
