import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import ProfileSwitcher from "../components/ProfileSwitcher.jsx";
import DiscoverPanel from "../components/DiscoverPanel.jsx";
import { getDiscoverLatest, startDiscovery } from "../api.js";
import { useApplications, useRuns } from "../hooks.js";
import { relativeTimeLabel } from "../display.js";

const DISCOVER_POLL_INTERVAL_MS = 4000;

// P9/P9c (F3): Dashboard.
//
// P9c wires back what P9a dropped for lack of an API (GET /api/runs, GET
// /api/applications -- see orchestrator/workers/P9a-app-views.md's "Dropped
// fields" and P9c's own spec):
//  - "Last run: found/assessed/matches" and "new since last visit" -- from
//    GET /api/runs?profile_id=<selected>, newest entry. "Matched" uses the
//    real verdict-based count (Verdict.MATCHED_ABOVE_THRESHOLD) in place of
//    the mockup's invented "strong" tier (see mockups/README.md open
//    question #1 -- the per-requirement matrix has no single "strong/
//    partial/weak" rollup field; MATCHED_ABOVE_THRESHOLD is the closest
//    real equivalent). "New since last visit" is still not literally
//    computable (no per-viewer "last seen" state exists) -- shown instead
//    as "new" from the run's own acquire pass (RowOutcome.NEW), which is
//    the real field closest to that intent.
//  - Application pipeline + "needs action" -- GET /api/applications' own
//    rows, grouped by `application_events.EVENT_KINDS` into the mockup's
//    5-stage funnel (saved/applied/interviewing/offer/closed -- README.md's
//    open question #4 notes this grouping is a display-only fold, not a
//    stored field; every event's own real `event_kind` is preserved in the
//    row data, only the funnel's stage buckets are a UI-side rollup).
//    "Needs action" is a plain, honest read over that same data (no prep
//    status/interview-date modeling exists yet -- README.md's own note):
//    events with no update in 10+ days.
//
// Watchlist health stays dropped (S26, not built).
//
// KEPT, on real data:
//  - Per-profile summary row (GET /api/profiles: label, titles, resume ref).
//  - Discovery status (GET /api/discover/latest) -- note this is NOT
//    profile-scoped (present_api.py's discover routes act on the one
//    shared find-jobs.json), so it's shown once, not per profile; the
//    mockup's per-profile discovery block is simplified accordingly.
const PIPELINE_STAGES = [
  { key: "saved", label: "Saved", kinds: ["saved"] },
  { key: "applied", label: "Applied", kinds: ["applied"] },
  { key: "interviewing", label: "Interviewing", kinds: ["interview_scheduled"] },
  { key: "offer", label: "Offer", kinds: ["offer_received"] },
  { key: "closed", label: "Closed", kinds: ["rejected", "withdrawn"] },
];

const STALE_DAYS = 10;

function currentApplications(applications) {
  // `current` (projection.py's own field): the latest, non-superseded event
  // per posting identity -- never double-counts a corrected/superseded row.
  return applications.filter((item) => item.current !== false);
}

function pipelineCounts(applications) {
  const counts = Object.fromEntries(PIPELINE_STAGES.map((stage) => [stage.key, 0]));
  for (const item of currentApplications(applications)) {
    const stage = PIPELINE_STAGES.find((candidate) => candidate.kinds.includes(item.event_kind));
    if (stage) {
      counts[stage.key] += 1;
    }
  }
  return counts;
}

function needsAction(applications) {
  const now = Date.now();
  return currentApplications(applications)
    .filter((item) => ["applied", "interview_scheduled"].includes(item.event_kind))
    .map((item) => {
      const occurred = new Date(item.occurred_at);
      const daysAgo = Number.isNaN(occurred.getTime()) ? null : Math.floor((now - occurred.getTime()) / 86400000);
      return { ...item, daysAgo };
    })
    .filter((item) => item.daysAgo === null || item.daysAgo >= STALE_DAYS)
    .sort((a, b) => (b.daysAgo ?? 0) - (a.daysAgo ?? 0));
}

function LastRunSummary({ profileId }) {
  const { loading, runs, error } = useRuns(profileId);
  if (loading) {
    return <p className="muted">Loading last run…</p>;
  }
  if (error) {
    return <div className="callout danger">Could not load runs: {error}</div>;
  }
  if (runs.length === 0) {
    return <p className="muted">No runs yet for this profile.</p>;
  }
  const last = runs[0];
  return (
    <div className="field">
      <div className="label">Last run</div>
      <div className="value">
        {relativeTimeLabel(last.created_at)} · {last.counts.found} found / {last.counts.new} new /{" "}
        {last.counts.assessed} assessed / {last.counts.matched} matched
      </div>
    </div>
  );
}

function PipelinePanel({ applications }) {
  const counts = useMemo(() => pipelineCounts(applications), [applications]);
  const actionable = useMemo(() => needsAction(applications), [applications]);
  return (
    <section className="panel">
      <h2>Application pipeline</h2>
      <div className="pipeline-row">
        {PIPELINE_STAGES.map((stage) => (
          <div className="pipeline-stage" key={stage.key}>
            <div className="stage-count">{counts[stage.key]}</div>
            <div className="stage-label">{stage.label}</div>
          </div>
        ))}
      </div>

      <h3>Needs action</h3>
      {actionable.length === 0 ? (
        <p className="muted">Nothing needs a follow-up right now.</p>
      ) : (
        <div className="profile-list">
          {actionable.map((item) => (
            <div key={item.event_id} className="action-item">
              <div>
                <div style={{ fontWeight: 600 }}>{item.linked_posting?.title || item.external_ref || item.opportunity_ref}</div>
                <div className="muted" style={{ fontSize: "0.8rem" }}>
                  {item.event_kind === "applied" ? "Applied" : "Interview scheduled"}
                  {item.daysAgo !== null ? `, ${item.daysAgo} day${item.daysAgo === 1 ? "" : "s"} ago, no update` : ""}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

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
  const applicationsState = useApplications();

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
            <LastRunSummary profileId={selected.profile_id} />
          </div>
        </section>
      )}

      {applicationsState.error && (
        <div className="callout danger">Could not load applications: {applicationsState.error}</div>
      )}
      {!applicationsState.loading && !applicationsState.error && (
        <PipelinePanel applications={applicationsState.applications} />
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
