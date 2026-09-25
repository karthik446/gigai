// Shared data hooks for the F3 app views (P9). Kept separate from App.jsx
// so each view module (views/*.jsx) can import just what it needs without
// growing App.jsx into the single file that owns every fetch.
import { useCallback, useEffect, useState } from "react";
import { getAnswers, getApplications, getAssessments, getProfiles, getRuns, selectProfile } from "./api.js";

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

// P9c: GET /api/runs, every find-jobs run for this target (newest first,
// each with its found/new/assessed/matched counts) -- shared by the
// dashboard's "last run" summary, the Profiles run-history table, and the
// Find-jobs past-run picker. `profileId` is optional; `undefined`/`null`
// fetches every profile's runs unfiltered.
export function useRuns(profileId) {
  const [state, setState] = useState({ loading: true, runs: [], error: null });

  const reload = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    getRuns(profileId ? { profileId } : undefined)
      .then((response) => setState({ loading: false, runs: response.runs, error: null }))
      .catch((error) => setState({ loading: false, runs: [], error: error.message || String(error) }));
  }, [profileId]);

  useEffect(reload, [reload]);

  return { ...state, reload };
}

// P9c: GET /api/applications, the projection's own application rows (incl.
// `linked_posting`, `null` when unmatched) -- shared by the dashboard's
// pipeline + needs-action panels.
export function useApplications() {
  const [state, setState] = useState({ loading: true, applications: [], error: null });

  const reload = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    getApplications()
      .then((response) => setState({ loading: false, applications: response.applications, error: null }))
      .catch((error) => setState({ loading: false, applications: [], error: error.message || String(error) }));
  }, []);

  useEffect(reload, [reload]);

  return { ...state, reload };
}

// Q4a-nav: every open question across postings -- the Questions view's
// list AND the top bar's badge count read this one state. The list is
// DERIVED exactly as PendingAnswersView always did (P3, operator decision
// 3): every stored quick assessment with verdict pending_user_answers
// (GET /api/assessments?verdict=pending_user_answers) minus any question_id
// already answered (GET /api/answers). `count` is the number of such
// questions still open; `reload` is called after anything that can change
// it (an answer, a re-assess, an on-demand assess).
export function usePendingQuestions() {
  const [state, setState] = useState({ loading: true, items: [], answeredIds: new Set(), error: null });

  const reload = useCallback(() => {
    setState((prev) => ({ ...prev, loading: true, error: null }));
    Promise.all([getAssessments({ verdict: "pending_user_answers" }), getAnswers()])
      .then(([assessments, answers]) => {
        setState({
          loading: false,
          items: assessments.items || [],
          answeredIds: new Set((answers.answers || []).map((answer) => answer.question_id)),
          error: null,
        });
      })
      .catch((error) => setState({ loading: false, items: [], answeredIds: new Set(), error: error.message || String(error) }));
  }, []);

  useEffect(reload, [reload]);

  const cards = state.items.map((item) => ({
    item,
    result: item.result,
    pendingQuestions: (item.result.structured_questions || []).filter((question) => !state.answeredIds.has(question.question_id)),
  }));
  const stillPending = cards.filter((card) => card.result.verdict === "pending_user_answers" || card.pendingQuestions.length > 0);
  const count = stillPending.reduce((total, card) => total + card.pendingQuestions.length, 0);

  return { ...state, cards: stillPending, count, reload };
}
