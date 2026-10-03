// 0110-047: deleting a profile. Pure (no React, no fetch), so the rules run under node.
//
// DELETE /api/profiles/{id} archives the profile as `deleted`: it leaves the switcher, new runs
// and background tagging; its runs and assessments stay in history (hidden from the default Jobs
// list); the story bank and answers are not touched. The server refuses the default profile and
// the only active profile; the UI says so before asking.

function active(profiles) {
  return (profiles || []).filter((item) => item.state !== "archived" && item.state !== "deleted");
}

// Why this profile cannot be deleted, or null when it can.
export function deleteBlockedReason(profiles, profile) {
  if (!profile) {
    return "No profile is selected.";
  }
  if (profile.is_default) {
    return "The default profile uses the setup settings and cannot be deleted.";
  }
  if (active(profiles).filter((item) => item.profile_id !== profile.profile_id).length === 0) {
    return "This is the only profile; add another one first.";
  }
  return null;
}

// The confirm text: names what happens, in order.
export function deleteConfirmText(profiles, profile) {
  const fallback = (profiles || []).find((item) => item.is_default);
  const parts = [
    `Delete "${profile.label}"? It leaves the profile list, the switcher and background searching.`,
    "Its past runs and assessments stay in history, hidden from the Jobs list. Your story bank and answers are not touched.",
  ];
  if (fallback && fallback.profile_id !== profile.profile_id) {
    parts.push(`Scout switches to "${fallback.label}".`);
  }
  return parts.join(" ");
}
