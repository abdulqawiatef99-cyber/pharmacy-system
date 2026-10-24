-- 0009 sales / cashier

CREATE TABLE customers (
    id           TEXT PRIMARY KEY,
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    name         TEXT NOT NULL,
    phone        TEXT,
    credit_limit INTEGER NOT NULL DEFAULT 0,   -- piasters; 0 = no credit allowed
    notes        TEXT,
    is_active    INTEGER NOT NULL DEFAULT 1,
    created_at   TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE UNIQUE INDEX ux_customer_phone ON customers(org_id, phone) WHERE phone IS NOT NULL;

-- Per-branch running numbers (sale receipts etc.), incremented inside the
-- same transaction that uses them -> no gaps from concurrent cashiers.
CREATE TABLE branch_counters (
    org_id    TEXT NOT NULL,
    branch_id TEXT NOT NULL,
    kind      TEXT NOT NULL,
    next_no   INTEGER NOT NULL,
    PRIMARY KEY (org_id, branch_id, kind)
);

CREATE TABLE sales (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    branch_id     TEXT NOT NULL REFERENCES branches(id),
    sale_no       INTEGER,                    -- assigned on completion
    status        TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','completed','voided')),
    customer_id   TEXT REFERENCES customers(id),
    subtotal      INTEGER NOT NULL DEFAULT 0,
    discount_total INTEGER NOT NULL DEFAULT 0,
    total         INTEGER NOT NULL DEFAULT 0,
    paid_cash     INTEGER NOT NULL DEFAULT 0,
    paid_card     INTEGER NOT NULL DEFAULT 0,
    on_credit     INTEGER NOT NULL DEFAULT 0,
    returned_total INTEGER NOT NULL DEFAULT 0,
    version       INTEGER NOT NULL DEFAULT 1,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    completed_at  TEXT,
    voided_by     TEXT,
    voided_at     TEXT,
    void_reason   TEXT,
    note          TEXT
);
CREATE UNIQUE INDEX ux_sale_no ON sales(org_id, branch_id, sale_no) WHERE sale_no IS NOT NULL;
CREATE INDEX ix_sales_org ON sales(org_id, branch_id, status, created_at);

CREATE TABLE sale_lines (
    id          TEXT PRIMARY KEY,
    sale_id     TEXT NOT NULL REFERENCES sales(id),
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    line_no     INTEGER NOT NULL,
    item_id     TEXT NOT NULL REFERENCES items(id),
    qty_base    INTEGER NOT NULL CHECK (qty_base > 0),
    discount_bp INTEGER NOT NULL DEFAULT 0 CHECK (discount_bp BETWEEN 0 AND 10000),
    partial_pack INTEGER NOT NULL DEFAULT 0,
    line_total  INTEGER,                      -- set on completion (after batch pricing)
    UNIQUE (sale_id, line_no)
);

-- Which batches a line was taken from, at which legal price and cost.
CREATE TABLE sale_allocations (
    id           TEXT PRIMARY KEY,
    sale_line_id TEXT NOT NULL REFERENCES sale_lines(id),
    batch_id     TEXT REFERENCES batches(id),
    qty_base     INTEGER NOT NULL CHECK (qty_base > 0),
    unit_price   INTEGER NOT NULL,            -- piasters per base unit, before discount
    unit_cost    TEXT,
    returned_qty INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE customer_ledger (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    amount      INTEGER NOT NULL,             -- + debt (credit sale), - payment/return
    kind        TEXT NOT NULL CHECK (kind IN ('credit_sale','payment','return','void','adjustment')),
    ref_id      TEXT,
    note        TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_customer_ledger ON customer_ledger(org_id, customer_id);

CREATE TRIGGER customer_ledger_no_update BEFORE UPDATE ON customer_ledger
BEGIN
    SELECT RAISE(ABORT, 'customer_ledger is append-only');
END;

CREATE TRIGGER customer_ledger_no_delete BEFORE DELETE ON customer_ledger
BEGIN
    SELECT RAISE(ABORT, 'customer_ledger is append-only');
END;

CREATE TABLE sale_returns (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    sale_id    TEXT NOT NULL REFERENCES sales(id),
    total      INTEGER NOT NULL,
    refund_method TEXT NOT NULL CHECK (refund_method IN ('cash','card','credit')),
    reason     TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'sales.create', '$[#]', 'sales.discount',
    '$[#]', 'sales.void', '$[#]', 'sales.return', '$[#]', 'customers.manage', '$[#]', 'sales.read')
    WHERE code = 'owner';
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'sales.create', '$[#]', 'sales.discount',
    '$[#]', 'sales.return', '$[#]', 'customers.manage', '$[#]', 'sales.read')
    WHERE code = 'pharmacist';
UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'sales.create', '$[#]', 'sales.read')
    WHERE code = 'assistant';
