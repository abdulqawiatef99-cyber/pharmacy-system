-- 0007 inventory counting (جرد)

CREATE TABLE count_sessions (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    branch_id    TEXT NOT NULL REFERENCES branches(id),
    title        TEXT NOT NULL,
    scope        TEXT NOT NULL CHECK (scope IN ('full','location','items')),
    scope_filter TEXT NOT NULL DEFAULT '{}',   -- {"locations": [...]} | {"item_ids": [...]}
    blind        INTEGER NOT NULL DEFAULT 1,   -- counters do not see system quantities
    status       TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','approved','cancelled')),
    created_by   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    approved_by  TEXT,
    approved_at  TEXT,
    report       TEXT
);
CREATE INDEX ix_count_sessions_org ON count_sessions(org_id, status);

CREATE TABLE count_entries (
    id            TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES count_sessions(id),
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    item_id       TEXT NOT NULL REFERENCES items(id),
    batch_key     TEXT NOT NULL DEFAULT '',     -- batch id, or '' = counted at item level
    location      TEXT NOT NULL DEFAULT '',
    qty           INTEGER NOT NULL CHECK (qty >= 0),   -- base units
    counted_by    TEXT NOT NULL,
    device        TEXT,
    counted_at    TEXT NOT NULL,
    ledger_seq    INTEGER NOT NULL,             -- max(stock_movements.rowid) when counted
    superseded_by TEXT REFERENCES count_entries(id) DEFERRABLE INITIALLY DEFERRED
);
CREATE INDEX ix_count_entries_session ON count_entries(session_id, item_id);
-- only one live entry per item/batch/location in a session (no double counting)
CREATE UNIQUE INDEX ux_count_live ON count_entries(session_id, item_id, batch_key, location)
    WHERE superseded_by IS NULL;

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'inventory.count')
    WHERE code IN ('owner','pharmacist','assistant');
