import { SETTINGS_HASH, navigate } from "../routing.js";

// Q4a-nav: the profile switcher is a dropdown in the top bar (it replaced
// the profile card every view used to show). Options are GET /api/profiles'
// own list (profiles.py's `_profile_to_json`: label, resume_ref, titles);
// choosing one calls POST /api/profiles/selection through `onSelect`
// (hooks.useProfiles.switchTo, which only updates once the server
// confirms). The last option opens Settings, where profiles are managed.
const MANAGE = "__manage__";

export default function ProfileSwitcher({ profiles, selectedProfileId, onSelect }) {
  if (!profiles || profiles.length === 0) {
    return (
      <a href={SETTINGS_HASH} className="top-profile-note">
        No profiles yet
      </a>
    );
  }
  return (
    <label className="profile-select">
      <span className="visually-hidden">Profile</span>
      <select
        aria-label="Profile"
        value={selectedProfileId || ""}
        onChange={(event) => {
          if (event.target.value === MANAGE) {
            event.target.value = selectedProfileId || "";
            navigate(SETTINGS_HASH);
            return;
          }
          onSelect(event.target.value);
        }}
      >
        {profiles.map((profile) => (
          <option key={profile.profile_id} value={profile.profile_id}>
            {profile.label}
            {profile.resume_ref.record_id && profile.titles[0] ? ` · ${profile.titles[0]}` : ""}
          </option>
        ))}
        <option value={MANAGE}>Manage profiles…</option>
      </select>
    </label>
  );
}
