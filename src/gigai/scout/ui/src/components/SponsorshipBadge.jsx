import { sponsorshipChip } from "../jobModel.js";

// `sponsorship` is a SponsorshipStatus value ("offered" | "not_offered" |
// "unknown") or null/undefined when not derived; all three render, since an
// operator filtering on sponsorship needs to see a silent posting as its
// own state rather than a blank cell.
//
// Q4b: `h1b` (rows[].h1b {approvals, fiscal_years}, the company catalog's
// H-1B join added by Q4b-data) is appended ONLY when the posting itself is
// silent and the count is a positive number. uat-batch1 (N9): the silent
// state reads "Sponsorship not stated" ("Unknown · 32 H-1B approvals" read
// as a contradiction: the posting is silent, the approvals are the
// company's record), and the chip takes the positive tone when the company
// has approvals. A stated "Sponsors visas"/"No sponsorship" never shows the
// count; no record or zero approvals shows the plain label, never a
// placeholder; `h1b.denials`, when present, goes in the tooltip only.
// (The chip itself renders only when visa_sponsorship_required is true;
// the callers gate that.)
export default function SponsorshipBadge({ sponsorship, h1b }) {
  const chip = sponsorshipChip(sponsorship, h1b);
  return (
    <span className={`status-badge sponsorship-${chip.status}${chip.positive ? " h1b-positive" : ""}`} title={chip.title}>
      {chip.label}
    </span>
  );
}
