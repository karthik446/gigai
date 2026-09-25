import { resumeDisplayLabel, resumeIdsTooltip } from "../display.js";

// Q1 (rolling window): exactly one publication cutoff rule
// (FindJobsConfig, contracts.py): a fixed `published_after` date wins when
// set; else the rolling `max_age_days` ("Last N days"); else no limit.
export function publicationWindowLabel(config) {
  if (config.published_after) {
    return `After ${config.published_after}`;
  }
  if (typeof config.max_age_days === "number") {
    return `Last ${config.max_age_days} days`;
  }
  return "no limit";
}

function SourceList({ sources }) {
  const active = Object.entries(sources)
    .filter(([, enabled]) => enabled)
    .map(([name]) => name);
  if (active.length === 0) {
    return <span className="muted">none enabled</span>;
  }
  return (
    <div className="tag-list">
      {active.map((name) => (
        <span className="tag" key={name}>
          {name}
        </span>
      ))}
    </div>
  );
}

export default function ConfigPanel({ config, resumePreview, resumeLabel, resumeCreatedAt, resumeMissingHint }) {
  return (
    <section className="panel">
      <h2>Configuration</h2>
      <div className="field-row">
        <div className="field">
          <div className="label">Roles</div>
          <div className="tag-list">
            {config.roles.map((role) => (
              <span className="tag" key={role}>
                {role}
              </span>
            ))}
          </div>
        </div>
        <div className="field">
          <div className="label">Queries</div>
          <div className="tag-list">
            {config.merged_queries.map((query) => (
              <span className="tag" key={query}>
                {query}
              </span>
            ))}
          </div>
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <div className="label">Location</div>
          <div className="value">{config.location || "any"}</div>
        </div>
        <div className="field">
          <div className="label">Remote</div>
          <div className="value">{config.remote ? "yes" : "no"}</div>
        </div>
        <div className="field">
          <div className="label">Publication window</div>
          <div className="value">{publicationWindowLabel(config)}</div>
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <div className="label">Sources</div>
          <SourceList sources={config.sources} />
        </div>
        <div className="field">
          <div className="label">Default cap</div>
          <div className="value">{config.default_assess_cap}</div>
        </div>
        <div className="field">
          <div className="label">Default model target</div>
          <div className="value">{config.default_model_target}</div>
        </div>
      </div>
      <div className="field-row">
        <div className="field">
          <div className="label">Resume</div>
          {resumePreview ? (
            <div className="value" title={resumeIdsTooltip(resumePreview)}>
              {resumeDisplayLabel(resumePreview, resumeLabel, resumeCreatedAt)}
            </div>
          ) : (
            <div className="callout warn" style={{ marginBottom: 0 }}>
              No resume saved: run <code>{resumeMissingHint || "gigai scout resume add <file>"}</code>{" "}
              before running.
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
