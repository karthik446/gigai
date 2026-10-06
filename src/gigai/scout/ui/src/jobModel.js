// Q4a (v0.1.9): the one job shape the card grid (JobsGrid/JobCard) and the
// job page (JobPage) render, built from EXISTING API responses only:
//
//   rows          boardRows.js rows from GET /api/runs/{id}/results (or the
//                 live /progress snapshot): posting + status + the run's own
//                 assessment (+ carried-forward, uat-bug-009)
//   rankScores    scores in POST /api/runs/{id}/rank's RankScore shape: the
//                 rows' stored `rank_score`s and a re-rank pass's answers
//                 (rankModel.js); a row's own `rank` line (the model's
//                 reasons and blockers) comes with the row
//   quickItems    GET /api/assessments?profile_id=…: the quick-assess store,
//                 where every job-page re-assessment lands (P3/P5) with its
//                 verdict history (Q4a)
//   runCreatedAt  GET /api/runs → runs[].created_at, for the run's own
//                 history entry and for "is the quick assessment newer?"
//
// Pure functions, no React: the merge/sort/filter rules are the part of
// this packet most worth reading in one place.
//
// Q4b fields (Q4b-data adds them; rendered by JobCard/JobPage ONLY when
// present, no placeholder chips -- operator answer 3):
//   posting.work_mode   "remote" | "hybrid" | "onsite"   -> workModeLabel
//   posting.pay         {min, max, currency, period}     -> payLabel
//   rows[].h1b          {approvals, fiscal_years}        -> h1bLabel, the
//                       sponsorship chip's suffix when the posting is silent
//                       (carried on the job as `job.h1b`, from boardRows)
//   rows[].work_mode_fit {mode, source, preference, area, in_area}
//                       -> whyPassedLine (uat-bug-028), carried as
//                       `job.workModeFit`; `source: "derived"` is a mode read
//                       from the location text, never the board's field
import { displayCompanyName, notAssessedReasonLabel, sponsorshipLabel } from "./display.js";
import { compareRank, isRanked, mergeRank, rankEntry, rankFilterValue } from "./rankModel.js";
import { POSTED_ANY, isPostedWindow, postedWithin } from "./postedWindowModel.js";

export const VERDICT_LABELS = {
  matched_above_threshold: "Matched",
  pending_user_answers: "Needs your answers",
  not_a_match: "Not a match",
  assessed: "Assessed",
  not_assessed: "Not assessed",
};

// Operator answer 1: sort by verdict group (matched > needs answers > not
// assessed > not a match), then the model's rank within a group
// (rankModel.compareRank: likely fits first, likely no-matches last, a
// posting with a blocker at the end). "assessed" is a result the prompt
// gave no verdict for (pre-P2 result shape): assessed, so it sits above the
// not-assessed group.
export const VERDICT_ORDER = {
  matched_above_threshold: 0,
  pending_user_answers: 1,
  assessed: 2,
  not_assessed: 3,
  not_a_match: 4,
};

export const MODE_LABELS = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site", on_site: "On-site" };

// uat-batch1 (N8): the requirement class and status in the operator's words.
// 0110-10-03: `list_item` is one tool of a list a single sentence names
// ("Docker, Helm, and Kubernetes"): it weighs less than a requirement of its own.
export const CLASS_LABELS = { hard: "Must-have", askable: "Can ask", list_item: "One of a list", nice_to_have: "Bonus" };
export const STATUS_LABELS = { met: "Met", unmet: "Not met", unclear: "Unclear", partial: "Partial", gap: "Gap" };

const CLASS_RANK = { hard: 0, askable: 1, list_item: 2, nice_to_have: 3 };
// Rows that never hold a match up by themselves (the server's requirement_weights.MINOR_CLASSES).
const MINOR_CLASSES = new Set(["list_item", "nice_to_have"]);
const GAPS_NAMED = 3;
const STATUS_RANK = { unmet: 0, unclear: 1, met: 2 };

