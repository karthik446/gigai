-- evals.sqlite version 0 -> 1: a gig's eval run records (gigai 2, V1).
-- Lives at <home>/gigs/<gig>/evals.sqlite (gigai.evals.records.evals_db_path). JSON columns hold canonical JSON text.
-- Column names follow the run-record field table (research D §4): dots become underscores.

-- One eval run: what was measured, on which items, under which pre-registration.
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    gig TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    manifest_digest TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_version TEXT NOT NULL,
    dataset_sha256 TEXT NOT NULL,
    dataset_slice_recipe TEXT NOT NULL,  -- JSON: filter + stratification + count
    dataset_seed INTEGER,  -- the sampling seed; NULL when the slice is not sampled
    prereg TEXT,  -- JSON {primary_metric, guard_metric, pass_rule, margin, n_planned, registered_at}; NULL when none
    step_id TEXT,  -- the step `eval run` ran; NULL for ctx.trace / ctx.model outside `eval run`
    suite TEXT,  -- 'ci', 'release' or 'upgrade' for `eval run`; NULL otherwise
    tag TEXT,  -- the operator's `--tag`
    k INTEGER CHECK (k >= 1),  -- repeats per item
    replay_of TEXT REFERENCES runs (run_id),  -- the run whose traces this run re-scored (`eval run --from-traces`)
    calls_made INTEGER CHECK (calls_made >= 0),  -- adapter calls the run made; set when `eval run` finishes or stops
    created_at TEXT NOT NULL
);

-- One model call (or one direct ctx.trace) of one step on one item: enough to re-score it with no model call.
CREATE TABLE traces (
    trace_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs (run_id),
    step_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_version TEXT NOT NULL,
    cluster_id TEXT,
    repeat_index INTEGER NOT NULL CHECK (repeat_index >= 0),
    attempt_index INTEGER NOT NULL CHECK (attempt_index >= 0),
    prompt_id TEXT,
    prompt_version TEXT,
    prompt_sha256 TEXT,
    input TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    output_raw_text TEXT NOT NULL,
    output_sha256 TEXT NOT NULL,
    output_parsed TEXT,  -- JSON: the typed object after the gig's postprocess
    parse_status TEXT NOT NULL,  -- 'ok', 'invalid', 'not_json' or 'not_parsed'
    model_id TEXT,  -- the configured model target
    model_provider TEXT,  -- the endpoint's adapter kind
    model_endpoint TEXT,
    model_adapter TEXT,  -- the adapter class identity
    model_name TEXT,  -- the model the request named
    resolved_model TEXT,
    adapter_version TEXT,
    argv TEXT NOT NULL,  -- JSON array: the child command line of a CLI adapter, [] otherwise, never a secret
    temperature REAL,
    top_p REAL,
    seed INTEGER,
    output_mode TEXT,
    schema_sha256 TEXT,
    retry_policy TEXT,  -- JSON {attempts, backoff_seconds}
    settings_unsupported TEXT NOT NULL,  -- JSON array: settings the adapter did not carry (their columns stay NULL)
    usage TEXT NOT NULL,  -- JSON {input_tokens, output_tokens, total_tokens, raw}
    latency_ms INTEGER,
    started_at TEXT,
    completed_at TEXT,
    cost_usd REAL,  -- NULL until P12
    outcome TEXT NOT NULL,  -- 'succeeded' or 'failed'
    error TEXT,  -- JSON {code, message, retryable}; NULL when the call succeeded
    recorded_at TEXT NOT NULL
);
CREATE INDEX traces_by_run ON traces (run_id, step_id, item_id, repeat_index, attempt_index);

-- One scorer's verdict on one trace (code scorer or judge).
CREATE TABLE scores (
    score_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL REFERENCES traces (trace_id),
    scorer_id TEXT NOT NULL,
    scorer_version TEXT NOT NULL,
    value REAL NOT NULL,
    explanation TEXT,
    judge_model TEXT,  -- the judge columns are NULL for a code scorer
    judge_prompt_sha256 TEXT,
    position_swapped INTEGER CHECK (position_swapped IN (0, 1)),
    judge_invocation_id TEXT,
    recorded_at TEXT NOT NULL
);
CREATE INDEX scores_by_trace ON scores (trace_id, scorer_id);

-- A reference label for one item (human or dataset), used to score and to validate judges.
CREATE TABLE labels (
    label_id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    item_id TEXT NOT NULL,
    item_version TEXT NOT NULL,
    label TEXT NOT NULL,  -- JSON
    source TEXT NOT NULL,  -- who or what gave the label
    recorded_at TEXT NOT NULL
);
CREATE INDEX labels_by_item ON labels (dataset_id, item_id, item_version);

-- One agreement check of a judge against labels.
CREATE TABLE judge_validations (
    validation_id TEXT PRIMARY KEY,
    scorer_id TEXT NOT NULL,
    scorer_version TEXT NOT NULL,
    judge_model TEXT NOT NULL,
    judge_prompt_sha256 TEXT NOT NULL,
    dataset_id TEXT NOT NULL,
    dataset_sha256 TEXT NOT NULL,
    n_items INTEGER NOT NULL CHECK (n_items >= 0),
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    details TEXT,  -- JSON
    recorded_at TEXT NOT NULL
);
