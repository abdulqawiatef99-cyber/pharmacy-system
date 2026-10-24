-- 0021 attendance and payroll
CREATE TABLE staff_pay (
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    user_id       TEXT NOT NULL REFERENCES users(id),
    monthly_salary INTEGER NOT NULL DEFAULT 0 CHECK (monthly_salary >= 0),
    work_days     INTEGER NOT NULL DEFAULT 26 CHECK (work_days BETWEEN 1 AND 31),
    PRIMARY KEY (org_id, user_id)
);
CREATE TABLE attendance (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    user_id    TEXT NOT NULL REFERENCES users(id),
    day        TEXT NOT NULL,                 -- local date
    check_in   TEXT NOT NULL,                 -- UTC ISO
    check_out  TEXT,
    source     TEXT NOT NULL DEFAULT 'pc',
    note       TEXT,
    edited_by  TEXT
);
CREATE INDEX ix_attendance ON attendance(org_id, user_id, day);
CREATE TABLE staff_advances (
    id         TEXT PRIMARY KEY,
    org_id     TEXT NOT NULL REFERENCES organizations(id),
    user_id    TEXT NOT NULL REFERENCES users(id),
    amount     INTEGER NOT NULL CHECK (amount > 0),
    month      TEXT NOT NULL,                 -- deducted from this month's salary
    note       TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE payroll (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    user_id     TEXT NOT NULL REFERENCES users(id),
    month       TEXT NOT NULL,
    base        INTEGER NOT NULL,
    work_days   INTEGER NOT NULL,
    days_present INTEGER NOT NULL,
    absence_deduction INTEGER NOT NULL,
    advances    INTEGER NOT NULL,
    bonus       INTEGER NOT NULL DEFAULT 0,
    deduction   INTEGER NOT NULL DEFAULT 0,
    net         INTEGER NOT NULL,
    note        TEXT,
    status      TEXT NOT NULL DEFAULT 'approved' CHECK (status IN ('approved','paid')),
    approved_by TEXT NOT NULL,
    paid_at     TEXT,
    expense_id  TEXT,
    UNIQUE (org_id, user_id, month)
);
