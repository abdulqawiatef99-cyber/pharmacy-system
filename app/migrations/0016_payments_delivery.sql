-- 0016 cashier: payment methods with the machine / wallet used, split payment,
-- home-delivery invoices.

-- card machines and transfer wallets the pharmacy uses (owner edits the list)
CREATE TABLE payment_channels (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    kind       TEXT NOT NULL CHECK (kind IN ('card','transfer')),
    name       TEXT NOT NULL,
    is_active  INTEGER NOT NULL DEFAULT 1,
    sort       INTEGER NOT NULL DEFAULT 0,
    UNIQUE (org_id, kind, name)
);

-- one row per payment part of a sale (cash 50 + card 40 = two rows)
CREATE TABLE sale_payments (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    sale_id    TEXT NOT NULL REFERENCES sales(id),
    method     TEXT NOT NULL CHECK (method IN ('cash','card','transfer','credit')),
    channel_id TEXT REFERENCES payment_channels(id),
    amount     INTEGER NOT NULL CHECK (amount > 0),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_sale_payments_sale ON sale_payments(sale_id);

ALTER TABLE sales ADD COLUMN paid_transfer INTEGER NOT NULL DEFAULT 0;
ALTER TABLE sales ADD COLUMN kind TEXT NOT NULL DEFAULT 'store';
ALTER TABLE sales ADD COLUMN delivery_fee INTEGER NOT NULL DEFAULT 0;

-- a home-delivery invoice: out -> delivered -> settled (the cash reached the drawer)
CREATE TABLE sale_deliveries (
    sale_id      TEXT PRIMARY KEY REFERENCES sales(id),
    org_id       TEXT NOT NULL REFERENCES organizations(id),
    address      TEXT,
    phone        TEXT,
    courier      TEXT,
    status       TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','out','delivered','settled','cancelled')),
    cash_due     INTEGER NOT NULL DEFAULT 0,
    out_at       TEXT,
    delivered_at TEXT,
    settled_at   TEXT,
    settled_by   TEXT
);
CREATE INDEX ix_deliveries_status ON sale_deliveries(org_id, status);
