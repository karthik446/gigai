import { useEffect, useState, useSyncExternalStore } from "react";
import { getFreeSearch, postApplication, postAssess } from "../api.js";
import { reassessErrorText } from "../answersModel.js";
import {
  NOT_RANKED,
  SEARCH_COPIES_RULE,
  US_ONLY_WITH_SHOW_ALL,
  appliedRow,
  applyRequest,
  assessLabel,
  assessRequest,
  assessedRow,
  canApply,
  canAssess,
  canSaveAsProfile,
  canSearch,
  defaultProfileOf,
  errorText,
  hiddenLabel,
  noMatchLine,
  profileDraft,
  profileNames,
  rowJobId,
  rowLabels,
  rowLocation,
  sameSearch,
  searchUsOnlyChecked,
  showAllLabel,
  shownLine,
  totalLine,
} from "../freeSearchModel.js";
import { createFreeSearchStore, keepProfileDraft } from "../freeSearchStore.js";
import { eventActionLabel } from "../jobStateModel.js";
import { CANONICAL_RULE, COPIES_RULE, US_ONLY_LABEL, US_ONLY_RULE, copiesTag, postedLine } from "../postingsModel.js";
import { REQUIREMENTS_UNREADABLE_TEXT, isRequirementsUnreadable } from "../rankModel.js";
import { SETTINGS_HASH, jobHash, navigate } from "../routing.js";
import { displayCompanyName } from "../display.js";

// 0.1.11.7 FS2: "Search all jobs" on the Jobs page (freeSearchModel.js has the rules, freeSearchStore.js what was read).
//
//   the box       a title (a comma separates titles), optional company and location words, and "Show all", which
//                 drops the default profile's location, work mode, countries and posted window. Its label says what
//                 those are ("remote, US, last 30 days"), read from the search's own answer
//   US only       0.1.11.8 N1: a checkbox of its own, by the posting's location; on by default for a US setup. It
//                 still applies with Show all until it is unticked (the help line under the switches says the rule)
//   copies        0.1.11.8 N2: the same company, title and description posted more than once is ONE row: one
//                 canonical job (its US posting, else the earliest posted), with every location ("Remote: Estonia,
//                 Lithuania, Latvia +4") and how many postings it stands for; Open, Assess and Mark applied act on
//                 that job. A row US only kept without knowing where it is says "unclear location"
//   the rows      newest posted first, never ranked: company, title, location, the posting's date and its labels (the
//                 profiles whose list holds it, its assessment state, its application). The page comes first and the
//                 total line after it; "Load more" adds the next 50
//   per row       Open (its job page), "Assess · 1 model call · as <default profile>" (asked once more before the
//                 call; POST /api/assess with the default profile's id), "Mark applied" (POST /api/applications)
//   the footer    "Not ranked. Save as a profile to rank.", "Show all N (any place, any date)" when the default
//                 filters hid rows, and "Save this search as a profile" (the new-profile form in Settings, filled in)
//
// The search takes no profile: the profile chips of the list below and the top bar's selector do not change it.
// Nothing is stored by a search; the results live in this tab until it is reloaded.
const store = createFreeSearchStore({ fetchSearch: getFreeSearch });

