// 0.1.10.7 K: the one-line notices shown beside Scout's outputs. Each is a
// copy of the constant in src/gigai/scout/wording.py, and
// tests/behaviors/ci_tooling/test_wording_one_source.py fails when one
// differs. The Scout label and Scout ATS sentences are not here: they arrive
// in the server's response (label.wording, ats.wording).

export const VERDICT_WORDING = "Your model's reading of the posting against your resume and answers. Check the posting yourself.";
export const TAILORED_WORDING = "Every line comes from your resume, answers or stories. Read it before you send it.";
export const PRIVACY_PROMISE = "GigAI never stores your name, email, phone, address or links.";
export const PRIVACY_PDF_LINE = "You type them only when you make a PDF, and GigAI forgets them right after.";
// 0.1.10.8: where to run GigAI, shown once before the very first Update
// sources (components/NetworkNotice.jsx). The lead is bold.
export const NETWORK_NOTICE_LEAD = "Run GigAI on your own computer and your own network, not a work laptop or office Wi-Fi.";
export const NETWORK_NOTICE_BODY = "Scout checks about 16,000 public job boards (Greenhouse, Lever, Ashby and six more hiring systems): about 18,000 requests on the first update, and it keeps checking 8 times a day. An employer can see that traffic.";

// The "?" beside each number and label: one docs page, one anchor each
// ("What Scout's numbers and labels mean", gigai-docs scout/numbers.md).
export const NUMBERS_DOCS_URL = "https://karthik446.github.io/gigai/latest/scout/numbers/";
export const HELP_LINKS = {
  rank: { testId: "help-rank", href: `${NUMBERS_DOCS_URL}#rank`, label: "What the rank means" },
  verdict: { testId: "help-verdict", href: `${NUMBERS_DOCS_URL}#verdict`, label: "What the verdict means" },
  "scout-label": { testId: "help-scout-label", href: `${NUMBERS_DOCS_URL}#scout-label`, label: "What the Scout label means" },
  ats: { testId: "help-ats", href: `${NUMBERS_DOCS_URL}#scout-ats-score`, label: "What the Scout ATS score means" },
};

// {testId, href, label} for a topic, or null for one this page does not explain.
export function helpLink(topic) {
  return Object.prototype.hasOwnProperty.call(HELP_LINKS, topic) ? HELP_LINKS[topic] : null;
}
