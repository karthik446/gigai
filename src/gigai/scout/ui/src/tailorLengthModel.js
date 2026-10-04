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

function leftOut(length) {
  const parts = (length.cut || []).map((role) => role.role).filter(Boolean);
  const trimmed = (length.trimmed || []).reduce((sum, role) => sum + (role.bullets || []).length, 0);
  if (trimmed > 0) {
    const roles = (length.trimmed || []).map((role) => `${(role.bullets || []).length} of ${role.role}`).join(", ");
    parts.push(`${plural(trimmed, "older bullet")} (${roles})`);
  }
  return parts.join("; ");
}

export function lengthNote(response) {
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
  const what = leftOut(length);
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