export function humanizeId(id) {
  const words = String(id === null || id === undefined ? "" : id)
    .replace(/[_:\-.]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : "";
}

// True when the model gave this posting a score (rankModel.isRanked).
export const isScored = isRanked;

export function classLabel(requirementClass) {
  return requirementClass ? CLASS_LABELS[requirementClass] || humanizeId(requirementClass) : "";
}

export function statusLabel(status) {
  return STATUS_LABELS[status] || humanizeId(status);
}

// "Must-have: Met"; a row the prompt gave no class reads "Met".
export function requirementStatusLabel(row) {
  const name = classLabel(row && row.class);
  const status = statusLabel(row && row.status);
  return name ? `${name}: ${status}` : status;
}

// 0110-10-03: the bonus and one-of-a-list rows that are not met. They never
// block a match: "Matched" reads "1 minor gap: Helm" beside it.
export function minorGaps(assessment) {
  return ((assessment && assessment.matrix) || [])
    .filter((row) => row && MINOR_CLASSES.has(row.class) && row.status !== "met")
    .map((row) => row.requirement);
}

// "1 minor gap: Helm" / "4 minor gaps: Helm, Istio, Argo CD +1 more"; null for none.
export function minorGapText(assessment) {
  const gaps = minorGaps(assessment);
  if (gaps.length === 0) {
    return null;
  }
  const named = gaps.slice(0, GAPS_NAMED).join(", ") + (gaps.length > GAPS_NAMED ? ` +${gaps.length - GAPS_NAMED} more` : "");
  return `${gaps.length} minor gap${gaps.length === 1 ? "" : "s"}: ${named}`;
}

// 0110-10-12: "minor gaps" is the wording of a match (they never block one). Under "Needs your answers" or "Not a
// match" the same rows are what the job still waits on or lacks, and the table says them: no line then (the Jobs row's
// rule, postingsModel.scoreText).
export function minorGapLine(assessment) {
  return effectiveVerdict(assessment) === "matched_above_threshold" ? minorGapText(assessment) : null;
}

// "Requirements (16)" / "Requirements (40, +5 not shown)": rows past the
// server's bound are counted, never dropped in silence.
export function requirementsHeading(assessment) {
  const count = assessment && assessment.matrix ? assessment.matrix.length : 0;
  const hidden = assessment && Number.isInteger(assessment.rows_not_shown) ? assessment.rows_not_shown : 0;
  return hidden > 0 ? `Requirements (${count}, +${hidden} not shown)` : `Requirements (${count})`;
}

// uat-batch1 (N4): the job page shows a short excerpt of the posting, the
// first paragraph or so; the rest is one click away (Open posting).
// Paragraphs are taken whole until the excerpt reaches `target` characters;
// past `limit` it is cut at the last sentence end (or word) before it.
// `cut`: the server already cut the text it gave (a Jobs row's 400-character preview): the start-of-posting line shows.
export function jdExcerpt(text, { target = 280, limit = 600, cut = false } = {}) {
  if (typeof text !== "string") {
    return null;
  }
  const paragraphs = text
    .replace(/\r\n?/g, "\n")
    .split(/\n\s*\n/)
    // Keep a paragraph's own line breaks (a "Requirements:" list, one item per line);
    // only runs of spaces and tabs inside a line collapse.
    .map((paragraph) =>
      paragraph
        .split("\n")
        .map((line) => line.replace(/[^\S\n]+/g, " ").trim())
        .filter(Boolean)
        .join("\n"),
    )
    .filter(Boolean);
  if (paragraphs.length === 0) {
    return null;
  }
  let excerpt = "";
  let used = 0;
  for (const paragraph of paragraphs) {
    excerpt = excerpt ? `${excerpt}\n\n${paragraph}` : paragraph;
    used += 1;
    if (excerpt.length >= target) {
      break;
    }
  }
  let truncated = cut || used < paragraphs.length;
  if (excerpt.length > limit) {
    const head = excerpt.slice(0, limit);
    const sentence = Math.max(head.lastIndexOf(". "), head.lastIndexOf("! "), head.lastIndexOf("? "), head.lastIndexOf(".\n"));
    const word = head.lastIndexOf(" ");
    const cut = sentence >= limit / 2 ? sentence + 1 : word > 0 ? word : limit;
    excerpt = `${head.slice(0, cut).trimEnd()} …`;
    truncated = true;
  }
  return { text: excerpt, truncated };
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
  // "$180k–$220k / yr", "from $180k / yr", "up to $220k / yr", "$65–$80 / hr".
  const amount = (value) => `${symbol}${value >= 10000 ? `${Math.round(value / 1000)}k` : String(value)}`;
  const range = hasMin && hasMax ? `${amount(pay.min)}–${amount(pay.max)}` : hasMin ? `from ${amount(pay.min)}` : `up to ${amount(pay.max)}`;
  const period = PERIOD_LABELS[pay.period] || (pay.period ? String(pay.period) : "");
  return period ? `${range} / ${period}` : range;
}

const PERIOD_LABELS = { year: "yr", month: "mo", week: "wk", day: "day", hour: "hr" };

// "9 H-1B approvals (FY2026)" from rows[].h1b {approvals, fiscal_years}
// (USCIS approvals, never "filings" -- coordinator msg_0f0cf5a89665); null
// unless `approvals` is a positive number, so no record and zero approvals
// both leave the chip a plain "Sponsorship not stated". Several fiscal
// years read "FY2024–2026"; none reads without the parenthesis.
export function h1bLabel(h1b) {
  if (!h1b || typeof h1b !== "object" || typeof h1b.approvals !== "number" || h1b.approvals <= 0) {
    return null;
  }
  const count = `${h1b.approvals} H-1B approval${h1b.approvals === 1 ? "" : "s"}`;
  const years = Array.isArray(h1b.fiscal_years) ? h1b.fiscal_years.filter((year) => Number.isInteger(year)).sort((a, b) => a - b) : [];
  if (years.length === 0) {
    return count;
  }
  const span = years.length === 1 ? `FY${years[0]}` : `FY${years[0]}–${years[years.length - 1]}`;
  return `${count} (${span})`;
}

// The sponsorship chip's words (SponsorshipBadge renders them): the
// posting's own statement, or "Sponsorship not stated" plus the company's
// H-1B approvals when the posting is silent. `positive` (the chip's green
// tone) is true only for a silent posting whose company has approvals.
export function sponsorshipChip(sponsorship, h1b) {
  const status = sponsorship || "unknown";
  const silent = status === "unknown";
  const approvals = silent ? h1bLabel(h1b) : null;
  let label = sponsorshipLabel(status);
  let title = silent ? "The posting does not mention sponsorship" : "Stated in the posting";
  if (approvals) {
    label = `${label} · ${approvals}`;
    title = `${title}; H-1B approvals from the company catalog (USCIS)`;
    if (typeof h1b.denials === "number") {
      title = `${title}, ${h1b.denials} denial${h1b.denials === 1 ? "" : "s"} in the same period`;
    }
  }
  return { status, label, title, positive: Boolean(approvals) };
}

export function workModeLabel(posting) {
  const mode = posting && posting.work_mode;
  return mode ? MODE_LABELS[mode] || mode : null;
}

// uat-bug-028: the card's mode chip. The board's own field first; else a
// mode the run read from the location text ("Remote - United States" on a
// Greenhouse posting), marked `derived` so it is never shown as the board's.
// null for a plain city or an unknown mode (the why-passed line says those).
export function workModeChip(job) {
  const board = workModeLabel(job && job.posting);
  if (board) {
    return { label: board, derived: false, title: "Stated by the job board" };
  }
  const fit = job && job.workModeFit;
  if (fit && fit.source === "derived" && MODE_LABELS[fit.mode]) {
    return { label: MODE_LABELS[fit.mode], derived: true, title: "Read from the posting's location text; the board does not say" };
  }
  return null;
}

// uat-bug-028: why a posting passed the run's work mode + area, in words:
// "Remote (from location text)", "Hybrid · Denver", "Work mode not stated".
// null when the run asked for Any (nothing was filtered on it) or the row
// carries no fit (a live row, an older run's results).
const FIT_MODE_TEXT = {
  remote: "Remote",
  hybrid: "Hybrid",
  onsite: "On-site",
  in_person: "Hybrid or on-site (not stated)",
};

export function whyPassedLine(fit) {
  if (!fit || fit.preference === "any" || !fit.mode) {
    return null;
  }
  if (fit.mode === "unknown") {
    return "Work mode not stated";
  }
  let line = FIT_MODE_TEXT[fit.mode] || humanizeId(fit.mode);
  if (fit.area) {
    line += fit.in_area === true ? ` · ${fit.area}` : " · area not stated";
  }
  if (fit.source === "derived" && fit.mode !== "in_person") {
    line += " (from location text)";
  }
  return line;
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
    const url = row.posting.normalized_url;
    seen.add(url);
    const quick = quickByUrl.get(url) || null;
    // uat-batch2: a row acquire kept no text for shows the text its quick
    // assessment fetched (posting_text), when there is one.
    const quickText = quick && typeof quick.posting_text === "string" && quick.posting_text ? quick.posting_text : null;
    const posting = !row.posting.text && quickText ? { ...row.posting, text: quickText } : row.posting;
    const runAt = row.status === "carried_forward" ? row.fromRunDate || runCreatedAt : runCreatedAt;
    const quickIsLatest = Boolean(quick) && (!row.assessment || !runAt || assessmentTime(quick) >= runAt);
    const assessment = quickIsLatest ? quick.result : row.assessment || null;
    const assessmentSource = assessment ? (quickIsLatest ? "quick" : "run") : null;
    // SCOPE-ADD-3 D: the row's own rank line (score, reasons, blockers),
    // with a newer score from a re-rank pass over it. A quick assessment's
    // stored `rank_score` is never shown: it came from the retired hosted ranker.
    const rank = mergeRank(rankEntry(row.rank), rankEntry(rankByUrl.get(url)));
    return {
      id: url,
      posting,
      row,
      status: row.status,
      notAssessedReason: row.notAssessedReason || null,
      fromRunDate: row.fromRunDate || null,
      runCreatedAt: runCreatedAt || null,
      rank,
      quick,
      assessment,
      assessmentSource,
      verdict: effectiveVerdict(assessment),
      sponsorship: (assessment && assessment.sponsorship) || posting.sponsorship || "unknown",
      h1b: row.h1b || null,
      workModeFit: row.workModeFit || null,
    };
  });

  // Q4a-nav: a posting assessed on demand ("+ Assess a job", the CLI, a
  // pasted text) that no row of the loaded run carries is still a job: its
  // job page (#/jobs/<job_identity>) is the same JobPage, so the store's
  // ResolvedJob (title / company / location / source_url, text never
  // serialized) stands in for the posting row. `row` is null for these.
  // uat-bug-016: these are never cards on Jobs (runJobs() drops them); they
  // stay in this list so a job page finds them by id. Which of them are
  // cards on Assessments is assessmentJobs()'s rule.
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

