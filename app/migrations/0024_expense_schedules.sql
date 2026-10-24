-- Expenses due every month (rent, electricity, internet...): a reminder before the due day,
-- days chosen per item. The expense itself is still recorded and approved the normal way.
CREATE TABLE expense_schedules (
    id            TEXT PRIMARY KEY,
    org_id        TEXT NOT NULL REFERENCES organizations(id),
    category_id   TEXT NOT NULL REFERENCES expense_categories(id),
    name          TEXT NOT NULL,
    amount        INTEGER,                       -- piasters; NULL = changes every time (electricity)
    day_of_month  INTEGER NOT NULL CHECK (day_of_month BETWEEN 1 AND 31),
    every_months  INTEGER NOT NULL DEFAULT 1 CHECK (every_months IN (1,2,3,6,12)),
    start_month   TEXT NOT NULL,                 -- YYYY-MM, first period
    remind_days   INTEGER NOT NULL DEFAULT 3 CHECK (remind_days BETWEEN 0 AND 60),
    paid_from     TEXT NOT NULL DEFAULT 'outside' CHECK (paid_from IN ('drawer','outside')),
    skipped       TEXT NOT NULL DEFAULT '[]',    -- periods marked "not this time"
    note          TEXT,
    is_active     INTEGER NOT NULL DEFAULT 1,
    created_by    TEXT NOT NULL,
    created_at    TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
ALTER TABLE expenses ADD COLUMN schedule_id TEXT;
ALTER TABLE expenses ADD COLUMN schedule_period TEXT;
CREATE INDEX ix_expenses_schedule ON expenses(schedule_id, schedule_period);
