import { useEffect, useState } from "react";
import { getWatchlist } from "./AddCompanyForm.jsx";
import { companyBoardsLine, sourceLabel, watchlistSummary } from "../runText.js";

const MODEL_TARGETS = ["ollama_local", "codex_cli", "openrouter_api"];

// uat-batch1 (N12): the dialog names what a run reads in plain words, and
// says how many company boards are on the watchlist and how many of them
// came from the company catalog. The count is GET /api/watchlist's own
// entry list (the route the "Add company" form already reads), fetched when
// the dialog opens; nothing is added to the API for it.
function useWatchlistSummary(enabled) {
  const [state, setState] = useState({ summary: null, failed: false });
  useEffect(() => {
    if (!enabled) {
      return undefined;
    }
    let current = true;
    getWatchlist()
      .then((response) => current && setState({ summary: watchlistSummary(response.entries), failed: false }))
      .catch(() => current && setState({ summary: null, failed: true }));
    return () => {
      current = false;
    };
  }, [enabled]);
  return state;
}

export default function RunConfirmDialog({ config, onConfirm, onCancel, submitting, error }) {
  const [selectionCap, setSelectionCap] = useState(config.default_assess_cap);
  const [modelTarget, setModelTarget] = useState(config.default_model_target);

  const activeSources = Object.entries(config.sources)
    .filter(([, enabled]) => enabled)
    .map(([name]) => name);
  const atsEnabled = Boolean(config.sources.ats);
  const watchlist = useWatchlistSummary(atsEnabled);

  function handleCapChange(event) {
    const value = Number(event.target.value);
    setSelectionCap(Number.isNaN(value) ? config.default_assess_cap : value);
  }

  function handleConfirm() {
    onConfirm({ selectionCap, modelTarget });
  }

  const capInvalid = !Number.isInteger(selectionCap) || selectionCap < 1 || selectionCap > 50;
  const isHosted = modelTarget !== "ollama_local";

  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="run-confirm-title">
      <div className="modal">
        <h2 id="run-confirm-title">Confirm run workflow</h2>
        <p>This will do exactly the following:</p>
        <ul>
          <li>
            <strong>Sources:</strong> {activeSources.length ? activeSources.map(sourceLabel).join("; ") : "none"}
          </li>
          <li data-role="company-boards">
            <strong>Company boards:</strong> {companyBoardsLine({ atsEnabled, summary: watchlist.summary, failed: watchlist.failed })}
          </li>
          <li>
            <strong>Queries:</strong> {config.merged_queries.join(", ")}
          </li>
          <li>
            <strong>Role filter:</strong> {config.roles.join(", ")}
          </li>
        </ul>

        <div className="form-group">
          <label className="form-label" htmlFor="selection-cap">
            Assessment cap (1-50)
          </label>
          <input
            id="selection-cap"
            type="number"
            min={1}
            max={50}
            value={selectionCap}
            onChange={handleCapChange}
          />
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="model-target">
            Model target
          </label>
          <select id="model-target" value={modelTarget} onChange={(event) => setModelTarget(event.target.value)}>
            {MODEL_TARGETS.map((target) => (
              <option key={target} value={target}>
                {target}
              </option>
            ))}
          </select>
        </div>

        <div className="callout warn">
          Acquisition uses the network to query job boards.
          {isHosted
            ? " This model target sends posting text and your resume text to that hosted provider."
            : " The local model target keeps posting and resume text on this machine."}
        </div>

        {error && <div className="callout danger">{error}</div>}

        <div className="actions">
          <button className="button secondary" onClick={onCancel} disabled={submitting}>
            Cancel
          </button>
          <button className="button" onClick={handleConfirm} disabled={submitting || capInvalid}>
            {submitting ? "Starting…" : "Confirm and run"}
          </button>
        </div>
      </div>
    </div>
  );
}
