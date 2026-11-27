-- A return has one row per tender, with its audit and receipt on the first row.
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
    part_of        TEXT REFERENCES sale_returns_next(id),
    acting_shift_id TEXT REFERENCES cash_shifts(id),
    register_recorded INTEGER NOT NULL DEFAULT 0,
    exchange_offset INTEGER NOT NULL DEFAULT 0 CHECK (exchange_offset >= 0 AND (exchange_offset = 0 OR exchange_offset <= total)),
    CHECK (refund_method IN ('card','transfer') OR channel_id IS NULL)
);
INSERT INTO sale_returns_next (rowid, id, org_id, sale_id, total, refund_method, reason, created_by,
                               created_at, rounding, channel_id, acting_shift_id, register_recorded)
SELECT rowid, id, org_id, sale_id, total, refund_method, reason, created_by,
       created_at, rounding, channel_id, acting_shift_id, register_recorded FROM sale_returns;
DROP TABLE sale_returns;
ALTER TABLE sale_returns_next RENAME TO sale_returns;
CREATE INDEX ix_sale_returns_sale ON sale_returns(sale_id);
CREATE INDEX ix_sale_returns_at ON sale_returns(org_id, created_at);
CREATE INDEX ix_sale_returns_sale_org_at ON sale_returns(sale_id, org_id, created_at);
CREATE INDEX ix_returns_acting_shift ON sale_returns(org_id, acting_shift_id) WHERE acting_shift_id IS NOT NULL;
CREATE INDEX ix_sale_returns_part ON sale_returns(org_id, part_of) WHERE part_of IS NOT NULL;

-- A carried payment settles the replacement without another external payment.
ALTER TABLE sale_payments ADD COLUMN exchange_offset INTEGER NOT NULL DEFAULT 0
    CHECK (exchange_offset BETWEEN 0 AND amount);
ALTER TABLE sale_payments ADD COLUMN verified_exchange_offset INTEGER NOT NULL DEFAULT 0
    CHECK (verified_exchange_offset BETWEEN 0 AND exchange_offset);
-- The original proof still governs any unreviewed wallet amount carried forward.
ALTER TABLE sale_payments ADD COLUMN pending_wallet_source TEXT REFERENCES sales(id);
