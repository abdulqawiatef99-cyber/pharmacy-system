-- Each branch starts with its existing drawer as register 1. Null printer overrides inherit current settings.
CREATE TABLE registers (
    org_id          TEXT NOT NULL REFERENCES organizations(id),
    branch_id       TEXT NOT NULL REFERENCES branches(id),
    id              INTEGER NOT NULL CHECK (id > 0),
    name            TEXT NOT NULL,
    receipt_printer TEXT,
    label_printer   TEXT,
    drawer_kick     INTEGER CHECK (drawer_kick IN (0, 1)),
    active          INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
    device_hash     TEXT,
    legacy_local    INTEGER NOT NULL DEFAULT 0 CHECK (legacy_local IN (0, 1)),
    PRIMARY KEY (org_id, branch_id, id),
    UNIQUE (device_hash)
);

INSERT INTO registers (org_id, branch_id, id, name, legacy_local)
SELECT org_id, id, 1, 'الجهاز الرئيسي', 1 FROM branches;

CREATE TRIGGER branch_main_register AFTER INSERT ON branches
BEGIN
    INSERT INTO registers (org_id, branch_id, id, name, legacy_local)
    VALUES (NEW.org_id, NEW.id, 1, 'الجهاز الرئيسي', 1);
END;

ALTER TABLE cash_shifts ADD COLUMN register_id INTEGER NOT NULL DEFAULT 1;
DROP INDEX ux_one_open_shift;
CREATE UNIQUE INDEX ux_one_open_shift ON cash_shifts(org_id, branch_id, register_id) WHERE status = 'open';
CREATE INDEX ix_shifts_register_history ON cash_shifts(org_id, branch_id, register_id, closed_at DESC);

-- SQLite cannot add a composite foreign key without rebuilding the shift table and its dependents.
CREATE TRIGGER cash_shift_active_register BEFORE INSERT ON cash_shifts
WHEN NOT EXISTS (SELECT 1 FROM registers r WHERE r.org_id=NEW.org_id AND r.branch_id=NEW.branch_id
                 AND r.id=NEW.register_id AND (NEW.status='closed' OR r.active=1))
BEGIN
    SELECT RAISE(ABORT, 'invalid cash shift register');
END;

CREATE TRIGGER cash_shift_register_immutable BEFORE UPDATE OF org_id, branch_id, register_id ON cash_shifts
WHEN OLD.org_id<>NEW.org_id OR OLD.branch_id<>NEW.branch_id OR OLD.register_id<>NEW.register_id
BEGIN
    SELECT RAISE(ABORT, 'cash shift register is immutable');
END;

CREATE TRIGGER register_with_open_shift BEFORE UPDATE OF active ON registers
WHEN NEW.active=0 AND EXISTS (SELECT 1 FROM cash_shifts s WHERE s.org_id=OLD.org_id AND s.branch_id=OLD.branch_id
                             AND s.register_id=OLD.id AND s.status='open')
BEGIN
    SELECT RAISE(ABORT, 'register has an open shift');
END;

-- Old non-cash events used shift time windows. New events have an explicit acting shift (possibly none).
ALTER TABLE sale_returns ADD COLUMN acting_shift_id TEXT REFERENCES cash_shifts(id);
ALTER TABLE sale_returns ADD COLUMN register_recorded INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_returns_acting_shift ON sale_returns(org_id, acting_shift_id) WHERE acting_shift_id IS NOT NULL;
ALTER TABLE sales ADD COLUMN void_shift_id TEXT REFERENCES cash_shifts(id);
ALTER TABLE sales ADD COLUMN void_register_recorded INTEGER NOT NULL DEFAULT 0;
CREATE INDEX ix_sales_void_shift ON sales(org_id, void_shift_id) WHERE void_shift_id IS NOT NULL;
