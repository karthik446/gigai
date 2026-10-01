// 0110-024 P4 / 0110-025 R4 / 0110-026 S5: the status lines under the sources
// strip (Jobs) and in Settings' Sources panel, as pure functions (no React)
// so the node-backed static test can pin them.
//
// The data is GET /api/sources/update (find_jobs/sources_status.py,
// snapshot.snapshot_status), all of it read-only:
//   refresh     {enabled, state, in_progress, trigger, last_updated_at,
//               last_updated_minutes_ago, next_tick_at, next_tick_in_minutes,
//               schedule {kind times | interval, weekdays, weekends,
//               interval_seconds, checks_today} (0110-029)}
//   tags        {available, titles, tagged_by_rules, tagged_by_model,
//               model_other, awaiting_model, awaiting_model_queued,
//               awaiting_model_not_queued, not_queued_reason, tagging
//               {state, detail, retry_after, model} (0110-028), setting
//               {model_enabled, ...}, models {demand, backfill}, queue (null
//               without a refresh thread) {demand, backfill: {model,
//               consecutive_failures, last_error, retry_after}}}
//   update      the running or last update: {status, trigger manual | auto,
//               boards {checked, total}, catch_up {tags {companies_done,
//               companies_total, pending}, text_index deferred | building}}
//   text_index  {available, postings_with_text, unchecked}
//   snapshot    {enabled, as_of, source, last_result imported | up_to_date |
//               skipped | refused | failed, last_reason offline |
//               not_published | disabled | ..., last_message}
// A server from before these blocks sends none of them: every function then
// answers "" / null and the strip reads as it did.
//
// These lines only say what is so. Nothing here starts an update, a model
// call or a download.
import { formatCount } from "./runText.js";

// The one-line data-source and removal note, with the docs page behind it.
export const DATA_SOURCE_NOTE = "Company and title data comes from public job boards; a company can ask to be left out.";
export const DATA_SOURCE_DOCS_URL = "https://karthik446.github.io/gigai/latest/scout/sources/#where-the-data-comes-from";

const MAX_ERROR_LENGTH = 160;

function count(value) {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? Math.floor(value) : 0;
}

function minutesOrNull(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? Math.floor(value) : null;
}

// "12 min", "3 hr", "2 days": the short span of the refresh line.
export function spanLabel(minutes) {
  if (minutes < 60) {
    return `${minutes} min`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 48) {
    return `${hours} hr`;
  }
  return `${Math.round(hours / 24)} days`;
}

// The two halves of "updated 12 min ago · next check in 48 min", or null when
// the server sent no `refresh` block.
//   updated  "updated 12 min ago" | "updated just now" | "" (never updated)
//   next     "next check in 48 min" | "next check due now" | "updating now" |
//            "automatic checks are off" | "automatic checks start after the
//            first update" | "" (this server runs no refresh thread)
export function refreshParts(status) {
  const refresh = status && status.refresh;
  if (!refresh || typeof refresh !== "object") {
    return null;
  }
  const ago = minutesOrNull(refresh.last_updated_minutes_ago);
  const updated = ago === null ? "" : ago < 1 ? "updated just now" : `updated ${spanLabel(ago)} ago`;
  let next = "";
  if (refresh.in_progress || refresh.state === "running") {
    next = "updating now";
  } else if (!refresh.enabled) {
    next = "automatic checks are off";
  } else if (refresh.state === "needs_first_update") {
    next = "automatic checks start after the first update";
  } else {
    const due = minutesOrNull(refresh.next_tick_in_minutes);
    if (due !== null) {
      next = due < 1 ? "next check due now" : `next check in ${spanLabel(due)}`;
    }
  }
  return { updated, next, enabled: Boolean(refresh.enabled) };
}

