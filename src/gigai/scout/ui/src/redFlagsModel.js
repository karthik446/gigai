// 0.1.11.12 dossier-C (gigai-6po): up to three red flags beside the fit number.
//
// The server derives them when it reads an assessment (gigai.scout.red_flags: location, a required
// language the resume lacks, level) and serves them as `red_flags: [{kind, text}]` on the Jobs row
// (scout_new._row_json), on a quick item (GET /api/assessments) and on POST /api/assess's response.
// Nothing here computes a flag: the page shows what it was served. A flag's text is the posting's
// own words (a requirement row, its languages): the views render it as text only, never as HTML.

function flagsOf(record) {
  if (!record || typeof record !== "object" || !Array.isArray(record.red_flags)) {
    return null;
  }
  return record.red_flags
    .filter((flag) => flag && typeof flag === "object" && typeof flag.text === "string" && flag.text.trim())
    .map((flag) => ({ kind: typeof flag.kind === "string" ? flag.kind : "", text: flag.text }));
}

// The job page's flags: the served answer first (POST /api/assess's response), then the Jobs row the
// page was opened from, then the quick item. The first record that carries the key decides, so a
// served `[]` (a clean record) wins over an older row's flags. Always an array.
export function redFlagsOf({ served, listedRow, quick } = {}) {
  for (const record of [served, listedRow, quick]) {
    const flags = flagsOf(record);
    if (flags) {
      return flags;
    }
  }
  return [];
}

// The Jobs row's one compact marker: the count, with every flag's text in its title; null when clean.
export function redFlagsMarker(flags) {
  const count = Array.isArray(flags) ? flags.length : 0;
  if (count === 0) {
    return null;
  }
  return { count, label: `${count} red flag${count === 1 ? "" : "s"}`, title: flags.map((flag) => flag.text).join("\n") };
}
