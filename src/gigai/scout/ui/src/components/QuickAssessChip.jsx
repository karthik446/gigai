// Q4a-nav-r1: the source chip for a job that came from an on-demand
// assessment (the quick-assess store: "+ Assess a job", a pasted text,
// `gigai scout assess`) rather than a find-jobs run. Styled like
// ProviderBadge (the same .ats-badge box) and rendered IN PLACE of it on
// on-demand cards / the job page header: an on-demand job has no
// PostingRow.provider (nothing acquired it), so the two never compete for
// the slot. The "via" suffix names how the posting reached the store
// (ResolvedJob.fetch_kind, assess_contracts.py): pasted text or a URL.
export const QUICK_ASSESS_LABEL = "Quick assess";

export function quickAssessVia(fetchKind) {
  if (fetchKind === "pasted") {
    return "pasted text";
  }
  if (fetchKind) {
    return "URL";
  }
  return "";
}

export default function QuickAssessChip({ fetchKind }) {
  const via = quickAssessVia(fetchKind);
  return (
    <span className="ats-badge ats-quick" title={via ? `Assessed on demand from a ${via}` : "Assessed on demand"}>
      {QUICK_ASSESS_LABEL}
      {via && <span className="ats-via"> via {via}</span>}
    </span>
  );
}
