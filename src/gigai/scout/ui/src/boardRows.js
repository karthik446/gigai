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
      h1b: null, // the live snapshot has no catalog join; the sealed results row does
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
  // uat-bug-009: a posting skipped as unchanged, with a successful earlier
  // assessment carried forward (additive, outside the assessed/
  // not-assessed partition -- see present_api.py's carried_forward_assessments).
  const carriedForwardByUrl = new Map(
    (payload.carried_forward_assessments || []).map((entry) => [entry.normalized_url, entry]),
  );
  return payload.rows.map((row) => {
    const url = row.posting.normalized_url;
    const assessment = assessedByUrl.get(url) || null;
    const notAssessedReason = notAssessedByUrl.get(url) || null;
    const carriedForward = carriedForwardByUrl.get(url) || null;
    const status = assessment
      ? "assessed"
      : carriedForward
        ? "carried_forward"
        : notAssessedReason
          ? "not_assessed"
          : "acquired";
    return {
      posting: row.posting,
      status,
      assessment: assessment || carriedForward?.result || null,
      notAssessedReason,
      fromRunDate: carriedForward?.from_run_date || null,
      // Q4b: rows[].h1b {approvals, fiscal_years} (the company catalog's
      // H-1B join, Q4b-data) -- null when the row does not carry it.
      h1b: row.h1b || null,
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