// uat-bug-016: Jobs and a run page list search (run) results only.
export function runJobs(jobs) {
  return (jobs || []).filter((job) => job.status !== "on_demand");
}

// The quick-assess store files an assessment under the resume it used: a
// profile's id, or "ephemeral" for a resume pasted for that one call
// (quick_assess.resume_key). GET /api/assessments?profile_id=ephemeral
// lists the pasted-resume ones.
export const PASTED_RESUME_KEY = "ephemeral";

export function usedPastedResume(item) {
  const resume = item && item.resume;
  return Boolean(resume) && !resume.profile_id && !resume.pinned;
}

// --- where a posting lives (uat-batch2-r1, operator N21) -----------------------------
//
// Jobs = search results, Assessments = on-demand only. The quick-assess
// store holds both kinds: "+ Assess a job" / `gigai scout assess`, and every
// assessment started from a run posting's job page ("Assess this posting",
// Re-assess, an answer).
//
// assess-origin-field: a store item says which in `origin`
// (assess_contracts.ASSESS_ORIGINS): "quick_assess" lives under
// Assessments, "job_page" under Jobs, whatever runs are loaded. So a job
// page's assessment stays off Assessments after a reload that loads another
// run, and a quick assessment stays on Assessments when a later run finds
// the same posting (that posting is then a card on Jobs too).
//
// An item stored before the field has no `origin`. For those the older rule
// decides, from the job's identity and the resume it used:
//
//   a store item is a RUN POSTING's (it lives under Jobs) when it used a
//   profile's resume AND its job identity is a posting of a find-jobs run
//   the app has loaded for this profile; anything else is on demand.
//
// `runPostingIds` is the postings' normalized_url of every run loaded since
// the page was opened (FindJobsView adds each run's rows): the newest
// successful run always, plus any run the operator opened.
export const ORIGIN_QUICK_ASSESS = "quick_assess";
export const ORIGIN_JOB_PAGE = "job_page";

