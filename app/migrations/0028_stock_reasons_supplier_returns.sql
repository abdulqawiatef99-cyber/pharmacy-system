-- Why stock went up or down outside buying and selling (expired, damaged, lost, ...),
-- and returns to a supplier (credit note) with their lines.
ALTER TABLE stock_movements ADD COLUMN reason TEXT;
CREATE TABLE supplier_returns (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    branch_id   TEXT NOT NULL REFERENCES branches(id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    return_no   INTEGER NOT NULL,
    total       INTEGER NOT NULL,          -- piasters credited by the supplier
    note        TEXT,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_supplier_returns ON supplier_returns(org_id, created_at);
CREATE TABLE supplier_return_lines (
    id          TEXT PRIMARY KEY,
    return_id   TEXT NOT NULL REFERENCES supplier_returns(id),
    item_id     TEXT NOT NULL REFERENCES items(id),
    batch_id    TEXT,
    qty_base    INTEGER NOT NULL CHECK (qty_base > 0),
    unit_value  INTEGER NOT NULL CHECK (unit_value >= 0),   -- piasters per base unit credited
    reason      TEXT NOT NULL
);
