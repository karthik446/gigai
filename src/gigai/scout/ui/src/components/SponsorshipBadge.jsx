import { sponsorshipLabel } from "../display.js";

// `sponsorship` is a SponsorshipStatus value ("offered" | "not_offered" |
// "unknown") or null/undefined when not derived; all three render, since an
// operator filtering on sponsorship needs to see "unknown" as its own state
// rather than a blank cell.
export default function SponsorshipBadge({ sponsorship }) {
  const status = sponsorship || "unknown";
  return <span className={`status-badge sponsorship-${status}`}>{sponsorshipLabel(sponsorship)}</span>;
}
