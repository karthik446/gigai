// 0110-10-05 C: what a tailored resume left out for length, as one line and
// one action. `result.length` (tailor_length.LengthFit) is absent when the
// resume fits with nothing left out; otherwise:
//
//   status cut        roles (whole, oldest first) and/or the later bullets
//                     of old roles are left out -> "Cut for length: ..." and
//                     Restore (PUT /api/tailored-resumes/length use: restore)
//   status restored   the user put it all back -> what "Cut again" would
//                     leave out (use: cut), and the page count now
//   status over       over the limit, and leaving out older roles would not
//                     fix it: nothing was cut, no action
//   status unmeasured the pages could not be measured: nothing was cut
//
// Pure: the panel renders `text` as a React text child and sends `action.use`.
function plural(count, word) {
  return `${count} ${word}${count === 1 ? "" : "s"}`;
}

// A role is an OLD role when its heading names a year, is not ongoing, and
// its latest year is more than OLD_ROLE_YEARS back (tailored_resume's
// LENGTH_RULE; tailor_length._old_role_label is the same rule).
export const OLD_ROLE_YEARS = 8;
const ONGOING = /\b(?:present|current|now|today|ongoing)\b/i;
const YEAR = /(?<!\d)(19[5-9]\d|20\d\d)(?!\d)/g;

export function isOldRole(role, year) {
  const label = String(role || "");
  if (ONGOING.test(label)) {
    return false;
  }
  const years = (label.match(YEAR) || []).map(Number);
  return years.length > 0 && year - Math.max(...years) > OLD_ROLE_YEARS;
}

// "3 older bullets (3 of <role>)" only when every role named is an old one:
// the master resume's fit also takes the lowest-value lines of RECENT roles
// (0.1.10.9 master P4), and those are "5 bullets (...)", never "older".
function leftOut(length, year) {
  const parts = (length.cut || []).map((role) => role.role).filter(Boolean);
  const trims = (length.trimmed || []).filter((role) => (role.bullets || []).length > 0);
  const trimmed = trims.reduce((sum, role) => sum + role.bullets.length, 0);
  if (trimmed > 0) {
    const roles = trims.map((role) => `${role.bullets.length} of ${role.role}`).join(", ");
    const word = trims.every((role) => isOldRole(role.role, year)) ? "older bullet" : "bullet";
    parts.push(`${plural(trimmed, word)} (${roles})`);
  }
  return parts.join("; ");
}

export function lengthNote(response, year = new Date().getFullYear()) {
  const length = response && response.result ? response.result.length : null;
  if (!length || typeof length !== "object") {
    return null;
  }
  const limit = `${length.max_pages}-page limit`;
  if (length.status === "unmeasured") {
    return { status: "unmeasured", text: `Length not checked: the pages could not be measured, so nothing was cut. The ${limit} applies.`, action: null };
  }
  if (length.status === "over") {
    return { status: "over", text: `${plural(length.pages, "page")}, over the ${limit}. Leaving out older roles would not fix it, so nothing was cut.`, action: null };
  }
  const what = leftOut(length, year);
  if (length.status === "restored") {
    const pages = length.full_pages;
    const size = pages && pages > length.max_pages ? ` The resume is now ${plural(pages, "page")}, over the ${limit}.` : "";
    return { status: "restored", text: `Put back (was cut for length): ${what}.${size}`, action: { use: "cut", label: "Cut for length again" } };
  }
  if (length.status !== "cut") {
    return null;
  }
  const was = length.full_pages && length.pages && length.full_pages !== length.pages ? ` (${length.full_pages} pages to ${length.pages})` : "";
  let tail = "";
  if (!length.pages) {
    tail = ` The pages could not be measured, so no role was cut. The ${limit} applies.`;
  } else if (length.pages > length.max_pages) {
    tail = ` Still ${plural(length.pages, "page")}, over the ${limit}: leaving out older roles would not fix it.`;
  }
  return { status: "cut", text: `Cut for length${was}: ${what}.${tail}`, action: { use: "restore", label: "Restore" } };
}
