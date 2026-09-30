import { useState } from "react";
import TagListInput from "./TagListInput.jsx";
import CountryPicker from "./CountryPicker.jsx";
import { numberQuestions, questionLabel } from "../questionNumbering.js";

const WORK_MODES = [
  { value: "remote", label: "Remote-only" },
  { value: "hybrid", label: "Hybrid" },
  { value: "onsite", label: "Onsite" },
  { value: "any", label: "Any" },
];

// The 11 S23 setup-interview questions (S23-scout-setup-interview-exa-agent.md
// §2), asked once. `prefs` is either the previously saved DiscoveryPrefs
// JSON (editing) or the /api/setup 404 response's `prefill` (first run,
// derived from the current find-jobs.json). `fieldErrors` comes straight
// from a PUT /api/setup 400 response's `field_errors`.
export default function SetupInterviewForm({ initialPrefs, onSave, onCancel, saving, error, fieldErrors, isFirstRun }) {
  const [fields, setFields] = useState(() => ({
    roles: initialPrefs.roles || [],
    titles_to_avoid: initialPrefs.titles_to_avoid || [],
    countries: initialPrefs.countries || [],
    work_mode: initialPrefs.work_mode || "any",
    city: initialPrefs.city || "",
    visa_sponsorship_required: Boolean(initialPrefs.visa_sponsorship_required),
    exclude_companies: initialPrefs.exclude_companies || [],
    watch_companies: initialPrefs.watch_companies || [],
    company_stage_size: initialPrefs.company_stage_size || "",
    industries_include: initialPrefs.industries_include || [],
    industries_exclude: initialPrefs.industries_exclude || [],
    must_have_stack: initialPrefs.must_have_stack || [],
    dealbreaker_stack: initialPrefs.dealbreaker_stack || [],
    cadence_days: initialPrefs.cadence_days ?? 7,
    budget_usd_per_session: initialPrefs.budget_usd_per_session ?? 0.5,
  }));

  function setField(name, value) {
    setFields((prev) => ({ ...prev, [name]: value }));
  }

  function handleSubmit(event) {
    event.preventDefault();
    onSave({
      ...fields,
      city: fields.work_mode === "remote" ? null : fields.city.trim() || null,
      company_stage_size: fields.company_stage_size.trim() || null,
    });
  }

  const showCity = fields.work_mode !== "remote";
  const errors = fieldErrors || {};
  const questionNumbers = numberQuestions(fields);

  return (
    <section className="panel">
      <h2>{isFirstRun ? "Set up Scout" : "Edit preferences"}</h2>
      <p className="muted">
        Answered once; edit anytime from the Preferences link. Drives both job matching and weekly company discovery.
      </p>

      <form onSubmit={handleSubmit}>
        <TagListInput
          id="setup-roles"
          label={questionLabel("roles", questionNumbers)}
          values={fields.roles}
          onChange={(values) => setField("roles", values)}
          placeholder="staff backend, senior backend…"
          error={errors.roles}
        />

        <TagListInput
          id="setup-titles-to-avoid"
          label={questionLabel("titles_to_avoid", questionNumbers)}
          values={fields.titles_to_avoid}
          onChange={(values) => setField("titles_to_avoid", values)}
          placeholder="optional…"
          error={errors.titles_to_avoid}
        />

        <CountryPicker
          id="setup-countries"
          label={questionLabel("countries", questionNumbers)}
          values={fields.countries}
          onChange={(values) => setField("countries", values)}
          error={errors.countries}
        />

        <div className="form-group">
          <label className="form-label" htmlFor="setup-work-mode">
            {questionLabel("work_mode", questionNumbers)}
          </label>
          <select
            id="setup-work-mode"
            className="text-input"
            value={fields.work_mode}
            onChange={(event) => setField("work_mode", event.target.value)}
          >
            {WORK_MODES.map((mode) => (
              <option key={mode.value} value={mode.value}>
                {mode.label}
              </option>
            ))}
          </select>
          {errors.work_mode && <div className="field-error">{errors.work_mode}</div>}
        </div>

        {showCity && (
          <div className="form-group">
            <label className="form-label" htmlFor="setup-city">
              {questionLabel("city", questionNumbers)}
            </label>
            <input
              id="setup-city"
              type="text"
              className="text-input"
              value={fields.city}
              placeholder="Denver, CO"
              onChange={(event) => setField("city", event.target.value)}
            />
            {errors.city && <div className="field-error">{errors.city}</div>}
          </div>
        )}

        <div className="form-group">
          <label className="form-label" htmlFor="setup-visa">
            {questionLabel("visa_sponsorship_required", questionNumbers)}
          </label>
          <div className="toggle-row">
            <button
              type="button"
              id="setup-visa"
              className={`toggle-button${fields.visa_sponsorship_required ? " active" : ""}`}
              aria-pressed={fields.visa_sponsorship_required}
              onClick={() => setField("visa_sponsorship_required", true)}
            >
              Yes (hard filter)
            </button>
            <button
              type="button"
              className={`toggle-button${!fields.visa_sponsorship_required ? " active" : ""}`}
              aria-pressed={!fields.visa_sponsorship_required}
              onClick={() => setField("visa_sponsorship_required", false)}
            >
              No
            </button>
          </div>
          {errors.visa_sponsorship_required && <div className="field-error">{errors.visa_sponsorship_required}</div>}
        </div>

        <TagListInput
          id="setup-exclude-companies"
          label={questionLabel("exclude_companies", questionNumbers)}
          values={fields.exclude_companies}
          onChange={(values) => setField("exclude_companies", values)}
          placeholder="not interested / current employer…"
          error={errors.exclude_companies}
        />

        <TagListInput
          id="setup-watch-companies"
          label={questionLabel("watch_companies", questionNumbers)}
          values={fields.watch_companies}
          onChange={(values) => setField("watch_companies", values)}
          placeholder="optional…"
          error={errors.watch_companies}
        />

        <div className="form-group">
          <label className="form-label" htmlFor="setup-stage-size">
            {questionLabel("company_stage_size", questionNumbers)}
          </label>
          <input
            id="setup-stage-size"
            type="text"
            className="text-input"
            value={fields.company_stage_size}
            placeholder="e.g. Series B-D, 200-1000 employees…"
            onChange={(event) => setField("company_stage_size", event.target.value)}
          />
          {errors.company_stage_size && <div className="field-error">{errors.company_stage_size}</div>}
        </div>

        <TagListInput
          id="setup-industries-include"
          label={questionLabel("industries_include", questionNumbers)}
          values={fields.industries_include}
          onChange={(values) => setField("industries_include", values)}
          placeholder="optional…"
          error={errors.industries_include}
        />

        <TagListInput
          id="setup-industries-exclude"
          label={questionLabel("industries_exclude", questionNumbers)}
          values={fields.industries_exclude}
          onChange={(values) => setField("industries_exclude", values)}
          placeholder="optional…"
          error={errors.industries_exclude}
        />

        <TagListInput
          id="setup-must-have-stack"
          label={questionLabel("must_have_stack", questionNumbers)}
          values={fields.must_have_stack}
          onChange={(values) => setField("must_have_stack", values)}
          placeholder="optional…"
          error={errors.must_have_stack}
        />

        <TagListInput
          id="setup-dealbreaker-stack"
          label={questionLabel("dealbreaker_stack", questionNumbers)}
          values={fields.dealbreaker_stack}
          onChange={(values) => setField("dealbreaker_stack", values)}
          placeholder="optional…"
          error={errors.dealbreaker_stack}
        />

        <div className="field-row">
          <div className="form-group">
            <label className="form-label" htmlFor="setup-cadence">
              {questionLabel("cadence_days", questionNumbers)}
            </label>
            <input
              id="setup-cadence"
              type="number"
              className="text-input"
              min={1}
              value={fields.cadence_days}
              onChange={(event) => setField("cadence_days", Number(event.target.value) || 1)}
            />
            {errors.cadence_days && <div className="field-error">{errors.cadence_days}</div>}
          </div>
          <div className="form-group">
            <label className="form-label" htmlFor="setup-budget">
              {questionLabel("budget_usd_per_session", questionNumbers)}
            </label>
            <input
              id="setup-budget"
              type="number"
              className="text-input"
              min={0.01}
              step={0.01}
              value={fields.budget_usd_per_session}
              onChange={(event) => setField("budget_usd_per_session", Number(event.target.value) || 0.01)}
            />
            {errors.budget_usd_per_session && <div className="field-error">{errors.budget_usd_per_session}</div>}
          </div>
        </div>

        {errors._ && <div className="callout danger">{errors._}</div>}
        {error && <div className="callout danger">{error}</div>}

        <div className="actions">
          {onCancel && (
            <button type="button" className="button secondary" onClick={onCancel} disabled={saving}>
              Cancel
            </button>
          )}
          <button type="submit" className="button" disabled={saving || fields.roles.length === 0}>
            {saving ? "Saving…" : "Save preferences"}
          </button>
        </div>
      </form>
    </section>
  );
}
