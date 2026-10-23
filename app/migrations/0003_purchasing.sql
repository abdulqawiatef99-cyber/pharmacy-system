-- 0003 purchase invoices
-- Lifecycle: draft -> approved (posts stock) -> cancelled (posts reversal).
-- Nothing touches stock or cost before approval.

CREATE TABLE source_files (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    sha256       TEXT NOT NULL,
    filename     TEXT NOT NULL,
    mime_type    TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    storage_path TEXT NOT NULL,
    uploaded_by  TEXT,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_source_files_hash ON source_files(org_id, sha256);

CREATE TABLE purchase_invoices (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL REFERENCES organizations(id),
    branch_id         TEXT NOT NULL REFERENCES branches(id),
    supplier_id       TEXT REFERENCES suppliers(id),
    supplier_name_raw TEXT,
    invoice_no        TEXT,
    invoice_date      TEXT,
    status            TEXT NOT NULL DEFAULT 'draft'
                      CHECK (status IN ('draft','approved','cancelled')),
    source_type       TEXT NOT NULL CHECK (source_type IN ('excel','csv','pdf','image','manual')),
    source_file_id    TEXT REFERENCES source_files(id),
    extraction_method TEXT,            -- 'structured','pdf_text','ai_vision','manual'
    extraction_model  TEXT,
    declared_total    INTEGER,         -- piasters, as printed (net payable)
    extra_discount    INTEGER NOT NULL DEFAULT 0,  -- invoice-level discount NOT already in lines
    extra_tax         INTEGER NOT NULL DEFAULT 0,  -- invoice-level tax NOT already in lines
    computed_total    INTEGER,
    acknowledged_issues TEXT NOT NULL DEFAULT '[]', -- JSON [{code, reason, by, at}]
    content_hash      TEXT,            -- normalized lines fingerprint (duplicate detection)
    raw_extraction    TEXT,            -- original extracted JSON, never edited
    notes             TEXT,
    version           INTEGER NOT NULL DEFAULT 1,  -- optimistic locking
    created_by        TEXT,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    approved_by       TEXT,
    approved_at       TEXT,
    cancelled_by      TEXT,
    cancelled_at      TEXT,
    cancel_reason     TEXT
);
CREATE INDEX ix_pinv_org_status ON purchase_invoices(org_id, status, created_at);
-- The same supplier invoice number can only be live once.
CREATE UNIQUE INDEX ux_pinv_supplier_no ON purchase_invoices(org_id, supplier_id, invoice_no)
    WHERE status <> 'cancelled' AND supplier_id IS NOT NULL AND invoice_no IS NOT NULL;

CREATE TABLE purchase_invoice_lines (
    id               TEXT PRIMARY KEY,
    invoice_id       TEXT NOT NULL REFERENCES purchase_invoices(id),
    org_id           TEXT NOT NULL REFERENCES organizations(id),
    line_no          INTEGER NOT NULL,
    raw_name         TEXT NOT NULL,
    raw_code         TEXT,
    raw_barcode      TEXT,
    item_id          TEXT REFERENCES items(id),
    match_method     TEXT,     -- barcode, supplier_code, alias, fuzzy, manual
    match_score      REAL,
    match_status     TEXT NOT NULL DEFAULT 'unmatched'
                     CHECK (match_status IN ('unmatched','suggested','auto','confirmed')),
    qty              INTEGER,  -- packs invoiced (paid)
    bonus_qty        INTEGER NOT NULL DEFAULT 0,  -- free packs
    public_price     INTEGER,  -- piasters per pack
    discount_bp      INTEGER,
    unit_net_price   INTEGER,  -- piasters per pack after discount, before tax
    tax_amount       INTEGER,  -- piasters for the line
    line_total       INTEGER,  -- piasters, as printed/extracted
    batch_no         TEXT,
    expiry_date      TEXT,
    expiry_precision TEXT,
    field_sources    TEXT NOT NULL DEFAULT '{}',  -- JSON: field -> read|inferred|db|user
    field_confidence TEXT NOT NULL DEFAULT '{}',  -- JSON: field -> 0..1
    UNIQUE (invoice_id, line_no)
);
CREATE INDEX ix_pinv_lines_invoice ON purchase_invoice_lines(invoice_id);
