import { useEffect, useState } from "react";
import ProfileSwitcher from "./ProfileSwitcher.jsx";
import { JOBS_HASH, NAV_VIEWS, SETTINGS_HASH, navViewFor, routeFor } from "../routing.js";

// Q4a-nav: the one persistent top bar every page shows.
//
//   Scout | Jobs | Questions (N) | Applications | Runs | <profile ▾> | ⚙
//
// Every link is a plain <a href="#/…"> from routing.js's ROUTES (the single
// route table), so back/forward work. The Questions badge is
// usePendingQuestions' count (GET /api/assessments?verdict=pending_user_answers
// minus GET /api/answers), never a client-side guess. Below 640px the links
// collapse behind a "Menu" button (the profile switcher and the gear move
// into the same sheet); the sheet closes on any navigation.
function GearIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09a1.65 1.65 0 0 0-1-1.51 1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09a1.65 1.65 0 0 0 1.51-1 1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
    </svg>
  );
}

export default function TopBar({ currentView, questionsCount, profiles, selectedProfileId, onSelectProfile, profilesLoading, profilesError }) {
  const [menuOpen, setMenuOpen] = useState(false);
  const activeNav = navViewFor(currentView);

  // Any navigation (a link in the sheet, back/forward) closes the sheet.
  useEffect(() => {
    const close = () => setMenuOpen(false);
    window.addEventListener("hashchange", close);
    return () => window.removeEventListener("hashchange", close);
  }, []);

  const links = NAV_VIEWS.map((view) => {
    const route = routeFor(view);
    const badge = view === "questions" && questionsCount > 0 ? questionsCount : null;
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
          <span className="nav-badge" aria-label={`${badge} open question${badge === 1 ? "" : "s"}`}>
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
      <span className="gear-label">Settings</span>
    </a>
  );

  return (
    <header className="top-bar" data-menu-open={menuOpen ? "true" : "false"}>
      <div className="top-bar-row">
        <a href={JOBS_HASH} className="brand" aria-label="Scout home">
          Scout
        </a>
        <nav className="top-links" aria-label="Main views">
          {links}
        </nav>
        <div className="top-right">
          {switcher}
          {gear}
        </div>
        <button
          type="button"
          className="menu-toggle"
          aria-expanded={menuOpen}
          aria-controls="top-menu-sheet"
          onClick={() => setMenuOpen((open) => !open)}
        >
          {menuOpen ? "Close" : "Menu"}
          {!menuOpen && questionsCount > 0 && <span className="nav-badge">{questionsCount}</span>}
        </button>
      </div>
      {menuOpen && (
        <div className="menu-sheet" id="top-menu-sheet">
          <nav className="menu-links" aria-label="Main views (menu)">
            {links}
            {gear}
          </nav>
          <div className="menu-profile">{switcher}</div>
        </div>
      )}
    </header>
  );
}
