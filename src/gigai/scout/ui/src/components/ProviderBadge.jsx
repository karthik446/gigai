// Q4a: where the posting came from -- PostingRow.provider (greenhouse /
// lever / ashby: acquire infers the ATS from the URL for every source) plus
// the source kind when it wasn't the ATS board itself (Exa search,
// hiring.cafe).
export default function ProviderBadge({ posting }) {
  const provider = posting.provider || "";
  const label = provider ? provider.charAt(0).toUpperCase() + provider.slice(1) : posting.source_kind || "";
  if (!label) {
    return null;
  }
  const via = provider && posting.source_kind && posting.source_kind !== "ats" ? ` via ${posting.source_kind}` : "";
  return (
    <span className={`ats-badge ats-${provider || "other"}`} title={`${label}${via}`}>
      {label}
      {via && <span className="ats-via">{via}</span>}
    </span>
  );
}
