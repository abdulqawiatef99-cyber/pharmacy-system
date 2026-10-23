-- 0002 catalog + inventory core
-- Quantities are INTEGER in the item's base unit (the smallest unit sold,
-- e.g. a strip). pack_size = base units per pack (the unit printed on supplier
-- invoices and carrying the official public price).

CREATE TABLE suppliers (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    name          TEXT NOT NULL,
    name_norm     TEXT NOT NULL,
    supplier_type TEXT NOT NULL DEFAULT 'other'
                  CHECK (supplier_type IN ('distributor','warehouse','company','other')),
    base_discount_local_bp    INTEGER CHECK (base_discount_local_bp BETWEEN 0 AND 10000),
    base_discount_imported_bp INTEGER CHECK (base_discount_imported_bp BETWEEN 0 AND 10000),
    payment_terms TEXT,
    phone         TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, name_norm)
);

CREATE TABLE items (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL REFERENCES organizations(id),
    global_item_id    TEXT,               -- future shared master catalog
    code              TEXT,               -- internal / legacy (e.g. E-Stock) code
    name_en           TEXT,
    name_ar           TEXT,
    name_norm         TEXT NOT NULL,      -- normalized search key
    active_ingredient TEXT,
    strength          TEXT,               -- as written, e.g. '500 mg'
    strength_norm     TEXT,               -- e.g. '500mg'
    dosage_form       TEXT,               -- normalized code: tab, cap, syrup, ...
    manufacturer      TEXT,
    is_drug           INTEGER,            -- NULL = unknown (legacy data is unreliable)
    is_imported       INTEGER,            -- NULL = unknown
    vat_bp            INTEGER NOT NULL DEFAULT 0 CHECK (vat_bp BETWEEN 0 AND 10000),
    pack_size         INTEGER NOT NULL DEFAULT 1 CHECK (pack_size >= 1),
    base_unit_name    TEXT NOT NULL DEFAULT 'unit',
    pack_unit_name    TEXT NOT NULL DEFAULT 'pack',
    public_price      INTEGER CHECK (public_price >= 0),   -- piasters per pack
    min_stock         INTEGER NOT NULL DEFAULT 0,          -- base units
    location          TEXT,
    status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    dedupe_key        TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, dedupe_key)
);
CREATE UNIQUE INDEX ux_items_org_code ON items(org_id, code) WHERE code IS NOT NULL;
CREATE INDEX ix_items_org_norm ON items(org_id, name_norm);

CREATE TABLE item_barcodes (
    org_id   TEXT NOT NULL REFERENCES organizations(id),
    barcode  TEXT NOT NULL,
    item_id  TEXT NOT NULL REFERENCES items(id),
    PRIMARY KEY (org_id, barcode)
);

-- Alternative spellings learned from invoices / user confirmations.
-- supplier_key = '' for org-wide aliases, else the supplier id.
CREATE TABLE item_aliases (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    item_id      TEXT NOT NULL REFERENCES items(id),
    supplier_key TEXT NOT NULL DEFAULT '',
    alias_norm   TEXT NOT NULL,
    alias_raw    TEXT NOT NULL,
    source       TEXT NOT NULL CHECK (source IN ('manual','learned','import')),
    created_by   TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, supplier_key, alias_norm)
);

CREATE TABLE supplier_item_codes (
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    code        TEXT NOT NULL,
    item_id     TEXT NOT NULL REFERENCES items(id),
    PRIMARY KEY (org_id, supplier_id, code)
);

CREATE TABLE item_price_history (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    item_id      TEXT NOT NULL REFERENCES items(id),
    public_price INTEGER NOT NULL,
    previous_price INTEGER,
    source       TEXT NOT NULL,          -- 'manual','purchase_invoice','import'
    ref_id       TEXT,
    observed_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_price_hist_item ON item_price_history(org_id, item_id, observed_at);

-- A batch (تشغيلة) of an item. expiry stored as a date; when the pack only
-- prints MM/YYYY we store the last day of that month with precision 'month'.
CREATE TABLE batches (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES organizations(id),
    item_id          TEXT NOT NULL REFERENCES items(id),
    batch_no         TEXT NOT NULL DEFAULT '',
    expiry_date      TEXT,
    expiry_precision TEXT CHECK (expiry_precision IN ('day','month')),
    public_price     INTEGER,      -- piasters per pack printed on THIS batch (Egyptian price
                                   -- rises: old batches keep their old legal price)
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, item_id, batch_no, expiry_date)
);
CREATE INDEX ix_batches_batchno ON batches(org_id, batch_no);

-- Immutable stock ledger: the single source of truth for quantities.
CREATE TABLE stock_movements (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    branch_id     TEXT NOT NULL REFERENCES branches(id),
    item_id       TEXT NOT NULL REFERENCES items(id),
    batch_id      TEXT REFERENCES batches(id),
    qty_delta     INTEGER NOT NULL CHECK (qty_delta <> 0),
    unit_cost     TEXT,                 -- decimal string, EGP per base unit
    movement_type TEXT NOT NULL CHECK (movement_type IN
                  ('purchase','purchase_reversal','sale','sale_return',
                   'supplier_return','adjustment','opening','transfer_in','transfer_out','damage')),
    ref_type      TEXT,
    ref_id        TEXT,
    ref_line_id   TEXT,
    note          TEXT,
    created_by    TEXT,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_moves_item ON stock_movements(org_id, branch_id, item_id);
CREATE INDEX ix_moves_ref ON stock_movements(ref_type, ref_id);

CREATE TRIGGER stock_movements_no_update BEFORE UPDATE ON stock_movements
BEGIN
    SELECT RAISE(ABORT, 'stock_movements is append-only; post a reversing movement');
END;

CREATE TRIGGER stock_movements_no_delete BEFORE DELETE ON stock_movements
BEGIN
    SELECT RAISE(ABORT, 'stock_movements is append-only; post a reversing movement');
END;

-- Cached balances, updated in the same transaction as each movement and
-- verifiable against the ledger (inventory.service.verify_balances).
CREATE TABLE stock_balances (
    org_id    TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    batch_key TEXT NOT NULL,           -- batch id or '' when untracked
    qty       INTEGER NOT NULL,
    PRIMARY KEY (org_id, branch_id, item_id, batch_key)
);

-- Moving weighted-average cost per branch.
CREATE TABLE item_costs (
    org_id     TEXT NOT NULL,
    branch_id  TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    avg_cost   TEXT NOT NULL,          -- EGP per base unit, decimal string
    updated_at TEXT NOT NULL,
    PRIMARY KEY (org_id, branch_id, item_id)
);
