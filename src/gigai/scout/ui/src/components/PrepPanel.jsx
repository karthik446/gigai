// P9/F3: "Prep for interview" -- per the task spec this shows the
// `gigai scout prep <url>` command (scout_cli.py's real `prep` command
// signature: `gigai scout prep POSTING_URL [--profile PROFILE_ID]`), since
// no API route runs prep synchronously from the UI. Copy-to-clipboard only;
// never executes anything.
export default function PrepPanel({ postingUrl, profileId }) {
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
