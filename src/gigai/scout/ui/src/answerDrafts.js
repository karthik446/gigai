// uat-batch1 (N5/N7): the state behind the answer boxes in the requirement
// table and the ONE "Re-assess" above it. The rules are answersModel.js's
// (pure); this hook holds what the operator typed and does the POSTs.
//
//   reassess()     POST /api/answers once per filled box, `reassess: null`
//                  on all but the last and the job identity on the last:
//                  every answer is recorded, then ONE re-assessment runs
//                  with all of them (find_jobs/api/answers.py records one
//                  answer per call and re-assesses only when asked). The
//                  returned `reassessed` AssessResponse goes to onAnswered.
//
// `priorAnswers` (Map question_id -> GET /api/answers row) fills a box the
// operator never touched: an answer given on another posting counts here
// without being typed again; the prompt alone decides what it means
// (operator answer 5).
//
// 0.1.10.7 C: answers are the user's, shared by every profile. For each
// open question with nothing on record the hook asks
// GET /api/answers/match once: a near match is
// offered as "We already know: ..., use it?" (`suggestionFor`), and
// `applySuggestion` puts that answer in the box for the operator to confirm
// or edit. Nothing is saved until Re-assess.
//
// `onReassessUnavailable`: an identity neither the quick-assess store nor a
// run knows answers 404 reassess_not_found AFTER recording the answer; the
// job page passes a fallback that runs POST /api/assess {job_url} instead.
//
// 0.1.11.6 AN1: `profileId` is the profile of the page that holds the boxes: the last POST names it beside the job
// (`reassess: {job_identity, profile_id}`), so the re-assessment is that profile's, whoever else holds the job.
//
// 0110-10-12: `stale` is why the assessment shown is old, in words ("older
// prompt"), or null. With no box filled, Re-assess then assesses the posting
// again as it is (`onReassessUnavailable`, the same POST /api/assess: ONE
// model call, no answer written). With a box filled it is what it always
// was: the answers are saved and the last one re-assesses.
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getAnswerMatch, postAnswer } from "./api.js";
import { answerRequests, answerStates, reassessErrorText, reassessGate } from "./answersModel.js";

export function useAnswerDrafts({ assessment, jobIdentity, profileId = null, priorAnswers, onAnswered, onReassessUnavailable, stale = null }) {
  const [drafts, setDrafts] = useState({});
  const [saved, setSaved] = useState({});
  const [suggestions, setSuggestions] = useState(() => new Map());
  const [used, setUsed] = useState({});
  const [busy, setBusy] = useState(null); // null | "reassess" | "saving"
  const [error, setError] = useState(null);

  useEffect(() => {
    setDrafts({});
    setSaved({});
    setUsed({});
    setBusy(null);
    setError(null);
  }, [jobIdentity]);

  const questions = useMemo(() => (assessment && assessment.structured_questions) || [], [assessment]);

  // What is on record: GET /api/answers, plus what this page saved since.
  const recorded = useMemo(() => {
    const merged = new Map(priorAnswers || []);
    Object.entries(saved).forEach(([id, answer]) => merged.set(id, { question_id: id, answer }));
    return merged;
  }, [priorAnswers, saved]);

  // 0110-034: one near-match lookup per open question nothing answers yet.
  // Keyed by the open questions themselves: a render that only makes new
  // objects of the same questions and answers asks nothing again.
  const openKey = useMemo(() => JSON.stringify(questions.filter((question) => !recorded.has(question.question_id)).map((question) => [question.question_id, question.question])), [questions, recorded]);
  useEffect(() => {
    let current = true;
    const open = JSON.parse(openKey).map(([question_id, question]) => ({ question_id, question }));
    if (open.length === 0) {
      setSuggestions(new Map());
      return undefined;
    }
    Promise.all(
      open.map((question) =>
        getAnswerMatch({ questionId: question.question_id, question: question.question })
          .then((response) => [question.question_id, response && response.match])
          .catch(() => [question.question_id, null]),
      ),
    ).then((pairs) => current && setSuggestions(new Map(pairs.filter(([, match]) => match))));
    return () => {
      current = false;
    };
  }, [openKey]);

  const states = useMemo(() => answerStates(questions, drafts, recorded, { suggestions, used }), [questions, drafts, recorded, suggestions, used]);
  const canAssess = Boolean(onReassessUnavailable);
  const gate = useMemo(() => reassessGate({ assessed: Boolean(assessment), states, stale, canAssess }), [assessment, states, stale, canAssess]);

  const setDraft = useCallback((id, value) => setDrafts((current) => ({ ...current, [id]: value })), []);
  const suggestionFor = useCallback((id) => (states.find((state) => state.question_id === id) || {}).suggestion || null, [states]);
  const applySuggestion = useCallback(
    (id) => {
      const suggestion = suggestions.get(id);
      if (suggestion) {
        setDrafts((current) => ({ ...current, [id]: suggestion.answer }));
        setUsed((current) => ({ ...current, [id]: suggestion.bank_question_id }));
      }
    },
    [suggestions],
  );
  const valueFor = useCallback(
    (id) => {
      if (Object.prototype.hasOwnProperty.call(drafts, id)) {
        return drafts[id];
      }
      const prior = recorded.get(id);
      return prior && typeof prior.answer === "string" ? prior.answer : "";
    },
    [drafts, recorded],
  );

  const reassess = useCallback(async () => {
    const requests = answerRequests(states, jobIdentity, profileId);
    if (busy) {
      return;
    }
    if (requests.length === 0) {
      // 0110-10-12: an old assessment and no answer to save: assess the posting again as it is.
      if (!stale || !onReassessUnavailable) {
        return;
      }
      setBusy("reassess");
      setError(null);
      try {
        const reassessed = await onReassessUnavailable();
        if (reassessed && onAnswered) {
          onAnswered(reassessed);
        }
      } catch (err) {
        setError(reassessErrorText(err));
      } finally {
        setBusy(null);
      }
      return;
    }
    setBusy("reassess");
    setError(null);
    const done = {};
    try {
      let reassessed = null;
      for (const body of requests) {
        try {
          const response = await postAnswer(body);
          done[body.question_id] = body.answer;
          if (response && response.reassessed) {
            reassessed = response.reassessed;
          }
        } catch (err) {
          if (!(body.reassess && err instanceof ApiError && err.code === "reassess_not_found" && onReassessUnavailable)) {
            throw err;
          }
          // The answer itself is recorded before the job lookup fails.
          done[body.question_id] = body.answer;
          reassessed = await onReassessUnavailable();
        }
      }
      setSaved((current) => ({ ...current, ...done }));
      setDrafts({});
      if (reassessed && onAnswered) {
        onAnswered(reassessed);
      }
    } catch (err) {
      setSaved((current) => ({ ...current, ...done }));
      setError(err.message || String(err));
    } finally {
      setBusy(null);
    }
  }, [states, jobIdentity, profileId, busy, onAnswered, onReassessUnavailable, stale]);

  return { questions, states, gate, busy, error, setDraft, valueFor, reassess, suggestionFor, applySuggestion, canReassess: Boolean(jobIdentity) };
}
