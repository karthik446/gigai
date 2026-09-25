// Q4a (v0.1.9): the one job shape the card grid (JobsGrid/JobCard) and the
// job page (JobPage) render, built from EXISTING API responses only:
//
//   rows          boardRows.js rows from GET /api/runs/{id}/results (or the
//                 live /progress snapshot): posting + status + the run's own
//                 assessment (+ carried-forward, uat-bug-009)
//   rankScores    POST /api/runs/{id}/rank (Jev pre-rank, P6)
//   quickItems    GET /api/assessments?profile_id=…: the quick-assess store,
//                 where every job-page re-assessment lands (P3/P5) with its
//                 verdict history (Q4a)
//   runCreatedAt  GET /api/runs → runs[].created_at, for the run's own
//                 history entry and for "is the quick assessment newer?"
//
// Pure functions, no React: the merge/sort/filter rules are the part of
// this packet most worth reading in one place.
//
// Phase 2 hooks (fields that arrive with Q2's acquire changes / catalog
// join; rendered by JobCard/JobPage ONLY when present, no placeholder):
//   posting.work_mode            "remote" | "hybrid" | "onsite"
//   posting.pay                  {min, max, currency, period}
//   posting.h1b_filings_fy2024   integer, the catalog's H-1B count
import { displayCompanyName, notAssessedReasonLabel } from "./display.js";

export const VERDICT_LABELS = {
  matched_above_threshold: "Matched",
  pending_user_answers: "Needs your answers",
  not_a_match: "Not a match",
  assessed: "Assessed",
  not_assessed: "Not assessed",
};

// Operator answer 1: sort by verdict group (matched > needs answers > not
// assessed > not a match), Jev score within a group. "assessed" is a result
// the prompt gave no verdict for (pre-P2 result shape): assessed, so it
// sits above the not-assessed group.
export const VERDICT_ORDER = {
  matched_above_threshold: 0,
  pending_user_answers: 1,
  assessed: 2,
  not_assessed: 3,
  not_a_match: 4,
};

// RankScore.reasons / mismatch_flags are category ids (jev_contracts.py),
// never prose; this is the plain-words table the mockup used for them.
export const JEV_REASON_TEXT = {
  domain_match: "same technical domain",
  seniority_match: "same level",
  stack_match: "stack overlaps",
  domain_mismatch: "different technical domain",
  seniority_mismatch: "different level (junior/manager)",
  location_mismatch: "location outside preferences",
};

export const MODE_LABELS = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site", on_site: "On-site" };

export const CLASS_LABELS = { hard: "hard", askable: "askable", nice_to_have: "nice-to-have" };

const CLASS_RANK = { hard: 0, askable: 1, nice_to_have: 2 };
const STATUS_RANK = { unmet: 0, unclear: 1, met: 2 };

export function jevReasonText(id) {
  return JEV_REASON_TEXT[id] || id;
}

export function jevReasonsLine(rank) {
  if (!rank || rank.fit === null || rank.fit === undefined) {
    return "";
  }
  const reasons = (rank.reasons || []).map(jevReasonText);
  const flags = (rank.mismatch_flags || []).map((flag) => `flag: ${jevReasonText(flag)}`);
  return reasons.concat(flags).join(" · ");
}

// --- labels for dates / pay ---------------------------------------------------------

export function ageLabel(iso, now = Date.now()) {
  if (!iso) {
    return "date unknown";
  }
  const parsed = new Date(iso).getTime();
  if (Number.isNaN(parsed)) {
    return "date unknown";
  }
  const days = Math.round((now - parsed) / 86400000);
  if (days <= 0) {
    return "today";
  }
  if (days === 1) {
    return "1d ago";
  }
  if (days < 30) {
    return `${days}d ago`;
  }
  return `${Math.round(days / 30)}mo ago`;
}

