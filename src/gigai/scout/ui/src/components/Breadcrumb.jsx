// Q4a-nav: "Jobs › Company · Title" on a job page, "Runs › <run>" on a run
// page (replaces phase 1's "← All postings" link). Every crumb but the last
// is a plain <a href="#/…">, so the browser back button still works and a
// crumb is a real link to that view.
export default function Breadcrumb({ crumbs }) {
  return (
    <nav className="breadcrumb" aria-label="Breadcrumb">
      <ol>
        {crumbs.map((crumb, index) => {
          const last = index === crumbs.length - 1;
          return (
            <li key={`${crumb.label}-${index}`}>
              {last || !crumb.href ? (
                <span aria-current={last ? "page" : undefined}>{crumb.label}</span>
              ) : (
                <a href={crumb.href}>{crumb.label}</a>
              )}
              {!last && <span className="crumb-sep" aria-hidden="true">›</span>}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
