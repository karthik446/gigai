import { helpLink } from "../wording.js";

// 0.1.10.7 I3: the "?" beside a number or a label. It opens the docs page
// "What Scout's numbers and labels mean" at that thing's section, in a new
// tab, like the data-source link (SourcesStatusLines).
export default function HelpLink({ topic }) {
  const link = helpLink(topic);
  if (!link) {
    return null;
  }
  return (
    <a className="help-link" href={link.href} target="_blank" rel="noreferrer" title={link.label} aria-label={link.label} data-testid={link.testId}>
      ?
    </a>
  );
}