export function dateLabel(iso) {
  if (!iso) {
    return null;
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

export function dateTimeLabel(iso) {
  if (!iso) {
    return null;
  }
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) {
    return iso;
  }
  return parsed.toLocaleString(undefined, { month: "short", day: "numeric", year: "numeric", hour: "numeric", minute: "2-digit" });
}

// Phase 2 hook: `posting.pay` {min, max, currency, period}. Null (no chip)
// unless the posting carries at least one bound -- operator answer 3: no
// "not listed" chips.
export function payLabel(pay) {
  if (!pay || typeof pay !== "object") {
    return null;
  }
  const hasMin = typeof pay.min === "number";
  const hasMax = typeof pay.max === "number";
  if (!hasMin && !hasMax) {
    return null;
  }
  const currency = pay.currency || "";
  const symbol = currency === "USD" ? "$" : currency === "EUR" ? "€" : currency === "GBP" ? "£" : currency ? `${currency} ` : "";
  const amount = (value) => (value >= 10000 ? `${Math.round(value / 1000)}k` : String(value));
  const range = hasMin && hasMax ? `${amount(pay.min)}–${amount(pay.max)}` : hasMin ? `from ${amount(pay.min)}` : `up to ${amount(pay.max)}`;
  const period = pay.period === "hour" ? "/hr" : pay.period === "year" ? "/yr" : pay.period ? `/${pay.period}` : "";
  return `${symbol}${range}${period}`;
}

export function workModeLabel(posting) {
  const mode = posting && posting.work_mode;
  return mode ? MODE_LABELS[mode] || mode : null;
}

// --- the merge ------------------------------------------------------------------

function assessmentTime(item) {
  return item.updated_at || item.created_at || "";
}

// One job per posting row. `assessment` is the LATEST result for this
// posting: the quick-assess store's when it is at least as new as the run
// that produced the row (a job-page re-assessment always is), else the
// run's own. Both carry the same result shape (matrix/verdict/
// structured_questions/sponsorship/...; AssessmentResult vs AssessmentBody,
// contracts.py / assess_contracts.py).
export function buildJobs({ rows, rankScores, quickItems, runCreatedAt }) {
  const rankByUrl = new Map((rankScores || []).map((score) => [score.normalized_url, score]));
  const quickByUrl = new Map();
  (quickItems || []).forEach((item) => {
    const keys = [item.job && item.job.normalized_url, item.job && item.job.job_identity].filter(Boolean);
    keys.forEach((key) => {
      const existing = quickByUrl.get(key);
      if (!existing || assessmentTime(item) > assessmentTime(existing)) {
        quickByUrl.set(key, item);
      }
    });
  });

  const seen = new Set();
  const jobs = (rows || []).map((row) => {
    const { posting } = row;
    const url = posting.normalized_url;
    seen.add(url);
    const quick = quickByUrl.get(url) || null;
    const runAt = row.status === "carried_forward" ? row.fromRunDate || runCreatedAt : runCreatedAt;
    const quickIsLatest = Boolean(quick) && (!row.assessment || !runAt || assessmentTime(quick) >= runAt);
    const assessment = quickIsLatest ? quick.result : row.assessment || null;
    const assessmentSource = assessment ? (quickIsLatest ? "quick" : "run") : null;
    return {
      id: url,
      posting,
      row,
      status: row.status,
      notAssessedReason: row.notAssessedReason || null,
      fromRunDate: row.fromRunDate || null,
      runCreatedAt: runCreatedAt || null,
      rank: rankByUrl.get(url) || null,
      quick,
      assessment,
      assessmentSource,
      verdict: effectiveVerdict(assessment),
      sponsorship: (assessment && assessment.sponsorship) || posting.sponsorship || "unknown",
    };
  });

  // Q4a-nav: a posting assessed on demand ("+ Assess a job", the CLI, a
  // pasted text) that no row of the loaded run carries is still a job: its
  // job page (#/jobs/<job_identity>) is the same JobPage, so the store's
  // ResolvedJob (title / company / location / source_url, text never
  // serialized) stands in for the posting row. `row` is null for these.
  const onDemand = [];
  const seenQuick = new Set();
  (quickItems || []).forEach((item) => {
    const identity = item.job && item.job.job_identity;
    if (!identity || seen.has(identity) || seen.has(item.job.normalized_url) || seenQuick.has(identity)) {
      return;
    }
    if (quickByUrl.get(identity) !== item) {
      return; // an older entry for the same job
    }
    seenQuick.add(identity);
    onDemand.push(quickOnlyJob(item, rankByUrl.get(identity) || null));
  });
  return jobs.concat(onDemand);
}

export function quickOnlyJob(item, rank = null) {
  const job = item.job || {};
  const posting = {
    title: job.title || "",
    company: job.company || "",
    location: job.location || "",
    url: job.source_url || null,
    normalized_url: job.normalized_url || job.job_identity,
    text: null,
    published_at: null,
    provider: null,
    source_kind: job.fetch_kind === "pasted" ? "pasted text" : "on demand",
  };
  const assessment = item.result || null;
  return {
    id: job.job_identity,
    posting,
    row: null,
    status: "on_demand",
    notAssessedReason: null,
    fromRunDate: null,
    runCreatedAt: null,
    rank,
    quick: item,
    assessment,
    assessmentSource: assessment ? "quick" : null,
    verdict: effectiveVerdict(assessment),
    sponsorship: (assessment && assessment.sponsorship) || "unknown",
  };
}

export function effectiveVerdict(assessment) {
  if (!assessment) {
    return "not_assessed";
  }
  return assessment.verdict || "assessed";
}

export function openQuestions(assessment) {
  return (assessment && assessment.structured_questions) || [];
}

// Operator answer 2: the requirement summary only after assess -- hard rows
// first, unmet/unclear before met, three lines + "N more".
export function requirementSummary(assessment, limit = 3) {
  const rows = ((assessment && assessment.matrix) || []).slice().sort((a, b) => {
    const classDelta = (CLASS_RANK[a.class] ?? 1) - (CLASS_RANK[b.class] ?? 1);
    if (classDelta !== 0) {
      return classDelta;
    }
    return (STATUS_RANK[a.status] ?? 3) - (STATUS_RANK[b.status] ?? 3);
  });
  return { shown: rows.slice(0, limit), more: Math.max(0, rows.length - limit) };
}

// Operator amendment (Q4a): the shared assessment table sorts unclear and
// unmet rows above met ones, otherwise keeping the model's order.
export function sortMatrixRows(matrix) {
  const rank = (row) => (row.status === "met" ? 1 : 0);
  return (matrix || [])
    .map((row, index) => ({ row, index }))
    .sort((a, b) => rank(a.row) - rank(b.row) || a.index - b.index)
    .map((item) => item.row);
}

// --- verdict history ----------------------------------------------------------------

// The job page's timeline: the run's own assessment first (trigger "run"),
// then the quick-assess store's history[] (Q4a; an older stored item with
// no history is shown as its one known state). Oldest first.
export function verdictHistoryFor(job) {
  const entries = [];
  if (job.row && job.row.assessment) {
    entries.push({
      at: job.status === "carried_forward" ? job.fromRunDate || job.runCreatedAt : job.runCreatedAt,
      verdict: job.row.assessment.verdict || null,
      trigger: "run",
    });
  }
  if (job.quick) {
    const history = job.quick.history && job.quick.history.length ? job.quick.history : [{ at: assessmentTime(job.quick), verdict: job.quick.result.verdict || null, trigger: "assess" }];
    history.forEach((entry) => entries.push({ at: entry.at, verdict: entry.verdict || null, trigger: entry.trigger }));
  }
  return entries.sort((a, b) => (a.at || "").localeCompare(b.at || ""));
}

export function triggerLabel(trigger) {
  if (trigger === "run") {
    return "Assessed by a find-jobs run";
  }
  if (trigger === "assess") {
    return "Assessed on demand";
  }
  if (trigger === "reassess") {
    return "Assessed again";
  }
  if (typeof trigger === "string" && trigger.startsWith("answer:")) {
    return `Re-assessed after you answered ${trigger.slice("answer:".length)}`;
  }
  return trigger || "";
}

// --- sort + filter ----------------------------------------------------------------

export function sortJobs(jobs) {
  return jobs.slice().sort((a, b) => {
    const groupDelta = (VERDICT_ORDER[a.verdict] ?? 5) - (VERDICT_ORDER[b.verdict] ?? 5);
    if (groupDelta !== 0) {
      return groupDelta;
    }
    const scoreA = a.rank && typeof a.rank.score === "number" ? a.rank.score : -1;
    const scoreB = b.rank && typeof b.rank.score === "number" ? b.rank.score : -1;
    if (scoreA !== scoreB) {
      return scoreB - scoreA;
    }
    return (b.posting.published_at || "").localeCompare(a.posting.published_at || "");
  });
}

export const EMPTY_FILTERS = { search: "", company: "", fit: "all", sponsorship: "all", assessed: "all", showHidden: false };

export function hasActiveFilter(filters) {
  return Boolean(filters.search || filters.company || filters.fit !== "all" || filters.sponsorship !== "all" || filters.assessed !== "all");
}

export function jobMatchesFilters(job, filters) {
  if (job.rank && job.rank.hidden_by_default && !filters.showHidden) {
    return false;
  }
  if (filters.company && job.posting.company !== filters.company) {
    return false;
  }
  if (filters.fit !== "all") {
    const fit = job.rank && job.rank.fit !== null && job.rank.fit !== undefined ? job.rank.fit : "unscored";
    if (fit !== filters.fit) {
      return false;
    }
  }
  if (filters.sponsorship !== "all" && job.sponsorship !== filters.sponsorship) {
    return false;
  }
  if (filters.assessed !== "all") {
    const verdict = filters.assessed === "not_assessed" ? (job.verdict === "not_assessed" ? "not_assessed" : "other") : job.verdict;
    if (verdict !== filters.assessed) {
      return false;
    }
  }
  if (filters.search) {
    const needle = filters.search.toLowerCase();
    const haystack = [
      job.posting.title,
      job.posting.company,
      displayCompanyName(job.posting.company),
      job.posting.location,
      ...((job.assessment && job.assessment.matrix) || []).map((row) => row.requirement),
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();
    if (!haystack.includes(needle)) {
      return false;
    }
  }
  return true;
}

export function filterJobs(jobs, filters) {
  return jobs.filter((job) => jobMatchesFilters(job, filters));
}

export function notAssessedLine(job) {
  if (job.status === "assessing") {
    return "Assessing…";
  }
  if (job.status === "acquired") {
    return "Waiting to be assessed";
  }
  if (job.status === "failed") {
    return job.notAssessedReason ? notAssessedReasonLabel(job.notAssessedReason) : "Assessment failed";
  }
  return job.notAssessedReason ? notAssessedReasonLabel(job.notAssessedReason) : "Not assessed";
}
