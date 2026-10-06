import { useCallback, useEffect, useRef, useState } from "react";

import { postPostingsRank } from "../api.js";
import { isRankAnswer, RANK_POLL_MS, rankButtons, rankJobRunning, rankOutcomeLine, rankRefusalLine, rankStatusLine, rerankDialog, staleResumeLine } from "../rankNowModel.js";
import RerankApprovalDialog from "./RerankApprovalDialog.jsx";

// 0.1.11.2 RANKUI: ranking on the Jobs page. One line says how far the rank is ("12 postings of the last 7 days are
// not ranked yet (ranked 45 of 57)"), "Rank now" ranks the unranked ones, and "Re-rank latest 100" asks first (its
// dialog shows the calls) and ranks the newest 100 again. A click starts a job on the server (POST /api/postings/rank,
// 202); the panel then reads it every RANK_POLL_MS, shows "ranked X of Y" as it goes and refreshes the list
// (`onRefresh`) when the count moves and when the job ends. With ranking off the buttons say so and the line says how
// to turn it on. 0.1.11.2: when the master changed after the postings were ranked (`ranking.stale_resume`) a line says
// so with a "Re-rank" button: the same "Re-rank latest 100" ask and dialog, never a model call without the yes.
// Nothing is read on load: `ranking` is the list's own block (GET /api/postings).
export default function RankPanel({ ranking, onRefresh }) {
  const [answer, setAnswer] = useState(null); // the last POST /api/postings/rank answer of this page
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState(null);
  const [dialog, setDialog] = useState(null);
  const [dialogError, setDialogError] = useState(null);
  const refresh = useRef(onRefresh);
  refresh.current = onRefresh;
  const seen = useRef(null);

  const job = answer ? answer.job : null;
  const running = rankJobRunning(job);
  // While the job runs the panel's own read is the fresher one; otherwise the list's.
  const shown = running && answer && answer.ranking ? answer.ranking : ranking;

  const take = useCallback((next) => {
    if (!isRankAnswer(next)) {
      return null; // not this route's answer: nothing is shown from it
    }
    setAnswer(next);
    return next;
  }, []);

  useEffect(() => {
    if (!running) {
      return undefined;
    }
    let stopped = false;
    const timer = setInterval(() => {
      postPostingsRank({})
        .then((next) => {
          if (stopped || !take(next)) {
            return;
          }
          const ranked = JSON.stringify(next.ranking && next.ranking.by_profile);
          const ended = !rankJobRunning(next.job);
          if (ended) {
            setNotice(rankOutcomeLine(next.job));
          }
          if (ended || ranked !== seen.current) {
            seen.current = ranked;
            refresh.current && refresh.current(); // the rows have new rank scores: the list is read again
          }
        })
        .catch(() => {}); // a read that failed is read again at the next tick
    }, RANK_POLL_MS);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [running, take]);

  const started = (next) => {
    if (!take(next)) {
      return;
    }
    if (rankJobRunning(next.job)) {
      setNotice(null);
    } else {
      setNotice(rankRefusalLine(next) || rankOutcomeLine(next.job));
      refresh.current && refresh.current();
    }
  };

  const rankNow = () => {
    setBusy(true);
    setNotice(null);
    postPostingsRank({ mode: "unranked", approve: true })
      .then(started)
      .catch((err) => setNotice(err.detail || err.message || String(err)))
      .finally(() => setBusy(false));
  };

  const askRerank = () => {
    setBusy(true);
    setNotice(null);
    setDialogError(null);
    postPostingsRank({ mode: "latest" })
      .then((next) => {
        take(next);
        const asked = rerankDialog(next);
        if (asked) {
          setDialog(asked);
        } else {
          setNotice("Could not read what a re-rank would cost. Nothing was ranked.");
        }
      })
      .catch((err) => setNotice(err.detail || err.message || String(err)))
      .finally(() => setBusy(false));
  };

  const approveRerank = () => {
    setBusy(true);
    setDialogError(null);
    postPostingsRank(dialog.approveBody)
      .then((next) => {
        setDialog(null);
        started(next);
      })
      .catch((err) => setDialogError(err.detail || err.message || String(err)))
      .finally(() => setBusy(false));
  };

  const howToEnable = answer && typeof answer.how_to_enable === "string" ? answer.how_to_enable : null;
  const line = rankStatusLine(shown, job, howToEnable);
  const buttons = rankButtons(shown, { job, busy, howToEnable });
  if (!line || !buttons) {
    return null;
  }
  const stale = staleResumeLine(shown, job);
  return (
    <>
      {stale && (
        <div className="result-count" data-testid="stale-resume-line">
          <span role="status">{stale}</span>
          <button
            type="button"
            className="button small"
            data-testid="rerank-stale"
            disabled={buttons.rerank.disabled}
            title="Asks first: how many postings and model calls. Nothing is ranked until you approve."
            onClick={askRerank}
          >
            Re-rank
          </button>
        </div>
      )}
      <div className="result-count" data-testid="rank-panel" data-ranking={running ? "running" : undefined}>
        <span data-testid="rank-status-line" role="status">
          {line}
        </span>
        <span className="rank-panel-actions">
          <button type="button" className="button small" data-testid="rank-now" disabled={buttons.rankNow.disabled} title={buttons.rankNow.title} onClick={rankNow}>
            {buttons.rankNow.label}
          </button>{" "}
          <button type="button" className="button small secondary" data-testid="rerank-latest" disabled={buttons.rerank.disabled} title={buttons.rerank.title} onClick={askRerank}>
            {buttons.rerank.label}
          </button>
        </span>
      </div>
      {notice && (
        <div className="result-count" data-testid="rank-notice" role="status">
          {notice}
        </div>
      )}
      {dialog && (
        <RerankApprovalDialog
          dialog={dialog}
          submitting={busy}
          error={dialogError}
          onApprove={approveRerank}
          onCancel={() => {
            setDialog(null);
            setDialogError(null);
          }}
        />
      )}
    </>
  );
}
