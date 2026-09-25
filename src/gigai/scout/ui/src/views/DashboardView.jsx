import { useCallback, useEffect, useRef, useState } from "react";
import ProfileSwitcher from "../components/ProfileSwitcher.jsx";
import DiscoverPanel from "../components/DiscoverPanel.jsx";
import { getDiscoverLatest, startDiscovery } from "../api.js";

const DISCOVER_POLL_INTERVAL_MS = 4000;

// P9/F3: Dashboard.
//
// DROPPED from the mockup (no backing API in this checkout -- see the PR
// body / worker report for the full list):
//  - "Last run: found/assessed/strong matches" and "new since last visit":
//    no route lists a profile's past runs (or any run at all) without
//    already holding a run_id; GET /api/runs/{run_id} needs one you already
//    have. Nothing here invents a run_id.
//  - Application pipeline + "needs action": application_events.py exists
//    in the codebase but no scout find-jobs API route reads it (confirmed:
//    server.py's whole route table has no /api/events, and no Backend
//    method resolves one). Per the task spec ("if not exposed by an API,
//    drop"), this section is dropped entirely, not stubbed.
//  - Watchlist health: S26 (company catalog), not built yet -- the mockup
//    itself marks this "proposed: S26".
//
// KEPT, on real data:
//  - Per-profile summary row (GET /api/profiles: label, titles, resume ref).
//  - Discovery status (GET /api/discover/latest) -- note this is NOT
//    profile-scoped (present_api.py's discover routes act on the one
//    shared find-jobs.json), so it's shown once, not per profile; the
//    mockup's per-profile discovery block is simplified accordingly.
export default function DashboardView({ profiles, selectedProfileId, onSelectProfile, cadenceDays }) {
  const [discoverLatest, setDiscoverLatest] = useState(null);
  const [discoverLoading, setDiscoverLoading] = useState(true);
  const [discoverStarting, setDiscoverStarting] = useState(false);
  const [discoverError, setDiscoverError] = useState(null);
  const pollTimer = useRef(null);

  const stopPolling = useCallback(() => {
    if (pollTimer.current) {
      clearTimeout(pollTimer.current);
      pollTimer.current = null;
    }
  }, []);

  const poll = useCallback((delayMs) => {
    pollTimer.current = setTimeout(() => {
      getDiscoverLatest()
        .then((latest) => {
          setDiscoverLatest(latest);
          if (latest.running) {
            poll(DISCOVER_POLL_INTERVAL_MS);
          }
        })
        .catch(() => poll(DISCOVER_POLL_INTERVAL_MS));
    }, delayMs);
  }, []);

  useEffect(() => {
    setDiscoverLoading(true);
    getDiscoverLatest()
      .then((latest) => {
        setDiscoverLatest(latest);
        setDiscoverLoading(false);
        if (latest.running) {
          poll(DISCOVER_POLL_INTERVAL_MS);
        }
      })
      .catch((error) => {
        setDiscoverLoading(false);
        setDiscoverError(error.message || String(error));
      });
    return stopPolling;
  }, [poll, stopPolling]);

  async function handleStartDiscovery() {
    setDiscoverStarting(true);
    setDiscoverError(null);
    try {
      await startDiscovery();
      stopPolling();
      poll(0);
    } catch (error) {
      setDiscoverError(error.message || String(error));
    } finally {
      setDiscoverStarting(false);
    }
  }

  const selected = profiles.find((profile) => profile.profile_id === selectedProfileId) || null;

  return (
    <div>
      <section className="panel">
        <h2>Dashboard</h2>
        <p className="muted">Across every profile — summary first, then the selected profile's detail below.</p>

        <div className="summary-grid">
          {profiles.map((profile) => (
            <button
              key={profile.profile_id}
              type="button"
              className={`stat-tile${profile.profile_id === selectedProfileId ? "" : ""}`}
              style={{ textAlign: "left", cursor: "pointer", border: profile.profile_id === selectedProfileId ? "1px solid var(--accent)" : undefined }}
              onClick={() => onSelectProfile(profile.profile_id)}
            >
              <div className="stat-label">{profile.label}</div>
              <div className="stat-value" style={{ fontSize: "0.9rem" }}>
                {profile.titles.slice(0, 2).join(", ") || "no titles set"}
              </div>
            </button>
          ))}
        </div>
      </section>

      {selected && (
        <section className="panel">
          <h2>
            Profile <span className="muted">{selected.label}</span>
          </h2>
          <div className="field-row">
            <div className="field">
              <div className="label">Job titles / queries</div>
              <div className="tag-list">
                {selected.titles.map((title) => (
                  <span className="tag" key={title}>
                    {title}
                  </span>
                ))}
              </div>
            </div>
          </div>
        </section>
      )}

      {/* Shared across every profile -- find-jobs.json (and therefore
          Discover) is not profile-scoped (S2-B), so this panel is shown
          once, not per profile; kept as-is per the task spec ("keep
          Discover as is for 0.1.9; it goes away with S26 later"). */}
      {!discoverLoading && (
        <DiscoverPanel
          latest={discoverLatest}
          running={Boolean(discoverLatest && discoverLatest.running)}
          cadenceDays={cadenceDays || 7}
          onStart={handleStartDiscovery}
          starting={discoverStarting}
          error={discoverError}
        />
      )}

      <section className="panel">
        <h2>Profile switcher</h2>
        <ProfileSwitcher profiles={profiles} selectedProfileId={selectedProfileId} onSelect={onSelectProfile} />
      </section>
    </div>
  );
}
