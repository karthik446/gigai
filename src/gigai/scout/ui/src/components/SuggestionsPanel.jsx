import { useEffect, useMemo, useState } from "react";
import { postJobSuggestion } from "../api.js";
import { briefCommands, suggestionRows } from "../jobResumeModel.js";

// 0.1.11 N6 (SPEC section 6, item 6): the suggestions of one job. What would
// make this resume fit the job better, and why: the kind, what it is about
// (the line's text or the requirement), the posting phrase, why, who wrote
// it, the status. A suggestion never holds a rewritten line, and NO button
// here rewrites anything with a model: the words are changed in chat, by the
// user's own agent, with the user.
//
//   Done / Dismiss   POST /api/jobs/suggestions (`resolve` with how it was
//                    done, or `dismiss`); recorded, never a delete. The
//                    record is read again after it
//   Work on this     shows the two brief commands to copy (SPEC 5.2: the
//   with your agent  user's part and the posting's part are two calls, never
//                    one response). Nothing is fetched and no model is called
//
// 0.1.11.3 item 9: the card is ONE line by default, "Suggestions (N open)",
// below the resume card (which holds Generate PDF). The line is a button: a
// click, Enter or Space opens the list and closes it again. Each job's card
// starts closed. The data, the CLI and the agent brief are unchanged.
//
// The list is the suggestion record's; while the page holds no record, the
// assessment's own structured suggestions are listed as open (they have no
// id in a record yet, so Done and Dismiss are not offered for them).
const DONE_HOW = { reword: "job_resume_edit", keyword: "job_resume_edit", order: "job_resume_edit", gap: "answer", master_line: "master_line" };

function copy(text) {
  if (typeof navigator !== "undefined" && navigator.clipboard && typeof navigator.clipboard.writeText === "function") {
    return navigator.clipboard.writeText(text).then(
      () => true,
      () => false,
    );
  }
  return Promise.resolve(false);
}

function Commands({ commands }) {
  const [copied, setCopied] = useState(null);
  return (
    <div className="callout info" data-role="agent-brief">
      <p>
        Tell your agent to work on your resume for this job. It runs these two commands (no model call, nothing sent anywhere), changes the wording with you in chat, and hands the result back
        for GigAI to check against your master.
      </p>
      {commands.map((item) => (
        <div key={item.part} data-brief-part={item.part}>
          <div className="muted small">{item.label}</div>
          <code data-role="brief-command">{item.command}</code>{" "}
          <button type="button" className="link-button" data-action="copy-brief" onClick={() => copy(item.command).then((ok) => setCopied(ok ? item.part : `failed-${item.part}`))}>
            {copied === item.part ? "Copied" : "Copy"}
          </button>
          {copied === `failed-${item.part}` && <span className="muted small"> Select the command and copy it.</span>}
          <div className="muted small" data-role="brief-route">
            or from the local server: <code>{item.route}</code>
          </div>
        </div>
      ))}
    </div>
  );
}

export default function SuggestionsPanel({ state, assessment, jobUrl }) {
  const [busy, setBusy] = useState(null); // the suggestion id being written
  const [error, setError] = useState(null);
  const [briefFor, setBriefFor] = useState(null);
  const [expanded, setExpanded] = useState(false);
  const jobKey = `${state.profileId}\n${state.jobIdentity}`;
  useEffect(() => {
    setExpanded(false);
  }, [jobKey]);
  const { record, stored } = state;
  // A line's text, for a line the stored resume prints (the page holds it already; no master read for this list).
  const texts = useMemo(() => {
    const found = new Map();
    const take = (line) => {
      (line.refs || []).forEach((ref) => {
        if (ref && ref.kind === "resume" && ref.item_id && !found.has(ref.item_id)) {
          found.set(ref.item_id, String(ref.text || "").replace(/^(?:[#>*\-•–—]+\s*)+/, "").trim());
        }
      });
    };
    ((stored && stored.result && stored.result.sections) || []).forEach((section) => {
      (section.lines || []).forEach(take);
      (section.entries || []).forEach((entry) => (entry.bullets || []).forEach(take));
    });
    return found;
  }, [stored]);
  const rows = suggestionRows({ record, assessment, lineText: (id) => texts.get(id) || "" });
  if (!assessment || rows.length === 0) {
    return null;
  }
  const commands = briefCommands(jobUrl, state.profileId);
  const write = (row, use) => {
    setBusy(row.id);
    setError(null);
    postJobSuggestion({ jobUrl: jobUrl || state.jobIdentity, profileId: state.profileId, action: use, id: row.id, how: use === "resolve" ? DONE_HOW[row.kind] || "job_resume_edit" : undefined })
      .then(() => state.reloadRecord())
      .catch((err) => setError(err.detail || err.message || String(err)))
      .finally(() => setBusy(null));
  };
  const open = rows.filter((row) => row.open).length;
  return (
    <section className="panel" id="job-suggestions" data-testid="job-suggestions" data-expanded={expanded ? "true" : "false"}>
      <h3 className="suggestions-heading">
        <button type="button" className="suggestions-toggle" data-action="toggle-suggestions" aria-expanded={expanded} aria-controls="job-suggestions-list" onClick={() => setExpanded(!expanded)}>
          <span aria-hidden="true">{expanded ? "▾" : "▸"}</span> Suggestions ({open} open{rows.length > open ? `, ${rows.length - open} closed` : ""})
        </button>
      </h3>
      {expanded && <p className="muted small">What would make this resume fit the job better. Nothing here is written for you by a model: you and your agent change the words, in chat.</p>}
      {expanded && error && (
        <div className="callout danger" role="alert" data-role="suggestion-error">
          {error}
        </div>
      )}
      <ul className="story-list" id="job-suggestions-list" data-role="suggestions" hidden={!expanded}>
        {(expanded ? rows : []).map((row) => (
          <li key={row.id} className="master-line" data-suggestion-id={row.id} data-kind={row.kind} data-status={row.status}>
            <div className="master-line-body">
              <strong data-role="suggestion-kind">{row.kindLabel}</strong>
              {row.about && <span data-role="suggestion-about"> · {row.about}</span>}
              {row.phrase && (
                <div className="muted small" data-role="suggestion-phrase">
                  The posting says: “{row.phrase}”
                </div>
              )}
              <div data-role="suggestion-why">{row.why}</div>
              <div className="muted small">
                <span data-role="suggestion-who">From {row.who}</span> · <span data-role="suggestion-status">{row.statusLine}</span>
              </div>
              {briefFor === row.id && <Commands commands={commands} />}
            </div>
            {row.open && (
              <span className="master-line-actions">
                {row.stored && (
                  <>
                    <button className="link-button" data-action="suggestion-done" disabled={busy !== null} onClick={() => write(row, "resolve")}>
                      Done
                    </button>
                    <button className="link-button" data-action="suggestion-dismiss" disabled={busy !== null} onClick={() => write(row, "dismiss")}>
                      Dismiss
                    </button>
                  </>
                )}
                {commands.length > 0 && (
                  <button className="link-button" data-action="suggestion-agent" aria-expanded={briefFor === row.id} onClick={() => setBriefFor(briefFor === row.id ? null : row.id)}>
                    Work on this with your agent
                  </button>
                )}
              </span>
            )}
          </li>
        ))}
      </ul>
    </section>
  );
}