// The item's own `origin`, or null when it has none (or one this UI does
// not know): the caller then uses the older rule.
export function storedOrigin(item) {
  const origin = item && item.origin;
  return origin === ORIGIN_QUICK_ASSESS || origin === ORIGIN_JOB_PAGE ? origin : null;
}

// The `origin` POST /api/assess gets from a job page: a posting of a run
// (a card with a row) is the job page's; the page of an on-demand
// assessment keeps what its store item says.
// The "Quick assess via URL" source chip belongs to an on-demand assessment.
// A posting assessed from the Jobs list or a job page is stored with
// origin "job_page" and is not a quick assessment, so it carries no such chip.
export function showQuickAssessChip(job) {
  return Boolean(job) && job.status === "on_demand" && storedOrigin(job.quick) !== ORIGIN_JOB_PAGE;
}

export function assessOriginFor(job) {
  const stored = storedOrigin(job && job.quick);
  if (stored) {
    return stored;
  }
  // 0.1.10.7 M4b: a posting opened from the Jobs list (postingsModel.postingJob) is a job page's too.
  return job && (job.row || job.fromPostings) ? ORIGIN_JOB_PAGE : ORIGIN_QUICK_ASSESS;
}

export function addRunPostings(known, rows) {
  let next = known instanceof Set ? known : new Set(known || []);
  const start = next;
  (rows || []).forEach((row) => {
    const id = row && row.posting && row.posting.normalized_url;
    if (id && !next.has(id)) {
      if (next === start) {
        next = new Set(start);
      }
      next.add(id);
    }
  });
  return next; // the SAME set when nothing was added, so a state setter is a no-op
}

