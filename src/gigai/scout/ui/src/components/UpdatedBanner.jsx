import { useEffect, useRef, useState } from "react";

import { isStaleUi, UI_CHECK_EVERY_MS, UI_UPDATED_LINE } from "../uiVersionModel.js";

// The bundle this page runs: the module script its own index.html named.
function runningScript() {
  const script = document.querySelector('script[type="module"][src]');
  return script ? script.getAttribute("src") : null;
}

// 0.1.11.2 RANKVIS: "Scout was updated": a tab that stayed open over an upgrade runs the old UI against the new server
// (uiVersionModel.js). When the tab is shown again and when the page changes (`hashchange`), at most once per
// UI_CHECK_EVERY_MS, `/` is read again (it is served `no-cache`); when it names another bundle than the one running, a
// banner says so with "Reload". Nothing reloads by itself, and nothing is read on load (the page is fresh then).
export default function UpdatedBanner() {
  const [stale, setStale] = useState(false);
  const last = useRef(0);

  useEffect(() => {
    if (stale) {
      return undefined; // said once: nothing more to read
    }
    let stopped = false;
    const check = () => {
      const now = Date.now();
      if (document.visibilityState === "hidden" || now - last.current < UI_CHECK_EVERY_MS) {
        return;
      }
      last.current = now;
      fetch("/", { cache: "no-store", headers: { Accept: "text/html" } })
        .then((response) => (response.ok ? response.text() : null))
        .then((html) => {
          if (!stopped && isStaleUi(html, runningScript())) {
            setStale(true);
          }
        })
        .catch(() => {}); // a server that is down says nothing about the UI
    };
    window.addEventListener("hashchange", check);
    document.addEventListener("visibilitychange", check);
    window.addEventListener("focus", check);
    return () => {
      stopped = true;
      window.removeEventListener("hashchange", check);
      document.removeEventListener("visibilitychange", check);
      window.removeEventListener("focus", check);
    };
  }, [stale]);

  if (!stale) {
    return null;
  }
  return (
    <div className="callout warn ui-updated-banner" role="status" data-testid="ui-updated-banner">
      {UI_UPDATED_LINE}{" "}
      <button type="button" className="button small" data-action="reload-ui" onClick={() => window.location.reload()}>
        Reload
      </button>
    </div>
  );
}
