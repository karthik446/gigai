// 0.1.10.8: the one-time network notice shown before the very first Update
// sources, as pure functions (no React) so the node-backed test can pin
// them. The sentences are wording.js's (copies of
// src/gigai/scout/wording.py NETWORK_NOTICE_LEAD / NETWORK_NOTICE_BODY).
//
// "First" is the server's fact, not the browser's: the store holds no
// company yet (GET /api/sources/update -> index). "Seen" is remembered per
// browser; `storage` is passed in and every call is wrapped, since
// localStorage can throw (blocked site data, a private window). With no
// usable storage the notice shows again until the store holds postings.
import { browserStorage } from "./theme.js";

export const NETWORK_NOTICE_KEY = "scout.networkNotice.seen";
export const NETWORK_NOTICE_SETTINGS_LINE = "Background checks can be turned off in Settings > Background updates.";

// True when no Update sources has stored anything on this machine yet.
export function isFirstUpdate(status) {
  const index = status && status.index;
  if (!index) {
    return false;
  }
  const stored = typeof index.companies_indexed === "number" && index.companies_indexed > 0;
  return index.status === "empty" || !stored;
}

export function noticeSeen(storage) {
  try {
    return Boolean(storage) && storage.getItem(NETWORK_NOTICE_KEY) === "1";
  } catch {
    return false;
  }
}

export function markNoticeSeen(storage) {
  try {
    if (storage) {
      storage.setItem(NETWORK_NOTICE_KEY, "1");
      return true;
    }
  } catch {
    /* the update still starts; the notice is just not remembered */
  }
  return false;
}

// Show the notice before this Update sources starts?
export function needsNetworkNotice(status, storage) {
  return isFirstUpdate(status) && !noticeSeen(storage);
}

export { browserStorage };