function SearchRow({ row, names, defaultProfile, usOnly, onOpen, onChanged }) {
  const [asking, setAsking] = useState(false); // the question before the model call
  const [busy, setBusy] = useState(null); // "assess" | "apply"
  const [error, setError] = useState(null);
  const [note, setNote] = useState(null);
  const id = rowJobId(row);
  const posted = postedLine(row);
  const labels = rowLabels(row, names, { usOnly });
  const company = row.company ? displayCompanyName(row.company) : "";
  const place = rowLocation(row);
  const copies = copiesTag(row);
  const details = [company, place].filter((part) => typeof part === "string" && part.trim()).join(" · ");

  const assess = () => {
    setAsking(false);
    setBusy("assess");
    setError(null);
    setNote(null);
    postAssess(assessRequest(row, defaultProfile))
      .then((response) => {
        setNote(`Assessed as ${defaultProfile.label}. Open it to read the result.`);
        onChanged(assessedRow(row, response, defaultProfile), "assessed");
      })
      .catch((err) => setError(isRequirementsUnreadable(err) ? `${REQUIREMENTS_UNREADABLE_TEXT}. It stays not assessed.` : reassessErrorText(err)))
      .finally(() => setBusy(null));
  };

  const apply = () => {
    setBusy("apply");
    setError(null);
    postApplication(applyRequest(row))
      .then(() => onChanged(appliedRow(row, new Date().toISOString()), "applied"))
      .catch((err) => setError(err.message || String(err)))
      .finally(() => setBusy(null));
  };

  return (
    <li className={`posting-row search-row${row.removed ? " removed" : ""}`} data-testid="search-row" data-job={id || undefined} data-copies={copies ? copies.count : 1}>
      <div className="posting-main">
        {id ? (
          <a className="posting-title" href={jobHash(id)} onClick={() => onOpen(row)} data-action="open-search-job">
            {row.title || "(untitled posting)"}
          </a>
        ) : (
          <span className="posting-title">{row.title || "(untitled posting)"}</span>
        )}
        {(details || posted) && (
          <div className="posting-detail">
            <span data-role="search-company">{company}</span>
            {company && place ? " · " : ""}
            <span data-role="search-location">{place}</span>
            {copies && (
              <>
                {" "}
                <span className="tag" data-role="search-copies" title={copies.title}>
                  {copies.label}
                </span>
              </>
            )}
            {details && posted ? " · " : ""}
            {posted && (
              <span data-role="posted" data-kind={posted.kind} data-at={posted.at} title={posted.title}>
                {posted.text} ({posted.date})
              </span>
            )}
          </div>
        )}
        {asking && (
          <div className="callout info search-ask" data-role="search-assess-ask">
            Assess this posting as <strong>{defaultProfile.label}</strong>? One model call; the assessment is stored under that profile.{" "}
            <button type="button" className="button small" data-action="search-assess-confirm" onClick={assess}>
              Assess
            </button>{" "}
            <button type="button" className="button small secondary" data-action="search-assess-cancel" onClick={() => setAsking(false)}>
              Not now
            </button>
          </div>
        )}
        {busy === "assess" && (
          <div className="muted small" role="status" data-role="search-assessing">
            Assessing with the model… this can take a minute.
          </div>
        )}
        {note && (
          <div className="muted small" role="status" data-role="search-row-note">
            {note}
          </div>
        )}
        {error && (
          <div className="field-error" data-role="search-row-error">
            {error}
          </div>
        )}
      </div>
      <div className="posting-chips" data-role="search-labels">
        {labels.map((label) => (
          <span key={`${label.kind}:${label.label}`} className={`state-pill tone-${label.tone}`} data-testid="search-label" data-kind={label.kind} data-status={label.status} title={label.title}>
            {label.label}
          </span>
        ))}
      </div>
      <div className="posting-actions">
        {id && (
          <a className="button small secondary" href={jobHash(id)} onClick={() => onOpen(row)}>
            Open
          </a>
        )}
        {canAssess(row, defaultProfile) && (
          <button type="button" className="link-button" disabled={busy !== null || asking} data-action="search-assess" onClick={() => setAsking(true)}>
            {busy === "assess" ? "Assessing…" : assessLabel(defaultProfile)}
          </button>
        )}
        {canApply(row) && (
          <button type="button" className="link-button" disabled={busy !== null} data-action="search-apply" onClick={apply}>
            {busy === "apply" ? "Saving…" : eventActionLabel("applied")}
          </button>
        )}
      </div>
    </li>
  );
}

