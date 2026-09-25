import TagListInput from "../components/TagListInput.jsx";
import CountryPicker from "../components/CountryPicker.jsx";
import { WORK_MODES } from "./wizardState.js";

// Screen 2 -- what to look for. Titles are pre-filled from screen 1's
// suggested titles on the first visit (SetupWizard copies them once); no
// question numbering (the mockup drops it).
export default function TargetScreen({ fields, setField, fieldErrors }) {
  const errors = fieldErrors || {};
  const showCity = fields.workMode !== "remote";
  return (
    <section className="panel">
      <h2>Target</h2>
      <p className="muted">
        {fields.extraction
          ? "Pre-filled from the resume's suggested titles (plus any title this profile already had). Edit freely."
          : "Type the job titles this profile should search for."}
      </p>

      <TagListInput
        id="wz-titles"
        label="Job titles to search for"
        values={fields.titles}
        onChange={(values) => setField("titles", values)}
        placeholder="staff backend engineer, senior platform engineer…"
        error={errors.roles || errors.titles || (fields.titles.length === 0 ? "Add at least one title." : undefined)}
      />

      <TagListInput
        id="wz-titles-avoid"
        label="Titles to avoid"
        values={fields.titlesToAvoid}
        onChange={(values) => setField("titlesToAvoid", values)}
        placeholder="optional…"
        error={errors.titles_to_avoid}
      />

      <CountryPicker
        id="wz-countries"
        label="Countries"
        values={fields.countries}
        onChange={(values) => setField("countries", values)}
        error={errors.countries || (fields.countries.length === 0 ? "Add at least one country code." : undefined)}
      />
      <small className="wz-hint" style={{ marginTop: "-10px", marginBottom: "14px" }}>
        2-letter country codes. US is the default; add more or remove it.
      </small>

      <div className="form-group">
        <span className="form-label">Remote, hybrid, onsite, or any?</span>
        <div className="toggle-row" role="radiogroup" aria-label="Work mode">
          {WORK_MODES.map((mode) => (
            <button
              key={mode.value}
              type="button"
              className={`toggle-button${fields.workMode === mode.value ? " active" : ""}`}
              aria-pressed={fields.workMode === mode.value}
              onClick={() => setField("workMode", mode.value)}
            >
              {mode.label}
            </button>
          ))}
        </div>
        {errors.work_mode && <div className="field-error">{errors.work_mode}</div>}
      </div>

      {showCity && (
        <div className="form-group">
          <label className="form-label" htmlFor="wz-city">
            City / area
          </label>
          <input
            id="wz-city"
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
        <span className="form-label">Do you need visa sponsorship?</span>
        <div className="toggle-row" role="radiogroup" aria-label="Visa sponsorship">
          <button
            type="button"
            className={`toggle-button${fields.visaSponsorshipRequired ? " active" : ""}`}
            aria-pressed={fields.visaSponsorshipRequired}
            onClick={() => setField("visaSponsorshipRequired", true)}
          >
            Yes (hard filter)
          </button>
          <button
            type="button"
            className={`toggle-button${!fields.visaSponsorshipRequired ? " active" : ""}`}
            aria-pressed={!fields.visaSponsorshipRequired}
            onClick={() => setField("visaSponsorshipRequired", false)}
          >
            No
          </button>
        </div>
        {errors.visa_sponsorship_required && <div className="field-error">{errors.visa_sponsorship_required}</div>}
      </div>
    </section>
  );
}
