import { sponsorshipLabel } from "../display.js";
import { h1bLabel } from "../jobModel.js";

// `sponsorship` is a SponsorshipStatus value ("offered" | "not_offered" |
// "unknown") or null/undefined when not derived; all three render, since an
// operator filtering on sponsorship needs to see "unknown" as its own state
// rather than a blank cell.
//
// Q4b: `h1b` (rows[].h1b {approvals, fiscal_years}, the company catalog's
// H-1B join added by Q4b-data) is appended ONLY when the posting itself is
// silent ("unknown") and the count is a positive number -- "Unknown · 9 H-1B
// approvals (FY2026)". A stated "Sponsors visas"/"No sponsorship" never
// shows it; no record or zero approvals shows plain "Unknown", never a
// placeholder; `h1b.denials`, when present, goes in the tooltip only.
// (The chip itself renders only when visa_sponsorship_required is true;
// the callers gate that.)
export default function SponsorshipBadge({ sponsorship, h1b }) {
  const status = sponsorship || "unknown";
  let label = sponsorshipLabel(sponsorship);
  let title = status === "unknown" ? "The posting does not mention sponsorship" : "Stated in the posting";
  const suffix = status === "unknown" ? h1bLabel(h1b) : null;
  if (suffix) {
    label = `${label} · ${suffix}`;
    title = `${title}; H-1B approvals from the company catalog (USCIS)`;
    if (typeof h1b.denials === "number") {
      title = `${title}, ${h1b.denials} denial${h1b.denials === 1 ? "" : "s"} in the same period`;
    }
  }
  return (
    <span className={`status-badge sponsorship-${status}`} title={title}>
      {label}
    </span>
  );
}
