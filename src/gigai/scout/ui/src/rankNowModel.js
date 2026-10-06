// 0.1.11.2 RANKUI: what the Jobs page says and does about ranking, as plain functions (tests/api_e2e/test_ui_rank_now_model.py).
//
// `ranking` is the block GET /api/postings (and POST /api/postings/rank) serves: {enabled, in_progress, window_days,
// by_profile: [{profile_id, ranked, total}]}; `total` is the ranked postings plus the unranked ones of the last
// `window_days` days (the only ones the rank lane ranks). An answer of POST /api/postings/rank
// (pipeline/rank_now.py) is {schema_version: "scout-rank-now:1", enabled, how_to_enable, calls_today, ranking, plan, job,
// started}.
//
// The rules the page keeps:
//   - the row is ALWAYS on the Jobs page (RANKVIS): "Ranked X of Y (last 7 days)" with the count of what is not ranked
//     yet, never a silent "not ranked yet" and never nothing;
//   - "Rank now" ranks them from the page; "Re-rank latest 100" shows its cost (the calls) BEFORE it runs;
//   - no model call without a click, and with ranking off the buttons say so and the line says how to turn it on.

export const RANK_SCHEMA = "scout-rank-now:1";
export const RANK_POLL_MS = 1500;

// The server's own words (rank_now.HOW_TO_ENABLE); the answer's `how_to_enable` is used when the page has one.
export const HOW_TO_ENABLE =
  'Ranking is off. To turn it on, set "rank": {"enabled": true} in the Scout settings of this project ' +
  "(PUT /api/settings/background, or the settings file); `gigai scout pipeline status` says what switched it off.";

export function isRankAnswer(answer) {
  return Boolean(answer) && typeof answer === "object" && answer.schema_version === RANK_SCHEMA;
}

function count(value) {
  return Number.isInteger(value) && value > 0 ? value : 0;
}

function plural(n, one, many) {
  return `${n} ${n === 1 ? one : many}`;
}

// {enabled, ranked, total, unranked, windowDays}, summed over the profiles; null with no ranking block.
export function rankTotals(ranking) {
  if (!ranking || typeof ranking !== "object" || !Array.isArray(ranking.by_profile)) {
    return null;
  }
  const sum = (key) => ranking.by_profile.reduce((total, item) => total + count(item && item[key]), 0);
  const ranked = sum("ranked");
  const total = Math.max(sum("total"), ranked);
  return { enabled: ranking.enabled === true, ranked, total, unranked: total - ranked, windowDays: count(ranking.window_days) || 7 };
}

// 0.1.11.2: the line above "Re-rank" when a profile's resume changed after its postings were ranked
// (`ranking.stale_resume`). A rank is made against the resume, so a posting ranked low for experience the master now
// has can only move up when it is ranked again. null when nothing changed, with ranking off, or while a rank job runs.
export const STALE_RESUME_LINE = "Your master changed since these postings were ranked";

export function staleResumeLine(ranking, job = null) {
  const totals = rankTotals(ranking);
  if (!totals || !totals.enabled || ranking.stale_resume !== true || rankJobRunning(job)) {
    return null;
  }
  return STALE_RESUME_LINE;
}

export function rankJobRunning(job) {
  return Boolean(job) && job.state === "running";
}

// 0.1.11.2 RANKVIS: THE ROW IS ALWAYS THERE. One small row above the list: "Ranked 57 of 57 (last 7 days)", then
// "Rank now" and "Re-rank latest 100". A button that cannot run is greyed and SAYS WHY in its own text ("Rank now:
// nothing to rank", "Rank now: ranking is off") and in its title; with no ranking block (an older server, a list that
// could not be read) the row says "Ranking status unavailable", never nothing.
export const RANK_UNAVAILABLE = "Ranking status unavailable";
export const RANK_READING = "Reading the ranking status…";

// The ONE status line of the row. `job`: the rank job the page started (or joined), or null. `loading`: the list's
// first read is on its way (no block yet).
export function rankStatusLine(ranking, job, howToEnable, { loading = false } = {}) {
  const totals = rankTotals(ranking);
  if (!totals) {
    return loading ? RANK_READING : `${RANK_UNAVAILABLE}.`;
  }
  const { ranked, total, unranked, windowDays } = totals;
  const count = `${ranked} of ${total} (last ${windowDays} days)`;
  if (!totals.enabled) {
    return `Ranked ${count} · ${howToEnable || HOW_TO_ENABLE}`;
  }
  if (rankJobRunning(job)) {
    return `${job.mode === "latest" ? "Re-ranking the latest 100" : "Ranking now"}: ranked ${count}`;
  }
  return unranked ? `Ranked ${count} · ${unranked} not ranked yet` : `Ranked ${count}`;
}

