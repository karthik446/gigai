import PostingsBoard from "./PostingsBoard.jsx";

// B4: the live view while a run is still acquiring/assessing. `rows` is
// already the merged flat row set (App.jsx folds each /progress poll in via
// mergeRows so a card never disappears between polls); this component only
// adds the cap/candidate-count banner PostingsBoard expects.
export default function ProgressBoard({ rows, progress }) {
  const cap =
    progress && progress.cap != null
      ? {
          cap: progress.cap,
          candidateCount: progress.candidate_count,
          notAssessedCounts: progress.not_assessed_counts,
        }
      : null;

  return <PostingsBoard rows={rows} cap={cap} />;
}
