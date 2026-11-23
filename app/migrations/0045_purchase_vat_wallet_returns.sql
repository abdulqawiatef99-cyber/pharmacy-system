-- requires: foreign_keys_off
-- One supplier lot can have stock wearing two official prices.
CREATE TABLE batches_next (
    id               TEXT PRIMARY KEY,
    org_id           TEXT NOT NULL REFERENCES organizations(id),
    item_id          TEXT NOT NULL REFERENCES items(id),
    batch_no         TEXT NOT NULL DEFAULT '',
    expiry_date      TEXT,
    expiry_precision TEXT CHECK (expiry_precision IN ('day','month')),
    public_price     INTEGER,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    owner            TEXT NOT NULL DEFAULT 'pharmacy' CHECK (owner IN ('pharmacy','hospital')),
    price_variant    INTEGER NOT NULL DEFAULT 0 CHECK (price_variant >= 0),
    UNIQUE (org_id, item_id, batch_no, expiry_date, price_variant)
);
INSERT INTO batches_next (id, org_id, item_id, batch_no, expiry_date, expiry_precision, public_price, created_at, owner)
SELECT id, org_id, item_id, batch_no, expiry_date, expiry_precision, public_price, created_at, owner FROM batches;
DROP TABLE batches;
ALTER TABLE batches_next RENAME TO batches;
CREATE INDEX ix_batches_batchno ON batches(org_id, batch_no);
CREATE INDEX ix_batches_owner ON batches(org_id, owner) WHERE owner <> 'pharmacy';

ALTER TABLE items ADD COLUMN untracked_public_price INTEGER;

ALTER TABLE purchase_invoice_lines ADD COLUMN tax_included INTEGER
    CHECK (tax_included IN (0, 1));

-- Existing returns retain their method and values. Wallet returns identify their original channel.
CREATE TABLE sale_returns_next (
    id             TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES organizations(id),
    sale_id        TEXT NOT NULL REFERENCES sales(id),
    total          INTEGER NOT NULL,
    refund_method  TEXT NOT NULL CHECK (refund_method IN ('cash','card','credit','transfer')),
    reason         TEXT NOT NULL,
    created_by     TEXT NOT NULL,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    rounding       INTEGER NOT NULL DEFAULT 0,
    channel_id     TEXT REFERENCES payment_channels(id),
    CHECK (refund_method = 'transfer' OR channel_id IS NULL)
);
INSERT INTO sale_returns_next (id, org_id, sale_id, total, refund_method, reason, created_by, created_at, rounding)
SELECT id, org_id, sale_id, total, refund_method, reason, created_by, created_at, rounding FROM sale_returns;
DROP TABLE sale_returns;
ALTER TABLE sale_returns_next RENAME TO sale_returns;
CREATE INDEX ix_sale_returns_sale ON sale_returns(sale_id);
CREATE INDEX ix_sale_returns_at ON sale_returns(org_id, created_at);
CREATE INDEX ix_sale_returns_sale_org_at ON sale_returns(sale_id, org_id, created_at);
