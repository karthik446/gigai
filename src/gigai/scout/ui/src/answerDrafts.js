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
//   saveUnsaved()  POST the answers the record does not hold yet, without
//                  re-assessing: "Tailor resume" calls it first, since
//                  tailoring reads the recorded answers.
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
import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError, getAnswerMatch, postAnswer } from "./api.js";
import { answerRequests, answerStates, reassessGate, unsavedAnswerRequests } from "./answersModel.js";

export function useAnswerDrafts({ assessment, jobIdentity, priorAnswers, onAnswered, onReassessUnavailable }) {
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
  useEffect(() => {
    let current = true;
    const open = questions.filter((question) => !recorded.has(question.question_id));
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
  }, [questions, recorded]);

  const states = useMemo(() => answerStates(questions, drafts, recorded, { suggestions, used }), [questions, drafts, recorded, suggestions, used]);
  const gate = useMemo(() => reassessGate({ assessed: Boolean(assessment), states }), [assessment, states]);

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
    const requests = answerRequests(states, jobIdentity);
    if (requests.length === 0 || busy) {
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
  }, [states, jobIdentity, busy, onAnswered, onReassessUnavailable]);

  // Rejects when a save fails, so the caller does not go on to tailor with
  // an answer missing.
  const saveUnsaved = useCallback(async () => {
    const requests = unsavedAnswerRequests(states);
    if (requests.length === 0) {
      return 0;
    }
    setBusy("saving");
    setError(null);
    const done = {};
    try {
      for (const body of requests) {
        await postAnswer(body);
        done[body.question_id] = body.answer;
      }
      return requests.length;
    } catch (err) {
      setError(err.message || String(err));
      throw err;
    } finally {
      setSaved((current) => ({ ...current, ...done }));
      setBusy(null);
    }
  }, [states]);

  return { questions, states, gate, busy, error, setDraft, valueFor, reassess, saveUnsaved, suggestionFor, applySuggestion, canReassess: Boolean(jobIdentity) };
}
