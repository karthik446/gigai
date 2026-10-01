import { DATA_SOURCE_DOCS_URL, DATA_SOURCE_NOTE } from "../sourcesStatusModel.js";

// 0110-024/025/026: the quiet lines under the sources strip and in Settings'
// Sources panel. `lines` is sourcesStatusModel.statusLines(status). The
// data-source note follows the starter-snapshot line, the one place that
// data did not come from this machine's own requests.
export default function SourcesStatusLines({ lines }) {
  if (!lines || lines.length === 0) {
    return null;
  }
  return (
    <ul className="sources-status-lines" data-role="sources-status-lines">
      {lines.map((item) => (
        <li key={item.role} data-role={`sources-${item.role}`} data-tone={item.tone}>
          {item.text}
          {item.role === "snapshot" && (
            <>
              {". "}
              {DATA_SOURCE_NOTE}{" "}
              <a href={DATA_SOURCE_DOCS_URL} target="_blank" rel="noreferrer">
                Where this data comes from
              </a>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}
