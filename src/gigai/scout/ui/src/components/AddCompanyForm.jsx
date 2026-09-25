import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api.js";

// Q1 (v0.1.9, SCOPE-ADD-2): "Add company" by board URL. Paste a Greenhouse /
// Lever / Ashby board (or job) URL and the board joins the watchlist, so
// every later find-jobs run polls it directly -- the UAT Kong case, where a
// live posting was never found because Kong was on no list at all.
//
// This component carries its own fetch helper on purpose (api.js belongs
// to another packet right now): same shape as api.js/wizardApi.js -- JSON
// in/out, an ApiError with status/code on any non-2xx -- and only borrows
// api.js's ApiError class so `instanceof ApiError` holds across modules.
//
// Routes (find_jobs/api/watchlist.py):
//   GET  /api/watchlist        -> {entries: [WatchlistEntry, ...]} newest first
//   POST /api/watchlist {url}  -> 201 {created: true, entry} on a first add,
//                                 200 {created: false, entry} for a board
//                                 already watched (idempotent), 422 for any
//                                 other host / an unusable URL.

const FRIENDLY = {
  unsupported_board_host: "Only Greenhouse (boards.greenhouse.io), Lever (jobs.lever.co) and Ashby (jobs.ashbyhq.com) URLs can be added.",
  invalid_value: "That is not a usable http(s) URL.",
  forbidden_origin: "This action was refused by the local server.",
  not_found: "No Scout target is configured yet. Run `gigai scout install` first.",
};

async function request(method, path, body) {
  let response;
  try {
    response = await fetch(path, {
      method,
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "Could not reach the local API. Is the server running on 127.0.0.1:8765?");
  }
  let payload = null;
  const text = await response.text();
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }
  if (!response.ok) {
    const errorBody = payload && typeof payload.error === "object" ? payload.error : null;
    const code = errorBody && typeof errorBody.code === "string" ? errorBody.code : undefined;
    const detail = errorBody && typeof errorBody.message === "string" ? errorBody.message : undefined;
    throw new ApiError(response.status, FRIENDLY[code] || detail || `Request failed with status ${response.status}.`, code);
  }
  return payload;
}

export function getWatchlist() {
  return request("GET", "/api/watchlist");
}

export function addCompany(url) {
  return request("POST", "/api/watchlist", { url });
}

function boardUrl(entry) {
  return entry.first_seen && entry.first_seen.source_url ? entry.first_seen.source_url : null;
}

// `compact` hides the "already watching" list (the wizard's Companies
// screen keeps its own two lists above this form); the dashboard shows it.
export default function AddCompanyForm({ compact = false, onAdded }) {
  const [url, setUrl] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [entries, setEntries] = useState(null);

  const reload = useCallback(() => {
    if (compact) {
      return;
    }
    getWatchlist()
      .then((response) => setEntries(response.entries || []))
      .catch(() => setEntries([]));
  }, [compact]);

  useEffect(reload, [reload]);

  async function submit(event) {
    event.preventDefault();
    const candidate = url.trim();
    if (!candidate) {
      setError("Paste a board or job URL first.");
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await addCompany(candidate);
      const entry = result.entry;
      setNotice(
        result.created
          ? `Added ${entry.company} (${entry.provider} board "${entry.board_token}"). The next run polls it.`
          : `${entry.company} (${entry.provider} board "${entry.board_token}") is already on the watchlist.`,
      );
      setUrl("");
      reload();
      if (onAdded) {
        onAdded(entry, result.created);
      }
    } catch (caught) {
      setError(caught.message || String(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="panel add-company-form">
      <h3>Add company</h3>
      <p className="muted">
        Paste a Greenhouse, Lever or Ashby board or job URL. Scout polls that board on every run, even when no
        search engine surfaces its postings.
      </p>
      <form onSubmit={submit}>
        <div className="form-group">
          <label className="form-label" htmlFor="add-company-url">
            Board or job URL
          </label>
          <input
            id="add-company-url"
            type="url"
            className="text-input"
            placeholder="https://jobs.ashbyhq.com/kong"
            value={url}
            disabled={busy}
            onChange={(event) => setUrl(event.target.value)}
          />
          {error && <div className="field-error">{error}</div>}
        </div>
        <button type="submit" className="button small" disabled={busy}>
          {busy ? "Adding…" : "Add company"}
        </button>
      </form>
      {notice && <div className="callout ok" style={{ marginTop: 12 }}>{notice}</div>}
      {!compact && entries !== null && (
        <div style={{ marginTop: 12 }}>
          <div className="form-label">Watching {entries.length} board{entries.length === 1 ? "" : "s"}</div>
          {entries.length === 0 && <p className="muted">Nothing yet. The first find-jobs run adds the boards Exa discovers.</p>}
          {entries.length > 0 && (
            <ul className="add-company-list">
              {entries.map((entry) => (
                <li key={entry.watchlist_id}>
                  {boardUrl(entry) ? (
                    <a href={boardUrl(entry)} target="_blank" rel="noreferrer">
                      {entry.company}
                    </a>
                  ) : (
                    entry.company
                  )}{" "}
                  <span className="muted">{entry.provider}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </section>
  );
}
