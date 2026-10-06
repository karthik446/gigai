import { useCallback, useEffect, useState } from "react";
import { postJobResumePick, postTailoredResumePdf } from "../api.js";
import { APPLY_LABEL, applyState, pickErrorText, shortenedText } from "../jobResumeModel.js";
import GeneratePdfForm from "./GeneratePdfForm.jsx";

// 0.1.11 N6 (SPEC section 6, item 7; D7: "Apply = the PDF. Nothing after
// it."), moved by 0.1.11.3 item 5: ONE button, "Generate PDF", INSIDE the
// "Suggested resume" card, right under its heading (JobResumePanel renders
// this; the page has no separate Apply card at the bottom any more). It opens
// the existing Generate PDF form (GeneratePdfForm.jsx: the name and contact
// details are typed there for this one PDF and never stored) and gives the PDF.
// `visaRequired` (the profile's sponsorship answer) prefills the form's
// optional "Work authorization" line (0.1.11.3 item 6).
//
// With a stale resume it first says so and offers the refresh that is allowed
// ("Re-pick first · no model call", or "Re-assess first · 1 model call" when
// the assessment itself is old) or "Use it as it is".
//
// After the PDF: nothing. This button opens no posting, asks nothing about
// having applied and writes no application record. (The State line's own
// "Mark applied" is the existing Applications feature, untouched.)
export default function ApplyPanel({ state, items, reassess, visaRequired = false }) {
  const { stored } = state;
  const [open, setOpen] = useState(false);
  const [asking, setAsking] = useState(false);
  const apply = applyState({ stored, items });
  const key = `${state.profileId}\n${state.jobIdentity}`;
  useEffect(() => {
    setOpen(false);
    setAsking(false);
  }, [key]);
  const renderPdf = useCallback(
    (header) => postTailoredResumePdf({ profileId: state.profileId, jobIdentity: state.jobIdentity, header }),
    [state.profileId, state.jobIdentity],
  );
  // 0.1.11.3 item 15: the form's "Shorten automatically"; the page then reads the job's resume again.
  const shorten = useCallback(async () => {
    let view;
    try {
      view = await postJobResumePick({ jobUrl: state.jobIdentity, profileId: state.profileId, action: "shorten" });
    } catch (err) {
      throw new Error(pickErrorText(err));
    }
    if (state.reload) {
      state.reload();
    }
    return shortenedText(view);
  }, [state]);
  if (!apply.available) {
    return null;
  }
  const click = () => {
    if (open) {
      setOpen(false);
    } else if (apply.stale) {
      setAsking(true);
    } else {
      setOpen(true);
    }
  };
  const choose = (use) => {
    setAsking(false);
    if (use === "as_is") {
      setOpen(true);
    } else if (use === "repick") {
      state.pick("refresh");
    } else if (reassess && reassess.enabled) {
      reassess.onClick();
    }
  };
  return (
    <div className="resume-apply" id="job-apply" data-testid="job-apply" data-stale={apply.stale ? "true" : undefined}>
      <div className="resume-apply-bar">
        <button type="button" className="button" aria-expanded={open} data-action="apply" disabled={state.picking !== null} onClick={click}>
          {open ? "Close" : APPLY_LABEL}
        </button>
      </div>
      {asking && !open && (
        <div className="callout" data-role="apply-stale">
          <span>{apply.ask}</span>{" "}
          {apply.offers.map((offer) => (
            <button
              key={offer.use}
              type="button"
              className={`button small${offer.use === "as_is" ? " secondary" : ""}`}
              data-action={`apply-${offer.use.replace(/_/g, "-")}`}
              disabled={offer.use === "reassess" && (!reassess || !reassess.enabled)}
              onClick={() => choose(offer.use)}
            >
              {offer.label}
            </button>
          ))}
        </div>
      )}
      {open && <GeneratePdfForm render={renderPdf} visaRequired={visaRequired} shorten={shorten} />}
    </div>
  );
}
