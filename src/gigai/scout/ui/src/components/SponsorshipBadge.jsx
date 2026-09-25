import { sponsorshipLabel } from "../display.js";

// `sponsorship` is a SponsorshipStatus value ("offered" | "not_offered" |
// "unknown") or null/undefined when not derived; all three render, since an
// operator filtering on sponsorship needs to see "unknown" as its own state
// rather than a blank cell.
//
// Q4a: `h1bFilings` (phase 2 hook: the posting's `h1b_filings_fy2024`, the
// company catalog's H-1B join) is appended ONLY when the posting itself is
// silent ("unknown") and the count is present -- a stated "Sponsors visas"/
// "No sponsorship" never shows it, and a missing count shows plain
// "Unknown", never a placeholder.
export default function SponsorshipBadge({ sponsorship, h1bFilings }) {
  const status = sponsorship || "unknown";
  let label = sponsorshipLabel(sponsorship);
  let title = status === "unknown" ? "The posting does not mention sponsorship" : "Stated in the posting";
  if (status === "unknown" && typeof h1bFilings === "number") {
    label = `${label} · ${h1bFilings} H-1B filing${h1bFilings === 1 ? "" : "s"} (FY2024)`;
    title = `${title}; H-1B count from the company catalog`;
  }
  return (
    <span className={`status-badge sponsorship-${status}`} title={title}>
      {label}
    </span>
  );
}
