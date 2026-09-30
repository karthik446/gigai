import ProfileSwitcher from "./ProfileSwitcher.jsx";
import ThemeToggle from "./ThemeToggle.jsx";
import { needAnswersLabel } from "../jobStateModel.js";
import { JOBS_HASH, NAV_VIEWS, SETTINGS_HASH, navViewFor, routeFor } from "../routing.js";

// Q4a-nav: the one persistent top bar every page shows.
//
//   Scout | Jobs (N) | Assessments (N) | Applications | Runs | Dark mode | <profile ▾> | ⚙
//
// Every link is a plain <a href="#/…"> from routing.js's ROUTES (the single
// route table), so back/forward work. uat-bug-018: the count beside Jobs
// and Assessments is how many of that list's jobs are in the state "Needs
// your answers" (`needAnswers` {jobs, assessments}, counted by FindJobsView
// over the same jobs its grids show); the Questions link is gone.
//
// uat-batch1 (N1/N2): desktop only (a 13in+ laptop, operator decision
// 2026-09-27), so the "Menu" button and its sheet are gone: one flex row.
// The light/dark toggle sits on the right (ThemeToggle.jsx).
function GearIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

export default function TopBar({ currentView, needAnswers, profiles, selectedProfileId, onSelectProfile, profilesLoading, profilesError }) {
  const activeNav = navViewFor(currentView);

  const links = NAV_VIEWS.map((view) => {
    const route = routeFor(view);
    const waiting = needAnswers && (view === "jobs" || view === "assessments") ? needAnswers[view] : 0;
    const badge = waiting > 0 ? waiting : null;
    return (
      <a
        key={view}
        href={route.path}
        className={`top-link${activeNav === view ? " active" : ""}`}
        aria-current={activeNav === view ? "page" : undefined}
        data-nav={view}
      >
        {route.label}
        {badge !== null && (
          <span className="nav-badge" data-role="need-answers" title={needAnswersLabel(badge)} aria-label={needAnswersLabel(badge)}>
            {badge}
          </span>
        )}
      </a>
    );
  });

  const switcher = profilesLoading ? (
    <span className="muted top-profile-note">Loading profiles…</span>
  ) : profilesError ? (
    <span className="top-profile-note danger-text" title={profilesError}>
      Profiles unavailable
    </span>
  ) : (
    <ProfileSwitcher profiles={profiles} selectedProfileId={selectedProfileId} onSelect={onSelectProfile} />
  );

  const gear = (
    <a
      href={SETTINGS_HASH}
      className={`top-gear${activeNav === "settings" ? " active" : ""}`}
      aria-label="Settings"
      title="Settings"
      aria-current={activeNav === "settings" ? "page" : undefined}
      data-nav="settings"
    >
      <GearIcon />
    </a>
  );

  return (
    <header className="top-bar">
      <div className="top-bar-row">
        <a href={JOBS_HASH} className="brand" aria-label="Scout home">
          Scout
        </a>
        <nav className="top-links" aria-label="Main views">
          {links}
        </nav>
        <div className="top-right">
          <ThemeToggle />
          {switcher}
          {gear}
        </div>
      </div>
    </header>
  );
}
