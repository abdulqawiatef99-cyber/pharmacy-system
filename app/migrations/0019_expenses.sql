-- 0019 expenses and consumables: recorded by anyone allowed, counted only after approval
CREATE TABLE expense_categories (
    id        TEXT PRIMARY KEY,
    org_id    TEXT NOT NULL REFERENCES organizations(id),
    name      TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    sort      INTEGER NOT NULL DEFAULT 0,
    UNIQUE (org_id, name)
);
CREATE TABLE expenses (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    branch_id     TEXT NOT NULL REFERENCES branches(id),
    category_id   TEXT NOT NULL REFERENCES expense_categories(id),
    amount        INTEGER NOT NULL CHECK (amount > 0),
    spent_on      TEXT NOT NULL,
    paid_from     TEXT NOT NULL CHECK (paid_from IN ('drawer','outside')),
    note          TEXT,
    status        TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected')),
    shift_id      TEXT,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    decided_by    TEXT,
    decided_at    TEXT,
    reject_reason TEXT
);
CREATE INDEX ix_expenses_org ON expenses(org_id, branch_id, spent_on);
