-- 0015 item card: sections (with "no return / no exchange" and a mark shown on
-- invoices), shortage flag, max stock.
CREATE TABLE categories (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    name       TEXT NOT NULL,
    no_return  INTEGER NOT NULL DEFAULT 0 CHECK (no_return IN (0,1)),
    mark       TEXT CHECK (mark IN ('fridge','injection')),
    sort       INTEGER NOT NULL DEFAULT 0,
    is_active  INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, name)
);
ALTER TABLE items ADD COLUMN category_id TEXT REFERENCES categories(id);
ALTER TABLE items ADD COLUMN max_stock INTEGER;
ALTER TABLE items ADD COLUMN shortage_at TEXT;
ALTER TABLE items ADD COLUMN shortage_by TEXT;
CREATE INDEX ix_items_category ON items(org_id, category_id);
