-- 0004 data migration runs (import from E-Stock / any Excel or CSV)
-- A run is analyzed first (dry run, nothing written to business tables),
-- reviewed, then applied once in a single transaction.

CREATE TABLE import_runs (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    branch_id    TEXT NOT NULL REFERENCES branches(id),
    kind         TEXT NOT NULL CHECK (kind IN ('items','opening_stock')),
    source_system TEXT NOT NULL DEFAULT 'generic',
    filename     TEXT NOT NULL,
    file_sha256  TEXT NOT NULL,
    mapping      TEXT NOT NULL,            -- JSON {column_index: field}
    options      TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'analyzed' CHECK (status IN ('analyzed','applied','discarded')),
    report       TEXT NOT NULL DEFAULT '{}',
    created_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    applied_by   TEXT,
    applied_at   TEXT
);
CREATE INDEX ix_import_runs_org ON import_runs(org_id, created_at);
-- the same file can be applied only once per kind
CREATE UNIQUE INDEX ux_import_applied ON import_runs(org_id, kind, file_sha256) WHERE status = 'applied';

CREATE TABLE import_rows (
    id         TEXT PRIMARY KEY,
    run_id     TEXT NOT NULL REFERENCES import_runs(id),
    row_no     INTEGER NOT NULL,           -- row number in the source sheet (1-based)
    raw        TEXT NOT NULL,              -- JSON of the original cells
    data       TEXT NOT NULL,              -- JSON normalized values
    action     TEXT NOT NULL CHECK (action IN ('create','update','link','skip','review','error')),
    target_id  TEXT,                       -- existing item id when update/link
    issues     TEXT NOT NULL DEFAULT '[]',
    decision   TEXT,                       -- user override: create | skip | link:<item_id>
    UNIQUE (run_id, row_no)
);
CREATE INDEX ix_import_rows_run ON import_rows(run_id, action);
