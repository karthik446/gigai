import { useState } from "react";

// A free-text list input rendered as tags: type a value, press Enter or
// comma to add it, click a tag's x to remove it. Used by the setup
// interview for every array-of-strings field (roles, titles_to_avoid,
// exclude_companies, watch_companies, industries_include/exclude,
// must_have_stack, dealbreaker_stack) -- CHANGE #2's "free-text list inputs
// as tags".
export default function TagListInput({ id, label, values, onChange, placeholder, error }) {
  const [draft, setDraft] = useState("");

  function commitDraft() {
    const value = draft.trim();
    if (!value) {
      setDraft("");
      return;
    }
    if (!values.includes(value)) {
      onChange([...values, value]);
    }
    setDraft("");
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

  return (
    <div className="form-group">
      <label className="form-label" htmlFor={id}>
        {label}
      </label>
      <div className={`tag-input${error ? " tag-input-error" : ""}`}>
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
          placeholder={values.length === 0 ? placeholder : ""}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={commitDraft}
        />
      </div>
      {error && <div className="field-error">{error}</div>}
    </div>
  );
}
