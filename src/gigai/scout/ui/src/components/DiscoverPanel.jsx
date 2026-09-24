function SponsorshipMark({ sponsorship }) {
  const status = sponsorship || "unknown";
  const label = status === "yes" ? "Sponsors" : status === "no" ? "No sponsorship" : "Sponsorship unknown";
  return <span className={`status-badge sponsorship-${status === "yes" ? "offered" : status === "no" ? "not_offered" : "unknown"}`}>{label}</span>;
}

function VerifiedMark({ verified }) {
  return (
    <span className={`status-badge ${verified ? "met" : "gap"}`}>
      {verified ? "Evidence verified" : "Evidence unverified"}
    </span>
  );
}

function NewBoardCard({ board }) {
  return (
    <div className="assessment-card">
      <div className="posting-card-heading">
        <a href={board.careers_url} target="_blank" rel="noreferrer">
          {board.company}
        </a>
        <span className="muted">{board.provider}</span>
      </div>
      <div className="posting-card-meta">
        <SponsorshipMark sponsorship={board.sponsorship} />
        <VerifiedMark verified={board.evidence_verified} />
        <span>{board.matching_us_postings} matching posting(s)</span>
      </div>
      {board.sponsorship_evidence && <p>{board.sponsorship_evidence}</p>}
      {board.evidence_source_url && (
        <a href={board.evidence_source_url} target="_blank" rel="noreferrer">
          Evidence source
        </a>
      )}
    </div>
  );
}

// Coordinator FYI (2026-09-24): the H-1B source only runs when
// visa_sponsorship_required is true; otherwise it's skipped with reason
// "sponsorship_not_required", which is expected, not an error -- shown
// plainly rather than folded into the same "error" styling as a real
// provider failure.
const SKIP_REASON_LABELS = {
  sponsorship_not_required: "not used, since sponsorship isn't required",
};

function sourceStatusLabel(source) {
  if (source.error) {
    return `error: ${source.error}`;
  }
  if (source.skip_reason) {
    return SKIP_REASON_LABELS[source.skip_reason] || `skipped: ${source.skip_reason}`;
  }
  return null;
}

function formatDaysAgo(daysAgo) {
  if (daysAgo === null || daysAgo === undefined) {
    return "never run";
  }
  if (daysAgo === 0) {
    return "today";
  }
  if (daysAgo === 1) {
    return "1 day ago";
  }
  return `${daysAgo} days ago`;
}

// CHANGE #1/#2: "last run N days ago", cost, new boards with sponsorship
// evidence (+ verified/unverified mark), and a button to start a session;
// weekly cadence is shown, not scheduled (gigai has no scheduler).
export default function DiscoverPanel({ latest, running, cadenceDays, onStart, starting, error }) {
  const result = latest && latest.result;
  const newBoards = result && Array.isArray(result.new_boards) ? result.new_boards : [];

  return (
    <section className="panel">
      <h2>Discover companies</h2>
      <p className="muted">
        Looks for new companies (not already watchlisted) with visa-sponsorship evidence, using OpenAI web search and
        the H-1B baseline. Runs roughly every {cadenceDays} day{cadenceDays === 1 ? "" : "s"} -- shown, not scheduled;
        gigai has no scheduler, so start each session by hand.
      </p>

      <div className="field-row">
        <div className="field">
          <div className="label">Last run</div>
          <div className="value">{formatDaysAgo(latest ? latest.days_ago : null)}</div>
        </div>
        {result && (
          <div className="field">
            <div className="label">Cost</div>
            <div className="value">${result.cost_usd?.toFixed(4)}</div>
          </div>
        )}
        {result && (
          <div className="field">
            <div className="label">Status</div>
            <div className="value">{result.status}</div>
          </div>
        )}
      </div>

      {(running || starting) && (
        <div className="callout info">
          Discovery is running… this can take 5-30 minutes. New companies will appear here once it finishes.
        </div>
      )}

      {error && <div className="callout danger">{error}</div>}

      <div className="actions" style={{ justifyContent: "flex-start", marginTop: 0, marginBottom: 12 }}>
        <button className="button" onClick={onStart} disabled={running || starting}>
          {running || starting ? "Running…" : "Start discovery session"}
        </button>
      </div>

      {result && result.sources && result.sources.length > 0 && (
        <div className="field">
          <div className="label">Sources</div>
          <ul>
            {result.sources.map((source) => {
              const statusLabel = sourceStatusLabel(source);
              return (
                <li key={source.name}>
                  {source.name}: {source.runs} run(s), ${source.cost_usd?.toFixed(4)}
                  {statusLabel ? ` -- ${statusLabel}` : ""}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      {newBoards.length > 0 ? (
        <div>
          <h3 style={{ fontSize: "0.95rem" }}>New boards found ({newBoards.length})</h3>
          {newBoards.map((board) => (
            <NewBoardCard key={`${board.provider}:${board.board_token}`} board={board} />
          ))}
        </div>
      ) : (
        result && <p className="muted">No new companies found on the last run.</p>
      )}
    </section>
  );
}
