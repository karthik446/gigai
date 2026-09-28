import { useCallback, useEffect, useState } from "react";
import { getJevSettings, putJevSettings } from "../api.js";
import { jevDisclosureLines, jevRunCapInForce } from "../jevModel.js";
import { rankUsageLine } from "../runText.js";

// ui-pass (uat-bug-021 decision a); jev-disclosure-fixes (TARGET 4) added
// the per-run cap. Settings' Jev panel. What a search sends to Jev and what
// it may cost (jevModel.jevDisclosureLines), "Rank with Jev: on/off", the
// daily budget, the per-run cap, and today's usage. All three settings are
// the home's (GET/PUT /api/jev/settings, find_jobs/api/jev_settings.py);
// each control saves only its own setting.
export default function JevSettingsPanel() {
  const [settings, setSettings] = useState(null);
  const [error, setError] = useState(null);
  const [budget, setBudget] = useState("");
  const [runCap, setRunCap] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(null);

  const apply = useCallback((response) => {
    setSettings(response);
    setBudget(String(response.jev_daily_budget_usd));
    setRunCap(String(response.jev_run_cap_usd));
    setError(null);
  }, []);

  useEffect(() => {
    getJevSettings()
      .then(apply)
      .catch((failure) => setError(failure.message || String(failure)));
  }, [apply]);

  function save(fields, message) {
    setSaving(true);
    setSaved(null);
    putJevSettings(fields)
      .then((response) => {
        apply(response);
        setSaved(message);
      })
      .catch((failure) => setError(failure.message || String(failure)))
      .finally(() => setSaving(false));
  }

  const budgetValue = Number(budget);
  const budgetValid = budget.trim() !== "" && Number.isFinite(budgetValue) && budgetValue >= 0;
  const budgetChanged = Boolean(settings) && budgetValid && budgetValue !== settings.jev_daily_budget_usd;
  const runCapValue = Number(runCap);
  const runCapValid = runCap.trim() !== "" && Number.isFinite(runCapValue) && runCapValue >= 0;
  const runCapChanged = Boolean(settings) && runCapValid && runCapValue !== settings.jev_run_cap_usd;
  const usageLine = settings ? rankUsageLine(settings.usage) : null;
  const inForce = settings && settings.usage ? settings.usage.daily_budget_usd : null;
  const runCapInForce = settings ? jevRunCapInForce(settings) : null;

  return (
    <section className="panel" id="settings-jev" data-role="jev-settings">
      <h2>Jev ranking</h2>
      <div className="privacy-note jev-disclosure" data-role="jev-disclosure">
        {jevDisclosureLines(inForce, runCapInForce).map((line) => (
          <p key={line}>{line}</p>
        ))}
      </div>
      {error && <div className="callout danger">Jev settings: {error}</div>}
      {!settings && !error && <p className="muted">Loading Jev settings…</p>}
      {settings && (
        <div className="jev-settings">
          <label className="filter-toggle jev-toggle">
            <input
              type="checkbox"
              checked={Boolean(settings.jev_rank_enabled)}
              disabled={saving}
              data-action="jev-rank-enabled"
              onChange={(event) => save({ jev_rank_enabled: event.target.checked }, event.target.checked ? "Rank with Jev is on." : "Rank with Jev is off.")}
            />{" "}
            Rank with Jev: <strong data-role="jev-rank-state">{settings.jev_rank_enabled ? "on" : "off"}</strong>
          </label>
          <div className="jev-budget-row">
            <label htmlFor="jev-daily-budget">Daily budget (USD)</label>
            <input
              id="jev-daily-budget"
              type="number"
              min="0"
              step="0.05"
              value={budget}
              disabled={saving}
              onChange={(event) => setBudget(event.target.value)}
            />
            <button
              type="button"
              className="button small secondary"
              data-action="jev-save-budget"
              disabled={!budgetChanged || saving}
              onClick={() => save({ jev_daily_budget_usd: budgetValue }, "Daily budget saved.")}
            >
              Save budget
            </button>
            <span className="muted small">0 stops every Jev call.</span>
          </div>
          {!budgetValid && <p className="muted small">The budget is a number of dollars, 0 or more.</p>}
          {settings.daily_budget_env && (
            <p className="muted small" data-role="jev-budget-env">
              GIGAI_JEV_DAILY_BUDGET_USD is set to ${settings.daily_budget_env} for this server, and it wins over this setting.
            </p>
          )}
          <div className="jev-budget-row">
            <label htmlFor="jev-run-cap">Per-run cap (USD)</label>
            <input
              id="jev-run-cap"
              type="number"
              min="0"
              step="0.05"
              value={runCap}
              disabled={saving}
              onChange={(event) => setRunCap(event.target.value)}
            />
            <button
              type="button"
              className="button small secondary"
              data-action="jev-save-run-cap"
              disabled={!runCapChanged || saving}
              onClick={() => save({ jev_run_cap_usd: runCapValue }, "Per-run cap saved.")}
            >
              Save cap
            </button>
            <span className="muted small">0 stops every ranking pass.</span>
          </div>
          {!runCapValid && <p className="muted small">The per-run cap is a number of dollars, 0 or more.</p>}
          {settings.run_cap_env && (
            <p className="muted small" data-role="jev-run-cap-env">
              GIGAI_JEV_COST_CAP_USD is set to ${settings.run_cap_env} for this server, and it wins over this setting.
            </p>
          )}
          {usageLine && (
            <p className="muted" data-role="jev-usage">
              {usageLine}
            </p>
          )}
          {!settings.has_key && (
            <p className="muted small" data-role="jev-no-key">
              No Jev key is set, so nothing is sent to Jev. Add one with <code>gigai secrets add jev</code>.
            </p>
          )}
          {saved && <p className="muted small">{saved}</p>}
        </div>
      )}
    </section>
  );
}
