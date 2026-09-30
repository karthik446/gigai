// P9/F3: "Prep for interview" -- per the task spec this shows the
// `gigai scout prep <url>` command (scout_cli.py's real `prep` command
// signature: `gigai scout prep POSTING_URL [--profile PROFILE_ID]`), since
// no API route runs prep synchronously from the UI. Copy-to-clipboard only;
// never executes anything.
//
// uat-bug-051: hidden in 0.1.9 (not operator-UAT'd; the command needs an
// OpenAI key). Every mount point (job page, posting cards) renders through
// this component, so flipping this one constant to true in 0.1.10 shows it
// again.
export const INTERVIEW_PREP_VISIBLE = false;

export default function PrepPanel({ postingUrl, profileId }) {
  if (!INTERVIEW_PREP_VISIBLE) return null;
  const command = `gigai scout prep ${postingUrl}${profileId ? ` --profile ${profileId}` : ""}`;

  function handleCopy() {
    if (navigator.clipboard) {
      navigator.clipboard.writeText(command).catch(() => {});
    }
  }

  return (
    <div className="prep-panel">
      <h4>Prep for interview</h4>
      <p className="muted">Run this in a terminal to research the company and generate likely interview questions:</p>
      <pre>{command}</pre>
      <button type="button" className="button small secondary" onClick={handleCopy}>
        Copy command
      </button>
    </div>
  );
}
