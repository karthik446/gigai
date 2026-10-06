// 0.1.11.2 RANKVIS: IS THIS TAB RUNNING THE UI THE SERVER SERVES NOW? Plain functions (tests/api_e2e/test_ui_version_model.py).
//
// The cause on record: after an upgrade and a server restart a tab that stayed open (or was sent to `#/jobs`, which is
// a navigation inside the same document) kept running the OLD bundle against the new server. The local release check
// read a Jobs page without the rank row and with the old "N weak fits, ranked low: show" line that way: both were the
// previous build's. `index.html` is served `no-cache` and names the hashed bundle, but it is only read on a real load.
//
// So the page reads `/` again now and then (UpdatedBanner.jsx) and compares the bundle it names with the one it runs.
// A different one: a banner says so with "Reload". The page never reloads by itself, and a read that fails or names no
// bundle says nothing (never a false alarm).

export const UI_UPDATED_LINE = "Scout was updated. This page still runs the old version, so it may miss what is new.";
export const UI_CHECK_EVERY_MS = 10000; // at most one read of `/` per this long

// The paths of the module scripts an index.html names: ["/assets/index-abc.js"].
export function bundleScripts(html) {
  if (typeof html !== "string") {
    return [];
  }
  const found = [];
  const tags = html.match(/<script\b[^>]*>/gi) || [];
  for (const tag of tags) {
    const src = /\bsrc\s*=\s*["']([^"']+)["']/i.exec(tag);
    if (src && /\btype\s*=\s*["']module["']/i.test(tag)) {
      found.push(scriptPath(src[1]));
    }
  }
  return found.filter(Boolean);
}

// "/assets/index-abc.js" of a URL or a path (no origin, no query); null when there is none.
export function scriptPath(src) {
  if (typeof src !== "string" || !src.trim()) {
    return null;
  }
  const path = src.trim().replace(/^[a-z][a-z0-9+.-]*:\/\/[^/]+/i, "").split(/[?#]/)[0];
  return path || null;
}

// True only when the served index.html names a bundle and it is not the one this page runs.
export function isStaleUi(html, running) {
  const served = bundleScripts(html);
  const mine = scriptPath(running);
  if (!served.length || !mine) {
    return false;
  }
  return !served.includes(mine);
}