function idSet(ids) {
  return ids instanceof Set ? ids : new Set(ids || []);
}

export function isRunPosting(item, runPostingIds) {
  const job = item && item.job;
  if (!job) {
    return false;
  }
  const origin = storedOrigin(item);
  if (origin) {
    return origin === ORIGIN_JOB_PAGE;
  }
  if (usedPastedResume(item)) {
    return false;
  }
  const ids = idSet(runPostingIds);
  return ids.has(job.job_identity) || (Boolean(job.normalized_url) && ids.has(job.normalized_url));
}

// "jobs" | "assessments": the list a store item's posting is a card on, and
// so where a link to it opens (#/jobs/<id> or #/assessments/<id>).
export function postingHome(item, runPostingIds) {
  return isRunPosting(item, runPostingIds) ? "jobs" : "assessments";
}

// uat-bug-016 → uat-batch2-r1: the Assessments page. The ON-DEMAND
// assessments in the quick-assess store for the profile
// (GET /api/assessments?profile_id=…) plus the ones made against a pasted
// resume (`pastedItems`, which belong to no profile), one card per job,
// newest first. An assessment started from a job page is never listed: that
// posting is a card on Jobs and its history grows there (see the rule
// above; for an item without `origin` the run rows in `jobs` count as run
// postings too, so the loaded run needs no `runPostingIds`). Every card is
// the store's own (quickOnlyJob), also when a loaded run carries the same
// posting: that run's card, under Jobs, is another one. A pasted-resume
// assessment of a run posting's address never stands in for that posting's
// verdict, which is the profile's.
export function assessmentJobs(quickItems, jobs, pastedItems, runPostingIds) {
  const byId = new Map((jobs || []).map((job) => [job.id, job]));
  const runIds = new Set(idSet(runPostingIds));
  (jobs || []).forEach((job) => job.row && runIds.add(job.id));
  const latest = new Map();
  (quickItems || []).concat(pastedItems || []).forEach((item) => {
    const identity = item && item.job && item.job.job_identity;
    if (!identity || isRunPosting(item, runIds)) {
      return;
    }
    const existing = latest.get(identity);
    if (!existing || assessmentTime(item) > assessmentTime(existing)) {
      latest.set(identity, item);
    }
  });
  return [...latest.values()]
    .sort((a, b) => assessmentTime(b).localeCompare(assessmentTime(a)))
    .map((item) => {
      const known = usedPastedResume(item) ? null : byId.get(item.job.job_identity) || byId.get(item.job.normalized_url) || null;
      if (known && !known.row) {
        return known;
      }
      // A run's card for the same posting lends its rank only.
      return quickOnlyJob(item, known ? known.rank : null);
    });
}

