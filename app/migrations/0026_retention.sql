-- Old notifications may be cleaned up (older than 180 days); recent ones stay append-only.
-- Invoices, ledgers and the audit log are never deleted by the program.
DROP TRIGGER notifications_no_delete;
CREATE TRIGGER notifications_no_delete BEFORE DELETE ON notifications
WHEN old.created_at > strftime('%Y-%m-%dT%H:%M:%fZ','now','-180 days')
BEGIN
    SELECT RAISE(ABORT, 'notifications are append-only');
END;
