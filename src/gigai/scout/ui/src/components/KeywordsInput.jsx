import { useState } from "react";
import { KEYWORDS_HELP, MAX_KEYWORDS, MAX_KEYWORD_LENGTH, addKeywords, removeKeyword } from "../keywordsModel.js";

// 0110-026 F2: the run dialog's "Keywords" (chips, like TagListInput): type
// one, press Enter or comma. The limits are the API's (keywordsModel), so a
// run is never refused for them.
export default function KeywordsInput({ id, values, onChange, disabled }) {
  const [draft, setDraft] = useState("");
  const [error, setError] = useState("");

  function commit(text) {
    const result = addKeywords(values, text);
    setError(result.error);
    setDraft("");
    if (result.values.length !== values.length) {
      onChange(result.values);
    }
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      commit(draft);
    } else if (event.key === "Backspace" && draft === "" && values.length > 0) {
      setError("");
      onChange(values.slice(0, -1));
    }
  }

  const full = values.length >= MAX_KEYWORDS;
  return (
    <div className="form-group" data-role="run-keywords-input">
      <label className="form-label" htmlFor={id}>
        Keywords
      </label>
      <div className={`tag-input${error ? " tag-input-error" : ""}`}>
        {values.map((value, index) => (
          <span className="tag tag-removable" key={value}>
            {value}
            <button
              type="button"
              className="tag-remove"
              aria-label={`Remove ${value}`}
              disabled={disabled}
              onClick={() => {
                setError("");
                onChange(removeKeyword(values, index));
              }}
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
          maxLength={MAX_KEYWORD_LENGTH * 4}
          disabled={disabled || full}
          placeholder={values.length === 0 ? "e.g. kubernetes, payments" : full ? `${MAX_KEYWORDS} of ${MAX_KEYWORDS}` : ""}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={() => draft.trim() && commit(draft)}
        />
      </div>
      {error && <div className="field-error">{error}</div>}
      <small className="muted">{KEYWORDS_HELP}</small>
    </div>
  );
}
