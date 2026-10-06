import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { getNewPeek, getPostings, getPostingsStatus, postAssessThese, postMarkAllSeen } from "../api.js";
import AssessApprovalDialog from "../components/AssessApprovalDialog.jsx";
import RankPanel from "../components/RankPanel.jsx";
import SponsorshipBadge from "../components/SponsorshipBadge.jsx";
import SourcesStrip from "../components/SourcesStrip.jsx";
import { useSourcesStatus } from "../components/SourcesUpdatePanel.jsx";
import { inProgressCount } from "../jobStateModel.js";
import { h1bLabel } from "../jobModel.js";
import {
  EMPTY_FILTER,
  ORDER_CHIP,
  PAGE_SIZES,
  PROFILE_FILTER_KEY,
  REMOVED_FILTER,
  approvalDialog,
  approvalBody,
  assessAllBody,
  assessAskBody,
  assessOutcomeLine,
  countLine,
  detailLine,
  hasFilter,
  isNew,
  jobsHash,
  keepActiveProfiles,
  listItems,
  needsAnswers,
  notAssessedLine,
  pageCount,
  pageNumbers,
  parseJobsHash,
  postedLine,
  profileChips,
  profileTags,
  rankingLine,
  rowChips,
  scoreText,
  secondProfiles,
  stateChips,
  timeChips,
  toggleProfile,
  toggleState,
  toggleSort,
  toggleWindow,
} from "../postingsModel.js";
import { createPostingsStore, waitingLine } from "../postingsStore.js";
import { APPLICATIONS_HASH, RUNS_HASH, jobHash } from "../routing.js";
import { sourcesStrip } from "../sourcesStripModel.js";

// 0.1.10.7 M4b: Jobs, by posting (#/jobs). No run: the rows are the stored
// postings every active profile matches (GET /api/postings), each tagged with
// the profiles it matches, best first, and shown for its best profile (or
// the one profile the filter names).
//
//   profile chips   a FILTER (several may be on), never a mode; remembered in
//                   this browser only
//   time chips      "New since last check (N)" / "7 days" / "30 days", one at
//                   a time; N is the PEEK's count (GET /api/new never moves
//                   the anchor). "Mark all seen" moves it (POST /api/new/seen)
//   state chips     needs your answers, assessed, Scout label recommended, weak fit
//                   (0110-10-02: off by default, and weak fits are listed only
//                   while it is on; the chip shows how many there are);
//                   "Removed" lists what the boards no longer show
//   order chip      0110-10-14: "Newest posted" (off by default: the best
//                   fit first). On, the server orders by the day the posting
//                   went up (`sort=newest_posted`); it is not a filter
//   count lines     0.1.11.2: "N not
//                   assessed" with "Assess all" (the same question and
//                   approval dialog: the top 50 by rank, the estimate, what is
//                   left), and "Ranking is still running: X of Y ranked"
//   Assess these    the selected rows, else the filter. The server is asked
//                   first (count and estimate); nothing is assessed until the
//                   approval dialog's Approve
//   per row         open its job page; "Assess as <profile>" for another
//                   profile it matches (the same question and approval).
//                   0110-10-14: its date, beside company and location:
//                   "posted 10 days ago" (the board's date), "updated ..."
//                   (a board that gives only its last change) or "first seen
//                   ..." (no board date), the exact day on hover
//                   (postingsModel.postedLine)
//
// Everything a row shows is the posting's own text, a code or a number, and
// is drawn as text. Old find-jobs runs are history: "Past runs".
function storedProfileFilter() {
  try {
    const value = JSON.parse(window.localStorage.getItem(PROFILE_FILTER_KEY) || "[]");
    return Array.isArray(value) ? value.filter((item) => typeof item === "string") : [];
  } catch {
    return [];
  }
}

function rememberProfileFilter(ids) {
  try {
    window.localStorage.setItem(PROFILE_FILTER_KEY, JSON.stringify(ids));
  } catch {
    /* a private window: the filter lasts for this page only */
  }
}

function Tile({ label, value, href, testId }) {
  const body = (
    <>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
    </>
  );
  return href ? (
    <a className="stat-tile stat-link" href={href} data-testid={testId}>
      {body}
    </a>
  ) : (
    <div className="stat-tile" data-testid={testId}>
      {body}
    </div>
  );
}

