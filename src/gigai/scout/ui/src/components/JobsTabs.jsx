import { useRef } from "react";
import { JOBS_TABS, tabForKey, tabHash } from "../jobsTabsModel.js";
import { navigate } from "../routing.js";

// 0.1.11.8 N3: the two tabs at the top of the Jobs page, "Your jobs" and "Search". Each is a plain link (the tab is
// the address: #/jobs, #/jobs/search), so a reload and Back / Forward keep it. role=tablist / tab with aria-selected
// and aria-controls; the selected tab alone is in the Tab order, and the arrow keys, Home and End move to the other
// one (and open it). `listHash`: where "Your jobs" goes back to (the list's page and filters).
export default function JobsTabs({ tab, listHash }) {
  const links = useRef({});

  const onKeyDown = (event) => {
    const next = tabForKey(tab, event.key);
    if (!next) {
      return;
    }
    event.preventDefault();
    if (next !== tab) {
      navigate(tabHash(next, listHash));
    }
    if (links.current[next]) {
      links.current[next].focus();
    }
  };

  return (
    <div className="jobs-tabs" role="tablist" aria-label="Jobs" data-testid="jobs-tabs" data-tab={tab} onKeyDown={onKeyDown}>
      {JOBS_TABS.map((entry) => {
        const selected = entry.tab === tab;
        return (
          <a
            key={entry.tab}
            ref={(node) => {
              links.current[entry.tab] = node;
            }}
            className={`jobs-tab${selected ? " active" : ""}`}
            role="tab"
            id={entry.id}
            href={tabHash(entry.tab, listHash)}
            aria-selected={selected}
            aria-controls={selected ? entry.panelId : undefined}
            tabIndex={selected ? 0 : -1}
            data-testid={`jobs-tab-${entry.tab}`}
          >
            {entry.label}
          </a>
        );
      })}
    </div>
  );
}
