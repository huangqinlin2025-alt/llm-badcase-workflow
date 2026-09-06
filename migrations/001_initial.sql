CREATE TABLE IF NOT EXISTS import_batches (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    status TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS source_files (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    project_id TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    source_type TEXT NOT NULL,
    side TEXT NOT NULL,
    evaluation_version TEXT NOT NULL,
    original_name TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    schema_json TEXT NOT NULL DEFAULT '{}',
    rows_total INTEGER NOT NULL DEFAULT 0,
    rows_parsed INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(project_id, sha256)
);

CREATE TABLE IF NOT EXISTS parse_issues (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    file_id TEXT REFERENCES source_files(id) ON DELETE CASCADE,
    row_no INTEGER,
    code TEXT NOT NULL,
    detail TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS aligned_cases (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    side TEXT NOT NULL,
    evaluation_version TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(batch_id, case_id, side, evaluation_version)
);

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES import_batches(id) ON DELETE RESTRICT,
    candidate_key TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL,
    rule_set_version TEXT NOT NULL,
    scene TEXT NOT NULL,
    severity TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    review_status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
    version INTEGER NOT NULL DEFAULT 1 CHECK(version > 0),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS review_decisions (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(id) ON DELETE RESTRICT,
    action TEXT NOT NULL CHECK(action IN ('APPROVED', 'REJECTED')),
    use_text TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    actor_id TEXT NOT NULL,
    expected_version INTEGER NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sheet_projections (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    target TEXT NOT NULL CHECK(target IN ('BIG_LIBRARY', 'SMALL_LIBRARY')),
    remote_row_id TEXT,
    state TEXT NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    UNIQUE(candidate_id, target)
);

CREATE TABLE IF NOT EXISTS outbox_tasks (
    id TEXT PRIMARY KEY,
    batch_id TEXT REFERENCES import_batches(id) ON DELETE CASCADE,
    candidate_id TEXT REFERENCES candidates(id) ON DELETE CASCADE,
    operation TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING',
    attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
    next_attempt_at TEXT,
    lease_expires_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    batch_id TEXT REFERENCES import_batches(id) ON DELETE SET NULL,
    candidate_id TEXT REFERENCES candidates(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    actor_id TEXT NOT NULL,
    request_id TEXT NOT NULL,
    payload_summary_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_import_batches_project_status ON import_batches(project_id, status);
CREATE INDEX IF NOT EXISTS idx_source_files_batch ON source_files(batch_id);
CREATE INDEX IF NOT EXISTS idx_aligned_cases_batch ON aligned_cases(batch_id);
CREATE INDEX IF NOT EXISTS idx_candidates_batch_status ON candidates(batch_id, review_status);
CREATE INDEX IF NOT EXISTS idx_outbox_tasks_ready ON outbox_tasks(state, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_audit_events_candidate ON audit_events(candidate_id, created_at);