// The sentence used where the strip's own line is not shown (the first-run
// stepper, Settings): "Updated 12 min ago · next check in 48 min." or "Not
// updated yet · automatic checks start after the first update."
export function refreshLine(status) {
  const parts = refreshParts(status);
  if (!parts) {
    return "";
  }
  const text = [parts.updated || "not updated yet", parts.next].filter(Boolean).join(" · ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}`;
}

function failingLane(queue) {
  if (!queue || typeof queue !== "object") {
    return null;
  }
  for (const name of ["demand", "backfill"]) {
    const lane = queue[name];
    if (!lane || typeof lane !== "object" || typeof lane.last_error !== "string" || !lane.last_error.trim()) {
      continue;
    }
    // `last_error` stays after the lane recovers; `consecutive_failures`
    // goes back to 0. A server that sends no counter is taken at its word.
    const failing = typeof lane.consecutive_failures === "number" ? lane.consecutive_failures > 0 : true;
    if (failing) {
      return lane;
    }
  }
  return null;
}

// Why the server says some titles will not be tagged by a model (tags.not_queued_reason).
const NOT_QUEUED_REASONS = {
  backfill_off: "background tagging of the rest is off",
  model_off: "tagging with the model is off",
  refresh_off: "automatic updates are off",
  no_refresh_thread: "this server runs no background tagging",
};

// 0110-028: the tags line of a server that says what a model WILL tag.
//   "Titles: 5,200 stored, each with a level from the rules. Function: 4,400
//    by rules, 600 by model, 50 a model could not place, 100 waiting for the
//    model, 50 not tagged by a model (background tagging of the rest is off)"
// The five numbers after "Function:" add up to the first one. "Waiting" is
// only what the model is going to tag under the settings in effect.
function honestTagsLine(tags) {
  const parts = [`${formatCount(count(tags.tagged_by_rules))} by rules`, `${formatCount(count(tags.tagged_by_model))} by model`];
  if (count(tags.model_other) > 0) {
    parts.push(`${formatCount(count(tags.model_other))} a model could not place`);
  }
  const queued = count(tags.awaiting_model_queued);
  const notQueued = count(tags.awaiting_model_not_queued);
  if (queued > 0 || notQueued === 0) {
    parts.push(`${formatCount(queued)} waiting for the model`);
  }
  if (notQueued > 0) {
    const reason = NOT_QUEUED_REASONS[tags.not_queued_reason];
    parts.push(`${formatCount(notQueued)} not tagged by a model${reason ? ` (${reason})` : ""}`);
  }
  return `Titles: ${formatCount(count(tags.titles))} stored, each with a level from the rules. Function: ${parts.join(", ")}`;
}

// "Tagging: running, 2,032 titles to go" / "waiting for this update to
// finish" / "off" / "paused"; "" when there is nothing to say (idle, a
// failing lane has its own line, no background thread).
function taggingLine(tags) {
  const tagging = tags.tagging;
  if (!tagging || typeof tagging !== "object") {
    return "";
  }
  const queued = count(tags.awaiting_model_queued);
  const titles = `${formatCount(queued)} title${queued === 1 ? "" : "s"}`;
  if (tagging.state === "running") {
    return `Tagging: running, ${titles} to go (it also runs while a background check does)`;
  }
  if (tagging.state === "waiting_for_update") {
    return `Tagging: ${titles} wait for this update to finish, then the model starts`;
  }
  if (tagging.state === "off") {
    return "Tagging: off (tagging with the model is off)";
  }
  if (tagging.state === "paused") {
    return "Tagging: paused (automatic updates are off)";
  }
  return "";
}

// The tags line and, when a model lane is failing right now, one quiet line
// with its last error; `tagging` says what the model queue is doing. Null
// when the tag store has nothing to count. A server from before 0110-028
// sends no queued count and reads as it did:
// "Titles tagged: 4,400 by rules, 600 by model, 150 waiting".
export function tagsLines(status) {
  const tags = status && status.tags;
  if (!tags || typeof tags !== "object" || !tags.available || count(tags.titles) === 0) {
    return null;
  }
  let line;
  let tagging = "";
  if (typeof tags.awaiting_model_queued === "number") {
    line = honestTagsLine(tags);
    tagging = taggingLine(tags);
  } else {
    const waiting = count(tags.awaiting_model);
    const refresh = status.refresh;
    let why = "";
    if (waiting > 0) {
      if (refresh && typeof refresh === "object" && !refresh.enabled) {
        why = " (paused: automatic updates are off)";
      } else if (tags.setting && tags.setting.model_enabled === false) {
        why = " (tagging with the model is off)";
      }
    }
    line = `Titles tagged: ${formatCount(count(tags.tagged_by_rules))} by rules, ${formatCount(count(tags.tagged_by_model))} by model, ${formatCount(waiting)} waiting${why}`;
  }
  const lane = failingLane(tags.queue);
  let error = "";
  if (lane) {
    const reason = lane.last_error.trim().replace(/\s+/g, " ");
    const short = reason.length > MAX_ERROR_LENGTH ? `${reason.slice(0, MAX_ERROR_LENGTH - 1)}…` : reason;
    const model = typeof lane.model === "string" && lane.model ? ` (${lane.model})` : "";
    error = `The model${model} could not tag titles: ${short}${/[.!?…]$/.test(short) ? "" : "."} It is tried again by itself.`;
  }
  return { line, error, tagging };
}

// "13:30" in the viewer's local time (the machine Scout runs on), or "".
export function clockLabel(stamp) {
  const moment = typeof stamp === "string" && stamp ? new Date(stamp) : null;
  if (!moment || Number.isNaN(moment.getTime())) {
    return "";
  }
  return `${String(moment.getHours()).padStart(2, "0")}:${String(moment.getMinutes()).padStart(2, "0")}`;
}

function boardsProgress(update) {
  const boards = update && typeof update === "object" ? update.boards : null;
  if (!boards || typeof boards !== "object" || count(boards.total) === 0) {
    return "";
  }
  return `${formatCount(count(boards.checked))} of ${formatCount(count(boards.total))} boards checked`;
}

// 0110-028/029: what runs in the background, said so that its pace never
// looks stuck. "" when the server sent no schedule (an older server).
//   idle     "Background check: 8 a day on weekdays, 2 on weekend days ·
//             next at 13:30 · last 11:58 (12 min ago)"
//   a check  "Background check running: 1,204 of 3,982 boards checked (busy
//             boards every check, quiet ones about twice a day), at a
//             moderate pace on purpose"
//   manual   "Update sources running at full speed: 1,204 of 10,360 boards checked"
//   off      "Background check: off"
export function checkLine(status) {
  const refresh = status && status.refresh;
  const schedule = refresh && typeof refresh === "object" ? refresh.schedule : null;
  if (!schedule || typeof schedule !== "object") {
    return "";
  }
  if (refresh.in_progress || refresh.state === "running") {
    const progress = boardsProgress(status.update);
    if (refresh.trigger === "auto") {
      return `Background check running: ${progress ? `${progress} ` : ""}(busy boards every check, quiet ones about twice a day), at a moderate pace on purpose`;
    }
    return `Update sources running at full speed${progress ? `: ${progress}` : ""}`;
  }
  if (!refresh.enabled) {
    return "Background check: off";
  }
  let often = "";
  if (schedule.kind === "times" && Array.isArray(schedule.weekdays) && Array.isArray(schedule.weekends)) {
    often = `${schedule.weekdays.length} a day on weekdays, ${schedule.weekends.length} on weekend days`;
  } else if (typeof schedule.interval_seconds === "number" && schedule.interval_seconds > 0) {
    often = `every ${spanLabel(Math.max(1, Math.round(schedule.interval_seconds / 60)))}`;
  }
  const parts = [];
  if (refresh.state === "needs_first_update") {
    parts.push("starts after the first update");
  } else {
    const next = clockLabel(refresh.next_tick_at);
    if (next) {
      parts.push(minutesOrNull(refresh.next_tick_in_minutes) === 0 ? "next due now" : `next at ${next}`);
    }
    const last = clockLabel(refresh.last_updated_at);
    const ago = minutesOrNull(refresh.last_updated_minutes_ago);
    if (last) {
      parts.push(`last ${last}${ago === null ? "" : ` (${ago < 1 ? "just now" : `${spanLabel(ago)} ago`})`}`);
    }
  }
  return [`Background check: ${often || "on"}`, ...parts].join(" · ");
}

// The one-time work an update does after its boards (0110-028), or "".
export function catchUpLine(status) {
  const update = status && status.update;
  const catchUp = update && typeof update === "object" && update.status === "running" ? update.catch_up : null;
  if (!catchUp || typeof catchUp !== "object") {
    return "";
  }
  if (catchUp.text_index === "building") {
    return "Building the keyword index from the stored descriptions (once; the boards are done)";
  }
  const tags = catchUp.tags;
  if (tags && typeof tags === "object" && tags.pending) {
    return `Tagging stored titles by the rules (once): ${formatCount(count(tags.companies_done))} of ${formatCount(count(tags.companies_total))} companies`;
  }
  return "";
}

// "Descriptions checked for 6,100 postings, 2,900 not yet", or "".
export function textLine(status) {
  const text = status && status.text_index;
  if (!text || typeof text !== "object" || !text.available) {
    return "";
  }
  const checked = count(text.postings_with_text);
  const unchecked = count(text.unchecked);
  if (checked + unchecked === 0) {
    return "";
  }
  return `Descriptions checked for ${formatCount(checked)} posting${checked === 1 ? "" : "s"}, ${formatCount(unchecked)} not yet`;
}

// The day of an `as_of` stamp ("2026-09-30"), else the text as the server sent it.
export function asOfLabel(asOf) {
  if (typeof asOf !== "string" || !asOf.trim()) {
    return "";
  }
  // Only a stamp that starts with a date is cut: Date() would read a year into almost any text.
  const text = asOf.trim();
  return /^\d{4}-\d{2}-\d{2}(T|$)/.test(text) ? text.slice(0, 10) : text;
}

// The starter snapshot's "as of" line. Offline, "none published" and a
// download that was not used are said quietly: a snapshot is a head start,
// never something an update needs. "" when the server sent no block.
export function snapshotLine(status) {
  const snapshot = status && status.snapshot;
  if (!snapshot || typeof snapshot !== "object") {
    return "";
  }
  const asOf = asOfLabel(snapshot.as_of);
  const off = snapshot.enabled === false;
  const result = snapshot.last_result;
  const reason = snapshot.last_reason;
  let note = "";
  if (off) {
    note = "downloads are off";
  } else if (result === "skipped" && reason === "offline") {
    note = asOf ? "a newer one could not be reached" : "it could not be reached, and is tried again at the next update";
  } else if (result === "skipped" && reason === "not_published") {
    note = asOf ? "" : "none is published yet";
  } else if (result === "refused" || result === "failed") {
    note = asOf ? "the last download was not used" : "the last download was not used; your own updates fill the store";
  } else if (!asOf) {
    note = result === "skipped" ? "none was used at the last update" : "not downloaded yet";
  }
  if (asOf) {
    return `Starter data: as of ${asOf}${note ? ` · ${note}` : ""}`;
  }
  return `Starter data: ${note}`;
}

// Every detail line of the strip, in order, for one read of the status:
//   [{role, text, tone}]  role refresh | check | catch-up | tags | tagging |
//   tags-error | text | snapshot;
//   tone is "quiet" for everything (none of these is an error to act on).
// `withRefresh` adds the refresh sentence (the places that do not already
// carry it in their own line).
export function statusLines(status, { withRefresh = false } = {}) {
  const lines = [];
  const push = (role, text) => text && lines.push({ role, text, tone: "quiet" });
  if (withRefresh) {
    push("refresh", refreshLine(status));
  }
  push("check", checkLine(status));
  push("catch-up", catchUpLine(status));
  const tags = tagsLines(status);
  if (tags) {
    push("tags", tags.line);
    push("tagging", tags.tagging);
    push("tags-error", tags.error);
  }
  push("text", textLine(status));
  push("snapshot", snapshotLine(status));
  return lines;
}
