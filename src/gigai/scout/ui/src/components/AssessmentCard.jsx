import AssessmentBody from "./AssessmentBody.jsx";
import SponsorshipBadge from "./SponsorshipBadge.jsx";
import { displayCompanyName } from "../display.js";

// Kept standalone for callers that already have a complete AssessmentResult
// in hand (its body now delegates to AssessmentBody.jsx, shared with the
// progressive PostingCard -- see B4).
export default function AssessmentCard({ assessment, posting }) {
  return (
    <details className="assessment-card">
      <summary>
        {posting ? `${posting.title} · ${displayCompanyName(posting.company)}` : assessment.posting.normalized_url}
        <SponsorshipBadge sponsorship={assessment.sponsorship || (posting && posting.sponsorship)} />
      </summary>

      <AssessmentBody assessment={assessment} />
    </details>
  );
}
