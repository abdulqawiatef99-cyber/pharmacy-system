-- 0010 hardening: tamper-evident audit chain, idempotent requests, tool entitlements

-- Hash chain over audit rows: changing or deleting a row (even with raw DB
-- access that bypasses the append-only triggers) breaks the chain, and
-- `manage.py verify` reports it. Rows written before this migration have no hash.
ALTER TABLE audit_log ADD COLUMN seq INTEGER;
ALTER TABLE audit_log ADD COLUMN prev_hash TEXT;
ALTER TABLE audit_log ADD COLUMN row_hash TEXT;
CREATE UNIQUE INDEX ux_audit_seq ON audit_log(seq) WHERE seq IS NOT NULL;

-- Replay protection for retried POSTs (network drop, double click).
CREATE TABLE idempotency_keys (
    org_id       TEXT NOT NULL,
    user_id      TEXT NOT NULL,
    key          TEXT NOT NULL,
    method       TEXT NOT NULL,
    path         TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    status       INTEGER NOT NULL,
    response     TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (org_id, user_id, key)
);

-- Domain-level idempotency for the two cashier actions that must never double.
ALTER TABLE sales ADD COLUMN complete_request_id TEXT;
ALTER TABLE sale_lines ADD COLUMN client_ref TEXT;
CREATE UNIQUE INDEX ux_sale_line_ref ON sale_lines(sale_id, client_ref) WHERE client_ref IS NOT NULL;

-- Every tool can be switched off per pharmacy without touching the others.
INSERT OR IGNORE INTO org_entitlements (org_id, feature)
    SELECT o.id, f.feature FROM organizations o,
    (SELECT 'sales' feature UNION ALL SELECT 'counting' UNION ALL SELECT 'procurement'
     UNION ALL SELECT 'recalls' UNION ALL SELECT 'safety' UNION ALL SELECT 'dataimport') f;
