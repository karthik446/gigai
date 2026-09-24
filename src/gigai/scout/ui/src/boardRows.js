// B4: turns either the live /progress payload or the final sealed /results
// payload into the one flat row shape PostingsBoard/PostingCard render:
//   { posting, status, assessment, notAssessedReason }
//
// Kept as pure functions (no React) so App.jsx's merge-on-every-poll logic
// stays trivial to reason about and to unit test without mounting anything.

export function rowsFromProgress(progress) {
  const assessmentByUrl = new Map(
    (progress.assessments || []).map((item) => [item.normalized_url, item]),
  );
  return (progress.postings || []).map((posting) => {
    const assessmentEntry = assessmentByUrl.get(posting.normalized_url);
    const status = assessmentEntry ? assessmentEntry.status : "acquired";
    return {
      posting,
      status,
      assessment: assessmentEntry?.assessment || null,
      notAssessedReason: assessmentEntry?.reason || null,
    };
  });
}

export function rowsFromResults(payload) {
  const assessedByUrl = new Map(
    payload.assessments.map((assessment) => [assessment.posting.normalized_url, assessment]),
  );
  const notAssessedByUrl = new Map(
    payload.not_assessed.map((entry) => [entry.posting.normalized_url, entry.reason]),
  );
  return payload.rows.map((row) => {
    const url = row.posting.normalized_url;
    const assessment = assessedByUrl.get(url) || null;
    const notAssessedReason = notAssessedByUrl.get(url) || null;
    return {
      posting: row.posting,
      status: assessment ? "assessed" : notAssessedReason ? "not_assessed" : "acquired",
      assessment,
      notAssessedReason,
    };
  });
}

// Merge a live-progress row set with an already-visible row set, keyed by
// normalized_url, so a card already on screen never disappears or resets
// while a later poll's snapshot is momentarily missing it (progress files
// are best-effort/append-only, never expected to shrink, but this keeps the
// UI robust to a transient partial read either way).
export function mergeRows(previousRows, nextRows) {
  const merged = new Map(previousRows.map((row) => [row.posting.normalized_url, row]));
  for (const row of nextRows) {
    merged.set(row.posting.normalized_url, row);
  }
  return [...merged.values()];
}
