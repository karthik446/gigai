import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { getNewPeek, getPostings, postAssessThese, postMarkAllSeen } from "../api.js";
import AssessApprovalDialog from "../components/AssessApprovalDialog.jsx";
import SourcesStrip from "../components/SourcesStrip.jsx";
import { useSourcesStatus } from "../components/SourcesUpdatePanel.jsx";
import { inProgressCount } from "../jobStateModel.js";
import {
  EMPTY_FILTER,
  PAGE_ROWS,
  PROFILE_FILTER_KEY,
  REMOVED_FILTER,
  STATE_FILTERS,
  approvalDialog,
  assessAskBody,
  assessOutcomeLine,
  countLine,
  detailLine,
  hasFilter,
  isNew,
  keepActiveProfiles,
  needsAnswers,
  newCountFromPeek,
  postingsQuery,
  profileChips,
  profileTags,
  rowChips,
  scoreText,
  secondProfiles,
  timeChips,
  toggleProfile,
  toggleState,
  toggleWindow,
} from "../postingsModel.js";
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
//   state chips     needs your answers, assessed, Scout label recommended;
//                   "Removed" lists what the boards no longer show
//   Assess these    the selected rows, else the filter. The server is asked
//                   first (count and estimate); nothing is assessed until the
//                   approval dialog's Approve
//   per row         open its job page; "Assess as <profile>" for another
//                   profile it matches (the same question and approval)
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
  return (
    <li className={`posting-row${row.removed_at ? " removed" : ""}`} data-testid="job-row" data-state={row.state}>
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
        {details && <div className="posting-detail">{details}</div>}
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

export default function JobsView({ selectedProfileId, onSelectProfile, applicationsState, onRows, onCounts }) {
  const [filter, setFilter] = useState(() => ({ ...EMPTY_FILTER, profileIds: storedProfileFilter() }));
  const [search, setSearch] = useState("");
  const [response, setResponse] = useState(null);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState(null);
  const [newCount, setNewCount] = useState(null);
  const [selectedIds, setSelectedIds] = useState([]);
  // The approval: {dialog} while the question is open; nothing is assessed before its Approve.
  const [approval, setApproval] = useState(null);
  const [asking, setAsking] = useState(false);
  const [approving, setApproving] = useState(false);
  const [approvalError, setApprovalError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [marking, setMarking] = useState(false);
  const request = useRef(0);
  const sources = useSourcesStatus({ enabled: true });

  const readPeek = useCallback(() => {
    getNewPeek()
      .then((peek) => setNewCount(newCountFromPeek(peek)))
      .catch(() => setNewCount(null));
  }, []);

  const load = useCallback(
    (shownFilter) => {
      const id = (request.current += 1);
      setLoading(true);
      setError(null);
      getPostings(postingsQuery(shownFilter, { limit: PAGE_ROWS, offset: 0 }))
        .then((loaded) => {
          if (request.current !== id) {
            return;
          }
          setResponse(loaded);
          setRows(loaded.postings.rows);
          setLoading(false);
          onRows(loaded.postings.rows);
          if (!hasFilter(shownFilter)) {
            onCounts(needsAnswers(loaded.counts));
          }
        })
        .catch((err) => {
          if (request.current !== id) {
            return;
          }
          setLoading(false);
          // A remembered profile that is no longer active: drop the filter and read again.
          if (err.code === "profile_not_found" && shownFilter.profileIds.length) {
            rememberProfileFilter([]);
            setFilter((current) => ({ ...current, profileIds: [] }));
            return;
          }
          setError(err.message || String(err));
        });
    },
    [onRows, onCounts],
  );

  useEffect(() => {
    load(filter);
    setSelectedIds([]);
  }, [filter, load]);

  useEffect(readPeek, [readPeek]);

  // The search box filters after a pause, not on every key.
  useEffect(() => {
    const timer = setTimeout(() => setFilter((current) => (current.query === search ? current : { ...current, query: search })), 350);
    return () => clearTimeout(timer);
  }, [search]);

  const profiles = response ? response.profiles : [];
  const counts = response ? response.counts : null;
  const anchor = response ? response.anchor : null;

  const setProfiles = (profileId) =>
    setFilter((current) => {
      const next = toggleProfile(keepActiveProfiles(current.profileIds, profiles), profileId);
      rememberProfileFilter(next);
      return { ...current, profileIds: next };
    });

  const showMore = () => {
    const id = request.current;
    setLoadingMore(true);
    getPostings(postingsQuery(filter, { limit: PAGE_ROWS, offset: rows.length }))
      .then((loaded) => {
        if (request.current !== id) {
          return;
        }
        setRows((current) => current.concat(loaded.postings.rows));
        onRows(loaded.postings.rows);
      })
      .catch((err) => setError(err.message || String(err)))
      .finally(() => setLoadingMore(false));
  };

  const markAllSeen = () => {
    setMarking(true);
    setNotice(null);
    postMarkAllSeen()
      .then(() => {
        readPeek();
        load(filter);
      })
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
    postAssessThese(approval.dialog.approveBody)
      .then((answer) => {
        setApproval(null);
        setNotice(assessOutcomeLine(answer));
        setSelectedIds([]);
        readPeek();
        load(filter);
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
  const busy = asking || approving;

  return (
    <div>
      <div className="summary-strip jobs-summary" aria-label="Summary">
        <Tile label="Postings" value={counts ? matched : "…"} />
        <Tile label="New since last check" value={newCount === null ? "–" : newCount} />
        <Tile label="Need your answers" value={counts ? needsAnswers(counts) : "…"} />
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
        <SourcesStrip strip={strip} read={sources.read} />
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
                {STATE_FILTERS.map((option) => (
                  <button
                    key={option.value}
                    type="button"
                    className={`chip${filter.states.includes(option.value) ? " active" : ""}`}
                    aria-pressed={filter.states.includes(option.value)}
                    data-state={option.value}
                    onClick={() => setFilter((current) => ({ ...current, states: toggleState(current.states, option.value) }))}
                  >
                    {option.label}
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
          </div>
          <div className="result-count">
            <span data-role="postings-count">
              {loading ? "Loading postings…" : countLine(counts, rows.length)}
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
                      setFilter(EMPTY_FILTER);
                    }}
                  >
                    Clear filters
                  </button>
                </>
              )}
            </span>
          </div>
        </div>
      </section>

      {error && <div className="callout danger">Could not load the postings: {error}</div>}
      {notice && (
        <div className="callout info" role="status" data-role="assess-notice">
          {notice}
        </div>
      )}

      <ul className="posting-list" data-testid="jobs-list">
        {rows.map((row) => (
          <PostingRow
            key={row.job_identity}
            row={row}
            profiles={profiles}
            anchor={anchor}
            selected={selectedIds.includes(row.job_identity)}
            onSelect={select}
            onOpen={open}
            onAssessAs={(target, profileId) => ask(assessAskBody({ selectedIds: [target.job_identity], profileId }))}
            busy={busy}
          />
        ))}
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
      {!loading && rows.length < matched && (
        <div className="show-more" data-role="show-more">
          <button type="button" className="button secondary" disabled={loadingMore} onClick={showMore}>
            {loadingMore ? "Loading…" : `Show ${Math.min(PAGE_ROWS, matched - rows.length)} more`}
          </button>
        </div>
      )}

      {approval && (
        <AssessApprovalDialog
          dialog={approval.dialog}
          submitting={approving}
          error={approvalError}
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
