-- requires: foreign_keys_off
-- 0011 data import: suppliers/customers with opening balances, saved column
-- profiles, supplier ledger.

CREATE TABLE import_runs_new (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    branch_id    TEXT NOT NULL REFERENCES branches(id),
    kind         TEXT NOT NULL CHECK (kind IN ('items','opening_stock','suppliers','customers')),
    source_system TEXT NOT NULL DEFAULT 'generic',
    filename     TEXT NOT NULL,
    file_sha256  TEXT NOT NULL,
    mapping      TEXT NOT NULL,
    options      TEXT NOT NULL DEFAULT '{}',
    status       TEXT NOT NULL DEFAULT 'analyzed' CHECK (status IN ('analyzed','applied','discarded')),
    report       TEXT NOT NULL DEFAULT '{}',
    created_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    applied_by   TEXT,
    applied_at   TEXT
);
INSERT INTO import_runs_new SELECT * FROM import_runs;
DROP TABLE import_runs;
ALTER TABLE import_runs_new RENAME TO import_runs;
CREATE INDEX ix_import_runs_org ON import_runs(org_id, created_at);
CREATE UNIQUE INDEX ux_import_applied ON import_runs(org_id, kind, file_sha256) WHERE status = 'applied';

-- Column mapping remembered per old system + file kind + exact header row,
-- so a pharmacy maps its E-Stock export once.
CREATE TABLE import_profiles (
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    source_system TEXT NOT NULL,
    kind          TEXT NOT NULL,
    header_sig    TEXT NOT NULL,
    mapping       TEXT NOT NULL,          -- {"normalized header text": field}
    updated_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (org_id, source_system, kind, header_sig)
);

-- What the pharmacy owes each supplier (+ = we owe). Append-only.
CREATE TABLE supplier_ledger (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    amount      INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('opening','purchase','payment','return','adjustment')),
    ref_id      TEXT,
    note        TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_supplier_ledger ON supplier_ledger(org_id, supplier_id);

CREATE TRIGGER supplier_ledger_no_update BEFORE UPDATE ON supplier_ledger
BEGIN
    SELECT RAISE(ABORT, 'supplier_ledger is append-only');
END;

CREATE TRIGGER supplier_ledger_no_delete BEFORE DELETE ON supplier_ledger
BEGIN
    SELECT RAISE(ABORT, 'supplier_ledger is append-only');
END;
