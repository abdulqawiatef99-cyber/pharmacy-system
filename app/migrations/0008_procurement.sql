-- 0008 procurement planning inputs

-- Sales/consumption per item per period. Filled from our own sale movements
-- later (Sales phase) and, until then, imported from the old system's monthly
-- sales report. One row per item per period per source.
CREATE TABLE consumption_history (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    branch_id    TEXT NOT NULL REFERENCES branches(id),
    item_id      TEXT NOT NULL REFERENCES items(id),
    period_start TEXT NOT NULL,
    period_end   TEXT NOT NULL,
    qty_base     INTEGER NOT NULL CHECK (qty_base >= 0),
    source       TEXT NOT NULL,          -- 'import:<run>' | 'manual'
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, branch_id, item_id, period_start, period_end, source)
);
CREATE INDEX ix_consumption_item ON consumption_history(org_id, branch_id, item_id, period_end);

ALTER TABLE suppliers ADD COLUMN lead_time_days INTEGER NOT NULL DEFAULT 1;
ALTER TABLE suppliers ADD COLUMN order_cycle_days INTEGER NOT NULL DEFAULT 30;
