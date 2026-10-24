-- Contracts (a company, a union, a club...): an agreed discount, different for local and
-- imported items, applied by itself to the invoices of the customers linked to it.
CREATE TABLE contracts (
    id                   TEXT PRIMARY KEY,
    org_id               TEXT NOT NULL REFERENCES organizations(id),
    name                 TEXT NOT NULL,
    discount_local_bp    INTEGER NOT NULL DEFAULT 0 CHECK (discount_local_bp BETWEEN 0 AND 5000),
    discount_imported_bp INTEGER NOT NULL DEFAULT 0 CHECK (discount_imported_bp BETWEEN 0 AND 5000),
    note                 TEXT,
    is_active            INTEGER NOT NULL DEFAULT 1,
    created_by           TEXT NOT NULL,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, name)
);
ALTER TABLE customers ADD COLUMN contract_id TEXT REFERENCES contracts(id);
ALTER TABLE sales ADD COLUMN contract_id TEXT;
ALTER TABLE sale_lines ADD COLUMN discount_source TEXT;
