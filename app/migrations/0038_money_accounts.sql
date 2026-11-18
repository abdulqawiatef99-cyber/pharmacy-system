-- requires: foreign_keys_off
-- 0038 money accounts (F105), supplier early-payment discount (F106), party balance reconciliation (F108).
-- Installer only (the in-app update refuses migrations).

-- Where money is kept besides the shift drawer: the main treasury (one per pharmacy) and the owner's own
-- accounts (bank / InstaPay, Vodafone Cash, ...). The balance is the sum of the account's movements.
CREATE TABLE money_accounts (
    id             TEXT PRIMARY KEY,
    org_id         TEXT NOT NULL REFERENCES organizations(id),
    kind           TEXT NOT NULL CHECK (kind IN ('treasury','bank','wallet','other')),
    name           TEXT NOT NULL,
    allow_negative INTEGER NOT NULL DEFAULT 0 CHECK (allow_negative IN (0,1)),
    is_active      INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0,1)),
    sort           INTEGER NOT NULL DEFAULT 0,
    created_by     TEXT,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE (org_id, name)
);
CREATE UNIQUE INDEX ux_one_treasury ON money_accounts(org_id) WHERE kind = 'treasury';

-- Every piaster in or out of an account (+ in, - out). Append-only.
CREATE TABLE money_movements (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    account_id TEXT NOT NULL REFERENCES money_accounts(id),
    amount     INTEGER NOT NULL CHECK (amount <> 0),
    kind       TEXT NOT NULL CHECK (kind IN ('opening','shift_close','transfer','supplier_payment','expense',
                                             'payroll','schedule','tax','owner_withdrawal','owner_deposit',
                                             'sale','customer_payment','refund','count_difference')),
    ref_type   TEXT,
    ref_id     TEXT,
    note       TEXT,
    meta       TEXT,                      -- JSON, information only (e.g. the old program's figure)
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_money_movements_account ON money_movements(account_id, created_at);
CREATE INDEX ix_money_movements_org ON money_movements(org_id, created_at);
CREATE INDEX ix_money_movements_ref ON money_movements(org_id, ref_id);
-- a closed shift moves its cash to the treasury once; an account has one opening balance
CREATE UNIQUE INDEX ux_money_shift_close ON money_movements(org_id, ref_id) WHERE kind = 'shift_close';
CREATE UNIQUE INDEX ux_money_opening ON money_movements(account_id) WHERE kind = 'opening';

CREATE TRIGGER money_movements_no_update BEFORE UPDATE ON money_movements
BEGIN
    SELECT RAISE(ABORT, 'money_movements is append-only');
END;

CREATE TRIGGER money_movements_no_delete BEFORE DELETE ON money_movements
BEGIN
    SELECT RAISE(ABORT, 'money_movements is append-only');
END;

-- Counting an account: what the program expected, what was really there, and why they differ.
CREATE TABLE money_counts (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    account_id  TEXT NOT NULL REFERENCES money_accounts(id),
    expected    INTEGER NOT NULL,
    counted     INTEGER NOT NULL,
    difference  INTEGER NOT NULL,
    reason      TEXT,
    movement_id TEXT,
    created_by  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_money_counts ON money_counts(account_id, created_at);

CREATE TRIGGER money_counts_no_update BEFORE UPDATE ON money_counts
BEGIN
    SELECT RAISE(ABORT, 'money_counts is append-only');
END;

CREATE TRIGGER money_counts_no_delete BEFORE DELETE ON money_counts
BEGIN
    SELECT RAISE(ABORT, 'money_counts is append-only');
END;

-- Which account a card / transfer payment lands in: per payment channel, or the method's default
-- (channel_id '' = any channel of that method without its own route). Set by the owner.
CREATE TABLE money_routes (
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    method     TEXT NOT NULL CHECK (method IN ('card','transfer')),
    channel_id TEXT NOT NULL DEFAULT '',
    account_id TEXT NOT NULL REFERENCES money_accounts(id),
    updated_by TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (org_id, method, channel_id)
);

-- Shift close: the cash left in the drawer for the next shift (the rest goes to the treasury).
ALTER TABLE cash_shifts ADD COLUMN float_left INTEGER CHECK (float_left IS NULL OR float_left >= 0);

-- The account an expense / a scheduled payment / a salary advance was paid from (NULL = the old
-- paid_from value tells: the drawer or outside the program's accounts).
ALTER TABLE expenses ADD COLUMN account_id TEXT REFERENCES money_accounts(id);
ALTER TABLE expense_schedules ADD COLUMN account_id TEXT REFERENCES money_accounts(id);
ALTER TABLE staff_advances ADD COLUMN account_id TEXT REFERENCES money_accounts(id);

-- Ledger kind 'discount': an early-payment discount from a supplier (F106) or a claim discount given to
-- a customer (F108). SQLite cannot change a CHECK, so both ledgers are rebuilt with the same columns,
-- index and append-only triggers.
CREATE TABLE supplier_ledger_new (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    supplier_id TEXT NOT NULL REFERENCES suppliers(id),
    amount      INTEGER NOT NULL,
    kind        TEXT NOT NULL CHECK (kind IN ('opening','purchase','payment','return','adjustment','discount')),
    ref_id      TEXT,
    note        TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    pay_method  TEXT,
    pay_ref     TEXT
);
INSERT INTO supplier_ledger_new (id, org_id, supplier_id, amount, kind, ref_id, note, created_by, created_at,
                                 pay_method, pay_ref)
    SELECT id, org_id, supplier_id, amount, kind, ref_id, note, created_by, created_at, pay_method, pay_ref
    FROM supplier_ledger;
DROP TABLE supplier_ledger;
ALTER TABLE supplier_ledger_new RENAME TO supplier_ledger;
CREATE INDEX ix_supplier_ledger ON supplier_ledger(org_id, supplier_id);

CREATE TRIGGER supplier_ledger_no_update BEFORE UPDATE ON supplier_ledger
BEGIN
    SELECT RAISE(ABORT, 'supplier_ledger is append-only');
END;

CREATE TRIGGER supplier_ledger_no_delete BEFORE DELETE ON supplier_ledger
BEGIN
    SELECT RAISE(ABORT, 'supplier_ledger is append-only');
END;

CREATE TABLE customer_ledger_new (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    customer_id TEXT NOT NULL REFERENCES customers(id),
    amount      INTEGER NOT NULL,             -- + debt (credit sale), - payment/return/discount
    kind        TEXT NOT NULL CHECK (kind IN ('credit_sale','payment','return','void','adjustment','discount')),
    ref_id      TEXT,
    note        TEXT,
    created_by  TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
INSERT INTO customer_ledger_new (id, org_id, customer_id, amount, kind, ref_id, note, created_by, created_at)
    SELECT id, org_id, customer_id, amount, kind, ref_id, note, created_by, created_at FROM customer_ledger;
DROP TABLE customer_ledger;
ALTER TABLE customer_ledger_new RENAME TO customer_ledger;
CREATE INDEX ix_customer_ledger ON customer_ledger(org_id, customer_id);

CREATE TRIGGER customer_ledger_no_update BEFORE UPDATE ON customer_ledger
BEGIN
    SELECT RAISE(ABORT, 'customer_ledger is append-only');
END;

CREATE TRIGGER customer_ledger_no_delete BEFORE DELETE ON customer_ledger
BEGIN
    SELECT RAISE(ABORT, 'customer_ledger is append-only');
END;

-- A party's statement balance checked against the program (F108), and how a difference was recorded.
CREATE TABLE party_reconciliations (
    id                TEXT PRIMARY KEY,
    org_id            TEXT NOT NULL REFERENCES organizations(id),
    party             TEXT NOT NULL CHECK (party IN ('supplier','customer')),
    party_id          TEXT NOT NULL,
    statement_balance INTEGER,
    program_balance   INTEGER NOT NULL,
    difference        INTEGER NOT NULL,
    adj_type          TEXT CHECK (adj_type IS NULL OR adj_type IN ('opening','early_discount','claim_discount','correction')),
    ledger_id         TEXT,
    ref_id            TEXT,
    note              TEXT,
    created_by        TEXT NOT NULL,
    created_at        TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_party_reconciliations ON party_reconciliations(org_id, party, party_id, created_at);

CREATE TRIGGER party_reconciliations_no_update BEFORE UPDATE ON party_reconciliations
BEGIN
    SELECT RAISE(ABORT, 'party_reconciliations is append-only');
END;

CREATE TRIGGER party_reconciliations_no_delete BEFORE DELETE ON party_reconciliations
BEGIN
    SELECT RAISE(ABORT, 'party_reconciliations is append-only');
END;
