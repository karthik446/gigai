import { useCallback, useEffect, useRef, useState } from "react";

import { getAssessBatchStatus, postAssessBatchCancel } from "./api.js";
import { batchRunning, createBatchWatch } from "./assessBatchModel.js";

// 0.1.11.5 (ASSESS-01): the assess batch as a page sees it. One read when the page opens (a batch started before, in
// another tab or in a terminal, is shown too); then a read every BATCH_POLL_MS only while a batch runs.
//   status     the last answer of GET /api/postings/assess/status, or null
//   running    a batch runs
//   adopt(a)   the approval's 202 answer: the page shows the batch at once and starts reading
//   cancel()   POST /api/postings/assess/cancel; `cancelError` when it could not be sent
//   onProgress a posting of the batch got its result; onEnd(last) the batch ended (both read through refs)
export function useAssessBatch({ onProgress, onEnd } = {}) {
  const [status, setStatus] = useState(null);
  const [cancelError, setCancelError] = useState(null);
  const [cancelSent, setCancelSent] = useState(false);
  const progress = useRef(onProgress);
  const end = useRef(onEnd);
  progress.current = onProgress;
  end.current = onEnd;
  const watch = useRef(null);

  useEffect(() => {
    const abort = new AbortController();
    const made = createBatchWatch({
      read: () => getAssessBatchStatus({ signal: abort.signal }),
      onStatus: setStatus,
      onProgress: (next) => progress.current && progress.current(next),
      onEnd: (last, next) => {
        setCancelSent(false);
        end.current && end.current(last, next);
      },
    });
    watch.current = made;
    made.check();
    return () => {
      made.stop();
      abort.abort();
      watch.current = null;
    };
  }, []);

  const adopt = useCallback((answer) => {
    setCancelError(null);
    setCancelSent(false);
    watch.current && watch.current.take(answer);
  }, []);

  const cancel = useCallback(() => {
    setCancelError(null);
    setCancelSent(true);
    return postAssessBatchCancel()
      .then((answer) => watch.current && watch.current.take(answer))
      .catch((err) => {
        setCancelSent(false);
        setCancelError(err.detail || err.message || String(err));
      });
  }, []);

  return { status, running: batchRunning(status), adopt, cancel, cancelSent, cancelError };
}