// The two buttons: {rankNow: {label, disabled, title}, rerank: {label, disabled, title}}. A disabled one carries its
// reason in the label. `busy`: a request of the row is on its way. With no ranking block the buttons stay on: a click
// asks the server, and its answer carries the ranking block the row then shows.
export function rankButtons(ranking, { job = null, busy = false, howToEnable = null } = {}) {
  const totals = rankTotals(ranking);
  const rerankTitle = "Ranks the newest 100 postings again, also the ranked ones. Asks first: the calls it makes. Nothing runs until you approve.";
  if (!totals) {
    return {
      rankNow: { label: "Rank now", disabled: busy, title: `${RANK_UNAVAILABLE}. Asks the server to rank the postings not ranked yet.` },
      rerank: { label: "Re-rank latest 100", disabled: busy, title: `${RANK_UNAVAILABLE}. ${rerankTitle}` },
    };
  }
  if (!totals.enabled) {
    const title = howToEnable || HOW_TO_ENABLE;
    return {
      rankNow: { label: "Rank now: ranking is off", disabled: true, title },
      rerank: { label: "Re-rank latest 100: ranking is off", disabled: true, title },
    };
  }
  const running = rankJobRunning(job);
  const hold = busy || running;
  const window = `the last ${totals.windowDays} days`;
  let rankNow;
  if (running && job.mode !== "latest") {
    rankNow = { label: "Ranking…", disabled: true, title: "Ranking the postings not ranked yet." };
  } else if (totals.unranked === 0) {
    rankNow = {
      label: "Rank now: nothing to rank",
      disabled: true,
      title: totals.total ? `Nothing to rank: every posting of ${window} is ranked.` : `Nothing to rank: no posting of ${window}.`,
    };
  } else {
    rankNow = {
      label: "Rank now",
      disabled: hold,
      title: `Ranks the ${plural(totals.unranked, "posting", "postings")} not ranked yet: one model call per 50, inside today's 100 rank calls.`,
    };
  }
  let rerank;
  if (running && job.mode === "latest") {
    rerank = { label: "Re-ranking…", disabled: true, title: rerankTitle };
  } else if (totals.total === 0) {
    rerank = { label: "Re-rank latest 100: nothing to rank", disabled: true, title: `Nothing to rank: no posting of ${window}.` };
  } else {
    rerank = { label: "Re-rank latest 100", disabled: hold, title: rerankTitle };
  }
  return { rankNow, rerank };
}

function refusalLine(plan, today) {
  if (!plan || !plan.refusal) {
    return null;
  }
  if (plan.refusal === "rank_daily_cap") {
    const limit = today && Number.isInteger(today.limit) ? today.limit : 100;
    return `Today's rank calls do not cover this: it needs ${count(plan.calls)}, ${count(plan.calls_left_today)} of ${limit} are left. The count starts again tomorrow.`;
  }
  if (plan.refusal === "nothing_to_rank") {
    return "No posting of the window to rank.";
  }
  if (plan.refusal === "rank_disabled") {
    return HOW_TO_ENABLE;
  }
  return String(plan.refusal).replace(/_/g, " ");
}

// The approval "Re-rank latest 100" asks for, from the ASK's answer: THE COST FIRST. null when the answer has no plan.
//   {title, postings, calls, costLine, todayLine, allowed, refusal, approveBody}
export function rerankDialog(answer) {
  if (!isRankAnswer(answer) || !answer.plan || answer.plan.mode !== "latest") {
    return null;
  }
  const plan = answer.plan;
  const today = answer.calls_today || {};
  const postings = count(plan.postings);
  const calls = count(plan.calls);
  const used = count(today.used);
  const limit = Number.isInteger(today.limit) ? today.limit : 100;
  const totals = rankTotals(answer.ranking);
  return {
    title: `Re-rank the latest ${plural(postings, "posting", "postings")}?`,
    postings,
    // The page's "Ranked X of Y (last 7 days)": the dialog takes the newest 100 of those Y, so it says so when Y is more.
    ofTotal: totals && totals.total > postings ? totals.total : null,
    calls,
    costLine: `${plural(calls, "model call", "model calls")} (up to 50 postings a call, at most ${count(plan.max_calls) || 2} calls)`,
    todayLine: `${used} of ${limit} rank calls used today; ${count(plan.calls_left_today)} left`,
    allowed: plan.allowed === true,
    refusal: refusalLine(plan, today),
    approveBody: plan.allowed === true ? { mode: "latest", approve: true } : null,
  };
}

// What a click that could not start says (the ask's refusal, or the 409's own message).
export function rankRefusalLine(answer) {
  return isRankAnswer(answer) ? refusalLine(answer.plan, answer.calls_today) : null;
}

// One line when a rank job has finished: what it did, in words. null while it runs or with no job.
export function rankOutcomeLine(job) {
  if (!job || job.state !== "done") {
    return null;
  }
  const calls = count(job.calls);
  const did = calls ? `${job.mode === "latest" ? "Re-ranked" : "Ranked"} ${plural(count(job.ranked), "posting", "postings")} in ${plural(calls, "call", "calls")}.` : "";
  const why = typeof job.reason === "string" ? job.reason.replace(/_/g, " ") : "";
  let rest;
  switch (job.outcome) {
    case "ran":
      rest = "";
      break;
    case "idle":
      rest = did ? "" : "Nothing to rank.";
      break;
    case "waiting":
      rest = "Today's rank calls are used up; ranking goes on tomorrow.";
      break;
    case "yielded":
      rest = `Ranking is waiting: ${why || "other work"} is running. Click again when that is done.`;
      break;
    case "busy_elsewhere":
      rest = "Ranking is already running in the background; the list updates as it goes.";
      break;
    case "disabled":
      rest = HOW_TO_ENABLE;
      break;
    default:
      rest = `Ranking stopped${why ? ` (${why})` : ""}.`;
  }
  return [did, rest].filter(Boolean).join(" ") || null;
}
