import { useEffect, useState } from "react";
import { getSourcesUpdate } from "../api.js";
import { relativeTimeLabel } from "../display.js";
import { indexedBoardsLine, sourceLabel } from "../runText.js";
import { SETTINGS_HASH } from "../routing.js";
import { MODEL_TARGETS, MODEL_TARGET_HINTS, modelTargetLabel } from "../modelTargets.js";
import { ASSESS_ALL, SELECTION_CAP_MAX, capValid, defaultSelectionCap, fullAssessmentsHelp, isAssessAll } from "../assessAllModel.js";

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

// uat-bug-042: "Full assessments" is "All new postings" (every new posting
// the run finds is assessed in full, ASSESS_CONCURRENCY at a time) or a number
// (the top-ranked ones, as before). "All new postings" is the starting choice
// for a local CLI target; a target billed per token (OpenRouter) starts on the
// saved number. Until the operator picks, the choice follows the model target.
export default function RunConfirmDialog({ config, onConfirm, onCancel, submitting, error }) {
  const [modelTarget, setModelTarget] = useState(config.default_model_target);
  const [selectionCap, setSelectionCap] = useState(() => defaultSelectionCap(config.default_assess_cap, config.default_model_target));
  const [capChosen, setCapChosen] = useState(false);
  const savedNumber = Number.isInteger(config.default_assess_cap) ? config.default_assess_cap : 10;

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
    setCapChosen(true);
    setSelectionCap(Number.isNaN(value) ? savedNumber : value);
  }

  function handleCapModeChange(event) {
    setCapChosen(true);
    setSelectionCap(event.target.value === ASSESS_ALL ? ASSESS_ALL : savedNumber);
  }

  function handleModelTargetChange(event) {
    const next = event.target.value;
    setModelTarget(next);
    if (!capChosen) {
      setSelectionCap(defaultSelectionCap(config.default_assess_cap, next));
    }
  }

  function handleConfirm() {
    onConfirm({ selectionCap, modelTarget });
  }

  const capInvalid = !capValid(selectionCap);
  const assessAll = isAssessAll(selectionCap);
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

        <div className="form-group" data-role="full-assessments">
          <label className="form-label" htmlFor="selection-cap-mode">
            Full assessments
          </label>
          <select id="selection-cap-mode" value={assessAll ? ASSESS_ALL : "number"} onChange={handleCapModeChange}>
            <option value={ASSESS_ALL}>All new postings</option>
            <option value="number">Top-ranked only (1-{SELECTION_CAP_MAX})</option>
          </select>
          {!assessAll && (
            <input
              id="selection-cap"
              type="number"
              aria-label="How many top-ranked postings to assess in full"
              min={1}
              max={50}
              value={selectionCap}
              onChange={handleCapChange}
            />
          )}
          <small className="muted" data-role="full-assessments-help">
            {fullAssessmentsHelp(selectionCap, modelTarget)}
          </small>
        </div>

        <div className="form-group">
          <label className="form-label" htmlFor="model-target">
            Model target
          </label>
          <select id="model-target" value={modelTarget} onChange={handleModelTargetChange}>
            {MODEL_TARGETS.map((target) => (
              <option key={target} value={target}>
                {modelTargetLabel(target)}
              </option>
            ))}
          </select>
          {MODEL_TARGET_HINTS[modelTarget] && <small className="muted">{MODEL_TARGET_HINTS[modelTarget]}</small>}
        </div>

        <div className="callout warn">
          Acquisition uses the network to query job boards.
          {isHosted
            ? " This model target sends posting text and your resume text to that hosted provider."
            : " The local model target keeps posting and resume text on this machine."}
          {/* SCOPE-ADD-3: the same model target ranks every posting that
              passes your filters before the run assesses its top ones. */}
          <div data-role="rank-run-note">
            This model also ranks every posting that passes your filters, likely fits first, so that can take a few
            minutes on a big search.
          </div>
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