function PostingRow({ row, profiles, anchor, selected, onSelect, onOpen, onAssessAs, busy }) {
  const tags = profileTags(row, profiles);
  const others = secondProfiles(row, profiles);
  const details = detailLine(row);
  const posted = postedLine(row);
  return (
    <li
      className={`posting-row${row.removed_at ? " removed" : ""}`}
      data-testid="job-row"
      data-state={row.state}
      data-fit={typeof row.fit === "number" ? row.fit : undefined}
      data-rank={typeof row.rank_score === "number" ? row.rank_score : undefined}
    >
      <input
        type="checkbox"
        className="posting-select"
        aria-label={`Select ${row.title || "this posting"}`}
        checked={selected}
        disabled={Boolean(row.removed_at)}
        onChange={(event) => onSelect(row.job_identity, event.target.checked)}
      />
      <div className="posting-main">
        <a className="posting-title" href={jobHash(row.job_identity)} onClick={() => onOpen(row)} data-action="open-job">
          {row.title || "(untitled posting)"}
        </a>
        {isNew(row, anchor) && (
          <span className="tag posting-new" data-role="new-tag">
            New
          </span>
        )}
        {(details || posted) && (
          <div className="posting-detail">
            {details}
            {details && posted ? " · " : ""}
            {posted && (
              <span data-role="posted" data-kind={posted.kind} data-at={posted.at} title={posted.title}>
                {posted.text}
              </span>
            )}
          </div>
        )}
        <div className="posting-tags" data-role="profile-tags">
          {tags.map((tag) => (
            <span
              key={tag.profileId}
              className={`profile-tag${tag.shown ? " shown" : ""}`}
              data-testid="profile-chip"
              data-best={tag.best ? "true" : undefined}
              title={tag.shown ? "This row shows this profile's state" : "This posting also matches this profile"}
            >
              {tag.label}
            </span>
          ))}
        </div>
      </div>
      <div className="posting-score" data-role="score">
        {scoreText(row)}
      </div>
      <div className="posting-chips" data-role="state-chips">
        {h1bLabel(row.h1b) && <SponsorshipBadge sponsorship={row.sponsorship} h1b={row.h1b} />}
        {rowChips(row).map((chip) => (
          <span key={`${chip.kind}:${chip.label}`} className={`state-pill tone-${chip.tone}`} data-testid={chip.testId} data-kind={chip.kind} title={chip.title}>
            {chip.label}
          </span>
        ))}
      </div>
      <div className="posting-actions">
        <a className="button small secondary" href={jobHash(row.job_identity)} onClick={() => onOpen(row)}>
          Open
        </a>
        {!row.removed_at &&
          others.map((tag) => (
            <button key={tag.profileId} type="button" className="link-button" disabled={busy} data-action="assess-as" onClick={() => onAssessAs(row, tag.profileId)}>
              Assess as {tag.label}
            </button>
          ))}
      </div>
    </li>
  );
}

// 0110-9-01: the list lives here, at module level (postingsStore.js), so it
// survives the route changes that unmount this page: opening a job and coming
// back shows the rows that were read, at once, and refreshes them in place.
const postingsStore = createPostingsStore({ fetchPostings: getPostings, fetchPeek: getNewPeek, fetchStatus: getPostingsStatus });

// 0110-10-01: the page, the page size and the filters live in the address (#/jobs?page=3&state=needs_answers), so
// Back from a job lands on the same page and a page can be bookmarked. The hash is the one source: a click writes
// it, and the list is whatever it says.
function currentView({ mounting = false } = {}) {
  const parsed = parseJobsHash(window.location.hash);
  if (parsed.bare) {
    // A plain #/jobs coming in (the job page's arrow): the page the list was left on, else the remembered profiles.
    // The Jobs tab clicked while the list is shown is a fresh start: page 1.
    const left = mounting ? postingsStore.lastView() : null;
    if (left) {
      return { filter: left.filter, page: left.page, size: left.size };
    }
    return { filter: { ...EMPTY_FILTER, profileIds: storedProfileFilter() }, page: 1, size: parsed.size };
  }
  return { filter: parsed.filter, page: parsed.page, size: parsed.size };
}

// The scroll position each page was left at (this tab only), put back when the page is shown again.
const scrollAt = new Map();
const JOBS_LIST_HASH = /^#\/jobs\/?(\?.*)?$/;