// When a job was last assessed on demand ("" when it never was).
export function assessedAt(job) {
  return job && job.quick ? assessmentTime(job.quick) : "";
}

export function sortByAssessedAt(jobs) {
  return jobs.slice().sort((a, b) => assessedAt(b).localeCompare(assessedAt(a)));
}

// uat-batch2: `posting_text` is the full public posting text (absent for a
// pasted job, whose text is never stored). `rank` is the rank a run's card
// for the same posting lends it, else a re-rank score for its identity; an
// item's own stored `rank_score` (the retired hosted ranker's) is not shown.
export function quickOnlyJob(item, rank = null) {
  const job = item.job || {};
  const posting = {
    title: job.title || "",
    company: job.company || "",
    location: job.location || "",
    url: job.source_url || null,
    normalized_url: job.normalized_url || job.job_identity,
    text: item.posting_text || null,
    published_at: null,
    provider: null,
    source_kind: job.fetch_kind === "pasted" ? "pasted text" : "on demand",
  };
  const assessment = item.result || null;
  const ownRank = rankEntry(rank);
  return {
    id: job.job_identity,
    posting,
    row: null,
    status: "on_demand",
    notAssessedReason: null,
    fromRunDate: null,
    runCreatedAt: null,
    rank: ownRank,
    pastedResume: usedPastedResume(item),
    quick: item,
    assessment,
    assessmentSource: assessment ? "quick" : null,
    verdict: effectiveVerdict(assessment),
    sponsorship: (assessment && assessment.sponsorship) || "unknown",
    h1b: null, // the store's ResolvedJob carries no catalog join
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

// `promptFor(question_id)` (optional) resolves an id to the question's
// prompt text: question ids are normalized tokens (orchestrator rule,
// coordinator msg_54bb50ff18c5), so a person reads the prompt, and the id
// is secondary detail the caller may show beside it.
export function triggerLabel(trigger, promptFor) {
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
    const id = trigger.slice("answer:".length);
    const prompt = typeof promptFor === "function" ? promptFor(id) : null;
    return prompt ? `Re-assessed after you answered “${prompt}”` : `Re-assessed after you answered ${id}`;
  }
  return trigger || "";
}

// The id inside an "answer:<question_id>" trigger, for the secondary detail.
export function triggerQuestionId(trigger) {
  return typeof trigger === "string" && trigger.startsWith("answer:") ? trigger.slice("answer:".length) : null;
}

// question_id -> prompt text, from every place a prompt is known: the
// assessments' own structured_questions (`question`; pass every assessment
// the job has -- a re-assessed job's latest result may carry no questions
// while the run's result still names them) and the recorded answers (GET
// /api/answers rows carry `question`). An answer recorded without a prompt
// is stored with the id AS its prompt (the CLI / a bare POST), which is no
// prompt at all: skipped, so the caller falls back to the id honestly.
export function questionPromptIndex({ answers, assessment, assessments }) {
  const index = new Map();
  const usable = (id, text) => id && typeof text === "string" && text.trim() && text.trim() !== id;
  [assessment].concat(assessments || []).forEach((item) => {
    ((item && item.structured_questions) || []).forEach((question) => {
      if (question && usable(question.question_id, question.question) && !index.has(question.question_id)) {
        index.set(question.question_id, question.question.trim());
      }
    });
  });
  (answers || []).forEach((answer) => {
    // 0.1.10.7 C: an answer row carries `question`; `prompt` was its name before.
    const words = answer && (answer.question || answer.prompt);
    if (answer && usable(answer.question_id, words) && !index.has(answer.question_id)) {
      index.set(answer.question_id, words.trim());
    }
  });
  return index;
}

// --- sort + filter ----------------------------------------------------------------

export function sortJobs(jobs) {
  return jobs.slice().sort((a, b) => {
    const groupDelta = (VERDICT_ORDER[a.verdict] ?? 5) - (VERDICT_ORDER[b.verdict] ?? 5);
    if (groupDelta !== 0) {
      return groupDelta;
    }
    const rankDelta = compareRank(a.rank, b.rank);
    if (rankDelta !== 0) {
      return rankDelta;
    }
    return (b.posting.published_at || "").localeCompare(a.posting.published_at || "");
  });
}

// uat-bug-018: `state` filters by the job's derived state (job.state.state,
// jobStateModel.withJobStates); it took the place of the grid's "Assessed"
// chips. `assessed` (by verdict) is still honoured for a caller that sets it.
// `fit` filters by the model's rank (rankModel.rankFilterValue: strong,
// maybe, no, blocked, unranked). Nothing is hidden by default: a posting
// with a blocker is demoted, never hidden (SCOPE-ADD-3).
// 0110-019: `posted` is the "Posted" chip: a number of days (the posting's
// own date is within them; postedWindowModel.postedWithin) or "any".
export const EMPTY_FILTERS = { search: "", company: "", fit: "all", sponsorship: "all", assessed: "all", state: "all", posted: POSTED_ANY };

export function hasActiveFilter(filters) {
  return Boolean(
    filters.search ||
      filters.company ||
      filters.fit !== "all" ||
      filters.sponsorship !== "all" ||
      filters.assessed !== "all" ||
      (filters.state && filters.state !== "all") ||
      isPostedWindow(filters.posted),
  );
}

export function jobMatchesFilters(job, filters, now = Date.now()) {
  if (filters.company && job.posting.company !== filters.company) {
    return false;
  }
  if (!postedWithin(job.posting.published_at, filters.posted, now)) {
    return false;
  }
  if (filters.fit !== "all") {
    if (rankFilterValue(job.rank) !== filters.fit) {
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
  if (filters.state && filters.state !== "all" && (job.state ? job.state.state : "not_assessed") !== filters.state) {
    return false;
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

export function filterJobs(jobs, filters, now = Date.now()) {
  return jobs.filter((job) => jobMatchesFilters(job, filters, now));
}

// uat-bug-025: what the run says about the jobs it left unassessed. A run
// posting past the run's assess cap stays "acquired" for good; once the run
// has ended that is not "waiting" but "beyond the cap" (`assessCap` is the
// run's own selection_cap, null when the read did not carry one).
export function withRunEnd(jobs, { ended, assessCap }) {
  if (!ended) {
    return jobs;
  }
  return jobs.map((job) => (job.row ? { ...job, runEnded: true, assessCap: assessCap || null } : job));
}

export function notAssessedLine(job) {
  if (job.status === "assessing") {
    return "Assessing…";
  }
  if (job.status === "acquired") {
    if (job.runEnded) {
      return job.assessCap ? `Not assessed: this run assessed its top ${job.assessCap}` : "Not assessed: past this run's full-assessment limit";
    }
    return "Waiting to be assessed";
  }
  if (job.status === "failed") {
    return job.notAssessedReason ? notAssessedReasonLabel(job.notAssessedReason) : "Assessment failed";
  }
  return job.notAssessedReason ? notAssessedReasonLabel(job.notAssessedReason) : "Not assessed";
}
