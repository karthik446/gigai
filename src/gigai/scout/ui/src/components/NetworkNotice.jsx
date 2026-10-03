import { useCallback, useState } from "react";
import { NETWORK_NOTICE_SETTINGS_LINE, browserStorage, markNoticeSeen, needsNetworkNotice } from "../networkNoticeModel.js";
import { SETTINGS_HASH } from "../routing.js";
import { NETWORK_NOTICE_BODY, NETWORK_NOTICE_LEAD } from "../wording.js";

// 0.1.10.8: asked once, before the very first Update sources. Nothing has
// been requested when this shows; Continue starts the update the click
// asked for and remembers that the notice was read.
export function NetworkNoticeDialog({ onContinue, onCancel }) {
  return (
    <div className="modal-backdrop" role="dialog" aria-modal="true" aria-labelledby="network-notice-title" data-testid="network-notice">
      <div className="modal">
        <h2 id="network-notice-title">Before the first Update sources</h2>
        <p data-role="network-notice-text">
          <strong>{NETWORK_NOTICE_LEAD}</strong> {NETWORK_NOTICE_BODY}
        </p>
        <p className="muted" data-role="network-notice-settings">
          {NETWORK_NOTICE_SETTINGS_LINE} <a href={`${SETTINGS_HASH}`}>Open Settings</a>
        </p>
        <div className="actions">
          <button type="button" className="button secondary" onClick={onCancel} data-action="network-notice-cancel">
            Not now
          </button>
          <button type="button" className="button" onClick={onContinue} data-action="network-notice-continue">
            Update sources
          </button>
        </div>
      </div>
    </div>
  );
}

// `status` is GET /api/sources/update. `guard(run)` runs `run` at once,
// or after Continue when this is the first update and the notice was not
// read in this browser; `dialog` is the element to render (or null).
export function useNetworkNotice(status) {
  const [pending, setPending] = useState(null);
  const guard = useCallback(
    (run) => {
      if (needsNetworkNotice(status, browserStorage())) {
        setPending(() => run);
      } else {
        run();
      }
    },
    [status],
  );
  const dialog = pending ? (
    <NetworkNoticeDialog
      onCancel={() => setPending(null)}
      onContinue={() => {
        markNoticeSeen(browserStorage());
        const run = pending;
        setPending(null);
        run();
      }}
    />
  ) : null;
  return { guard, dialog };
}
