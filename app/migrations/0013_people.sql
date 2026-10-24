-- 0013 people: owner/manager/pharmacist/assistant, per-person exceptions,
-- discount limits, PIN for quick login and on-screen approval, notifications.

-- per-person exceptions set by the owner (JSON arrays of permission codes)
ALTER TABLE memberships ADD COLUMN perm_grants TEXT NOT NULL DEFAULT '[]';
ALTER TABLE memberships ADD COLUMN perm_denies TEXT NOT NULL DEFAULT '[]';
-- NULL = the role's limit
ALTER TABLE memberships ADD COLUMN max_discount_bp INTEGER CHECK (max_discount_bp BETWEEN 0 AND 10000);

ALTER TABLE roles ADD COLUMN max_discount_bp INTEGER NOT NULL DEFAULT 0
    CHECK (max_discount_bp BETWEEN 0 AND 10000);

ALTER TABLE users ADD COLUMN pin_hash TEXT;
ALTER TABLE users ADD COLUMN pin_failed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN pin_locked_until TEXT;

CREATE TABLE notifications (
    id          TEXT PRIMARY KEY,
    org_id      TEXT NOT NULL REFERENCES organizations(id),
    branch_id   TEXT,
    kind        TEXT NOT NULL,              -- discount, return, void, below_cost, cash_difference, price_change, cost_increase
    severity    TEXT NOT NULL CHECK (severity IN ('info','warning','error')),
    title       TEXT NOT NULL,
    body        TEXT,
    entity_type TEXT,
    entity_id   TEXT,
    actor_id    TEXT,
    approver_id TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX ix_notifications_org ON notifications(org_id, created_at);

CREATE TABLE notification_reads (
    notification_id TEXT NOT NULL REFERENCES notifications(id),
    user_id         TEXT NOT NULL REFERENCES users(id),
    read_at         TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (notification_id, user_id)
);

CREATE TRIGGER notifications_no_update BEFORE UPDATE ON notifications
BEGIN
    SELECT RAISE(ABORT, 'notifications are append-only');
END;
CREATE TRIGGER notifications_no_delete BEFORE DELETE ON notifications
BEGIN
    SELECT RAISE(ABORT, 'notifications are append-only');
END;

-- who gave a line discount, and who approved it on screen (if anyone)
ALTER TABLE sale_lines ADD COLUMN discount_by TEXT;
ALTER TABLE sale_lines ADD COLUMN discount_approved_by TEXT;
