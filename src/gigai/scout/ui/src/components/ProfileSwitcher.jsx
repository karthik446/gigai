// F1/P9: the profile-switcher pill row every app view shows (mockup's
// `.profile-switcher`/`.profile-pill`). `profiles` is GET /api/profiles's
// own list (profiles.py's `_profile_to_json`: label, resume_ref, titles,
// ...) -- no resume text, no proposed fields.
export default function ProfileSwitcher({ profiles, selectedProfileId, onSelect }) {
  if (!profiles || profiles.length === 0) {
    return <p className="muted">No profiles yet.</p>;
  }
  return (
    <div className="profile-switcher">
      {profiles.map((profile) => (
        <button
          key={profile.profile_id}
          type="button"
          className={`profile-pill${profile.profile_id === selectedProfileId ? " active" : ""}`}
          onClick={() => onSelect(profile.profile_id)}
        >
          <div className="pname">{profile.label}</div>
          <div className="presume">{profile.resume_ref.record_id ? profile.titles[0] || "" : ""}</div>
        </button>
      ))}
    </div>
  );
}
