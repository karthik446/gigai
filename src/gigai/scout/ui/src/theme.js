// uat-batch1 (N2): light / dark theme. styles.css carries both token sets:
// the dark one applies under `prefers-color-scheme: dark` unless the root
// says data-theme="light", and always under data-theme="dark" (the
// mockup's rule, cards-and-job-page.html). The top bar's toggle sets the
// attribute and remembers the choice per browser.
//
// Pure functions (no React, no globals touched at import time): `storage`
// and `root` are passed in, and every storage call is wrapped, since
// localStorage can throw (blocked site data, a private window).
export const THEME_KEY = "scout.theme";

const isTheme = (value) => value === "light" || value === "dark";

// window.localStorage, or null when even reading the property throws.
export function browserStorage() {
  try {
    return typeof window !== "undefined" ? window.localStorage : null;
  } catch {
    return null;
  }
}

export function readStoredTheme(storage) {
  try {
    const value = storage ? storage.getItem(THEME_KEY) : null;
    return isTheme(value) ? value : null;
  } catch {
    return null;
  }
}

export function storeTheme(storage, theme) {
  try {
    if (storage && isTheme(theme)) {
      storage.setItem(THEME_KEY, theme);
      return true;
    }
  } catch {
    /* the choice still applies to this page; it just is not remembered */
  }
  return false;
}

// The theme on screen: the stored choice, else the system's.
export function effectiveTheme(stored, systemDark) {
  return isTheme(stored) ? stored : systemDark ? "dark" : "light";
}

export function nextTheme(current) {
  return current === "dark" ? "light" : "dark";
}

// The toggle names the theme a click switches TO.
export function toggleLabel(current) {
  return current === "dark" ? "Light mode" : "Dark mode";
}

export function applyTheme(root, theme) {
  if (!root) {
    return;
  }
  if (isTheme(theme)) {
    root.setAttribute("data-theme", theme);
  } else {
    root.removeAttribute("data-theme");
  }
}

// The app has its own dark theme, so a dark-mode extension must not
// repaint it: with Dark Reader on, "Light mode" rendered as the extension's
// inversion of the light tokens (seen in the uat-batch1 hand-check).
// <meta name="darkreader-lock"> is Dark Reader's documented opt-out for a
// page that themes itself; it changes nothing without the extension.
export function lockExtensionDarkMode(doc) {
  try {
    if (!doc || !doc.head || doc.querySelector('meta[name="darkreader-lock"]')) {
      return false;
    }
    const meta = doc.createElement("meta");
    meta.setAttribute("name", "darkreader-lock");
    doc.head.appendChild(meta);
    return true;
  } catch {
    return false;
  }
}
