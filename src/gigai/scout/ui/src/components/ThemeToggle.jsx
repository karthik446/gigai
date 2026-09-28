import { useEffect, useState } from "react";
import { applyTheme, browserStorage, effectiveTheme, nextTheme, readStoredTheme, storeTheme, toggleLabel } from "../theme.js";

const SYSTEM_DARK = "(prefers-color-scheme: dark)";

function systemPrefersDark() {
  return typeof window.matchMedia === "function" && window.matchMedia(SYSTEM_DARK).matches;
}

// uat-batch1 (N2): the top bar's light/dark toggle (the mockup's). Until it
// is clicked the app follows the system (styles.css's
// prefers-color-scheme rule); a click sets data-theme on <html> and
// remembers the choice in this browser (theme.js; main.jsx applies the
// stored choice before the first paint).
export default function ThemeToggle() {
  const [stored, setStored] = useState(() => readStoredTheme(browserStorage()));
  const [systemDark, setSystemDark] = useState(systemPrefersDark);

  useEffect(() => {
    if (typeof window.matchMedia !== "function") {
      return undefined;
    }
    const query = window.matchMedia(SYSTEM_DARK);
    const onChange = (event) => setSystemDark(event.matches);
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);

  const current = effectiveTheme(stored, systemDark);

  return (
    <button
      type="button"
      className="theme-toggle"
      data-theme-current={current}
      aria-label={`Switch to ${toggleLabel(current).toLowerCase()}`}
      onClick={() => {
        const next = nextTheme(current);
        applyTheme(document.documentElement, next);
        storeTheme(browserStorage(), next);
        setStored(next);
      }}
    >
      {toggleLabel(current)}
    </button>
  );
}
