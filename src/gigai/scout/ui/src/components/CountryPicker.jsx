import { useState } from "react";

// ISO-3166-1 alpha-2 country codes as tags. No name->code lookup table is
// shipped to the UI (present_api.py's routes don't expose one, and
// duplicating contracts.py/filters.py's pycountry-derived table into JS
// would drift from the server's own validation) -- the operator types a
// 2-letter code directly (e.g. "US", "DE"), which is exactly what
// FindJobsConfig.countries / DiscoveryPrefs.countries already store and
// what PUT /api/setup validates server-side (present_api.py's
// _setup_countries, same ISO-3166 alpha-2 regex).
const CODE_PATTERN = /^[A-Za-z]{2}$/;

export default function CountryPicker({ id, label, values, onChange, error }) {
  const [draft, setDraft] = useState("");
  const [localError, setLocalError] = useState(null);

  function commitDraft() {
    const value = draft.trim().toUpperCase();
    if (!value) {
      setDraft("");
      setLocalError(null);
      return;
    }
    if (!CODE_PATTERN.test(value)) {
      setLocalError(`"${draft.trim()}" is not a 2-letter country code (e.g. US, DE, IN).`);
      return;
    }
    if (!values.includes(value)) {
      onChange([...values, value]);
    }
    setDraft("");
    setLocalError(null);
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commitDraft();
    } else if (event.key === "Backspace" && draft === "" && values.length > 0) {
      onChange(values.slice(0, -1));
    }
  }

  function removeAt(index) {
    onChange(values.filter((_, i) => i !== index));
  }

  const shownError = error || localError;

  return (
    <div className="form-group">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <div className={`tag-input${shownError ? " tag-input-error" : ""}`}>
        {values.map((value, index) => (
          <span className="tag tag-removable" key={`${value}-${index}`}>
            {value}
            <button
              type="button"
              className="tag-remove"
              aria-label={`Remove ${value}`}
              onClick={() => removeAt(index)}
            >
              ×
            </button>
          </span>
        ))}
        <input
          id={id}
          type="text"
          className="tag-input-field"
          value={draft}
          placeholder={values.length === 0 ? "US, DE, IN…" : ""}
          maxLength={2}
          onChange={(event) => {
            setDraft(event.target.value);
            setLocalError(null);
          }}
          onKeyDown={handleKeyDown}
          onBlur={commitDraft}
        />
      </div>
      {shownError && <div className="field-error">{shownError}</div>}
    </div>
  );
}