function Pager({ page, pages, size, onPage, onSize }) {
  return (
    <nav className="pager" aria-label="Pages" data-testid="pager" data-page={page} data-pages={pages}>
      <button type="button" className="button small secondary" data-testid="pager-prev" disabled={page <= 1} onClick={() => onPage(page - 1)}>
        Prev
      </button>
      <span className="pager-numbers">
        {pageNumbers(page, pages).map((number, index) =>
          number === "…" ? (
            <span key={`gap-${index}`} className="pager-gap" aria-hidden="true">
              …
            </span>
          ) : (
            <button
              key={number}
              type="button"
              className={`pager-number${number === page ? " active" : ""}`}
              data-testid="pager-page"
              data-page={number}
              aria-current={number === page ? "page" : undefined}
              onClick={() => onPage(number)}
            >
              {number}
            </button>
          ),
        )}
      </span>
      <button type="button" className="button small secondary" data-testid="pager-next" disabled={page >= pages} onClick={() => onPage(page + 1)}>
        Next
      </button>
      <label className="pager-size">
        Per page{" "}
        <select value={size} data-testid="pager-size" onChange={(event) => onSize(Number(event.target.value))}>
          {PAGE_SIZES.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
    </nav>
  );
}

export default function JobsView({ selectedProfileId, onSelectProfile, applicationsState, onRows, onCounts }) {
  // The page, its size and the filters, from the address.
  const [place, setPlace] = useState(() => currentView({ mounting: true }));
  const { filter, page, size } = place;
  const placeRef = useRef(place);
  placeRef.current = place;
  const [search, setSearch] = useState(() => filter.query || "");
  const listed = useSyncExternalStore(postingsStore.subscribe, postingsStore.getState);
  const { response, rows, loading, error, newCount } = listed;
  const [selectedIds, setSelectedIds] = useState([]);
  // The approval: {dialog} while the question is open; nothing is assessed before its Approve.
  const [approval, setApproval] = useState(null);
  // 0110-10-02: the dialog's second question (the low-ranked postings), off until ticked.
  const [includeLowRank, setIncludeLowRank] = useState(false);
  const [asking, setAsking] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approvalError, setApprovalError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [marking, setMarking] = useState(false);
  const sources = useSourcesStatus({ enabled: true });

  // The scroll position to put back once the rows of the page shown are there (0: the top of a page not seen before).
  const restore = useRef(scrollAt.get(jobsHash(filter, page, size)) ?? null);
  const adopt = useCallback((next) => {
    restore.current = scrollAt.get(jobsHash(next.filter, next.page, next.size)) ?? 0;
    setPlace(next);
  }, []);

  // Every move writes the address; Back and a bookmark read it. `replace`: a filter or a size is not a history step.
  const go = useCallback(
    (next, { replace = false } = {}) => {
      const hash = jobsHash(next.filter, next.page, next.size);
      if (replace || window.location.hash === hash) {
        window.history.replaceState(window.history.state, "", hash);
        adopt(next);
      } else {
        window.location.hash = hash; // the hashchange below reads it back
      }
    },
    [adopt],
  );

  useEffect(() => {
    const onChange = () => {
      if (JOBS_LIST_HASH.test(window.location.hash)) {
        adopt(currentView());
      }
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, [adopt]);

  // A plain #/jobs that was filled in from the remembered view: the address says what the page shows.
  useEffect(() => {
    const hash = jobsHash(filter, page, size);
    if (/^#\/jobs\/?$/.test(window.location.hash) && hash !== "#/jobs") {
      window.history.replaceState(window.history.state, "", hash);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The kept rows are shown at once; one read goes out unless one is in flight or the kept one is fresh.
  useEffect(() => {
    postingsStore.show(filter, { page, size });
    setSelectedIds([]);
  }, [filter, page, size]);

  // The scroll position of a page is remembered while it is shown.
  useEffect(() => {
    const onScroll = () => scrollAt.set(jobsHash(filter, page, size), window.scrollY);
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, [filter, page, size]);
  useLayoutEffect(() => {
    if (restore.current !== null && rows.length > 0) {
      window.scrollTo(0, restore.current);
      restore.current = null;
    }
  }, [rows]);

  useEffect(() => {
    postingsStore.peekNew();
    // Leaving the page: nothing stays in flight and nothing keeps polling; the rows that were read stay.
    return () => postingsStore.release();
  }, []);

  // What the list read goes up to the app: the rows (a job page is built from its row) and the waiting count.
  useEffect(() => {
    if (!response) {
      return;
    }
    onRows(rows);
    if (!hasFilter(filter)) {
      onCounts(needsAnswers(response.counts));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [response, rows, onRows, onCounts]);

  // A remembered profile that is no longer active: drop the filter and read again.
  useEffect(() => {
    if (listed.errorCode === "profile_not_found" && filter.profileIds.length) {
      rememberProfileFilter([]);
      setFilter((current) => ({ ...current, profileIds: [] }));
    }
  }, [listed.errorCode, filter.profileIds.length]);

  // A chip or the search changes the list: back to page 1 (the filter is part of the address, not a history step).
  const setFilter = useCallback(
    (update) => {
      const current = placeRef.current;
      const next = typeof update === "function" ? update(current.filter) : update;
      if (next !== current.filter) {
        go({ filter: next, page: 1, size: current.size }, { replace: true });
      }
    },
    [go],
  );

  // The search box filters after a pause, not on every key.
  useEffect(() => {
    const timer = setTimeout(() => setFilter((current) => (current.query === search ? current : { ...current, query: search })), 350);
    return () => clearTimeout(timer);
  }, [search, setFilter]);

  // Back or a bookmark brought another search: the box says it.
  useEffect(() => {
    setSearch(filter.query || "");
  }, [filter.query]);

  const profiles = response ? response.profiles : [];
  const counts = response ? response.counts : null;
  const anchor = response ? response.anchor : null;

  const setProfiles = (profileId) =>
    setFilter((current) => {
      const next = toggleProfile(keepActiveProfiles(current.profileIds, profiles), profileId);
      rememberProfileFilter(next);
      return { ...current, profileIds: next };
    });


  const markAllSeen = () => {
    setMarking(true);
    setNotice(null);
    postMarkAllSeen()
      .then(() => postingsStore.refresh(filter, { page, size }))
      .catch((err) => setNotice(err.message || String(err)))
      .finally(() => setMarking(false));
  };

  // The ASK: the server answers the count and the estimate; no model call.
  const ask = (body) => {
    setAsking(true);
    setNotice(null);
    setApprovalError(null);
    postAssessThese(body)
      .then((answer) => {
        const dialog = approvalDialog(answer, profiles);
        if (dialog) {
          setIncludeLowRank(false);
          setApproval({ dialog });
        } else {
          setNotice(assessOutcomeLine(answer));
        }
      })
      .catch((err) => setNotice(err.detail || err.message || String(err)))
      .finally(() => setAsking(false));
  };

  const approve = () => {
    setApproving(true);
    setApprovalError(null);
    postAssessThese(approvalBody(approval.dialog, includeLowRank))
      .then((answer) => {
        setApproval(null);
        setNotice(assessOutcomeLine(answer));
        setSelectedIds([]);
        postingsStore.refresh(filter, { page, size });
      })
      .catch((err) => setApprovalError(err.detail || err.message || String(err)))
      .finally(() => setApproving(false));
  };

  const select = (id, on) => setSelectedIds((current) => (on ? (current.includes(id) ? current : current.concat(id)) : current.filter((item) => item !== id)));
  const open = (row) => {
    // A job page shows one profile's view of the job: the one this row shows.
    if (row.profile_id && row.profile_id !== selectedProfileId) {
      onSelectProfile(row.profile_id);
    }
  };

  const strip = sourcesStrip(sources.status, { hasRun: true });
  const inProgress = useMemo(() => inProgressCount(applicationsState.applications || []), [applicationsState.applications]);
  const matched = counts ? counts.matched : 0;
  const pages = pageCount(matched, size);
  // The list got shorter than the address says (a bookmark, a refresh): the last page there is.
  useEffect(() => {
    if (response && !loading && !listed.refreshing && matched > 0 && rows.length === 0 && page > pages) {
      go({ filter, page: pages, size }, { replace: true });
    }
  }, [response, loading, listed.refreshing, matched, rows.length, page, pages, filter, size, go]);
  // The header counts are totals: with a filter on, the unfiltered total last read stays.
  const totalRef = useRef(null);
  if (counts && !hasFilter(filter)) {
    totalRef.current = { matched: counts.matched, needsAnswers: needsAnswers(counts) };
  }
  const totals = counts ? (hasFilter(filter) && totalRef.current ? totalRef.current : { matched, needsAnswers: needsAnswers(counts) }) : null;
  const busy = asking || approving;
  // 0.1.11.2: every row is listed; a plain "Ranked low (N)" divider stands above the ranked-low ones (never a collapse).
  const items = listItems(rows, counts, filter.sort);
  const waitingAssess = filter.removed ? null : notAssessedLine(counts);
  const ranking = rankingLine(response && response.ranking);

  return (
    <div>
      <div className="summary-strip jobs-summary" aria-label="Summary">
        <Tile label="Postings" value={totals ? totals.matched : "…"} />
        <Tile label="New since last check" value={newCount === null ? "–" : newCount} />
        <Tile label="Need your answers" value={totals ? totals.needsAnswers : "…"} />
        <Tile label="In progress" value={applicationsState.loading ? "…" : inProgress} href={APPLICATIONS_HASH} />
        <Tile label="History" value="Past runs" href={RUNS_HASH} />
      </div>

      <section className="panel jobs-header">
        <div className="jobs-header-row">
          <h2>Jobs</h2>
          <div className="jobs-header-actions">
            <button type="button" className="button secondary" onClick={markAllSeen} disabled={marking || !newCount} data-testid="mark-all-seen">
              {marking ? "Marking…" : "Mark all seen"}
            </button>
            <button
              type="button"
              className="button"
              onClick={() => ask(assessAskBody({ selectedIds, filter, rows }))}
              disabled={busy || loading || rows.length === 0 || filter.removed}
              data-testid="assess-these"
              title="Asks first: the count and an estimate. Nothing is assessed until you approve."
            >
              {asking ? "Counting…" : selectedIds.length ? `Assess these (${selectedIds.length} selected)` : "Assess these"}
            </button>
          </div>
        </div>
        <SourcesStrip strip={strip} read={sources.read} status={sources.status} />
      </section>

      <section className="panel" style={{ padding: "12px 16px" }}>
        <div className="filter-bar">
          <div className="filter-row">
            <div className="filter-group filter-search">
              <label className="chip-group-label" htmlFor="jobs-filter-search">
                Search
              </label>
              <input id="jobs-filter-search" type="search" placeholder="Title, company, location…" value={search} onChange={(event) => setSearch(event.target.value)} />
            </div>
            <div className="filter-group" data-role="profile-filter">
              <div className="chip-group-label">Profiles</div>
              <div className="chip-list">
                {profileChips(profiles, filter.profileIds).map((chip) => (
                  <button
                    key={chip.profileId}
                    type="button"
                    className={`chip${chip.active ? " active" : ""}`}
                    aria-pressed={chip.active}
                    data-role="profile-filter-chip"
                    onClick={() => setProfiles(chip.profileId)}
                  >
                    {chip.label}
                    {chip.matched !== null && <span className="chip-count"> {chip.matched}</span>}
                  </button>
                ))}
                {profiles.length === 0 && !loading && <span className="muted">No active profile</span>}
              </div>
            </div>
          </div>
          <div className="filter-row">
            <div className="filter-group" data-role="time-filter">
              <div className="chip-group-label">When</div>
              <div className="chip-list">
                {timeChips(newCount, filter.window).map((chip) => (
                  <button
                    key={chip.window}
                    type="button"
                    className={`chip${chip.active ? " active" : ""}`}
                    aria-pressed={chip.active}
                    data-testid={chip.testId}
                    onClick={() => setFilter((current) => ({ ...current, window: toggleWindow(current.window, chip.window) }))}
                  >
                    {chip.label}
                  </button>
                ))}
              </div>
            </div>
            <div className="filter-group" data-role="state-filter">
              <div className="chip-group-label">State</div>
              <div className="chip-list">
                {stateChips(filter.states, counts).map((chip) => (
                  <button
                    key={chip.value}
                    type="button"
                    className={`chip${chip.active ? " active" : ""}`}
                    aria-pressed={chip.active}
                    data-state={chip.value}
                    title={chip.title}
                    onClick={() => setFilter((current) => ({ ...current, states: toggleState(current.states, chip.value) }))}
                  >
                    {chip.label}
                    {chip.count !== null && (
                      <span className="chip-count" data-role="chip-count">
                        {" "}
                        {chip.count}
                      </span>
                    )}
                  </button>
                ))}
                <button
                  type="button"
                  className={`chip${filter.removed ? " active" : ""}`}
                  aria-pressed={filter.removed}
                  data-state={REMOVED_FILTER.value}
                  onClick={() => setFilter((current) => ({ ...current, removed: !current.removed }))}
                >
                  {REMOVED_FILTER.label}
                </button>
              </div>
            </div>
            <div className="filter-group" data-role="order-filter">
              <div className="chip-group-label">Order</div>
              <div className="chip-list">
                <button
                  type="button"
                  className={`chip${filter.sort === ORDER_CHIP.value ? " active" : ""}`}
                  aria-pressed={filter.sort === ORDER_CHIP.value}
                  data-testid="order-chip-newest-posted"
                  title={ORDER_CHIP.title}
                  onClick={() => setFilter((current) => ({ ...current, sort: toggleSort(current.sort) }))}
                >
                  {ORDER_CHIP.label}
                </button>
              </div>
            </div>
          </div>
          <div className="result-count">
            <span data-role="postings-count" data-refreshing={listed.refreshing ? "true" : undefined}>
              {/* 0110-9-01: a message with the percent while the server prepares, never an endless spinner; rows that
                  are there stay while they are refreshed in place. */}
              {waitingLine(listed) || countLine(counts, rows.length, page, size)}
              {hasFilter(filter) && (
                <>
                  {" · "}
                  <button
                    type="button"
                    className="link-button"
                    data-action="clear-filters"
                    onClick={() => {
                      rememberProfileFilter([]);
                      setSearch("");
                      setFilter((current) => ({ ...EMPTY_FILTER, sort: current.sort })); // the order is not a filter
                    }}
                  >
                    Clear filters
                  </button>
                </>
              )}
            </span>
          </div>
          {/* 0.1.11.2: what waits for an assessment, and how far the rank is. */}
          {waitingAssess && (
            <div className="result-count" data-testid="not-assessed-line">
              <span data-role="not-assessed-count">{waitingAssess}</span>{" "}
              <button
                type="button"
                className="button small"
                data-testid="assess-all"
                disabled={busy || loading}
                title="Asks first: the top 50 by rank, the estimate and how many are left. Nothing is assessed until you approve."
                onClick={() => ask(assessAllBody({ filter, rows }))}
              >
                Assess all
              </button>
            </div>
          )}
          {ranking && (
            <div className="result-count muted" data-testid="ranking-line">
              {ranking}
            </div>
          )}
          {/* 0.1.11.2 RANKUI + RANKVIS: the rank row, ALWAYS there: "Ranked X of Y (last 7 days)", "Rank now" and
              "Re-rank latest 100" (cost shown first); "Ranking status unavailable" when the list has no ranking block. */}
          <RankPanel ranking={response && response.ranking} loading={loading && !response && !error} onRefresh={() => postingsStore.refresh(filter, { page, size })} />
        </div>
      </section>

      {error && <div className="callout danger">Could not load the postings: {error}</div>}
      {notice && (
        <div className="callout info" role="status" data-role="assess-notice">
          {notice}
        </div>
      )}

      <ul className="posting-list" data-testid="jobs-list">
        {items.map((item) =>
          item.kind === "divider" ? (
            <li key={item.key} className="posting-divider" data-testid={item.testId}>
              {item.text}
            </li>
          ) : (
            <PostingRow
              key={item.row.job_identity}
              row={item.row}
              profiles={profiles}
              anchor={anchor}
              selected={selectedIds.includes(item.row.job_identity)}
              onSelect={select}
              onOpen={open}
              onAssessAs={(target, profileId) => ask(assessAskBody({ selectedIds: [target.job_identity], profileId }))}
              busy={busy}
            />
          ),
        )}
        {!loading && !error && rows.length === 0 && (
          <li className="empty-state" data-role="jobs-empty">
            {hasFilter(filter)
              ? "No postings match these filters."
              : strip.kind === "empty"
                ? "No postings are stored yet. Update sources above to download them."
                : "No stored posting matches your profiles yet."}
          </li>
        )}
      </ul>
      {matched > PAGE_SIZES[0] && (
        <Pager
          page={page}
          pages={pages}
          size={size}
          onPage={(next) => go({ filter, page: next, size })}
          onSize={(next) => go({ filter, page: 1, size: next }, { replace: true })}
        />
      )}

      {approval && (
        <AssessApprovalDialog
          dialog={approval.dialog}
          submitting={approving}
          error={approvalError}
          includeLowRank={includeLowRank}
          onIncludeLowRank={setIncludeLowRank}
          onApprove={approve}
          onCancel={() => {
            setApproval(null);
            setApprovalError(null);
          }}
        />
      )}
    </div>
  );
}
