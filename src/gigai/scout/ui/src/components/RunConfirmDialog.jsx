import { useEffect, useState } from "react";
import { getSourcesUpdate } from "../api.js";
import { relativeTimeLabel } from "../display.js";
import { indexedBoardsLine, sourceLabel } from "../runText.js";
import { SETTINGS_HASH } from "../routing.js";

const MODEL_TARGETS = ["ollama_local", "codex_cli", "openrouter_api"];

// uat-batch1 (N12): the dialog names what a run reads in plain words.
// uat-batch2: the "Company boards" line is how many company boards are
// stored (indexed) on this machine and when they were last updated, from
// GET /api/sources/update's `index` block, read when the dialog opens. It
// replaces batch 1's count of GET /api/watchlist's entries (4.5 MB / 1.8 s
// at catalog size). With nothing stored, or stored postings out of date,
// the line says to update sources first and links to Settings.
function useStoredIndex(enabled) {
  const [state, setState] = useState({ index: null, failed: false });
  useEffect(() => {
    if (!enabled) {
      return undefined;
    }
    let current = true;
    getSourcesUpdate()
      .then((response) => current && setState({ index: (response && response.index) || null, failed: !(response && response.index) }))
      .catch(() => current && setState({ index: null, failed: true }));
    return () => {
      current = false;
    };
  }, [enabled]);
  return state;
}

export default function RunConfirmDialog({ config, onConfirm, onCancel, submitting, error, jevLine }) {
  const [selectionCap, setSelectionCap] = useState(config.default_assess_cap);
  const [modelTarget, setModelTarget] = useState(config.default_model_target);

  const activeSources = Object.entries(config.sources)
    .filter(([, enabled]) => enabled)
    .map(([name]) => name);
  const atsEnabled = Boolean(config.sources.ats);
  const stored = useStoredIndex(atsEnabled);
  const boards = indexedBoardsLine({
    atsEnabled,
    index: stored.index,
    failed: stored.failed,
    lastUpdated: stored.index && stored.index.last_checked_at ? relativeTimeLabel(stored.index.last_checked_at) : "",
  });

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
            <strong>Company boards:</strong> {boards.line}{" "}
            {boards.needsUpdate && (
              <a href={SETTINGS_HASH} onClick={onCancel}>
                Open Settings
              </a>
            )}
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
          {/* ui-pass (uat-bug-021): with a Jev key and ranking on, the run
              also sends the resume's start to Jev (jevModel.jevRunConsentLine). */}
          {jevLine && <div data-role="jev-run-consent">{jevLine}</div>}
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
