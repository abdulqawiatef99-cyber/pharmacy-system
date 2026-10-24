-- 0012 cash drawer shifts (الخزنة / الوردية)

CREATE TABLE cash_shifts (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    branch_id     TEXT NOT NULL REFERENCES branches(id),
    status        TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','closed')),
    opening_cash  INTEGER NOT NULL CHECK (opening_cash >= 0),
    opened_by     TEXT NOT NULL,
    opened_at     TEXT NOT NULL,
    expected_cash INTEGER,
    counted_cash  INTEGER,
    difference    INTEGER,
    close_note    TEXT,
    closed_by     TEXT,
    closed_at     TEXT
);
-- one open drawer per branch
CREATE UNIQUE INDEX ux_one_open_shift ON cash_shifts(org_id, branch_id) WHERE status = 'open';

CREATE TABLE cash_movements (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    shift_id   TEXT NOT NULL REFERENCES cash_shifts(id),
    amount     INTEGER NOT NULL CHECK (amount <> 0),    -- + into drawer, - out of drawer
    kind       TEXT NOT NULL CHECK (kind IN ('sale','refund','void','customer_payment','payout','deposit')),
    ref_id     TEXT,
    note       TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_cash_movements_shift ON cash_movements(shift_id);

CREATE TRIGGER cash_movements_no_update BEFORE UPDATE ON cash_movements
BEGIN
    SELECT RAISE(ABORT, 'cash_movements is append-only');
END;

CREATE TRIGGER cash_movements_no_delete BEFORE DELETE ON cash_movements
BEGIN
    SELECT RAISE(ABORT, 'cash_movements is append-only');
END;

ALTER TABLE sales ADD COLUMN shift_id TEXT REFERENCES cash_shifts(id);

UPDATE roles SET permissions = json_insert(permissions, '$[#]', 'cash.manage') WHERE code IN ('owner','pharmacist');
