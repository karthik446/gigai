import { resumeWarningText } from "../resumeWarning.js";

// 0.1.10-001: the amber personal-info warning, on every resume-entry surface.
export default function ResumeWarning({ modelTarget }) {
  return (
    <div className="callout warn" role="note" data-role="resume-warning">
      {resumeWarningText(modelTarget)}
    </div>
  );
}
