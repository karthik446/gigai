// Shared data hooks for the F3 app views (P9). Kept separate from App.jsx
// so each view module (views/*.jsx) can import just what it needs without
// growing App.jsx into the single file that owns every fetch.
import { useCallback, useEffect, useState } from "react";
import { getProfiles, selectProfile } from "./api.js";

// F1: the profile list + which one is selected, shared by every view
// (dashboard/profiles/find-jobs all read the same GET /api/profiles).
// `switchTo` calls POST /api/profiles/selection and updates local state
// optimistically-on-success (never before the server confirms it, so a
// rejected switch never shows the wrong profile as active).
export function useProfiles() {
  const [state, setState] = useState({ loading: true, profiles: [], selectedProfileId: null, error: null });

  const reload = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    getProfiles()
      .then((response) =>
        setState({ loading: false, profiles: response.profiles, selectedProfileId: response.selected_profile_id, error: null }),
      )
      .catch((error) => setState({ loading: false, profiles: [], selectedProfileId: null, error: error.message || String(error) }));
  }, []);

  useEffect(reload, [reload]);

  const switchTo = useCallback((profileId) => {
    return selectProfile(profileId).then((response) => {
      setState((prev) => ({ ...prev, selectedProfileId: response.selected_profile_id }));
    });
  }, []);

  return { ...state, reload, switchTo };
}