export default function FreeSearchPanel({ profiles, onOpenRow, onRowChanged, usOnlyDefault }) {
  const { form, results, loading, loadingMore, counting, error, moreError, defaultsText, usOnlyDefault: knownDefault } = useSyncExternalStore(store.subscribe, store.getState);
  // The setup's US-only default, as the Jobs list below read it: the box shows it before the first search.
  useEffect(() => store.knowUsOnlyDefault(usOnlyDefault), [usOnlyDefault]);
  const usOnly = searchUsOnlyChecked(form, results, knownDefault);
  const defaultProfile = defaultProfileOf(profiles);
  const names = profileNames(results, profiles);
  const shown = results ? shownLine(results) : null;
  const total = results ? totalLine(results) : null;
  const hidden = results ? hiddenLabel(results) : null;
  // The boxes were changed since the rows were read: the rows are of the search the lines name, not of the boxes.
  const edited = Boolean(results) && !sameSearch(form, results.form);

  const changed = (row, what) => {
    store.changeRow(row);
    if (onRowChanged) {
      onRowChanged(row, what);
    }
  };

  const saveAsProfile = () => {
    keepProfileDraft(profileDraft(results));
    navigate(SETTINGS_HASH);
  };

  return (
    <section className="panel free-search" data-testid="free-search" aria-label="Search all jobs">
      <form
        className="filter-row"
        onSubmit={(event) => {
          event.preventDefault();
          store.search();
        }}
      >
        <div className="filter-group filter-search free-search-title">
          <label className="chip-group-label" htmlFor="free-search-title">
            Search all jobs
          </label>
          <input
            id="free-search-title"
            type="search"
            placeholder="Title: senior systems engineer, staff systems engineer"
            value={form.title}
            onChange={(event) => store.setForm({ title: event.target.value })}
            data-testid="free-search-title"
          />
        </div>
        <div className="filter-group filter-search free-search-word">
          <label className="chip-group-label" htmlFor="free-search-company">
            Company (optional)
          </label>
          <input id="free-search-company" type="search" value={form.company} onChange={(event) => store.setForm({ company: event.target.value })} data-testid="free-search-company" />
        </div>
        <div className="filter-group filter-search free-search-word">
          <label className="chip-group-label" htmlFor="free-search-location">
            Location (optional)
          </label>
          <input id="free-search-location" type="search" value={form.location} onChange={(event) => store.setForm({ location: event.target.value })} data-testid="free-search-location" />
        </div>
        <div className="filter-group">
          <button type="submit" className="button" disabled={loading || !canSearch(form)} data-testid="free-search-submit">
            {loading ? "Searching…" : "Search"}
          </button>
        </div>
        {(results || error) && (
          <div className="filter-group">
            <button type="button" className="button secondary" data-testid="free-search-clear" onClick={() => store.clear()}>
              Clear
            </button>
          </div>
        )}
      </form>
      <label className="filter-toggle free-search-all">
        <input type="checkbox" role="switch" checked={form.showAll} onChange={(event) => store.setShowAll(event.target.checked)} data-testid="free-search-show-all" />
        <span data-role="free-search-show-all-label">{showAllLabel(defaultsText)}</span>
      </label>
      <label className="filter-toggle free-search-us-only">
        <input type="checkbox" checked={usOnly} onChange={(event) => store.setUsOnly(event.target.checked)} data-testid="free-search-us-only" />
        <span data-role="free-search-us-only-label">{US_ONLY_LABEL}</span>
      </label>
      <p className="muted small free-search-rules" data-role="free-search-rules">
        {US_ONLY_RULE} {US_ONLY_WITH_SHOW_ALL} {COPIES_RULE} {CANONICAL_RULE} {SEARCH_COPIES_RULE}
      </p>
      {!results && !loading && !error && (
        <p className="muted small free-search-hint" data-role="free-search-hint">
          Searches every stored posting, in a profile's list or in none. A comma separates titles; every word of a typed title must be in the posting's title. The
          profile chips below do not change it, and a search stores nothing.
        </p>
      )}
      {loading && (
        <p className="muted" role="status" data-role="free-search-loading">
          Searching the stored postings…
        </p>
      )}
      {error && (
        <div className={`callout ${error.code === "config_unavailable" ? "info" : "danger"}`} role="alert" data-testid="free-search-error" data-code={error.code || undefined}>
          {errorText(error)}
          {error.code === "config_unavailable" && !form.showAll && (
            <>
              {" "}
              <button type="button" className="button small" data-action="free-search-use-show-all" onClick={() => store.setShowAll(true)}>
                Show all
              </button>
            </>
          )}
        </div>
      )}

      {results && (
        <div className="free-search-results" data-testid="free-search-results" data-rows={results.rows.length} data-source={results.source || undefined}>
          <div className="result-count">
            <span>
              {shown ? <span data-role="free-search-shown">{shown}</span> : <span data-role="free-search-empty">{noMatchLine(results)}</span>}{" "}
              {results.rows.length > 0 &&
                (counting ? (
                  <span data-role="free-search-counting">Counting…</span>
                ) : (
                  total && (
                    <strong data-testid="free-search-total" data-total={results.total}>
                      {total}
                    </strong>
                  )
                ))}
              {edited && <span data-role="free-search-edited"> The boxes changed since: press Search to search again.</span>}
            </span>
          </div>
          {hidden && (
            <div className="result-count">
              <button type="button" className="link-button" data-testid="free-search-show-hidden" onClick={() => store.setShowAll(true)}>
                {hidden}
              </button>
            </div>
          )}
          {results.source === "scan" && (
            <p className="muted small" data-role="free-search-scan">
              The search index did not answer, so every company file was read instead, which is slower. Updating sources builds the index.
            </p>
          )}
          {!results.labelsRead && (
            <p className="muted small" data-role="free-search-no-labels">
              The labels (profiles, assessment, application) could not be read for these rows.
            </p>
          )}

          {results.rows.length > 0 && (
            <ul className="posting-list" data-testid="free-search-list">
              {results.rows.map((row) => (
                <SearchRow key={`${row.company_key}|${row.job_url}`} row={row} names={names} defaultProfile={defaultProfile} usOnly={Boolean(results.usOnly && results.usOnly.on)} onOpen={onOpenRow} onChanged={changed} />
              ))}
            </ul>
          )}
          {results.more && (
            <div className="free-search-more">
              <button type="button" className="button secondary" disabled={loadingMore} data-testid="free-search-more" onClick={() => store.loadMore()}>
                {loadingMore ? "Loading…" : "Load more (the next 50)"}
              </button>
              {moreError && <span className="field-error"> {errorText(moreError)}</span>}
            </div>
          )}

          <div className="free-search-footer" data-testid="free-search-footer">
            {results.rows.length > 0 && <span data-role="free-search-not-ranked">{NOT_RANKED}</span>}
            {results.filters && results.filters.work_mode_note && results.rows.length > 0 && (
              <span className="muted small" data-role="free-search-work-mode-note">
                {results.filters.work_mode_note}
              </span>
            )}
            <button
              type="button"
              className="button small secondary"
              data-testid="save-as-profile"
              disabled={!canSaveAsProfile(results)}
              title={canSaveAsProfile(results) ? "Opens the new-profile form in Settings with these titles filled in. Nothing is created until you create it there." : "Type a title to save this search as a profile."}
              onClick={saveAsProfile}
            >
              Save this search as a profile
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
